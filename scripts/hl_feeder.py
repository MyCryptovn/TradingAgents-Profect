"""
Hyperliquid market-data feeder for TradingAgents-Profect.

Read-only market data:
- Discovers native perpetuals and HIP-3 perp DEXs.
- Fetches metadata and asset contexts once per DEX.
- Fetches completed candles and L2 books on demand.
- Calculates multi-timeframe, USD-depth and order-book features.
- No wallet, signing key, order placement or live trading.
"""

from __future__ import annotations

import math
import time
import json
import urllib.request
import urllib.error
from typing import Any

INFO_URL = "https://api.hyperliquid.xyz/info"
SNAPSHOT_FILE = "tradingagents-paper-results/hl_oi_snapshots.json"
USER_AGENT = "TradingAgents-Profect/market-feeder"

# Cache lifetime in seconds.
METADATA_TTL = 30
BOOK_TTL = 5
CANDLE_TTL = 15

# Limit retries to avoid long workflow stalls.
MAX_RETRIES = 3
REQUEST_TIMEOUT = 20

_metadata_cache: dict[str, tuple[float, Any]] = {}
_book_cache: dict[str, tuple[float, Any]] = {}
_candle_cache: dict[str, tuple[float, Any]] = {}


def _post(payload: dict, retries: int = MAX_RETRIES) -> Any:
    """Read-only POST to Hyperliquid Info API with bounded retry."""
    body = json.dumps(payload).encode("utf-8")
    last_error = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(
            INFO_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req, timeout=REQUEST_TIMEOUT
            ) as response:
                result = json.loads(response.read().decode("utf-8"))

            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(str(result["error"]))

            return result

        except urllib.error.HTTPError as exc:
            last_error = exc

            # Retry temporary server/rate-limit responses only.
            if exc.code not in (429, 500, 502, 503, 504):
                raise

            if attempt >= retries:
                raise

            retry_after = exc.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(
                    2 ** attempt, 8
                )
            except (TypeError, ValueError):
                delay = min(2 ** attempt, 8)

            time.sleep(max(1.0, min(delay, 15.0)))

        except Exception as exc:
            last_error = exc

            if attempt >= retries:
                raise

            time.sleep(min(2 ** attempt, 8))

    raise RuntimeError(f"Hyperliquid request failed: {last_error}")


def _cached(cache, key: str, ttl: int, loader):
    now = time.time()
    item = cache.get(key)

    if item and now - item[0] < ttl:
        return item[1]

    value = loader()
    cache[key] = (now, value)
    return value


def _number(value, default: float = 0.0) -> float:
    try:
        result = float(value)
        if result == result and abs(result) != float("inf"):
            return result
    except (TypeError, ValueError):
        pass
    return default


def _strict_number(value) -> float | None:
    """Return a finite float, or None for invalid input."""
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(result):
        return None

    return result


def _normalize_candle(
    row: dict,
    interval: str,
    now_ms: int,
) -> dict | None:
    """
    Normalize one Hyperliquid candle.

    Reject:
    - Invalid or future timestamps.
    - Unclosed candles.
    - Invalid OHLC relationships.
    - Negative or non-finite volume/trade count.
    """
    if not isinstance(row, dict):
        return None

    timestamp = _strict_number(row.get("t"))
    close_time = _strict_number(row.get("T"))

    open_price = _strict_number(row.get("o"))
    high_price = _strict_number(row.get("h"))
    low_price = _strict_number(row.get("l"))
    close_price = _strict_number(row.get("c"))

    volume = _strict_number(row.get("v"))
    trades = _strict_number(row.get("n"))

    if any(value is None for value in (
        timestamp,
        close_time,
        open_price,
        high_price,
        low_price,
        close_price,
        volume,
        trades,
    )):
        return None

    if timestamp <= 0 or close_time < timestamp:
        return None

    # Exclude candles that have not closed.
    if close_time > now_ms:
        return None

    if min(open_price, high_price, low_price, close_price) <= 0:
        return None

    if volume < 0 or trades < 0:
        return None

    # Validate the OHLC price structure.
    if high_price < max(open_price, close_price, low_price):
        return None

    if low_price > min(open_price, close_price, high_price):
        return None

    if trades != int(trades):
        return None

    return {
        "time": int(timestamp),
        "close_time": int(close_time),
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "trades": int(trades),
        "interval": interval,
    }


