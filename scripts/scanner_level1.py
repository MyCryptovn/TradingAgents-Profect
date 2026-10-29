"""Broad real-market scanner with multi-timeframe validation + real 1m features (Level 1 upgrade).

Stage 1: Kraken AssetPairs + Ticker cheaply builds the liquid USD spot universe.
Stage 2: the best pre-ranked markets are checked with completed OHLC candles on
1m, 15m, 1h and 4h. Only data-quality-passing markets receive the final score.
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
PREFILTER_N = 20
MIN_VOLUME_USD = 500_000.0
TIMEFRAMES = (15, 60, 240)          # vẫn giữ multi-TF chính
ONE_MIN_INTERVAL = 1


@dataclass
class Candidate:
    pair: str
    symbol: str
    price: float
    volume_usd: float
    spread_bps: float
    score: float
    trend_1m: float
    momentum_1m: float
    vol_spike_1m: float
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
    if quote not in {"USD", "ZUSD"} or base.upper() in {
        "USDC", "USDT", "DAI", "PYUSD", "USDG", "RLUSD", "TUSD", "FDUSD",
        "EURC", "EURT", "GBP", "LIGHTER", "EUR", "AUD", "CAD", "CHF", "JPY", "US"
    }:
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
            volume_usd = base_volume * mid
            if volume_usd < MIN_VOLUME_USD:
                continue
            spread_bps = max(0.0, (ask - bid) / mid * 10_000.0)
            if spread_bps > 45.0:
                continue
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
    # Drop the still-forming candle
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


def timeframe_features_1m(candles: list[list[float]]) -> tuple[float, float, float] | None:
    """Dedicated 1m features – more sensitive, needs more bars."""
    if len(candles) < 90:
        return None
    closes = [float(row[4]) for row in candles if float(row[4]) > 0]
    volumes = [float(row[6]) for row in candles if float(row[6]) >= 0]
    if len(closes) < 90 or len(volumes) < 90:
        return None

    # Short-term trend (last \~8 vs \~40 minutes)
    short = sum(closes[-8:]) / 8
    long = sum(closes[-40:]) / 40
    trend = short / long - 1.0

    # Recent momentum (last 12 minutes)
    momentum = closes[-1] / closes[-12] - 1.0

    # Volume spike of the latest completed candle
    avg_vol = sum(volumes[-50:-1]) / 49
    vol_spike = volumes[-1] / max(avg_vol, 1e-12)
    vol_spike = min(vol_spike, 12.0) / 12.0          # normalize 0-1

    # Volatility-adjusted trend
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    recent_vol = math.sqrt(sum(r * r for r in returns[-20:]) / 20)
    norm_trend = trend / max(recent_vol * math.sqrt(8), 1e-6)

    return norm_trend, momentum, vol_spike


def spike_score(pair: str) -> float:
    try:
        candles = fetch_ohlc(pair, 5)
    except Exception:
        return 0.0
    if len(candles) < 60:
        return 0.0
    vols = [float(r[6]) for r in candles]
    closes = [float(r[4]) for r in candles]
    avg_vol = sum(vols[-49:-1]) / 48
    if avg_vol <= 0:
        return 0.0
    if closes[-1] * vols[-1] < 20_000:
        return 0.0
    move = closes[-1] / closes[-4] - 1.0
    if move <= 0:
        return 0.0
    return min(vols[-1] / avg_vol, 8.0) / 8.0


def main() -> int:
    started = time.time()
    rows = ticker_rows()
    print(len(rows))
    if not rows:
        raise RuntimeError("No qualifying USD spot markets were returned")

    volumes = [math.log1p(r[3]) for r in rows]
    spreads = [r[4] for r in rows]
    ranges = [r[5] for r in rows]
    changes = [r[6] for r in rows]
    top_change = sorted(rows, key=lambda r: r[6], reverse=True)[:10]
    print("TOP GAINERS 24H:", [(r[1], round(r[6] * 100, 1), round(r[3])) for r in top_change])
    pre = []
    for r in rows:
        liquidity = percentile(volumes, math.log1p(r[3]))
        tightness = 1.0 - percentile(spreads, r[4])
        activity = percentile(ranges, r[5])
        abs_move = percentile([abs(c) for c in changes], abs(r[6]))
        score = 0.15 * liquidity + 0.15 * tightness + 0.35 * activity + 0.35 * abs_move
        pre.append((score, r))
    pre.sort(key=lambda x: x[0], reverse=True)
    print("PRE:", [r[1] for _, r in pre[:PREFILTER_N]])

    validated = []
    failures = 0
    for _, row in pre[:PREFILTER_N]:
        pair, wsname, price, volume_usd, spread_bps, _, _ = row
        features = []
        failed = False

        # --- Multi-timeframe chính (15m / 1h / 4h) ---
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

        # --- 1m riêng ---
        try:
            base = wsname.split("/")[0].upper()
            base = {"XBT": "BTC"}.get(base, base)

        feat_hl = compute_1m_features(base)
            if feat_hl is None:
                failures += 1
                continue

        trend_1m = feat_hl["trend_1m"]
        momentum_1m = feat_hl["momentum_1m"]
        vol_spike_1m = feat_hl["vol_spike_1m"]
        imbalance = feat_hl["imbalance"]
        funding = feat_hl["funding"]
            if feat_1m is None:
                failures += 1
                continue
        except Exception:
            failures += 1
            continue

        trends = [f[0] for f in features]          # 15m, 1h, 4h
        momenta = [f[1] for f in features]
        volume_ratios = [f[2] for f in features]
        trend_1m, momentum_1m, vol_spike_1m = feat_1m

        trend_strength = sum(trends) / 3.0
        momentum_strength = sum(momenta) / 3.0
        activity_strength = sum(min(max(v, 0.0), 3.0) for v in volume_ratios) / 9.0
        consistency = 1.0 - min(1.0, (max(trends) - min(trends)) / 3.0)
        signs = [1 if t > 0 else (-1 if t < 0 else 0) for t in trends]
        agreement = abs(sum(signs)) / 3.0
        quality = 0.45 * agreement + 0.30 * consistency + 0.25 * activity_strength

        validated.append((
            quality, trend_strength, momentum_strength, row,
            trends, trend_1m, momentum_1m, vol_spike_1m
        ))

    if not validated:
        raise RuntimeError("No prefiltered markets passed multi-timeframe data-quality validation")

    trend_values = [x[1] for x in validated]
    momentum_values = [x[2] for x in validated]
    quality_values = [x[0] for x in validated]

    candidates = []
    for quality, trend, momentum, row, trends, trend_1m, momentum_1m, vol_spike_1m in validated:
        pair, wsname, price, volume_usd, spread_bps, _, _ = row
        liquidity = percentile(volumes, math.log1p(volume_usd))
        tightness = 1.0 - percentile(spreads, spread_bps)
        trend_rank = percentile(trend_values, trend)
        momentum_rank = percentile(momentum_values, momentum)
        quality_rank = percentile(quality_values, quality)
        spike = spike_score(pair)

        # Long pullback logic (giữ nguyên)
        pullback = 1.0 if (trends[1] > 0 and trends[2] > 0 and trends[0] <= 0) else 0.0

        # Short bias (giữ nguyên + hơi nới)
        short_bias = 0.0
        if trends[0] < -0.15 and trends[1] <= 0.10:
            short_bias = 1.0
        elif trends[0] < 0 and trends[1] < 0 and trends[2] < 0:
            short_bias = 1.0

        # === NEW: 1m confirmation ===
        one_min_confirm = 0.0
        if trend_1m > 0.08 and trends[0] > 0:          # 1m + 15m cùng chiều long
            one_min_confirm = 0.7
        elif trend_1m < -0.12 and trends[0] < 0:       # 1m + 15m cùng chiều short
            one_min_confirm = 0.85

        abs_trend_rank = percentile([abs(v) for v in trend_values], abs(trend))

        print(f"SPIKE {pair}: {spike:.2f} | 1m_trend={trend_1m:+.3f} vol_spike={vol_spike_1m:.2f} "
              f"quality={quality:.2f} short={short_bias:.0f} pullback={pullback:.0f} confirm1m={one_min_confirm:.2f}")

        score = (
            0.10 * liquidity
            + 0.09 * tightness
            + 0.11 * abs_trend_rank
            + 0.11 * momentum_rank
            + 0.13 * quality_rank
            + 0.09 * spike
            + 0.14 * pullback
            + 0.14 * short_bias
            + 0.06 * one_min_confirm          # 1m confirmation (không quá nặng)
            + 0.03 * vol_spike_1m             # volume spike 1m nhẹ
        )

        candidates.append(Candidate(
            pair=pair,
            symbol=wsname,
            price=price,
            volume_usd=volume_usd,
            spread_bps=spread_bps,
            score=score,
            trend_1m=trend_1m,
            momentum_1m=momentum_1m,
            vol_spike_1m=vol_spike_1m,
            trend_15m=trends[0],
            trend_1h=trends[1],
            trend_4h=trends[2],
            quality=quality,
        ))

    candidates.sort(key=lambda x: x.score, reverse=True)

    def is_short(c: Candidate) -> bool:
        return (c.trend_15m < 0 and c.trend_1h <= 0.05)

    shorts = [c for c in candidates if is_short(c)]
    longs = [c for c in candidates if not is_short(c)]
    mixed: list[Candidate] = []
    for c in shorts[:1]:
        mixed.append(c)
    for c in longs:
        if len(mixed) >= TOP_N:
            break
        mixed.append(c)
    if mixed and longs and all(is_short(c) for c in mixed):
        mixed[-1] = longs[0]
    if len(mixed) < TOP_N:
        for c in candidates:
            if c not in mixed:
                mixed.append(c)
            if len(mixed) >= TOP_N:
                break
    candidates = mixed

    print("\nBROAD REAL-MARKET MULTI-TIMEFRAME SCANNER (Level 1 - Real 1m)")
    print("DATA SOURCE: KRAKEN PUBLIC SPOT MARKET DATA")
    print("CANDLES: COMPLETED ONLY (including 1m)")
    print("LIVE ORDERS: DISABLED")
    print(f"USD SPOT UNIVERSE: {len(rows)} qualifying after broad ticker gate")
    print(f"MULTI-TIMEFRAME PREFILTER: {min(PREFILTER_N, len(pre))}")
    print(f"VALIDATION FAILURES: {failures}")
    print(f"VALIDATED MARKETS: {len(candidates)}")
    print(f"TOP {min(TOP_N, len(candidates))} CANDIDATES:")
    for rank, c in enumerate(candidates[:TOP_N], 1):
        bias = "short" if is_short(c) else "long"
        print(f"{rank:02d} {c.symbol:12} price=\( {c.price:,.8g} vol24h= \){c.volume_usd:,.0f} "
              f"spread={c.spread_bps:.2f}bps score={c.score:.4f} quality={c.quality:.4f} "
              f"1m={c.trend_1m:+.3f} 15m={c.trend_15m:+.2f} 1h={c.trend_1h:+.2f} 4h={c.trend_4h:+.2f} "
              f"vol1m={c.vol_spike_1m:.2f} → {bias}")
    print(f"SCANNER ELAPSED: {time.time() - started:.1f}s")
    print("NEXT GATE: Only these candidates should enter TradingAgents deep analysis.")
    print("NEXT GATE: Ranking is not a trade instruction; BUY/SELL/HOLD/REVIEW remains downstream.")
    print("LIVE TRADING GATE: DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
