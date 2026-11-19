from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


# ---------------------------------------------------------------------------
# Cấu hình mặc định (có thể chỉnh qua ENV hoặc tham số khởi tạo)
# ---------------------------------------------------------------------------
@dataclass
class HLWatchConfig:
    watch_mode: str = "close"                  # "close" | "observe"
    ledger_path: str = "paper-ledger-hl/ledger.json"
    loop_minutes: float = 15.0                 # tổng thời gian chạy mỗi lần gọi watch
    interval_seconds: float = 60.0             # chu kỳ mỗi vòng quét
    trade_window_seconds: float = 75.0         # cửa sổ gom lệnh khớp gần nhất
    book_top_levels: int = 10                  # số tầng sổ lệnh L2 tính imbalance
    http_timeout: float = 6.0

    # Ngưỡng lọc dòng tiền & thoát lệnh
    default_large_trade_usd: float = 15_000.0
    large_trade_by_coin: dict[str, float] = field(
        default_factory=lambda: {
            "BTC": 50_000.0,
            "ETH": 30_000.0,
            "SOL": 20_000.0,
            "XMR": 15_000.0,
            "ARB": 10_000.0,
            "INJ": 10_000.0,
            "ONDO": 10_000.0,
            "AERO": 8_000.0,
        }
    )

    # Luật thoát lệnh ưu tiên dòng tiền (phân biệt rũ nhẹ vs bơm/xả ngược chiều)
    min_pnl_giveback: float = 0.8              # lãi tối thiểu để xét giveback (%)
    giveback_exit_pct: float = 1.5             # mức trả lại từ đỉnh lãi (%) khi dòng tiền xấu
    giveback_min_align: float = -0.25          # aligned_with_side <= -0.25 nghĩa là phe ngược đè rõ

    lock_profit_pnl_pct: float = 2.5           # vùng lãi tốt cần bảo vệ khi lực đẩy đảo chiều bùng lên
    lock_profit_giveback_pct: float = 1.0
    lock_profit_min_align: float = -0.35

    mild_loss_floor_pct: float = -1.5          # vùng âm nhẹ (-1.5% -> 0%)
    mild_loss_cut_align: float = -0.45         # chỉ cắt âm nhẹ khi dòng tiền xả ngược thực sự mạnh
    mild_loss_burst_align: float = -0.20       # nếu có lệnh lớn ngược chiều + align <= -0.20 thì vẫn cắt

    @classmethod
    def from_env(cls) -> "HLWatchConfig":
        cfg = cls()
        cfg.watch_mode = os.getenv("WATCH_MODE", cfg.watch_mode).strip().lower()
        cfg.ledger_path = os.getenv("WATCH_LEDGER", cfg.ledger_path).strip()
        cfg.loop_minutes = float(os.getenv("WATCH_LOOP_MINUTES", cfg.loop_minutes))
        cfg.interval_seconds = float(os.getenv("WATCH_INTERVAL_SECONDS", cfg.interval_seconds))
        cfg.trade_window_seconds = float(os.getenv("HL_TRADE_WINDOW_SECONDS", cfg.trade_window_seconds))
        cfg.http_timeout = float(os.getenv("HL_HTTP_TIMEOUT", cfg.http_timeout))
        cfg.default_large_trade_usd = float(
            os.getenv("HL_LARGE_TRADE_USD", cfg.default_large_trade_usd)
        )
        return cfg


# ---------------------------------------------------------------------------
# Dữ liệu dòng tiền & chuẩn hóa vị thế
# ---------------------------------------------------------------------------
@dataclass
class HLFlowSnapshot:
    coin: str
    side: str
    taker_buy_notional: float = 0.0
    taker_sell_notional: float = 0.0
    taker_buy_share: float = 0.5       # 0.0 -> 1.0 (0.5 = cân bằng)
    net_taker_notional: float = 0.0    # buy - sell (USD)
    large_buy_notional: float = 0.0
    large_sell_notional: float = 0.0
    book_imbalance: float = 0.0        # -1.0 (đè bán) -> +1.0 (đè mua)
    pressure_score: float = 0.0        # -1.0 (áp lực giảm) -> +1.0 (áp lực tăng)
    aligned_with_side: float = 0.0     # > 0 ủng hộ lệnh; < 0 bơm/xả đè ngược lệnh
    trade_count: int = 0


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None or isinstance(val, bool):
        return default
    try:
        out = float(val)
        return default if out != out else out  # chặn NaN
    except (TypeError, ValueError):
        return default


