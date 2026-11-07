"""Lớp thực thi Hyperliquid — CHỈ DRY-RUN. Không ký, không gửi lệnh.

Đọc các lệnh BUY/SELL đang mở trong sổ, tính khối lượng dự định theo giới hạn
rủi ro, và ghi ra file để đối chiếu với sổ paper. Không có hàm nào gửi lệnh.
"""
import json
import os
from datetime import datetime, timezone

LEDGER_PATH = os.getenv("EXEC_LEDGER", "paper-ledger-hl/ledger.json")
OUT_PATH = os.getenv("EXEC_OUT", "tradingagents-paper-results/exec_dryrun.json")

NOTIONAL_USD = float(os.getenv("EXEC_NOTIONAL_USD", "50"))          # mỗi lệnh (dry-run)
MAX_OPEN = int(os.getenv("EXEC_MAX_OPEN", "2"))                     # số lệnh mở tối đa
DAILY_LOSS_LIMIT_PCT = float(os.getenv("EXEC_DAILY_LOSS_PCT", "2")) # lỗ tối đa/ngày (%)
HALT_FILE = os.getenv("EXEC_HALT_FILE", "HALT")                     # có file này thì không mở lệnh mới


def halted() -> bool:
    return os.path.exists(HALT_FILE)


def load_open(led):
    return [t for t in led["trades"]
            if t.get("status") == "open" and t.get("action") in ("BUY", "SELL")]


def daily_pnl_pct(led, day):
    total = 0.0
    for t in led["trades"]:
        if t.get("status") == "closed" and t.get("t1"):
            d = datetime.fromtimestamp(int(t["t1"]), tz=timezone.utc).strftime("%Y-%m-%d")
            if d == day and "pnl_pct" in t:
                total += float(t["pnl_pct"]) * NOTIONAL_USD / 100
    return total / NOTIONAL_USD * 100 if NOTIONAL_USD else 0.0


def plan(led):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reasons = []
    if halted():
        reasons.append("HALT")
    if daily_pnl_pct(led, today) <= -DAILY_LOSS_LIMIT_PCT:
        reasons.append("DAILY_LOSS_LIMIT")
    opens = load_open(led)
    if len(opens) > MAX_OPEN:
        reasons.append("MAX_OPEN_EXCEEDED")

    orders = []
    for t in opens:
        orders.append({
            "id": t["id"],
            "coin": t.get("pair"),
            "side": "B" if t["action"] == "BUY" else "A",
            "notional_usd": NOTIONAL_USD,
            "entry_ref": t.get("entry"),
            "stop_ref_pct": 3.0,
            "would_send": not reasons,   # dry-run: không bao giờ gửi
        })
    return {"ts": datetime.now(timezone.utc).isoformat(), "dry_run": True,
            "blocked_reasons": reasons, "orders": orders}


def main():
    with open(LEDGER_PATH) as f:
        led = json.load(f)
    out = plan(led)
    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"DRY RUN: {len(out['orders'])} lệnh dự định, chặn bởi {out['blocked_reasons'] or 'không'}")
    print("REAL ORDERS: DISABLED")


if __name__ == "__main__":
    main()
