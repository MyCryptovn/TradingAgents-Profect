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

from scripts.paper_ledger import COST, get, now, price, settle

LEDGER_PATH = os.getenv("WATCH_LEDGER", "paper-ledger/ledger.json")
MODE = os.getenv("WATCH_MODE", "shadow")

# === THAM SỐ CÓ THỂ CHỈNH ===
MIN_AGE = 300                # giây (5 phút) mới bắt đầu canh
GIVEBACK_SOFT = 0.018        # 1.8% từ đỉnh → cảnh báo
GIVEBACK_HARD = 0.030        # 3% từ đỉnh → ưu tiên đóng
PROFIT_LOCK = 0.030          # lãi ≥ 3% thì bắt đầu bảo vệ
STRONG_TREND_RSI = 55        # RSI còn cao → coi như xu hướng còn khỏe


def ohlc(pair, interval):
    res = get("OHLC", pair=pair, interval=interval)
    rows = res[next(k for k in res if k != "last")]
    return rows[:-1] if len(rows) > 1 else []


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


def flow5(pair):
    res = get("Trades", pair=pair)
    rows = res[next(k for k in res if k != "last")]
    cut = time.time() - 300
    w = [r for r in rows if float(r[2]) >= cut]
    b = sum(float(r[0]) * float(r[1]) for r in w if r[3] == "b")
    s = sum(float(r[0]) * float(r[1]) for r in w if r[3] == "s")
    return len(w), b / max(b + s, 1)


def check_reversal(t):
    if now() - t["t0"] < MIN_AGE:
        return False

    pair = t["pair"]
    d = 1 if t["action"] == "BUY" else -1
    entry = t["entry"]
    px = price(pair)

    # PnL hiện tại (đã trừ chi phí ước tính)
    raw_pnl = d * (px / entry - 1)
    pnl = raw_pnl - COST - t.get("spread", 0)

    r5 = ohlc(pair, 5)
    r1 = ohlc(pair, 1)
    if len(r5) < 15 or len(r1) < 20:
        return False

    c5 = [float(c[4]) for c in r5]
    c1 = [float(c[4]) for c in r1]
    ema9 = ema(c5[-40:], 9)
    ema21 = ema(c5[-60:] if len(c5) >= 60 else c5, 21)
    rs = rsi(c1)
    n5, share = flow5(pair)

    last2 = r5[-2:]
    last3 = r1[-3:]
    since = [c for c in r5 if float(c[0]) >= t["t0"] - 300]

    # --- Tín hiệu đảo chiều ---
    if d == 1:  # lệnh BUY
        peak = max([entry] + [float(c[2]) for c in since])
        give = (peak - px) / peak if peak > 0 else 0
        s_flow = n5 >= 5 and share < 0.42
        s_5m = all(float(c[4]) < float(c[1]) for c in last2) and c5[-1] < ema9
        s_1m = all(float(c[4]) < float(c[1]) for c in last3) and rs < 48
        s_structure = c5[-1] < ema21
    else:       # lệnh SELL
        trough = min([entry] + [float(c[3]) for c in since])
        give = (px - trough) / trough if trough > 0 else 0
        s_flow = n5 >= 5 and share > 0.55
        s_5m = all(float(c[4]) > float(c[1]) for c in last2) and c5[-1] > ema9
        s_1m = all(float(c[4]) > float(c[1]) for c in last3) and rs > 52
        s_structure = c5[-1] > ema21

    hits = sum([s_flow, s_5m, s_1m, s_structure])
    info = (f"pnl={pnl:+.2%} give={give:.1%} flow={share:.0%}/{n5} "
            f"5m={s_5m} 1m={s_1m} rsi={rs:.0f} hits={hits}")

    print("Canh gác", t["symbol"], t["action"], info)

    # === LOGIC QUYẾT ĐỊNH ĐÓNG LỆNH ===

    # 1. Xu hướng vẫn còn rất khỏe + đang lãi → để chạy tiếp
    if pnl > 0.035 and rs > STRONG_TREND_RSI if d == 1 else rs < 100 - STRONG_TREND_RSI and hits <= 1:
        print("→ Xu hướng còn khỏe, giữ lệnh")
        return False

    # 2. Đang lãi khá + có tín hiệu đảo chiều rõ → chốt
    if pnl >= PROFIT_LOCK and (hits >= 2 or give >= GIVEBACK_SOFT):
        reason = "REVERSAL_PROFIT"
        should_close = True

    # 3. Đang lỗ hoặc hòa + tín hiệu đảo chiều mạnh → cắt sớm
    elif pnl < 0.01 and hits >= 3:
        reason = "REVERSAL_CUT"
        should_close = True

    # 4. Giveback cứng từ đỉnh
    elif give >= GIVEBACK_HARD and hits >= 1:
        reason = "REVERSAL_GIVEBACK"
        should_close = True

    else:
        should_close = False

    if not should_close:
        return False

    # Ghi nhận hoặc đóng lệnh
    rev = round(pnl * 100, 3)

    if MODE != "close":
        if "rev_px" in t:
            return False
        t.update(rev_px=px, rev_t=now(), rev_pnl_pct=rev, watch=info)
        print("Tín hiệu đảo chiều (shadow):", t["symbol"], reason, rev)
        return True

    # MODE = close → đóng thật
    t.update(
        status="closed",
        exit=px,
        t1=now(),
        reason=reason,
        pnl_pct=rev,
        watch=info,
    )
    print("Chốt sớm:", t["symbol"], t["action"], reason, f"{rev:+.2f}%")
    return True


def main():
    if not os.path.exists(LEDGER_PATH):
        print("Chưa có sổ", LEDGER_PATH)
        return

    with open(LEDGER_PATH) as f:
        led = json.load(f)

    open_n = sum(
        1 for t in led["trades"]
        if t["status"] == "open" and t["action"] != "HOLD" and not t.get("base")
    )
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
