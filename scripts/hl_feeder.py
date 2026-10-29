"""
Hyperliquid data feeder – dùng cho tín hiệu 1m + orderflow + funding.
Không cần API key.
"""
from __future__ import annotations

import time
import requests
from typing import Any

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def _post(payload: dict) -> Any:
    resp = requests.post(HL_INFO_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_candles(coin: str, interval: str = "1m", lookback_minutes: int = 180) -> list[dict]:
    """Lấy nến gần nhất (tối đa \~5000 nến)."""
    end = int(time.time() * 1000)
    start = end - lookback_minutes * 60 * 1000
    data = _post({
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start,
            "endTime": end,
        }
    })
    # Trả về list dict đã sắp xếp theo thời gian tăng dần
    return sorted(data, key=lambda x: x["t"]) if data else []


def get_l2_book(coin: str) -> dict:
    """Lấy orderbook L2 hiện tại."""
    return _post({"type": "l2Book", "coin": coin})


def get_asset_ctx(coin: str) -> dict | None:
    """Lấy funding + open interest + mark price hiện tại."""
    meta = _post({"type": "metaAndAssetCtxs"})
    if not meta or len(meta) < 2:
        return None
    universe = meta[0].get("universe", [])
    ctxs = meta[1]
    for i, asset in enumerate(universe):
        if asset.get("name") == coin:
            return ctxs[i] if i < len(ctxs) else None
    return None


def compute_1m_features(coin: str) -> dict | None:
    """
    Tính các feature 1m chính từ Hyperliquid.
    Trả về dict hoặc None nếu dữ liệu không đủ.
    """
    candles = get_candles(coin, interval="1m", lookback_minutes=200)
    if len(candles) < 90:
        return None

    closes = [float(c["c"]) for c in candles]
    volumes = [float(c["v"]) for c in candles]

    # Trend 1m (ngắn vs dài hơn)
    short = sum(closes[-8:]) / 8
    long = sum(closes[-40:]) / 40
    trend_1m = short / long - 1.0

    # Momentum gần
    momentum_1m = closes[-1] / closes[-12] - 1.0

    # Volume spike
    avg_vol = sum(volumes[-50:-1]) / 49
    vol_spike_1m = min(volumes[-1] / max(avg_vol, 1e-12), 12.0) / 12.0

    # Orderbook imbalance (top levels)
    try:
        book = get_l2_book(coin)
        levels = book.get("levels", [[], []])
        bids = levels[0][:10]  # top 10
        asks = levels[1][:10]
        bid_vol = sum(float(x["sz"]) for x in bids)
        ask_vol = sum(float(x["sz"]) for x in asks)
        imbalance = (bid_vol - ask_vol) / max(bid_vol + ask_vol, 1e-12)
    except Exception:
        imbalance = 0.0

    # Funding + OI
    ctx = get_asset_ctx(coin)
    funding = float(ctx.get("funding", 0)) if ctx else 0.0
    open_interest = float(ctx.get("openInterest", 0)) if ctx else 0.0

    return {
        "trend_1m": trend_1m,
        "momentum_1m": momentum_1m,
        "vol_spike_1m": vol_spike_1m,
        "imbalance": imbalance,
        "funding": funding,
        "open_interest": open_interest,
        "last_price": closes[-1],
    }


# Test nhanh
if __name__ == "__main__":
    for coin in ["BTC", "ETH", "SOL", "HYPE"]:
        feat = compute_1m_features(coin)
        print(coin, feat)
