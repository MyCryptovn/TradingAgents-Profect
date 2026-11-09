#!/usr/bin/env python3
"""Canh gác lệnh paper đang mở. Dữ liệu Hyperliquid mới mỗi vòng.

WATCH_MODE=shadow : chỉ ghi tín hiệu (mặc định).
WATCH_MODE=close  : đóng lệnh trong sổ paper.
WATCH_LOOP_MINUTES=15 : lặp mỗi WATCH_INTERVAL_SECONDS trong 15 phút.
WATCH_INTERVAL_SECONDS trong tối đa 15 phút.
"""

import tempfile
import math
import json
import os
import time
from datetime import datetime, timezone

from scripts.paper_ledger import COST, now, price, candles, settle, trade_coin, hl_post

LEDGER_PATH = os.getenv("WATCH_LEDGER", "paper-ledger-hl/ledger.json")
MODE = os.getenv("WATCH_MODE", "shadow")
LOOP_MINUTES = float(os.getenv("WATCH_LOOP_MINUTES", "15"))
INTERVAL = int(os.getenv("WATCH_INTERVAL_SECONDS", "60"))

MIN_AGE = 60
GIVEBACK_SOFT = 0.018
GIVEBACK_HARD = 0.03
STRONG_TREND_RSI = 55
LIVE_MOVE = 0.005        # biến động 5 phút tối thiểu để tính bơm/xả (ước lượng ban đầu, cần đo)
LIVE_VOL_RATIO = 2.0     # khối lượng 5 phút so với trung bình 60 phút (ước lượng ban đầu)

_CACHE = {}  # reset mỗi vòng, tránh dùng dữ liệu cũ


def ema(values, n):
    if not values:
        return 0.0
    k = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, n=14):
    """Wilder RSI using completed candle closes."""
    if n < 2 or not isinstance(closes, (list, tuple)):
        return None

    values = []

    for value in closes:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None

        if not math.isfinite(value) or value <= 0:
            return None

        values.append(value)

    if len(values) < n + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    for i in range(n, len(gains)):
        avg_gain = ((avg_gain * (n - 1)) + gains[i]) / n
        avg_loss = ((avg_loss * (n - 1)) + losses[i]) / n

    if avg_gain == 0 and avg_loss == 0:
        return 50.0

    if avg_loss == 0:
        return 100.0

    if avg_gain == 0:
        return 0.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _meta(dex=None):
    """Cache metadata riêng cho từng perp DEX trong một vòng Watch."""
    key = ("meta", dex or "native")

    if key not in _CACHE:
        try:
            payload = {"type": "metaAndAssetCtxs"}

            if dex:
                payload["dex"] = dex

            ctxs = hl_post(payload)

            if (
                not isinstance(ctxs, list)
                or len(ctxs) < 2
                or not isinstance(ctxs[0], dict)
                or not isinstance(ctxs[1], list)
            ):
                raise RuntimeError("Invalid metaAndAssetCtxs response")

            universe = ctxs[0].get("universe", [])

            if len(universe) != len(ctxs[1]):
                raise RuntimeError("Metadata/context length mismatch")

            mapping = {}

            for asset, context in zip(universe, ctxs[1]):
                name = asset.get("name")

                if name and isinstance(context, dict):
                    mapping[name] = context

                    if dex:
                        mapping[f"{dex}:{name}"] = context

            _CACHE[key] = mapping

        except Exception as ex:
            print("HL meta lỗi:", dex or "native", ex)
            _CACHE[key] = None

    return _CACHE[key]


