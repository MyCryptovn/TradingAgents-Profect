#!/usr/bin/env python3
"""Sổ lệnh paper + chấm điểm PnL. Chỉ dùng thư viện chuẩn, giá lấy từ Kraken.

  python scripts/paper_ledger.py record   # ghi quyết định của lần chạy này vào sổ
  python scripts/paper_ledger.py score    # chốt lệnh đến hạn / chạm SL-TP + báo cáo
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

RESULT_FILE = os.getenv("RESULT_FILE", "tradingagents-paper-results/decisions.json")
LEDGER = "paper-ledger/ledger.json"
REPORT = "paper-ledger/report.md"
HORIZON_H = float(os.getenv("HORIZON_HOURS", "24"))       # giữ lệnh tối đa
SL = float(os.getenv("STOP_LOSS_PCT", "3")) / 100          # cắt lỗ
TP = float(os.getenv("TAKE_PROFIT_PCT", "6")) / 100        # chốt lời
FEE = float(os.getenv("FEE_PCT", "0.26"))                  # phí taker mỗi chiều (%)
SLIP = float(os.getenv("SLIPPAGE_PCT", "0.05"))            # trượt giá mỗi chiều (%)
COST = 2 * (FEE + SLIP) / 100                              # chi phí khứ hồi
API = "https://api.kraken.com/0/public/"


def now():
    return int(time.time())


def get(path, **params):
    url = API + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    if d.get("error"):
        raise RuntimeError(d["error"])
    return d["result"]


def candles(pair, since):
    res = get("OHLC", pair=pair, interval=15, since=since)
    return res[next(k for k in res if k != "last")]


def price(pair):
    res = get("Ticker", pair=pair)
    return float(next(iter(res.values()))["c"][0])


def spread(pair):
    t = next(iter(get("Ticker", pair=pair).values()))
    a, b = float(t["a"][0]), float(t["b"][0])
    return (a - b) / ((a + b) / 2)
def price_at(pair, ts):
    rows = candles(pair, ts - 900)
    return float(rows[0][4]) if rows else price(pair)


def pair_of(sym):
    s = sym.upper().replace("-", "/").replace("_", "/")
    base = s.split("/")[0]
    if "/" not in s and base.endswith("USD"):
        base = base[:-3]
    base = {"BTC": "XBT"}.get(base, base)
    return base + "USD"


def extract(data):
    """Đọc decisions.json theo nhiều dạng phổ biến -> [(symbol, BUY|SELL|HOLD, raw)]."""
    items = data
    if isinstance(data, dict):
        for k in ("decisions", "results", "candidates", "items"):
            if isinstance(data.get(k), list):
                items = data[k]
                break
        else:
            items = [
                dict(v, symbol=k) if isinstance(v, dict) else {"symbol": k, "decision": v}
                for k, v in data.items()
            ]
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        sym = next((it[k] for k in ("symbol", "ticker", "pair", "coin", "asset") if it.get(k)), None)
        raw = str(next((it[k] for k in ("decision", "action", "rating", "signal", "final_decision")
                        if it.get(k)), "HOLD")).upper()
        act = "BUY" if ("BUY" in raw or "OVERWEIGHT" in raw) else \
                        "SELL" if ("SELL" in raw or "UNDERWEIGHT" in raw) else "HOLD"
        if sym:
            out.append((str(sym), act, raw))
    return out


def load():
    try:
        with open(LEDGER) as f:
            return json.load(f)
    except Exception:
        return {"trades": []}


def save(led):
    os.makedirs("paper-ledger", exist_ok=True)
    with open(LEDGER, "w") as f:
        json.dump(led, f, indent=1)


def record():
    if not os.path.exists(RESULT_FILE):
        print("Không thấy", RESULT_FILE)
        return
    with open(RESULT_FILE) as f:
        decisions = extract(json.load(f))
    with open(RESULT_FILE) as f:
        scans = {d.get("symbol"): d.get("scan", "") for d in json.load(f) if isinstance(d, dict)}
    led = load()
    known = {t["id"] for t in led["trades"]}
    run = os.getenv("GITHUB_RUN_ID", str(now()))
    btc0 = price("XBTUSD")
    for sym, act, raw in decisions:
        if raw == "REVIEW":
            continue
        tid = f"{run}-{sym}"
        if tid in known:
            continue
        try:
            p = pair_of(sym)
            entry = price(p)
            sp = spread(p)
        except Exception as e:
            print("Bỏ qua", sym, e)
            continue
        led["trades"].append({"id": tid, "symbol": sym, "pair": p, "action": act, "raw": raw,
                              "t0": now(), "entry": entry, "btc0": btc0, "status": "open", "spread": sp, "scan": scans.get(sym, "") })
        print("Ghi:", sym, act, entry)
    save(led)


def settle(t):
    end = t["t0"] + HORIZON_H * 3600
    if t["action"] == "HOLD":
        if now() < end:
            return
        px = price_at(t["pair"], end)
        t.update(status="closed", exit=px, t1=end, move_pct=round((px / t["entry"] - 1) * 100, 3)),
        return
    d = 1 if t["action"] == "BUY" else -1  # SELL = mô phỏng short
    e = t["entry"]
    sl, tp = e * (1 - d * SL), e * (1 + d * TP)
    exit_px = reason = t1 = None
    best = 0.0
    for c in candles(t["pair"], t["t0"]):
        ts, hi, lo = int(c[0]), float(c[2]), float(c[3])
        if ts >= end:
            break
        fav = (hi / e - 1) if d == 1 else (1 - lo / e)
        best = max(best, fav)
        sl_hit = lo <= sl if d == 1 else hi >= sl
        tp_hit = hi >= tp if d == 1 else lo <= tp
        if sl_hit:  # cùng nến chạm cả hai -> tính SL (thận trọng)
            exit_px, reason, t1 = sl, "SL", ts
            break
        if tp_hit:
            exit_px, reason, t1 = tp, "TP", ts
            break
    if exit_px is None:
        if now() < end:
            return
        exit_px, reason, t1 = price_at(t["pair"], end), "TIME", end
    t.update(status="closed", exit=exit_px, t1=t1, reason=reason,
             pnl_pct=round((d * (exit_px / e - 1) - COST - t.get("spread", 0)) * 100, 3),
             mfe_pct=round(best * 100, 3))


def report(led):
    T = led["trades"]
    B = [t for t in T if t.get("base")]
    T = [t for t in T if not t.get("base")]
    cl = sorted([t for t in T if t["status"] == "closed" and t["action"] != "HOLD"], key=lambda t: t["t1"])
    op = [t for t in T if t["status"] == "open" and t["action"] != "HOLD"]
    hd = [t for t in T if t["action"] == "HOLD" and t["status"] == "closed"]
    L = ["# Báo cáo paper trading",
         f"Cập nhật: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
         f"- Lệnh đã chốt: **{len(cl)}** | đang mở: {len(op)} | HOLD đã chấm: {len(hd)}",
         f"- Thiết lập: giữ tối đa {HORIZON_H:g}h, SL {SL*100:g}%, TP {TP*100:g}%, "
         f"chi phí khứ hồi {COST*100:.2f}%"]
    if cl:
        p = [t["pnl_pct"] for t in cl]
        w = [x for x in p if x > 0]
        l = [x for x in p if x <= 0]
        eq = peak = dd = 0.0
        for x in p:
            eq += x
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        pf = f"{sum(w) / abs(sum(l)):.2f}" if l and sum(l) else "n/a"
        bench = sum(t.get("bench_pct,0") for t in cl)
        L += ["", "## Kết quả lệnh BUY/SELL",
              f"- Win rate: **{len(w) / len(p) * 100:.1f}%** ({len(w)}/{len(p)})",
              f"- PnL trung bình/lệnh: {sum(p) / len(p):+.3f}%",
              f"- Tổng PnL (cộng dồn): **{sum(p):+.2f}%**",
              f"- Profit factor: {pf}",
              f"- Max drawdown: {dd:.2f}%",
              f"- Benchmark (cùng hướng, cùng kỳ, theo BTC): {bench:+.2f}%  "
              f"-> bot {'THẮNG' if sum(p) > bench else 'THUA'} benchmark"]
        if len(cl) < 20:
            L.append("- ⚠️ Dưới 20 lệnh: chưa đủ ý nghĩa thống kê.")
        L += ["", "## 10 lệnh gần nhất", "| Coin | Hướng | Vào | Ra | Lý do | PnL % |", "|---|---|---|---|---|---|"]
        for t in cl[-10:][::-1]:
            L.append(f"| {t['symbol']} | {t['action']} | {t['entry']:.6g} | {t['exit']:.6g} | "
                     f"{t['reason']} | {t['pnl_pct']:+.2f} |")
        mf = [t["mfe_pct"] for t in cl if "mfe_pct" in t]
        if mf:
            L.append(f"- Lãi tối đa từng đạt (trung bình): {sum(mf) / len(mf):.2f}% ({len(mf)} lệnh)")
    if hd:
        big = [h for h in hd if abs(h["move_pct"]) >= SL * 100]
        L += ["", "## Quyết định HOLD",
              f"- Sau {HORIZON_H:g}h, giá biến động ≥ {SL*100:g}% ở {len(big)}/{len(hd)} lần "
              f"(có thể đã bỏ lỡ cơ hội hoặc tránh được lỗ)",
              f"- Biến động tuyệt đối trung bình: {sum(abs(h['move_pct']) for h in hd) / len(hd):.2f}%"]
        up = [h for h in big if h["move_pct"] > 0]
        down = [h for h in big if h["move_pct"] < 0]
        L += [f"- Trong số đó: tăng ≥ {SL*100:g}% = {len(up)} lần (HOLD bỏ lỡ BUY), "
              f"giảm ≥ {SL*100:g}% = {len(down)} lần (HOLD tránh được lỗ)",
              f"- Biến động trung bình có dấu: {sum(h['move_pct'] for h in hd) / len(hd):+.2f}%"]
    text = "\n".join(L) + "\n"
    os.makedirs("paper-ledger", exist_ok=True)
    with open(REPORT, "w") as f:
        f.write(text)
    summ = os.getenv("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as f:
            f.write(text)
    print(text)


def score():
    led = load()
    for t in led["trades"]:
        if t["status"] == "open":
            try:
                settle(t)
            except Exception as e:
                print("Lỗi chấm", t["id"], e)
    save(led)
    report(led)


if __name__ == "__main__":
    {"record": record}.get(sys.argv[1] if len(sys.argv) > 1 else "score", score)()