def _dex_list() -> list[str]:
    """
    Return native DEX plus all named HIP-3 DEXs.

    The native perp DEX is represented by an empty string.
    """
    payload = _post({"type": "perpDexs"})

    names = [""]

    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)

    return names


def get_dex_names() -> list[str]:
    return _cached(
        _metadata_cache,
        "__dex_names__",
        METADATA_TTL,
        _dex_list,
    )


def _load_dex_metadata(dex: str) -> dict:
    request = {"type": "metaAndAssetCtxs"}

    if dex:
        request["dex"] = dex

    payload = _post(request)

    if (
        not isinstance(payload, list)
        or len(payload) < 2
        or not isinstance(payload[0], dict)
        or not isinstance(payload[1], list)
    ):
        raise RuntimeError(
            f"Unexpected metadata response for DEX {dex or 'native'}"
        )

    universe = payload[0].get("universe", [])
    contexts = payload[1]

    markets = []

    for index, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue

        coin = str(asset.get("name") or "").strip()
        if not coin:
            continue

        context = contexts[index] if index < len(contexts) else {}

        if not isinstance(context, dict):
            context = {}

        # Keep the full DEX-prefixed name for HIP-3.
        full_coin = f"{dex}:{coin}" if dex else coin

        markets.append({
            "coin": full_coin,
            "base_coin": coin,
            "dex": dex,
            "is_native": not bool(dex),
            "asset_index": index,
            "sz_decimals": asset.get("szDecimals"),
            "max_leverage": asset.get("maxLeverage"),
            "only_isolated": bool(asset.get("onlyIsolated", False)),
            "mark_price": _number(context.get("markPx")),
            "mid_price": _number(context.get("midPx")),
            "oracle_price": _number(context.get("oraclePx")),
            "funding": _number(context.get("funding")),
            "open_interest": _number(context.get("openInterest")),
            "day_volume_usd": _number(context.get("dayNtlVlm")),
            "previous_day_price": _number(context.get("prevDayPx")),
            "premium": _number(context.get("premium")),
        })

    return {
        "dex": dex,
        "markets": markets,
        "fetched_at": time.time(),
    }


def get_dex_markets(dex: str = "") -> dict:
    """Fetch metadata and current contexts for one DEX."""
    return _cached(
        _metadata_cache,
        f"metadata:{dex}",
        METADATA_TTL,
        lambda: _load_dex_metadata(dex),
    )


def discover_perp_markets() -> list[dict]:
    """
    Discover all markets exposed by the perp DEX list.

    A failed DEX does not erase successful results from other DEXs.
    Errors are printed so incomplete discovery is visible.
    """
    markets = []
    errors = []

    for dex in get_dex_names():
        try:
            result = get_dex_markets(dex)
            markets.extend(result["markets"])
        except Exception as exc:
            label = dex or "native"
            errors.append(f"{label}: {type(exc).__name__}: {exc}")

    if errors:
        print("HYPERLIQUID DEX DISCOVERY WARNINGS:")
        for error in errors:
            print(f"  - {error}")

    # Prevent duplicate full market names.
    unique = {}
    for market in markets:
        unique[market["coin"]] = market

    return list(unique.values())


def get_candles(
    coin: str,
    interval: str = "1m",
    lookback_minutes: int = 360,
) -> list[dict]:
    """
    Return normalized, completed Hyperliquid candles.

    Duplicate timestamps are removed.
    Invalid and unfinished candles are rejected.
    """
    allowed = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "8h": 480,
        "12h": 720,
        "1d": 1440,
    }

    if interval not in allowed:
        raise ValueError(f"Unsupported candle interval: {interval}")

    try:
        lookback_minutes = int(lookback_minutes)
    except (TypeError, ValueError):
        raise ValueError("lookback_minutes must be an integer")

    lookback_minutes = max(
        allowed[interval],
        min(lookback_minutes, 5000 * allowed[interval]),
    )

    cache_key = f"{coin}:{interval}:{lookback_minutes}"

    def load():
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - lookback_minutes * 60 * 1000

        payload = _post({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": now_ms,
            },
        })

        if not isinstance(payload, list):
            raise RuntimeError(
                f"Invalid candle response for {coin} {interval}"
            )

        unique = {}

        for raw in payload:
            candle = _normalize_candle(
                raw,
                interval,
                now_ms,
            )

            if candle is None:
                continue

            # Keep the last valid row for each timestamp.
            unique[candle["time"]] = candle

        return sorted(
            unique.values(),
            key=lambda item: item["time"],
        )

    return _cached(
        _candle_cache,
        cache_key,
        CANDLE_TTL,
        load,
    )
    
        