def hl_context(coin):
    """
    Return:
        funding, imbalance, funding_ok, book_ok

    None means unavailable. Never convert an API error into neutral zero.
    """
    funding = None
    imbalance = None
    funding_ok = False
    book_ok = False

    # Preserve full HIP-3 market identifier, e.g. xyz:COIN.
    if ":" in coin:
        dex, asset_name = coin.split(":", 1)
        metadata = _meta(dex)
    else:
        dex = None
        asset_name = coin
        metadata = _meta()

    if metadata is not None:
        context = (
            metadata.get(coin)
            or metadata.get(asset_name)
        )

        if context is not None:
            try:
                value = float(context["funding"])

                if math.isfinite(value):
                    funding = value
                    funding_ok = True

            except (KeyError, TypeError, ValueError, OverflowError):
                pass

    try:
        key = ("book", coin)

        if key not in _CACHE:
            _CACHE[key] = hl_post({
                "type": "l2Book",
                "coin": coin,
            })

        data = _CACHE[key]
        levels = data["levels"]

        if len(levels) < 2:
            raise ValueError("Invalid L2 levels")

        bids = levels[0][:10]
        asks = levels[1][:10]

        if not bids or not asks:
            raise ValueError("Empty orderbook side")

        def depth_usd(rows):
            total = 0.0

            for row in rows:
                px = float(row["px"])
                sz = float(row["sz"])

                if (
                    not math.isfinite(px)
                    or not math.isfinite(sz)
                    or px <= 0
                    or sz < 0
                ):
                    raise ValueError("Invalid orderbook level")

                total += px * sz

            return total

        bid_value = depth_usd(bids)
        ask_value = depth_usd(asks)
        denominator = bid_value + ask_value

        if denominator <= 0:
            raise ValueError("Empty orderbook depth")

        imbalance = (
            bid_value - ask_value
        ) / denominator

        book_ok = math.isfinite(imbalance)

    except Exception as ex:
        print("HL book lỗi:", coin, ex)

    return funding, imbalance, funding_ok, book_ok


def live_bomxa(r1):
    """Bơm/xả tự tính từ 5 nến 1m đã đóng gần nhất so với nền 60 phút trước đó.
    Trả về (nhãn, biến động 5m, tỷ lệ khối lượng). Nhãn rỗng nếu không đủ điều kiện."""
    if len(r1) < 65:
        return "", 0.0, 0.0
    vol5 = sum(r[5] for r in r1[-5:])
    base = [sum(r[5] for r in r1[i:i + 5]) for i in range(len(r1) - 65, len(r1) - 5, 5)]
    avg5 = sum(base) / len(base) if base else 0.0
    ratio = vol5 / avg5 if avg5 > 0 else 0.0
    move = r1[-1][4] / r1[-6][4] - 1 if r1[-6][4] > 0 else 0.0
    if move <= -LIVE_MOVE and ratio >= LIVE_VOL_RATIO:
        label = "XA_LIVE"
    elif move >= LIVE_MOVE and ratio >= LIVE_VOL_RATIO:
        label = "BOM_LIVE"
    else:
        label = ""
    return label, move, ratio


def timeframe_state(rows, direction, min_count):
    """
    Analyze completed candles.
    direction: 1 = detect bearish reversal against LONG
              -1 = detect bullish reversal against SHORT
    """
    result = {
        "valid": False,
        "early": False,
        "confirm": False,
        "structure": False,
        "rsi": None,
        "close": None,
        "ema9": None,
        "ema21": None,
    }

    if not isinstance(rows, list) or len(rows) < min_count:
        return result

    try:
        opens = [float(row[1]) for row in rows]
        closes = [float(row[4]) for row in rows]

        if any(
            not math.isfinite(x) or x <= 0
            for x in opens + closes
        ):
            return result

        if len(closes) < 22:
            return result

        e9 = ema(closes[-60:], 9)
        e21 = ema(closes[-60:], 21)
        rs = rsi(closes)

        if rs is None:
            return result

        last2 = rows[-2:]

        if direction == 1:
            adverse = all(
                float(row[4]) < float(row[1])
                for row in last2
            )

            confirm = adverse and closes[-1] < e9
            structure = closes[-1] < e21 and e9 < e21
            early = (
                closes[-1] < closes[-2]
                and closes[-2] < closes[-3]
            )

        else:
            adverse = all(
                float(row[4]) > float(row[1])
                for row in last2
            )

            confirm = adverse and closes[-1] > e9
            structure = closes[-1] > e21 and e9 > e21
            early = (
                closes[-1] > closes[-2]
                and closes[-2] > closes[-3]
            )

        result.update({
            "valid": True,
            "early": early,
            "confirm": confirm,
            "structure": structure,
            "rsi": rs,
            "close": closes[-1],
            "ema9": e9,
            "ema21": e21,
        })

        return result

    except (TypeError, ValueError, IndexError, OverflowError):
        return result


