#!/usr/bin/env python3
"""Canh gác đảo chiều: xem lệnh paper đang mở bằng nến 1m, 5m và dòng tiền Kraken.
WATCH_MODE=shadow (mặc định): chỉ ghi nhận tín hiệu, KHÔNG đóng lệnh.
WATCH_MODE=close: đóng lệnh với lý do REVERSAL."""
import json
import os
import time

from scripts.paper_ledger import COST, get, now, price, settle

LEDGER_PATH = os.getenv("WATCH_LEDGER", "paper-ledger/ledger.json")
MODE = os.getenv("WATCH_MODE", "shadow")
MIN_AGE = 1200
GIVEBACK = 0.02


def ohlc(pair, interval):
    res = get("OHLC", pair=pair, interval=interval)
    rows = res[next(k for k in res if k != "last")]
    return rows[:-1] if len(rows) > 1 else []


def ema(values, n):
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
    ag, al = sum(gains) / n, sum(losses) / n
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
    e = t["entry"]
    r5 = ohlc(pair, 5)
    r1 = ohlc(pair, 1)
    if len(r5) < 12 or len(r1) < 15:
        return False
    c5 = [float(c[4]) for c in r5]
    c1 = [float(c[4]) for c in r1]
    ema9 = ema(c5[-40:], 9)
    last2 = r5[-2:]
    last3 = r1[-3:]
    n5, share = flow5(pair)
    since = [c for c in r5 if float(c[0]) >= t["t0"] - 300]
    px = price(pair)
    rs = rsi(c1)
    if d == 1:
        peak = max([e] + [float(c[2]) for c in since])
        give = (peak - px) / peak
        s_flow = n5 >= 5 and share < 0.40
        s_5m = all(float(c[4]) < float(c[1]) for c in last2) and c5[-1] < ema9
        s_1m = all(float(c[4]) < float(c[1]) for c in last3) and rs < 45
    else:
        trough = min([e] + [float(c[3]) for c in since])
        give = (px - trough) / trough
        s_flow = n5 >= 5 and share > 0.60
        s_5m = all(float(c[4]) > float(c[1]) for c in last2) and c5[-1] > ema9
        s_1m = all(float(c[4]) > float(c[1]) for c in last3) and rs > 55
    s_give = give >= GIVEBACK
    hits = [s_flow, s_5m, s_1m, s_give]
    info = f"flow5={share:.0%}/{n5} 5m={s_5m} 1m={s_1m} rsi1m={rs:.0f} give={give:.1%}"
    print("Canh gác", t["symbol"], t["action"], info, "tín hiệu:", sum(hits))
    if sum(hits) < 2:
        return False
    rev = round((d * (px / e - 1) - COST - t.get("spread", 0)) * 100, 3)
    if MODE != "close":
        if "rev_px" in t:
            return False
        t.update(rev_px=px, rev_t=now(), rev_pnl_pct=rev, watch=info)
        print("Tín hiệu đảo chiều (chỉ ghi nhận, chưa đóng)", t["symbol"], t["action"], px, rev)
        return True
    t.update(status="closed", exit=px, t1=now(), reason="REVERSAL", pnl_pct=rev, watch=info)
    print("Chốt sớm", t["symbol"], t["action"], px)
    return True


def main():
    if not os.path.exists(LEDGER_PATH):
        print("Chưa có sổ", LEDGER_PATH)
        return
    with open(LEDGER_PATH) as f:
        led = json.load(f)
    open_n = sum(1 for t in led["trades"]
                 if t["status"] == "open" and t["action"] != "HOLD" and not t.get("base"))
    print(f"Canh gác (chế độ {MODE}): {open_n} lệnh đang mở")
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