def normalize_hl_coin(raw_coin: str) -> str:
    """Chuẩn hóa 'ARB/USD', 'XMR-PERP', 'aero_usd' -> 'ARB', 'XMR', 'AERO'."""
    s = (raw_coin or "").strip().upper()
    for sep in ("/", "-", ":", "_"):
        if sep in s:
            s = s.split(sep, 1)[0]
    for suf in ("USDT", "USDC", "USD"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
            break
    return s


def normalize_side(raw_side: Any) -> str:
    """Chuẩn hóa mọi tên chiều lệnh về 'BUY' hoặc 'SELL'."""
    s = str(raw_side or "").strip().upper()
    if s in {"BUY", "LONG", "B"}:
        return "BUY"
    if s in {"SELL", "SHORT", "A", "S"}:
        return "SELL"
    return "BUY"


def extract_position_state(pos: Mapping[str, Any]) -> dict[str, Any]:
    """
    Đọc linh hoạt nhiều tên biến vị thế thường gặp trong ledger / watch:
    - Coin: coin | symbol | ticker | asset | market
    - Side: side | direction | dir | pos_side
    - PnL (%): pnl_pct | pnl | unrealized_pnl_pct | roi_pct
    - Giveback (%): giveback_pct | give | giveback (hoặc tự tính từ peak_pnl_pct - pnl_pct)
    - HTF: htf_same_direction | htf_aligned | trend_aligned (mặc định True nếu chưa truyền)
    - Nến nhỏ đổi chiều: minor_candle_flip | candle_flip | flip_1m_15m (mặc định False)
    """
    raw_coin = (
        pos.get("coin")
        or pos.get("symbol")
        or pos.get("ticker")
        or pos.get("asset")
        or pos.get("market")
        or ""
    )
    coin = normalize_hl_coin(str(raw_coin))
    side = normalize_side(
        pos.get("side")
        or pos.get("direction")
        or pos.get("dir")
        or pos.get("pos_side")
    )

    pnl_pct = _to_float(
        pos.get("pnl_pct")
        if "pnl_pct" in pos
        else pos.get("pnl", pos.get("unrealized_pnl_pct", pos.get("roi_pct", 0.0)))
    )

    peak_pnl = _to_float(pos.get("peak_pnl_pct") or pos.get("max_pnl_pct"), default=max(0.0, pnl_pct))
    if pnl_pct > peak_pnl:
        peak_pnl = pnl_pct

    raw_give = pos.get("giveback_pct")
    if raw_give is None:
        raw_give = pos.get("give", pos.get("giveback"))

    if raw_give is not None:
        giveback_pct = max(0.0, _to_float(raw_give))
    else:
        giveback_pct = max(0.0, peak_pnl - pnl_pct)

    htf_val = pos.get("htf_same_direction")
    if htf_val is None:
        htf_val = pos.get("htf_aligned", pos.get("trend_aligned", True))
    htf_same_direction = bool(htf_val)

    flip_val = pos.get("minor_candle_flip")
    if flip_val is None:
        flip_val = pos.get("candle_flip", pos.get("flip_1m_15m", False))
    minor_candle_flip = bool(flip_val)

    return {
        "coin": coin,
        "side": side,
        "pnl_pct": pnl_pct,
        "peak_pnl_pct": peak_pnl,
        "giveback_pct": giveback_pct,
        "htf_same_direction": htf_same_direction,
        "minor_candle_flip": minor_candle_flip,
    }


# ---------------------------------------------------------------------------
# Gọi Hyperliquid Info API (l2Book + recentTrades)
# ---------------------------------------------------------------------------
def post_hl_info(payload: Mapping[str, Any], timeout: float = 6.0, retries: int = 2) -> Any:
    body = json.dumps(dict(payload)).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                HL_INFO_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"HL info request failed ({payload.get('type')}): {last_err}")