def check_reversal(t):
    if MODE != "close" and "rev_px" in t:
        return False

    if now() - t["t0"] < MIN_AGE:
        return False

    if t.get("action") not in ("BUY", "SELL"):
        return False

    coin = trade_coin(t)
    direction = 1 if t["action"] == "BUY" else -1

    entry = float(t["entry"])
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError(f"Invalid entry: {coin}")

    px = float(price(coin))
    if not math.isfinite(px) or px <= 0:
        raise ValueError(f"Invalid price: {coin}")

    pnl = direction * (px / entry - 1) - COST

    # Four independent timeframe histories.
    r1 = candles(
        coin, now() - 3 * 3600, interval="1m"
    )
    r15 = candles(
        coin, now() - 25 * 3600, interval="15m"
    )
    r60 = candles(
        coin, now() - 5 * 86400, interval="1h"
    )
    r240 = candles(
        coin, now() - 11 * 86400, interval="4h"
    )

    # 1m is the early warning layer.
    m1 = timeframe_state(r1, direction, 300)

    # 15m is the primary reversal confirmation.
    m15 = timeframe_state(r15, direction, 60)

    # 1h confirms structural deterioration.
    h1 = timeframe_state(r60, direction, 60)

    # 4h is background context, not an exit veto.
    h4 = timeframe_state(r240, direction, 40)

    if not m15["valid"] or not h1["valid"]:
        print(
            f"WATCH CORE DATA INSUFFICIENT {coin}: "
            f"15m={len(r15)}/60, 1h={len(r60)}/60"
        )
        return False

    # Track maximum favorable excursion.
    if direction == 1:
        peak = max(float(t.get("peak", entry)), px)
        t["peak"] = peak
        give = (peak - px) / peak if peak > 0 else 0.1
    else:
        trough = min(float(t.get("trough", entry)), px)
        t["trough"] = trough
        give = (px - trough) / trough if trough > 0 else 0.1

    # 1m live pressure.
    live, move5, ratio = live_bomxa(r1)

    if direction == 1:
        live_pressure = live == "XA_LIVE"
    else:
        live_pressure = live == "BOM_LIVE"

    # Funding and orderbook pressure.
    context = hl_context(coin)

    funding = None
    imbalance = None
    funding_ok = False
    book_ok = False

    # Only trust the four-value version of hl_context().
    # The legacy two-value version cannot distinguish API errors
    # from a genuine neutral reading.
    if isinstance(context, (tuple, list)) and len(context) == 4:
        funding, imbalance, funding_ok, book_ok = context

    funding_pressure = False
    book_pressure = False

    if funding_ok and funding is not None:
        if direction == 1:
            funding_pressure = funding > 0.0001
        else:
            funding_pressure = funding < -0.0001

    if book_ok and imbalance is not None:
        if direction == 1:
            book_pressure = imbalance < -0.18
        else:
            book_pressure = imbalance > 0.18

    # 1m: early warning, never sufficient alone.
    early_hits = sum([
        bool(m1["early"]),
        bool(live_pressure),
    ])

    # 15m: primary confirmation.
    confirm_15m = bool(m15["confirm"])

    # 1h: structural confirmation.
    confirm_1h = bool(h1["structure"])

    # 4h: background trend.
    context_4h = bool(h4["structure"]) if h4["valid"] else False

    # Independent evidence groups.
    # Funding and book are grouped with live pressure to avoid
    # counting correlated observations as independent votes.
    pressure_hits = sum([
        bool(funding_pressure),
        bool(book_pressure),
        bool(live_pressure),
    ])

    pressure_confirmed = pressure_hits >=2

    # Strong trend is based on 15m RSI and higher timeframe context.
    rs15 = m15["rsi"]

    if direction == 1:
        strong = (
            pnl > 0.025
            and rs15 > STRONG_TREND_RSI
            and not confirm_15m
            and not confirm_1h
            and not context_4h
            and not pressure_confirmed
        )
    else:
        strong = (
            pnl > 0.025
            and rs15 < 100 - STRONG_TREND_RSI
            and not confirm_15m
            and not confirm_1h
            and not context_4h
            and not pressure_confirmed
        )

    funding_text = (
        f"{funding:.5f}" if funding_ok else "UNKNOWN"
    )
    book_text = (
        f"{imbalance:+.2f}" if book_ok else "UNKNOWN"
    )

    info = (
        f"pnl={pnl:+.2%} give={give:.1%} "
        f"1m={m1['early']} live={live or '-'} "
        f"15m={confirm_15m} "
        f"1h={confirm_1h} "
        f"4h={context_4h} "
        f"RSI15={rs15:.1f} "
        f"pressure={pressure_hits}/3 "
        f"fund={funding_text} book={book_text} "
        f"mv5={move5:+.2%} vol={ratio:.1f}"
    )

    print("Canh gác", t["symbol"], t["action"], info)

    # Risk protection always has priority.
    reason = None

    if give >= GIVEBACK_HARD:
        reason = "REVERSAL_GIVEBACK"

    elif strong:
        print("→ Xu hướng còn khỏe, giữ lệnh")
        return False

    # Profit protection:
    # 15m confirmation + at least one independent supporting group.
    elif (
        pnl >= 0.028
        and confirm_15m
        and (
            confirm_1h
            or early_hits >= 1
            or pressure_confirmed
        )
    ):
        reason = "REVERSAL_PROFIT"

    # Weak/losing position:
    # Require 15m confirmation and another supporting timeframe/group.
    elif (
        pnl < 0.008
        and confirm_15m
        and (
            confirm_1h
            or early_hits >= 1
            or pressure_confirmed
        )
    ):
        reason = "REVERSAL_CUT"

    # Soft giveback:
    # Need real pressure and confirmation from 15m or 1h.
    elif (
        give >= GIVEBACK_SOFT
        and pressure_confirmed
        and (confirm_15m or confirm_1h)
    ):
        reason = "REVERSAL_HL_PRESSURE"

    if reason is None:
        return False

    rev = round(pnl * 100, 3)

    if MODE != "close":
        t.update(
            rev_px=px,
            rev_t=now(),
            rev_first=rev,
            rev_pnl_pct=rev,
            rev_reason=reason,
            watch=info,
        )

        print(
            "Tín hiệu đảo chiều (shadow):",
            t["symbol"],
            reason,
            rev,
        )
        return True

    if t.get("status") != "open":
        return False

    t.update(
        status="closed",
        exit=px,
        t1=now(),
        reason=reason,
        pnl_pct=rev,
        watch=info,
    )

    print(
        "Chốt sớm:",
        t["symbol"],
        t["action"],
        reason,
        f"{rev:+.2f}%",
    )

    return True


