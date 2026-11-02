#!/usr/bin/env python3
"""
Canh gác đảo chiều thông minh (bản nâng cấp).
- Để lệnh thắng chạy khi xu hướng còn khỏe.
- Chủ động chốt lãi khi xuất hiện tín hiệu đảo chiều rõ.
- Bảo vệ vốn khi lệnh xấu.

WATCH_MODE=shadow  : chỉ ghi nhận tín hiệu (mặc định)
WATCH_MODE=close   : được phép đóng lệnh
"""

import json
import os
import time

from scripts.paper_ledger import COST, now, price, candles, settle, trade_coin

LEDGER_PATH = os.getenv("WATCH_LEDGER", "paper-ledger/ledger.json")
MODE = os.getenv("WATCH_MODE", "shadow")

# === THAM SỐ CÓ THỂ CHỈNH ===
MIN_AGE = 60                # giây (1 phút) mới bắt đầu canh
GIVEBACK_SOFT = 0.018        # 2% từ đỉnh → cảnh báo
GIVEBACK_HARD = 0.030        # 3% từ đỉnh → ưu tiên đóng
PROFIT_LOCK = 0.035          # lãi ≥ 3,5% thì bắt đầu bảo vệ
STRONG_TREND_RSI = 55        # RSI còn cao → coi như xu hướng còn khỏe


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


def taker_share(r1, minutes=15):
    """Ước lượng tỷ lệ áp lực mua từ nến 1m: vị trí giá đóng trong biên độ nến nhân khối lượng.
    Xấp xỉ, không phải dòng lệnh mua/bán thật."""
    rows = r1[-minutes:]
    buy = sell = 0.0
    for r in rows:
        o, h, l, c, v = r[1], r[2], r[3], r[4], r[5]
        rng = h - l
        frac = (c - l) / rng if rng > 0 else (1.0 if c >= o else 0.0)
        buy += v * frac
        sell += v * (1 - frac)
    tot = buy + sell
    return (buy / tot if tot > 0 else 0.5), len(rows)


def check_reversal(t):
    if MODE != "close" and "rev_px" in t:
        return False
    if now() - t["t0"] < MIN_AGE:
        return False

    coin = trade_coin(t)
    d = 1 if t["action"] == "BUY" else -1
    entry = t["entry"]
    px = price(coin)
    pnl = d * (px / entry - 1) - COST - t.get("spread", 0)

    r5 = candles(coin, now() - 8 * 3600, interval="5m")
    r1 = candles(coin, now() - 3 * 3600, interval="1m")
    if len(r5) < 15 or len(r1) < 20:
        return False

    c5 = [r[4] for r in r5]
    c1 = [r[4] for r in r1]
    ema9 = ema(c5[-40:], 9)
    ema21 = ema(c5[-60:], 21)
    rs = rsi(c1)
    share15, n15 = taker_share(r1, 15)   # 15 phút gần nhất
    share5, _ = taker_share(r1, 5)       # 5 phút gần nhất, bắt nhịp xả đang diễn ra
    share = share15

    last2 = r5[-2:]
    last3 = r1[-3:]

    if d == 1:
        peak = max(t.get("peak", entry), px)
        t["peak"] = peak
        give = (peak - px) / peak if peak > 0 else 0
        s_flow = share < 0.42
        s_5m = all(r[4] < r[1] for r in last2) and c5[-1] < ema9
        s_1m = all(r[4] < r[1] for r in last3) and rs < 48
        s_structure = c5[-1] < ema21
    else:
        trough = min(t.get("trough", entry), px)
        t["trough"] = trough
        give = (px - trough) / trough if trough > 0 else 0
        s_flow = share > 0.55
        s_5m = all(r[4] > r[1] for r in last2) and c5[-1] > ema9
        s_1m = all(r[4] > r[1] for r in last3) and rs > 52
        s_structure = c5[-1] > ema21

    hits = sum([s_flow, s_5m, s_1m, s_structure])
    info = (f"pnl={pnl:+.2%} give={give:.1%} flow15={share15:.0%} flow5={share5:.0%}"
            f"5m={s_5m} 1m={s_1m} rsi={rs:.0f} hits={hits}")
    print("Canh gác", t["symbol"], t["action"], info)

    if d == 1:
        strong = pnl > 0.035 and rs > STRONG_TREND_RSI and hits <= 1
    else:
        strong = pnl > 0.035 and rs < 100 - STRONG_TREND_RSI and hits <= 1
    if strong:
        print("→ Xu hướng còn khỏe, giữ lệnh")
        return False

    if pnl >= 0.03 and hits >= 1 and (hits >= 2 or give >= GIVEBACK_SOFT):
        reason = "REVERSAL_PROFIT"
    elif pnl < 0.01 and hits >= 3:
        reason = "REVERSAL_CUT"
    elif give >= GIVEBACK_HARD and hits >= 1:
        reason = "REVERSAL_GIVEBACK"
    else:
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


def main():
    if not os.path.exists(LEDGER_PATH):
        print("Chưa có sổ", LEDGER_PATH)
        return
    with open(LEDGER_PATH) as f:
        led = json.load(f)

    open_n = sum(1 for t in led["trades"]
                 if t["status"] == "open" and t["action"] != "HOLD" and not t.get("base"))
    print(f"Canh gác thông minh (mode={MODE}): {open_n} lệnh đang mở")

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


if __name__ == "__main__":
    main()