def get_l2_book(coin: str) -> dict:
    """Get an L2 book snapshot for a native or DEX-prefixed coin."""
    def load():
        payload = _post({"type": "l2Book", "coin": coin})

        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid L2 book for {coin}")

        return payload

    return _cached(_book_cache, coin, BOOK_TTL, load)


def orderbook_features(coin: str) -> dict:
    """
    Calculate spread, USD depth and imbalance from L2 price levels.

    Depth is calculated as price * size, not raw token quantity.
    """
    book = get_l2_book(coin)
    levels = book.get("levels", [[], []])

    bids = levels[0] if len(levels) > 0 else []
    asks = levels[1] if len(levels) > 1 else []

    bid_rows = []
    ask_rows = []

    for row in bids:
        px = _number(row.get("px"))
        sz = _number(row.get("sz"))
        if px > 0 and sz > 0:
            bid_rows.append((px, sz, px * sz))

    for row in asks:
        px = _number(row.get("px"))
        sz = _number(row.get("sz"))
        if px > 0 and sz > 0:
            ask_rows.append((px, sz, px * sz))

    if not bid_rows or not ask_rows:
        return {
            "book_valid": False,
            "spread_bps": None,
            "mid_price": None,
            "imbalance_usd": 0.0,
            "bid_depth_usd": 0.0,
            "ask_depth_usd": 0.0,
            "depth_10bps_usd": 0.0,
            "depth_25bps_usd": 0.0,
            "book_time": _number(book.get("time")),
        }

    best_bid = max(x[0] for x in bid_rows)
    best_ask = min(x[0] for x in ask_rows)

    if best_bid <= 0 or best_ask <= best_bid:
        return {
            "book_valid": False,
            "spread_bps": None,
            "mid_price": None,
            "imbalance_usd": 0.0,
            "bid_depth_usd": 0.0,
            "ask_depth_usd": 0.0,
            "depth_10bps_usd": 0.0,
            "depth_25bps_usd": 0.0,
            "book_time": _number(book.get("time")),
        }

    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 10000

    bid_usd = sum(x[2] for x in bid_rows)
    ask_usd = sum(x[2] for x in ask_rows)
    total = bid_usd + ask_usd

    imbalance = (
        (bid_usd - ask_usd) / total
        if total > 0 else 0.0
    )

    def depth_within(bps: float) -> tuple[float, float]:
        lower = mid * (1 - bps / 10000)
        upper = mid * (1 + bps / 10000)

        bid_depth = sum(
            value for px, _, value in bid_rows if px >= lower
        )
        ask_depth = sum(
            value for px, _, value in ask_rows if px <= upper
        )

        return bid_depth, ask_depth

    bid_10, ask_10 = depth_within(10)
    bid_25, ask_25 = depth_within(25)

    return {
        "book_valid": True,
        "spread_bps": spread_bps,
        "mid_price": mid,
        "imbalance_usd": imbalance,
        "bid_depth_usd": bid_usd,
        "ask_depth_usd": ask_usd,
        "depth_10bps_usd": bid_10 + ask_10,
        "depth_25bps_usd": bid_25 + ask_25,
        "bid_depth_10bps_usd": bid_10,
        "ask_depth_10bps_usd": ask_10,
        "book_time": _number(book.get("time")),
    }


