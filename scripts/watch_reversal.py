#!/usr/bin/env python3
"""Canh gác lệnh paper đang mở. Dữ liệu Hyperliquid mới mỗi vòng.

WATCH_MODE=shadow : chỉ ghi tín hiệu (mặc định).
WATCH_MODE=close  : đóng lệnh trong sổ paper.
WATCH_LOOP_MINUTES=0  : chạy một vòng rồi thoát.
WATCH_LOOP_MINUTES=15 : lặp mỗi WATCH_INTERVAL_SECONDS trong 30 phút.
"""

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
GIVEBACK_HARD = 0.028
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
    if len(closes) < n + 1:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(len(closes) - n, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(len(closes) - n, len(closes))]
    ag = sum(gains) / n
    al = sum(losses) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _meta():
    """metaAndAssetCtxs: lấy 1 lần mỗi vòng cho mọi coin."""
    if "meta" not in _CACHE:
        try:
            ctxs = hl_post({"type": "metaAndAssetCtxs"})
            names = [u["name"] for u in ctxs[0]["universe"]]
            _CACHE["meta"] = dict(zip(names, ctxs[1]))
        except Exception as ex:
            print("HL meta lỗi:", ex)
            _CACHE["meta"] = {}
    return _CACHE["meta"]


def hl_context(coin):
    """Funding và imbalance order book mới trong vòng hiện tại. Lỗi thì trả 0."""
    funding = 0.0
    imb = 0.0
    try:
        funding = float(_meta().get(coin, {}).get("funding", 0) or 0)
    except Exception as ex:
        print("HL funding lỗi:", coin, ex)
    try:
        key = ("book", coin)
        if key not in _CACHE:
            _CACHE[key] = hl_post({"type": "l2Book", "coin": coin})
        d = _CACHE[key]
        bv = sum(float(x["px"]) * float(x["sz"]) for x in d["levels"][0][:10])
        av = sum(float(x["px"]) * float(x["sz"]) for x in d["levels"][1][:10])
        imb = (bv - av) / (bv + av) if (bv + av) > 0 else 0.0
    except Exception as ex:
        print("HL book lỗi:", coin, ex)
    return funding, imb


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


def check_reversal(t):
    if MODE != "close" and "rev_px" in t:
        return False
    if now() - t["t0"] < MIN_AGE:
        return False

    coin = trade_coin(t)
    d = 1 if t["action"] == "BUY" else -1
    entry = t["entry"]
    px = price(coin)
    pnl = d * (px / entry - 1) - COST

    r5 = candles(coin, now() - 8 * 3600, interval="5m")
    r1 = candles(coin, now() - 3 * 3600, interval="1m")
    if len(r5) < 15 or len(r1) < 20:
        return False

    c5 = [r[4] for r in r5]
    c1 = [r[4] for r in r1]
    ema9 = ema(c5[-40:], 9)
    ema21 = ema(c5[-60:], 21)
    rs = rsi(c1)
    last2 = r5[-2:]
    last3 = r1[-3:]

    funding, imbalance = hl_context(coin)
    live, move5, ratio = live_bomxa(r1)

    if d == 1:
        peak = max(t.get("peak", entry), px)
        t["peak"] = peak
        give = (peak - px) / peak if peak > 0 else 0
        s_5m = all(r[4] < r[1] for r in last2) and c5[-1] < ema9
        s_1m = all(r[4] < r[1] for r in last3) and rs < 47
        s_structure = c5[-1] < ema21
        s_funding = funding > 0.0006
        s_imbalance = imbalance < -0.18
        s_bomxa = live == "XA_LIVE"
    else:
        trough = min(t.get("trough", entry), px)
        t["trough"] = trough
        give = (px - trough) / trough if trough > 0 else 0
        s_5m = all(r[4] > r[1] for r in last2) and c5[-1] > ema9
        s_1m = all(r[4] > r[1] for r in last3) and rs > 52
        s_structure = c5[-1] > ema21
        s_funding = funding < -0.0006
        s_imbalance = imbalance > 0.18
        s_bomxa = live == "BOM_LIVE"

    classic_hits = sum([s_5m, s_1m, s_structure])
    hl_hits = sum([s_funding, s_imbalance, s_bomxa])
    total_hits = classic_hits + hl_hits

    info = (f"pnl={pnl:+.2%} give={give:.1%} 1m={s_1m} 5m={s_5m} rsi={rs:.0f} "
            f"hits={classic_hits}+{hl_hits} fund={funding:.5f} imb={imbalance:+.2f} "
            f"live={live or '-'} mv5={move5:+.2%} vr={ratio:.1f}")
    print("Canh gác", t["symbol"], t["action"], info)

    if d == 1:
        strong = pnl > 0.035 and rs > STRONG_TREND_RSI and total_hits <= 1
    else:
        strong = pnl > 0.035 and rs < 100 - STRONG_TREND_RSI and total_hits <= 1
    if strong:
        print("→ Xu hướng còn khỏe, giữ lệnh")
        return False

    reason = None
    if pnl >= 0.028 and total_hits >= 2:
        reason = "REVERSAL_PROFIT"
    elif pnl < 0.008 and total_hits >= 3:
        reason = "REVERSAL_CUT"
    elif give >= GIVEBACK_HARD and total_hits >= 1:
        reason = "REVERSAL_GIVEBACK"
    elif hl_hits >= 2 and give >= GIVEBACK_SOFT:
        reason = "REVERSAL_HL_PRESSURE"

    if reason is None:
        return False

    rev = round(pnl * 100, 3)
    if MODE != "close":
        t.update(rev_px=px, rev_t=now(), rev_first=rev, rev_pnl_pct=rev, watch=info)
        print("Tín hiệu đảo chiều (shadow):", t["symbol"], reason, rev)
        return True

    if t.get("status") != "open":
        return False
    t.update(status="closed", exit=px, t1=now(), reason=reason, pnl_pct=rev, watch=info)
    print("Chốt sớm:", t["symbol"], t["action"], reason, f"{rev:+.2f}%")
    return True


def run_once():
    """Một vòng: chấm SL/TP/TIME bằng settle, rồi canh tín hiệu đảo chiều."""
    _CACHE.clear()
    if not os.path.exists(LEDGER_PATH):
        print("Chưa có sổ", LEDGER_PATH)
        return
    with open(LEDGER_PATH) as f:
        led = json.load(f)

    open_n = sum(1 for t in led["trades"]
                 if t["status"] == "open" and t["action"] != "HOLD" and not t.get("base"))
    print(f"Canh gác HL (mode={MODE}): {open_n} lệnh đang mở")

    changed = False
    for t in led["trades"]:
        if t["status"] != "open" or t["action"] == "HOLD" or t.get("base"):
            continue
        try:
            settle(t)
        except Exception as ex:
            print("Lỗi chấm", t["id"], ex)
        if t["status"] != "open":
            changed = True
            continue
        try:
            if check_reversal(t):
                changed = True
        except Exception as ex:
            print("Lỗi canh gác", t["id"], ex)

    if changed:
        with open(LEDGER_PATH, "w") as f:
            json.dump(led, f, indent=1)


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