def fetch_hl_market_flow_inputs(
    coin: str,
    window_seconds: float = 75.0,
    timeout: float = 6.0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    hl_coin = normalize_hl_coin(coin)
    book_raw = post_hl_info({"type": "l2Book", "coin": hl_coin}, timeout=timeout)
    trades_raw = post_hl_info({"type": "recentTrades", "coin": hl_coin}, timeout=timeout)

    now_ms = time.time() * 1000.0
    cutoff_ms = now_ms - max(10.0, window_seconds) * 1000.0

    recent_trades: list[dict[str, Any]] = []
    if isinstance(trades_raw, list):
        for tr in trades_raw:
            if not isinstance(tr, Mapping):
                continue
            ts = _to_float(tr.get("time") or tr.get("t"), default=now_ms)
            if ts >= cutoff_ms:
                recent_trades.append(dict(tr))

    return (dict(book_raw) if isinstance(book_raw, Mapping) else None), recent_trades


def compute_hl_book_imbalance(book: Mapping[str, Any] | None, top_levels: int = 10) -> float:
    if not book:
        return 0.0
    levels = book.get("levels")
    if not isinstance(levels, (list, tuple)) or len(levels) < 2:
        return 0.0

    bids = levels[0] or []
    asks = levels[1] or []

    bid_notional = 0.0
    ask_notional = 0.0

    for lvl in list(bids)[:top_levels]:
        if isinstance(lvl, Mapping):
            bid_notional += max(0.0, _to_float(lvl.get("px")) * _to_float(lvl.get("sz")))

    for lvl in list(asks)[:top_levels]:
        if isinstance(lvl, Mapping):
            ask_notional += max(0.0, _to_float(lvl.get("px")) * _to_float(lvl.get("sz")))

    denom = bid_notional + ask_notional
    if denom <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, (bid_notional - ask_notional) / denom))


def compute_hl_flow_snapshot(
    coin: str,
    side: str,
    trades: Iterable[Mapping[str, Any]],
    book: Mapping[str, Any] | None,
    config: HLWatchConfig,
) -> HLFlowSnapshot:
    hl_coin = normalize_hl_coin(coin)
    norm_side = normalize_side(side)
    is_buy = norm_side == "BUY"

    large_threshold = config.large_trade_by_coin.get(hl_coin, config.default_large_trade_usd)

    buy_notional = 0.0
    sell_notional = 0.0
    large_buy = 0.0
    large_sell = 0.0
    count = 0

    for tr in trades or []:
        raw_side = str(tr.get("side") or "").strip().upper()
        px = _to_float(tr.get("px"))
        sz = _to_float(tr.get("sz"))
        notional = px * sz
        if notional <= 0.0:
            continue
        count += 1

        if raw_side in {"B", "BUY", "LONG"}:
            buy_notional += notional
            if notional >= large_threshold:
                large_buy += notional
        elif raw_side in {"A", "S", "SELL", "SHORT"}:
            sell_notional += notional
            if notional >= large_threshold:
                large_sell += notional

    total = buy_notional + sell_notional
    buy_share = (buy_notional / total) if total > 0.0 else 0.5
    net_notional = buy_notional - sell_notional
    taker_imb = (net_notional / total) if total > 0.0 else 0.0
    book_imb = compute_hl_book_imbalance(book, top_levels=config.book_top_levels)

    # 70% dòng khớp chủ động trực tiếp (taker) + 30% độ lệch sổ lệnh (book)
    pressure = max(-1.0, min(1.0, 0.70 * taker_imb + 0.30 * book_imb))
    aligned = pressure if is_buy else -pressure

    return HLFlowSnapshot(
        coin=hl_coin,
        side=norm_side,
        taker_buy_notional=round(buy_notional, 2),
        taker_sell_notional=round(sell_notional, 2),
        taker_buy_share=round(buy_share, 4),
        net_taker_notional=round(net_notional, 2),
        large_buy_notional=round(large_buy, 2),
        large_sell_notional=round(large_sell, 2),
        book_imbalance=round(book_imb, 4),
        pressure_score=round(pressure, 4),
        aligned_with_side=round(aligned, 4),
        trade_count=count,
    )