def candle_features(candles: list[dict]) -> dict:
    """
    Calculate features from normalized completed candles.

    Data gaps and candle freshness are exposed explicitly.
    They are not silently converted into neutral market signals.
    """
    if not isinstance(candles, list):
        return {
            "valid": False,
            "candle_count": 0,
            "error": "INVALID_CANDLE_COLLECTION",
        }

    if len(candles) < 30:
        return {
            "valid": False,
            "candle_count": len(candles),
            "error": "INSUFFICIENT_CANDLES",
        }

    interval = candles[-1].get("interval")

    interval_minutes = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "8h": 480,
        "12h": 720,
        "1d": 1440,
    }.get(interval)

    if not interval_minutes:
        return {
            "valid": False,
            "candle_count": len(candles),
            "error": "UNKNOWN_CANDLE_INTERVAL",
        }

    # Validate chronological order and candle values again.
    normalized = []

    for candle in candles:
        if not isinstance(candle, dict):
            continue

        values = [
            _strict_number(candle.get(key))
            for key in ("time", "close_time", "open", "high",
                        "low", "close", "volume")
        ]

        if any(value is None for value in values):
            continue

        timestamp, close_time, op, hi, lo, cl, vol = values

        if (
            timestamp <= 0
            or close_time < timestamp
            or min(op, hi, lo, cl) <= 0
            or vol < 0
            or hi < max(op, cl, lo)
            or lo > min(op, cl, hi)
        ):
            continue

        normalized.append(candle)

    normalized.sort(key=lambda item: item["time"])

    # Remove duplicate timestamps.
    unique = {
        int(item["time"]): item
        for item in normalized
    }
    candles = list(unique.values())

    if len(candles) < 30:
        return {
            "valid": False,
            "candle_count": len(candles),
            "error": "INSUFFICIENT_VALID_CANDLES",
        }

    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]

    last = closes[-1]

    sma8 = sum(closes[-8:]) / 8
    sma20 = sum(closes[-20:]) / 20
    sma30 = sum(closes[-30:]) / 30

    momentum_3 = last / closes[-4] - 1
    momentum_10 = last / closes[-11] - 1

    avg_volume = sum(volumes[-21:-1]) / 20

    volume_ratio = (
        volumes[-1] / avg_volume
        if avg_volume > 0 else 0.0
    )

    changes = [
        closes[i] - closes[i - 1]
        for i in range(1, len(closes))
    ]

    recent = changes[-14:]

    gains = sum(max(x, 0) for x in recent) / 14
    losses = sum(max(-x, 0) for x in recent) / 14

    if gains == 0 and losses == 0:
        rsi = 50.0
    elif losses == 0:
        rsi = 100.0
    else:
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

    # Detect missing intervals between consecutive candles.
    expected_ms = interval_minutes * 60 * 1000
    gap_count = 0
    max_gap_intervals = 1
    misaligned_intervals = 0

    for previous, current in zip(candles, candles[1:]):
        delta = current["time"] - previous["time"]

        if delta <= 0:
            continue

        steps = max(1, round(delta / expected_ms))

        missing = max(0, steps - 1)
        gap_count += missing
        max_gap_intervals = max(max_gap_intervals, steps)

        if abs(delta - steps * expected_ms) > 1000:
            misaligned_intervals += 1

    now_ms = int(time.time() * 1000)
    last_close_time = candles[-1]["close_time"]

    age_ms = max(0, now_ms - last_close_time)
    max_age_ms = expected_ms * 3 + 60_000

    return {
        "valid": True,
        "candle_count": len(candles),

        "last_price": last,
        "trend": sma8 / sma30 - 1 if sma30 else 0.0,
        "momentum_3": momentum_3,
        "momentum_10": momentum_10,
        "volume_ratio": volume_ratio,
        "rsi14": rsi,

        "last_candle_time": candles[-1]["time"],
        "last_candle_close_time": last_close_time,

        # New data-quality fields.
        "expected_interval_ms": expected_ms,
        "last_candle_age_ms": age_ms,
        "is_stale": age_ms > max_age_ms,

        "gap_count": gap_count,
        "max_gap_intervals": max_gap_intervals,
        "misaligned_intervals": misaligned_intervals,
        "has_gaps": gap_count > 0 or misaligned_intervals > 0,
    }


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


ONE_MIN_SPREAD_LIMIT_BPS = 35.0
ONE_MIN_DEPTH_MIN_USD = 10_000.0


