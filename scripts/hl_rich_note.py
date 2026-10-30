"""
Rich Hyperliquid context note for TradingAgents.
Ưu tiên đọc từ scanner.json (đồng bộ với ranking), fallback sang live nếu cần.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RESULT_FILE = Path("tradingagents-paper-results/scanner.json")


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def hl_rich_note(ticker: str) -> str:
    """
    Tạo ghi chú giàu thông tin từ Hyperliquid cho agent.
    ticker ví dụ: "BTC-USD" hoặc "ETH-USD"
    """
    symbol = ticker.replace("-USD", "").replace("-", "").upper()
    if symbol == "XBT":
        symbol = "BTC"

    # 1. Ưu tiên đọc từ scanner.json (đã được phân tích kỹ)
    if RESULT_FILE.exists():
        try:
            data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            all_rows = (
                data.get("top_long", [])
                + data.get("top_short", [])
                + data.get("watchlist", [])
                + data.get("analyzed_markets", [])
            )

            for row in all_rows:
                if str(row.get("symbol", "")).upper() == symbol:
                    return _format_from_scanner(row)
        except Exception:
            pass

    # 2. Fallback: gọi trực tiếp hl_feeder (nếu scanner.json không có)
    try:
        from scripts.hl_feeder import compute_market_features, discover_perp_markets

        markets = discover_perp_markets()
        market = next((m for m in markets if m.get("coin") == symbol), None)
        if market is None:
            return f"[HL] No data found for {symbol}"

        features = compute_market_features(symbol)
        return _format_live(symbol, market, features)
    except Exception as e:
        return f"[HL] Error loading rich data for {symbol}: {e}"


def _format_from_scanner(row: dict) -> str:
    scores = row.get("scores", {})
    quality = row.get("quality", {})
    features = row.get("features", {})
    timeframes = features.get("timeframes", {})
    book = features.get("book", {})

    lines = [
        f"HYPERLIQUID RICH CONTEXT for {row.get('symbol')}:",
        f"- Direction bias: {scores.get('direction', 'N/A')} "
        f"(Long {scores.get('long_score', 0):.1f} / Short {scores.get('short_score', 0):.1f})",
        f"- Quality score: {quality.get('quality_score', 0):.1f}/100",
        f"- Spread: {quality.get('spread_bps', 0):.2f} bps | "
        f"Depth 10bps: ${quality.get('depth_10bps_usd', 0):,.0f}",
        f"- Day volume: ${row.get('day_volume_usd', 0):,.0f}",
        f"- Funding rate: {row.get('funding', 0):.6f}",
        f"- Open Interest: {row.get('open_interest', 0):,.0f}",
    ]

    if scores.get("oi_change_pct") is not None:
        lines.append(f"- OI change: {scores['oi_change_pct']:+.2f}%")

    # Timeframe signals
    tf_parts = []
    for tf in ("1m", "5m", "15m", "1h"):
        sig = scores.get("timeframe_signals", {}).get(tf)
        if sig is not None:
            tf_parts.append(f"{tf}:{sig:+.2f}")
    if tf_parts:
        lines.append("- TF signals: " + " | ".join(tf_parts))

    # Orderbook
    imb = book.get("imbalance_usd") or scores.get("orderbook_imbalance")
    if imb is not None:
        lines.append(f"- Orderbook imbalance: {imb:+.3f}")

    lines.append(
        "Prefer entries that align with the directional bias and higher-timeframe signals. "
        "Avoid chasing when funding is extreme against the intended direction."
    )

    return " ".join(lines)


def _format_live(symbol: str, market: dict, features: dict) -> str:
    """Fallback formatter khi không có scanner.json."""
    timeframes = features.get("timeframes", {})
    book = features.get("book", {})

    lines = [
        f"HYPERLIQUID LIVE CONTEXT for {symbol}:",
        f"- Mark price: {market.get('mark_price')}",
        f"- Funding: {market.get('funding', 0):.6f}",
        f"- Open Interest: {market.get('open_interest', 0):,.0f}",
        f"- Day volume: ${market.get('day_volume_usd', 0):,.0f}",
    ]

    if book.get("spread_bps") is not None:
        lines.append(f"- Spread: {book['spread_bps']:.2f} bps")

    imb = book.get("imbalance_usd")
    if imb is not None:
        lines.append(f"- Orderbook imbalance: {imb:+.3f}")

    return " ".join(lines)