# ---------------------------------------------------------------------------
# Luật thoát lệnh thuần dòng tiền Hyperliquid
# ---------------------------------------------------------------------------
def evaluate_hl_exit(
    state: Mapping[str, Any],
    flow: HLFlowSnapshot,
    config: HLWatchConfig,
) -> tuple[bool, str]:
    pnl_pct = _to_float(state.get("pnl_pct"))
    giveback_pct = max(0.0, _to_float(state.get("giveback_pct")))
    htf_same = bool(state.get("htf_same_direction", True))
    minor_flip = bool(state.get("minor_candle_flip", False))

    opposed_large_notional = (
        flow.large_sell_notional if flow.side == "BUY" else flow.large_buy_notional
    )
    has_big_counter_burst = opposed_large_notional > 0.0

    # 1. Đang có lãi tốt (>= min_pnl_giveback) và bị giật ngược + dòng tiền đè ngược rõ
    if (
        pnl_pct >= config.min_pnl_giveback
        and giveback_pct >= config.giveback_exit_pct
        and flow.aligned_with_side <= config.giveback_min_align
    ):
        return (
            True,
            f"HL_GIVEBACK_EXIT pnl={pnl_pct:+.2f}% give={giveback_pct:.2f}% "
            f"align={flow.aligned_with_side:+.2f} share={flow.taker_buy_share:.2f}",
        )

    # 2. Đang có lãi lớn (>= lock_profit_pnl_pct) nhưng xuất hiện lực bơm/xả ngược chiều mạnh
    if (
        pnl_pct >= config.lock_profit_pnl_pct
        and giveback_pct >= config.lock_profit_giveback_pct
        and (flow.aligned_with_side <= config.lock_profit_min_align or has_big_counter_burst)
    ):
        return (
            True,
            f"HL_LOCK_PROFIT_EXIT pnl={pnl_pct:+.2f}% give={giveback_pct:.2f}% "
            f"align={flow.aligned_with_side:+.2f} big={opposed_large_notional:.0f}",
        )

    # 3. Đang âm nhẹ (mild_loss_floor_pct -> 0%): ưu tiên giữ qua nhịp rũ nhẹ nếu dòng tiền chưa xả mạnh
    if config.mild_loss_floor_pct <= pnl_pct < 0.0:
        if htf_same and flow.aligned_with_side > config.mild_loss_cut_align and not has_big_counter_burst:
            tag = "MINOR_CANDLE_NOISE_HOLD" if minor_flip else "PULLBACK_HOLD"
            return False, f"{tag} align={flow.aligned_with_side:+.2f} share={flow.taker_buy_share:.2f}"

        if flow.aligned_with_side <= config.mild_loss_cut_align or (
            has_big_counter_burst and flow.aligned_with_side <= config.mild_loss_burst_align
        ):
            return (
                True,
                f"HL_FLOW_CUT pnl={pnl_pct:+.2f}% align={flow.aligned_with_side:+.2f} "
                f"big={opposed_large_notional:.0f}",
            )

    return False, f"HOLD align={flow.aligned_with_side:+.2f} share={flow.taker_buy_share:.2f}"


# ---------------------------------------------------------------------------
# Đọc / ghi Ledger an toàn & chạy vòng lặp Watch
# ---------------------------------------------------------------------------
def load_ledger_safe(path_str: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    p = Path(path_str)
    if not p.exists():
        return {"open_positions": [], "closed_positions": []}, []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"positions": data}, data
    if isinstance(data, dict):
        for key in ("open_positions", "positions", "trades"):
            if isinstance(data.get(key), list):
                return data, data[key]
    return {"open_positions": []}, []