def run_once():
    """Một vòng: chấm SL/TP/TIME bằng settle, rồi canh tín hiệu đảo chiều."""
    _CACHE.clear()

    if not os.path.exists(LEDGER_PATH):
        print("Chưa có sổ", LEDGER_PATH)
        return

    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            led = json.load(f)
    except Exception as ex:
        print("LỖI ĐỌC LEDGER — KHÔNG GHI ĐÈ:", ex)
        return

    if not isinstance(led, dict) or not isinstance(led.get("trades"), list):
        print("LEDGER KHÔNG HỢP LỆ — KHÔNG GHI ĐÈ")
        return

    trades = led["trades"]

    open_n = sum(
        1 for t in trades
        if isinstance(t, dict)
        and t.get("status") == "open"
        and t.get("action") != "HOLD"
        and not t.get("base")
    )

    print(f"Canh gác HL (mode={MODE}): {open_n} lệnh đang mở")

    changed = False

    for t in trades:
        if not isinstance(t, dict):
            continue

        if (
            t.get("status") != "open"
            or t.get("action") == "HOLD"
            or t.get("base")
        ):
            continue

        before_status = t.get("status")

        try:
            settle(t)
        except Exception as ex:
            print("Lỗi chấm", t.get("id"), ex)

        if t.get("status") != before_status:
            changed = True

        if t.get("status") != "open":
            continue

        # Peak/trough must be persisted even without an exit signal.
        before_extremes = (
            t.get("peak"),
            t.get("trough"),
            t.get("rev_px"),
        )

        try:
            check_reversal(t)
        except Exception as ex:
            print("Lỗi canh gác", t.get("id"), ex)

        after_extremes = (
            t.get("peak"),
            t.get("trough"),
            t.get("rev_px"),
        )

        if after_extremes != before_extremes:
            changed = True

        if t.get("status") != "open":
            changed = True

    if not changed:
        return

    # Atomic save: never leave a partially written ledger.
    directory = os.path.dirname(os.path.abspath(LEDGER_PATH))
    os.makedirs(directory, exist_ok=True)

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".watch-ledger-",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = f.name
            json.dump(led, f, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, LEDGER_PATH)
        print("Đã lưu ledger Watch an toàn.")

    except Exception:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise   

def main():
    end = time.time() + LOOP_MINUTES * 60
    n = 0
    while True:
        n += 1
        print(f"WATCH VÒNG {n} {datetime.now(timezone.utc):%H:%M:%S} UTC")
        run_once()
        if LOOP_MINUTES <= 0 or time.time() + INTERVAL > end:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
