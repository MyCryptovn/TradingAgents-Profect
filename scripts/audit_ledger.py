"""Đối chiếu sổ paper: công thức PnL, nguồn giá, và thống kê theo hướng/lý do."""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")
from scripts.pnl import pnl_pct

LEDGER = Path(sys.argv[1] if len(sys.argv) > 1 else "paper-ledger-hl/ledger.json")


def main():
    led = json.loads(LEDGER.read_text(encoding="utf-8"))
    trades = [t for t in led["trades"] if t.get("status") == "closed" and t.get("action") in ("BUY", "SELL")]
    bad_formula, no_src, stats = [], [], defaultdict(list)

    for t in trades:
        if t.get("src") != "hyperliquid":
            no_src.append(t["id"])
        if t.get("exit") and t.get("entry") and "pnl_pct" in t:
            expect = pnl_pct(t["action"], t["entry"], t["exit"])
            if abs(expect - t["pnl_pct"]) > 0.01:
                bad_formula.append((t["id"], t["pnl_pct"], expect))
        stats[("dir", t["action"])].append(t["pnl_pct"])
        stats[("why", t.get("reason") or "NONE")].append(t["pnl_pct"])

    print(f"Lệnh đã đóng: {len(trades)} | không có src=hyperliquid: {len(no_src)} | PnL lệch công thức: {len(bad_formula)}")
    for (k, name), vals in sorted(stats.items()):
        wins = sum(1 for v in vals if v > 0)
        avg_w = sum(v for v in vals if v > 0) / max(wins, 1)
        losses = [v for v in vals if v <= 0]
        avg_l = sum(losses) / max(len(losses), 1)
        print(f"{k[0]:>4} {name:<18} n={len(vals):<4} win={wins / len(vals):.0%} "
              f"tổng={sum(vals):+.2f}% TB_thắng={avg_w:+.2f}% TB_thua={avg_l:+.2f}%")
    for row in bad_formula[:10]:
        print("LỆCH:", row)


if __name__ == "__main__":
    main()