def save_ledger_safe(path_str: str, ledger_obj: Any) -> None:
    p = Path(path_str)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = p.with_suffix(p.suffix + ".tmp")
    tmp_path.write_text(json.dumps(ledger_obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, p)


def run_watch_cycle(
    positions: list[dict[str, Any]],
    config: HLWatchConfig,
    round_idx: int = 1,
) -> tuple[list[str], int]:
    """Chạy 1 vòng quét cho toàn bộ vị thế đang mở. Trả về (dòng log, số lệnh đóng vòng này)."""
    ts_utc = time.strftime("%H:%M:%S UTC", time.gmtime())
    logs: list[str] = [f"WATCH VÒNG {round_idx} {ts_utc} | mode={config.watch_mode} | open={len(positions)}"]
    closed_this_round = 0

    for pos in positions:
        if not isinstance(pos, dict):
            continue
        status = str(pos.get("status") or "OPEN").upper()
        if status not in {"OPEN", "ACTIVE", ""}:
            continue

        state = extract_position_state(pos)
        coin = state["coin"]
        side = state["side"]
        if not coin:
            logs.append("  -> Bỏ qua vị thế thiếu mã coin/symbol")
            continue

        # Cập nhật lại peak_pnl_pct vào dict vị thế để lần quét sau tính giveback chính xác
        pos["peak_pnl_pct"] = state["peak_pnl_pct"]

        try:
            book, trades = fetch_hl_market_flow_inputs(
                coin=coin,
                window_seconds=config.trade_window_seconds,
                timeout=config.http_timeout,
            )
            flow = compute_hl_flow_snapshot(coin=coin, side=side, trades=trades, book=book, config=config)
            should_exit, reason = evaluate_hl_exit(state=state, flow=flow, config=config)

            line = (
                f"  [HL] {coin} {side} pnl={state['pnl_pct']:+.2f}% give={state['giveback_pct']:.2f}% "
                f"buy_share={flow.taker_buy_share:.2f} book={flow.book_imbalance:+.2f} "
                f"align={flow.aligned_with_side:+.2f} n_tr={flow.trade_count} -> {reason}"
            )
            logs.append(line)

            if should_exit and config.watch_mode == "close":
                pos["status"] = "CLOSED"
                pos["close_reason"] = reason
                pos["closed_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                closed_this_round += 1
                logs.append(f"  -> ĐÓNG LỆNH: {coin} {side} ({reason})")
        except Exception as exc:
            logs.append(f"  [HL] {coin} {side} warn={exc} -> GIỮ LỆNH AN TOÀN")

    return logs, closed_this_round


def run_watch_loop(config: HLWatchConfig | None = None, max_rounds: int | None = None) -> None:
    cfg = config or HLWatchConfig.from_env()
    deadline = time.monotonic() + max(1.0, cfg.loop_minutes) * 60.0
    round_idx = 0

    while True:
        if max_rounds is not None and round_idx >= max_rounds:
            break
        if max_rounds is None and time.monotonic() >= deadline:
            break

        round_idx += 1
        ledger_obj, positions = load_ledger_safe(cfg.ledger_path)
        logs, closed_count = run_watch_cycle(positions=positions, config=cfg, round_idx=round_idx)
        for line in logs:
            print(line, flush=True)

        if closed_count > 0:
            save_ledger_safe(cfg.ledger_path, ledger_obj)
            print("  -> Đã lưu ledger Hyperliquid an toàn.", flush=True)

        remaining = deadline - time.monotonic()
        if max_rounds is None and remaining <= 0:
            break
        sleep_sec = cfg.interval_seconds if max_rounds is not None else min(cfg.interval_seconds, max(0.0, remaining))
        if sleep_sec > 0:
            time.sleep(sleep_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperliquid pure-flow position watcher")
    parser.add_argument("--ledger", default=None, help="Đường dẫn file ledger.json")
    parser.add_argument("--mode", choices=["close", "observe"], default=None, help="Chế độ chạy")
    parser.add_argument("--loop-minutes", type=float, default=None, help="Số phút chạy mỗi lần gọi")
    parser.add_argument("--interval-seconds", type=float, default=None, help="Giây nghỉ giữa các vòng")
    parser.add_argument("--once", action="store_true", help="Chỉ chạy đúng 1 vòng rồi thoát")
    args = parser.parse_args()

    cfg = HLWatchConfig.from_env()
    if args.ledger:
        cfg.ledger_path = args.ledger
    if args.mode:
        cfg.watch_mode = args.mode
    if args.loop_minutes is not None:
        cfg.loop_minutes = args.loop_minutes
    if args.interval_seconds is not None:
        cfg.interval_seconds = args.interval_seconds

    run_watch_loop(config=cfg, max_rounds=1 if args.once else None)


if __name__ == "__main__":
    main()
