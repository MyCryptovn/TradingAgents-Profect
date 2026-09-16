"""Broad real-market scanner using Kraken public Spot market data.

Purpose: reduce a large public market universe to a small, auditable candidate
set before any expensive TradingAgents/LLM analysis. No credentials, wallets,
or order endpoints are used.
"""
from __future__ import annotations

import json
import math
import statistics
import time
import urllib.request
from dataclasses import dataclass

API = "https://api.kraken.com/0/public/"
EXCLUDED_QUOTES = {"USDT", "USDC", "DAI", "EUR", "GBP", "CAD", "JPY"}
TOP_N = 10
MIN_VOLUME_USD = 100_000.0


@dataclass
class Candidate:
    pair: str
    symbol: str
    price: float
    volume_usd: float
    spread_bps: float
    range_pct: float
    change_pct: float
    score: float


def get_json(path: str) -> dict:
    request = urllib.request.Request(API + path, headers={"User-Agent": "TradingAgents-Profect/scanner"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"Kraken API error: {payload['error']}")
    return payload["result"]


def normalize_pair(name: str, info: dict) -> tuple[str, str] | None:
    wsname = str(info.get("wsname") or "")
    if "/" not in wsname:
        return None
    base, quote = wsname.split("/", 1)
    if quote not in {"USD", "ZUSD"}:
        return None
    status = str(info.get("status") or "online").lower()
    if status not in {"online", "post_only"}:
        return None
    # Exclude obvious leveraged/derivative-style symbols from the spot universe.
    if any(token in base.upper() for token in (".S", ".M", "3L", "3S", "5L", "5S")):
        return None
    return name, wsname


def percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return 1.0
    rank = sum(v <= value for v in ordered) - 1
    return rank / (len(ordered) - 1)


def main() -> int:
    started = time.time()
    pairs = get_json("AssetPairs")
    universe = []
    for name, info in pairs.items():
        normalized = normalize_pair(name, info)
        if normalized:
            universe.append(normalized)

    tickers = get_json("Ticker")
    rows = []
    for pair, wsname in universe:
        ticker = tickers.get(pair)
        if not ticker:
            # Some aliases appear in AssetPairs but not in the current ticker map.
            alt = pair.replace("X", "")
            ticker = tickers.get(alt)
        if not ticker:
            continue
        try:
            ask = float(ticker["a"][0])
            bid = float(ticker["b"][0])
            last = float(ticker["c"][0])
            high = float(ticker["h"][1])
            low = float(ticker["l"][1])
            base_volume = float(ticker["v"][1])
            open_price = float(ticker["o"])
            if min(ask, bid, last, high, low, base_volume, open_price) <= 0:
                continue
            mid = (ask + bid) / 2.0
            spread_bps = max(0.0, (ask - bid) / mid * 10_000.0)
            volume_usd = base_volume * mid
            range_pct = (high - low) / mid
            change_pct = last / open_price - 1.0
            if volume_usd < MIN_VOLUME_USD:
                continue
            rows.append((pair, wsname, last, volume_usd, spread_bps, range_pct, change_pct))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue

    if not rows:
        raise RuntimeError("No qualifying USD spot markets were returned")

    volumes = [math.log1p(row[3]) for row in rows]
    spreads = [row[4] for row in rows]
    ranges = [row[5] for row in rows]
    changes = [row[6] for row in rows]

    candidates: list[Candidate] = []
    for row in rows:
        pair, wsname, price, volume_usd, spread_bps, range_pct, change_pct = row
        liquidity = percentile(volumes, math.log1p(volume_usd))
        tightness = 1.0 - percentile(spreads, spread_bps)
        activity = percentile(ranges, range_pct)
        momentum = percentile(changes, change_pct)
        # Ranking only: no fixed take-profit or BUY threshold is used.
        score = 0.40 * liquidity + 0.25 * tightness + 0.15 * activity + 0.20 * momentum
        candidates.append(Candidate(pair, wsname, price, volume_usd, spread_bps, range_pct * 100, change_pct * 100, score))

    candidates.sort(key=lambda item: item.score, reverse=True)
    selected = candidates[:TOP_N]

    print("BROAD REAL-MARKET SCANNER")
    print("DATA SOURCE: KRAKEN PUBLIC SPOT MARKET DATA")
    print("LIVE ORDERS: DISABLED")
    print(f"USD SPOT UNIVERSE: {len(universe)}")
    print(f"QUALIFYING MARKETS: {len(candidates)}")
    print(f"TOP {TOP_N} CANDIDATES:")
    for rank, item in enumerate(selected, 1):
        print(
            f"{rank:02d} {item.symbol:12} price=${item.price:,.8g} "
            f"vol24h=${item.volume_usd:,.0f} spread={item.spread_bps:.2f}bps "
            f"range24h={item.range_pct:+.2f}% change24h={item.change_pct:+.2f}% "
            f"rank_score={item.score:.4f}"
        )
    print(f"SCANNER ELAPSED: {time.time() - started:.1f}s")
    print("NEXT GATE: Only these candidates should enter TradingAgents deep analysis.")
    print("NEXT GATE: Scanner ranking is not a trade instruction; BUY/SELL/HOLD/REVIEW remains downstream.")
    print("LIVE TRADING GATE: DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