def get_1m_candles_raw(coin: str, minutes: int = 180) -> list[dict]:
    """Lấy nến 1m, giữ cả nến đang chạy (is_closed = False)."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - minutes * 60 * 1000

    payload = _post({
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": "1m",
            "startTime": start_ms,
            "endTime": now_ms,
        },
    })

    if not isinstance(payload, list):
        return []

    rows = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        close_time = _number(row.get("T"))
        candle = {
            "time": _number(row.get("t")),
            "close_time": close_time,
            "open": _number(row.get("o")),
            "high": _number(row.get("h")),
            "low": _number(row.get("l")),
            "close": _number(row.get("c")),
            "volume": _number(row.get("v")),
            "is_closed": 0 < close_time <= now_ms,
        }
        if candle["time"] > 0 and candle["open"] > 0 and candle["close"] > 0:
            rows[candle["time"]] = candle

    return [rows[k] for k in sorted(rows)]


def compute_1m_features(coin: str) -> dict | None:
    """Tín hiệu bơm/xả ngắn hạn từ nến 1m đang chạy + order book.
    Dương = áp lực mua (bơm), âm = áp lực bán (xả)."""
    try:
        now_ms = int(time.time() * 1000)
        raw = get_1m_candles_raw(coin, 180)
        closed = [c for c in raw if c["is_closed"]]

        features = candle_features(closed)
        if not features.get("valid"):
            return None

        forming_rows = [c for c in raw if not c["is_closed"]]
        if not forming_rows:
            return None
        forming = forming_rows[-1]

        book = orderbook_features(coin)
        if not book.get("book_valid"):
            return None

        spread = book.get("spread_bps")
        if spread is None or spread > ONE_MIN_SPREAD_LIMIT_BPS:
            return None

        depth_10 = book.get("depth_10bps_usd", 0.0) or 0.0
        if depth_10 < ONE_MIN_DEPTH_MIN_USD:
            return None

        avg_volume = sum(c["volume"] for c in closed[-21:-1]) / 20 if len(closed) > 21 else 0.0

        elapsed = _clamp((now_ms - forming["time"]) / 60000.0, 0.05, 1.0)
        move_pct = forming["close"] / forming["open"] - 1.0 if forming["open"] else 0.0
        projected_ratio = (forming["volume"] / elapsed) / avg_volume if avg_volume > 0 else 0.0
        reliable = elapsed >= 0.5 and avg_volume > 0

        imbalance = book.get("imbalance_usd", 0.0) or 0.0
        vol_spike = features.get("volume_ratio", 1.0) or 1.0

        score = 0.0
        if reliable:
            score += _clamp((projected_ratio - 1.0) / 2.0) * 0.40
        score += _clamp(move_pct * 25) * 0.25
        score += _clamp(imbalance * 1.5) * 0.25
        score += _clamp((vol_spike - 1.0) / 1.5) * 0.10

        if depth_10 < 40_000:
            score *= 0.7
        if spread > 6:
            score *= 0.85

        bom_xa_score = round(_clamp(score), 4)

        if bom_xa_score >= 0.55:
            label = "BOM_MANH"
        elif bom_xa_score >= 0.25:
            label = "BOM"
        elif bom_xa_score <= -0.55:
            label = "XA_MANH"
        elif bom_xa_score <= -0.25:
            label = "XA"
        else:
            label = "TRUNG_TINH"

        return {
            "bom_xa_score": bom_xa_score,
            "bom_xa_label": label,
            "move_pct": round(move_pct * 100, 4),
            "projected_volume_ratio": round(projected_ratio, 4),
            "elapsed_fraction": round(elapsed, 4),
            "signal_reliable": reliable,
            "trend_1m": features["trend"],
            "rsi_1m": features["rsi14"],
            "imbalance": imbalance,
            "spread_bps": spread,
            "depth_10bps_usd": depth_10,
        }

    except Exception as exc:
        print(f"HL 1M FEATURE ERROR {coin}: {type(exc).__name__}: {exc}")
        return None


def compute_market_features(coin: str) -> dict:
    """Đa khung thời gian + order book + tín hiệu bơm/xả 1m cho scanner."""
    minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
    features = {"coin": coin, "timeframes": {}, "book": {}, "one_minute": {}}

    for tf, mins in minutes.items():
        try:
            candles = get_candles(coin=coin, interval=tf, lookback_minutes=mins * 200)
            row = candle_features(candles)
        except Exception as exc:
            print(f"HL TF ERROR {coin} {tf}: {type(exc).__name__}: {exc}")
            row = {"valid": False}
        features["timeframes"][tf] = row

    try:
        features["book"] = orderbook_features(coin)
    except Exception as exc:
        print(f"HL BOOK ERROR {coin}: {type(exc).__name__}: {exc}")
        features["book"] = {"book_valid": False}

    features["one_minute"] = compute_1m_features(coin) or {}
    return features

if __name__ == "__main__":
    print("HYPERLIQUID READ-ONLY FEEDER TEST")
    print("LIVE ORDERS: DISABLED")

    markets = discover_perp_markets()
    print(f"DISCOVERED PERP MARKETS: {len(markets)}")

    for market in markets[:5]:
        print(
            market["coin"],
            "DEX=", market["dex"] or "native",
            "24H_USD=", round(market["day_volume_usd"], 2),
            "OI=", market["open_interest"],
        )

    if markets:
        sample = markets[0]["coin"]
        print(f"\nFEATURE TEST: {sample}")
        print(json.dumps(
            compute_market_features(sample),
            indent=2,
            default=str,
        ))
