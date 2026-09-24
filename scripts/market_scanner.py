"""Broad real-market scanner with multi-timeframe validation.

Stage 1: Kraken AssetPairs + Ticker cheaply builds the liquid USD spot universe.
Stage 2: the best pre-ranked markets are checked with completed OHLC candles on
15m, 1h and 4h. Only data-quality-passing markets receive the final score.
This is a ranking gate, not a trading signal. Live orders are disabled.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://api.kraken.com/0/public/"
TOP_N = 3
PREFILTER_N = 5
MIN_VOLUME_USD = 1_000_000.0
TIMEFRAMES = (15, 60, 240)


@dataclass
class Candidate:
    pair: str
    symbol: str
    price: float
    volume_usd: float
    spread_bps: float
    score: float
    trend_15m: float
    trend_1h: float
    trend_4h: float
    quality: float


def get_json(path: str, params: dict[str, str] | None = None) -> dict:
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "TradingAgents-Profect/scanner"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"Kraken API error: {payload['error']}")
    return payload["result"]


def percentile(values: list[float], value: float) -> float:
    if len(values) <= 1:
        return 1.0 if values else 0.0
    ordered = sorted(values)
    rank = sum(v <= value for v in ordered) - 1
    return rank / (len(ordered) - 1)


def normalize_pair(name: str, info: dict) -> tuple[str, str] | None:
    wsname = str(info.get("wsname") or "")
    if "/" not in wsname:
        return None
    base, quote = wsname.split("/", 1)
    if quote not in {"USD", "ZUSD","USDC", "USDT"}:
        return None
    status = str(info.get("status") or "online").lower()
    if status not in {"online", "post_only"}:
        return None
    if any(token in base.upper() for token in (".S", ".M", "3L", "3S", "5L", "5S")):
        return None
    return name, wsname


def ticker_rows() -> list[tuple[str, str, float, float, float, float, float]]:
    pairs = get_json("AssetPairs")
    universe = []
    for name, info in pairs.items():
        normalized = normalize_pair(name, info)
        if normalized:
            universe.append(normalized)
    tickers = get_json("Ticker")
    rows = []
    for pair, wsname in universe:
        ticker = tickers.get(pair) or tickers.get(pair.replace("X", ""))
        if not ticker:
            continue
        try:
            ask = float(ticker["a"][0]); bid = float(ticker["b"][0])
            last = float(ticker["c"][0]); high = float(ticker["h"][1])
            low = float(ticker["l"][1]); base_volume = float(ticker["v"][1])
            open_price = float(ticker["o"])
            if min(ask, bid, last, high, low, base_volume, open_price) <= 0:
                continue
            mid = (ask + bid) / 2.0
            volume_usd = base_volume * mid
            if volume_usd < MIN_VOLUME_USD:
                continue
            spread_bps = max(0.0, (ask - bid) / mid * 10_000.0)
            range_pct = (high - low) / mid
            change_pct = last / open_price - 1.0
            rows.append((pair, wsname, last, volume_usd, spread_bps, range_pct, change_pct))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return rows


def fetch_ohlc(pair: str, interval: int) -> list[list[float]]:
    result = get_json("OHLC", {"pair": pair, "interval": str(interval)})
    key = next((k for k in result if k != "last"), None)
    if not key:
        return []
    rows = result[key]
    # Kraken can return a final still-forming candle. Drop it deliberately.
    return rows[:-1] if len(rows) > 1 else []


def timeframe_features(candles: list[list[float]]) -> tuple[float, float, float] | None:
    if len(candles) < 60:
        return None
    closes = [float(row[4]) for row in candles if float(row[4]) > 0]
    volumes = [float(row[6]) for row in candles if float(row[6]) >= 0]
    if len(closes) < 60 or len(volumes) < 60:
        return None
    short = sum(closes[-12:]) / 12
    long = sum(closes[-48:]) / 48
    trend = short / long - 1.0
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    recent = returns[-24:]
    if len(recent) < 12:
        return None
    vol = math.sqrt(sum(r * r for r in recent) / len(recent))
    momentum = closes[-1] / closes[-24] - 1.0
    volume_ratio = (sum(volumes[-12:]) / 12) / max(sum(volumes[-48:]) / 48, 1e-12)
    normalized_trend = trend / max(vol * math.sqrt(12), 1e-6)
    return normalized_trend, momentum, volume_ratio


def main() -> int:
    started = time.time()
    rows = ticker_rows()
    if not rows:
        raise RuntimeError("No qualifying USD spot markets were returned")

    volumes = [math.log1p(r[3]) for r in rows]
    spreads = [r[4] for r in rows]
    ranges = [r[5] for r in rows]
    changes = [r[6] for r in rows]
    pre = []
    for r in rows:
        liquidity = percentile(volumes, math.log1p(r[3]))
        tightness = 1.0 - percentile(spreads, r[4])
        activity = percentile(ranges, r[5])
        momentum = percentile(changes, r[6])
        score = 0.45 * liquidity + 0.25 * tightness + 0.15 * activity + 0.15 * momentum
        pre.append((score, r))
    pre.sort(key=lambda x: x[0], reverse=True)

    validated = []
    failures = 0
    for _, row in pre[:PREFILTER_N]:
        pair, wsname, price, volume_usd, spread_bps, _, _ = row
        features = []
        failed = False
        for interval in TIMEFRAMES:
            try:
                candles = fetch_ohlc(pair, interval)
                feature = timeframe_features(candles)
                if feature is None:
                    failed = True
                    break
                features.append(feature)
            except Exception:
                failed = True
                break
        if failed:
            failures += 1
            continue
        trends = [f[0] for f in features]
        momenta = [f[1] for f in features]
        volume_ratios = [f[2] for f in features]
        agreement = sum(1 for x in trends if x > 0) / 3.0
        trend_strength = sum(trends) / 3.0
        momentum_strength = sum(momenta) / 3.0
        activity_strength = sum(min(max(v, 0.0), 3.0) for v in volume_ratios) / 9.0
        consistency = 1.0 - min(1.0, (max(trends) - min(trends)) / 3.0)
        quality = 0.45 * agreement + 0.30 * consistency + 0.25 * activity_strength
        validated.append((quality, trend_strength, momentum_strength, row, trends))

    if not validated:
        raise RuntimeError("No prefiltered markets passed multi-timeframe data-quality validation")

    trend_values = [x[1] for x in validated]
    momentum_values = [x[2] for x in validated]
    quality_values = [x[0] for x in validated]
    candidates = []
    for quality, trend, momentum, row, trends in validated:
        pair, wsname, price, volume_usd, spread_bps, _, _ = row
        liquidity = percentile(volumes, math.log1p(volume_usd))
        tightness = 1.0 - percentile(spreads, spread_bps)
        trend_rank = percentile(trend_values, trend)
        momentum_rank = percentile(momentum_values, momentum)
        quality_rank = percentile(quality_values, quality)
        score = (0.25 * liquidity + 0.15 * tightness + 0.25 * trend_rank
                 + 0.15 * momentum_rank + 0.20 * quality_rank)
        candidates.append(Candidate(pair, wsname, price, volume_usd, spread_bps, score,
                                    trends[0], trends[1], trends[2], quality))

    candidates.sort(key=lambda x: x.score, reverse=True)

    print("BROAD REAL-MARKET MULTI-TIMEFRAME SCANNER")
    print("DATA SOURCE: KRAKEN PUBLIC SPOT MARKET DATA")
    print("CANDLES: COMPLETED ONLY")
    print("LIVE ORDERS: DISABLED")
    print(f"USD SPOT UNIVERSE: {len(rows)} qualifying after broad ticker gate")
    print(f"MULTI-TIMEFRAME PREFILTER: {min(PREFILTER_N, len(pre))}")
    print(f"VALIDATION FAILURES: {failures}")
    print(f"VALIDATED MARKETS: {len(candidates)}")
    print(f"TOP {min(TOP_N, len(candidates))} CANDIDATES:")
    for rank, c in enumerate(candidates[:TOP_N], 1):
        print(f"{rank:02d} {c.symbol:12} price=${c.price:,.8g} vol24h=${c.volume_usd:,.0f} "
              f"spread={c.spread_bps:.2f}bps mtf_score={c.score:.4f} quality={c.quality:.4f} "
              f"trend15={c.trend_15m:+.2f} trend1h={c.trend_1h:+.2f} trend4h={c.trend_4h:+.2f}")
    print(f"SCANNER ELAPSED: {time.time() - started:.1f}s")
    print("NEXT GATE: Only these candidates should enter TradingAgents deep analysis.")
    print("NEXT GATE: Ranking is not a trade instruction; BUY/SELL/HOLD/REVIEW remains downstream.")
    print("LIVE TRADING GATE: DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
