#!/usr/bin/env python3
"""Paper Review: phân tích ledger + decisions.json, xuất báo cáo Markdown.
Chạy: python scripts/paper_review.py
Không cần API key. Chỉ dùng số liệu trong file.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path(os.getenv("LEDGER_FILE", "paper-ledger/ledger.json"))
DECISIONS = Path(os.getenv("RESULT_FILE", "tradingagents-paper-results/decisions.json"))
OUT_MD = Path(os.getenv("REVIEW_OUT", "paper-ledger/review.md"))
BENCHMARK_BTC_PCT = float(os.getenv("BENCHMARK_BTC_PCT", "1.53"))  # chỉnh nếu biết chính xác


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def analyze(led: dict) -> dict:
    trades = led.get("trades") or []
    closed = [
        t for t in trades
        if t.get("status") == "closed"
        and t.get("action") in ("BUY", "SELL")
        and not t.get("base")
    ]
    open_t = [
        t for t in trades
        if t.get("status") == "open"
        and t.get("action") in ("BUY", "SELL")
        and not t.get("base")
    ]
    holds = [t for t in trades if t.get("action") == "HOLD"]

    pnls = [safe_float(t.get("pnl_pct")) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    n = len(pnls)
    win_rate = (len(wins) / n * 100) if n else 0.0
    avg_pnl = (sum(pnls) / n) if n else 0.0
    total_pnl = sum(pnls)
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # Max drawdown xấp xỉ trên chuỗi pnl cộng dồn
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    # Phân loại lý do đóng
    reasons = Counter()
    for t in closed:
        r = str(t.get("reason") or "UNKNOWN").upper()
        if "SL" in r:
            reasons["SL"] += 1
        elif "TP" in r:
            reasons["TP"] += 1
        elif "REVERSAL" in r or "REV" in r:
            reasons["REVERSAL"] += 1
        elif "TIME" in r:
            reasons["TIME"] += 1
        else:
            reasons[r or "OTHER"] += 1

    # Theo hướng
    by_side = defaultdict(list)
    for t in closed:
        by_side[t.get("action", "?")].append(safe_float(t.get("pnl_pct")))

    # 10 lệnh gần nhất
    recent = sorted(closed, key=lambda t: t.get("t1") or t.get("t0") or 0, reverse=True)[:10]

    return {
        "n_closed": n,
        "n_open": len(open_t),
        "n_hold_records": len(holds),
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "total_pnl": total_pnl,
        "profit_factor": profit_factor,
        "max_dd": max_dd,
        "reasons": dict(reasons),
        "by_side": {k: {"n": len(v), "avg": sum(v) / len(v) if v else 0, "sum": sum(v)} for k, v in by_side.items()},
        "recent": recent,
        "open_t": open_t,
        "pnls": pnls,
    }


def top_issues_and_strengths(s: dict) -> tuple[list[str], list[str]]:
    issues, strengths = [], []

    if s["n_closed"] < 20:
        issues.append(f"Sample size nhỏ ({s['n_closed']} lệnh) → chưa đủ ý nghĩa thống kê.")
    if s["win_rate"] < 40:
        issues.append(f"Win rate thấp ({s['win_rate']:.1f}%).")
    if s["profit_factor"] < 1:
        issues.append(f"Profit Factor {s['profit_factor']:.2f} < 1 → kỳ vọng âm.")
    if s["max_dd"] >= 15:
        issues.append(f"Max DD xấp xỉ {s['max_dd']:.1f}% khá cao.")
    if s["total_pnl"] < 0 and BENCHMARK_BTC_PCT > 0:
        issues.append(f"Tổng PnL {s['total_pnl']:+.2f}% thua benchmark BTC \~{BENCHMARK_BTC_PCT:+.2f}%.")

    sl = s["reasons"].get("SL", 0)
    if s["n_closed"] and sl / s["n_closed"] >= 0.5:
        issues.append("Tỷ lệ đóng bằng SL cao (≥50%) → timing vào lệnh hoặc xác nhận dòng tiền yếu.")

    # Strengths
    if s["n_hold_records"] > 50:
        strengths.append("Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.")
    tp = s["reasons"].get("TP", 0)
    if tp > 0:
        strengths.append(f"Có {tp} lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.")
    if s["reasons"].get("REVERSAL", 0) > 0:
        strengths.append("Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.")
    if s["max_dd"] < 25 and s["n_closed"] > 0:
        strengths.append("Hard SL kiểm soát được lỗ từng lệnh (không thấy blow-up).")
    if not strengths:
        strengths.append("Chưa đủ dữ liệu tích cực rõ ràng — cần thêm lệnh đóng từ rule hiện tại.")

    return issues[:3], strengths[:3]


def render_md(s: dict, decisions_count: int | None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    issues, strengths = top_issues_and_strengths(s)

    lines = [
        f"# Paper Review Report",
        f"",
        f"- Thời điểm: **{now}**",
        f"- Ledger: `{LEDGER}`",
        f"- Decisions file: `{DECISIONS}`" + (f" ({decisions_count} records)" if decisions_count is not None else " (không có)"),
        f"",
        f"## 1. Tổng quan hiệu suất",
        f"",
        f"| Chỉ số | Giá trị |",
        f"|--------|---------|",
        f"| Số lệnh đã đóng | **{s['n_closed']}** |",
        f"| Lệnh đang mở | **{s['n_open']}** |",
        f"| Win rate | **{s['win_rate']:.1f}%** |",
        f"| PnL trung bình / lệnh | **{s['avg_pnl']:+.3f}%** |",
        f"| Tổng PnL | **{s['total_pnl']:+.2f}%** |",
        f"| Profit Factor | **{s['profit_factor']:.2f}** |",
        f"| Max DD (xấp xỉ) | **{s['max_dd']:.2f}%** |",
        f"| Benchmark BTC (tham chiếu) | **{BENCHMARK_BTC_PCT:+.2f}%** |",
        f"",
        f"## 2. Phân loại lý do đóng lệnh",
        f"",
        f"| Lý do | Số lệnh |",
        f"|-------|---------|",
    ]
    for k, v in sorted(s["reasons"].items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    if not s["reasons"]:
        lines.append("| (chưa có) | 0 |")

    lines += [
        f"",
        f"## 3. Theo hướng BUY / SELL",
        f"",
        f"| Hướng | Số lệnh | Avg PnL | Tổng PnL |",
        f"|-------|---------|---------|----------|",
    ]
    for side, info in s["by_side"].items():
        lines.append(f"| {side} | {info['n']} | {info['avg']:+.3f}% | {info['sum']:+.2f}% |")
    if not s["by_side"]:
        lines.append("| — | 0 | — | — |")

    lines += [
        f"",
        f"## 4. Top 3 vấn đề",
        f"",
    ]
    for i, x in enumerate(issues, 1):
        lines.append(f"{i}. {x}")
    if not issues:
        lines.append("1. Chưa đủ dữ liệu để kết luận vấn đề lớn.")

    lines += [
        f"",
        f"## 5. Top 3 điểm mạnh",
        f"",
    ]
    for i, x in enumerate(strengths, 1):
        lines.append(f"{i}. {x}")

    lines += [
        f"",
        f"## 6. 10 lệnh đóng gần nhất",
        f"",
        f"| Symbol | Hướng | PnL % | Lý do |",
        f"|--------|-------|-------|-------|",
    ]
    for t in s["recent"]:
        sym = t.get("symbol", "?")
        act = t.get("action", "?")
        pnl = safe_float(t.get("pnl_pct"))
        reason = t.get("reason", "")
        lines.append(f"| {sym} | {act} | {pnl:+.2f} | {reason} |")
    if not s["recent"]:
        lines.append("| — | — | — | — |")

    if s["open_t"]:
        lines += [
            f"",
            f"## 7. Lệnh đang mở",
            f"",
            f"| Symbol | Hướng | Entry | ",
            f"|--------|-------|-------|",
        ]
        for t in s["open_t"]:
            lines.append(f"| {t.get('symbol','?')} | {t.get('action','?')} | {t.get('entry','?')} |")

    lines += [
        f"",
        f"## 8. Gợi ý hành động (không tự sửa code)",
        f"",
        f"- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.",
        f"- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.",
        f"- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.",
        f"- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.",
        f"",
        f"---",
        f"*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*",
        f"",
    ]
    return "\n".join(lines)


def main():
    led = load_json(LEDGER)
    if not led:
        print(f"Không tìm thấy ledger: {LEDGER}")
        return 1

    decisions = load_json(DECISIONS)
    dec_count = len(decisions) if isinstance(decisions, list) else (len(decisions) if isinstance(decisions, dict) else None)

    stats = analyze(led)
    md = render_md(stats, dec_count)

    print(md)

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"\nĐã ghi: {OUT_MD}")

    # GitHub Step Summary (nếu có)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)
            f.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
