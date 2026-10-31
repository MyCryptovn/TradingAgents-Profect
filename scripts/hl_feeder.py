
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
    Get candles and exclude the currently forming candle.

    Hyperliquid candle rows contain t (open time) and T (close time).
    Only rows whose close time has passed are returned.
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

    lookback_minutes = max(
        allowed[interval],
        min(int(lookback_minutes), 5000 * allowed[interval]),
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
            return []

        completed = []

        for row in payload:
            if not isinstance(row, dict):
                continue

            close_time = _number(row.get("T"))
            if close_time <= 0 or close_time > now_ms:
                continue

            candle = {
                "time": _number(row.get("t")),
                "close_time": close_time,
                "open": _number(row.get("o")),
                "high": _number(row.get("h")),
                "low": _number(row.get("l")),
                "close": _number(row.get("c")),
                "volume": _number(row.get("v")),
                "trades": int(_number(row.get("n"))),
                "interval": interval,
            }

            if (
                candle["time"] > 0
                and candle["open"] > 0
                and candle["high"] > 0
                and candle["low"] > 0
                and candle["close"] > 0
            ):
                completed.append(candle)

        completed.sort(key=lambda x: x["time"])

        # Remove duplicate timestamps.
        unique = {row["time"]: row for row in completed}
        return list(unique.values())

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
    """Calculate basic momentum, trend, volume and RSI from completed candles."""
    if len(candles) < 30:
        return {
            "valid": False,
            "candle_count": len(candles),
        }

    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]

    last = closes[-1]
    sma8 = sum(closes[-8:]) / 8
    sma20 = sum(closes[-20:]) / 20
    sma30 = sum(closes[-30:]) / 30

    momentum_3 = last / closes[-4] - 1 if closes[-4] else 0
    momentum_10 = last / closes[-11] - 1 if closes[-11] else 0

    avg_volume = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 0

    changes = [
        closes[i] - closes[i - 1]
        for i in range(1, len(closes))
    ]

    recent = changes[-14:]
    gains = sum(max(x, 0) for x in recent) / 14
    losses = sum(max(-x, 0) for x in recent) / 14

    if losses == 0:
        rsi = 100.0 if gains > 0 else 50.0
    else:
        rs = gains / losses
        rsi = 100 - (100 / (1 + rs))

    return {
        "valid": True,
        "candle_count": len(candles),
        "last_price": last,
        "trend": sma8 / sma30 - 1 if sma30 else 0,
        "momentum_3": momentum_3,
        "momentum_10": momentum_10,
        "volume_ratio": volume_ratio,
        "rsi14": rsi,
        "last_candle_time": candles[-1]["time"],
        "last_candle_close_time": candles[-1]["close_time"],
    }
def compute_market_features(coin: str) -> dict:
    """
    Lấy dữ liệu đa timeframe (closed + forming) và order book,
    sau đó tính toàn bộ feature cho một coin.
    """
    intervals = {
        "1m": 180,
        "5m": 600,
        "15m": 1800,
        "1h": 7200,
    }

    result = {
        "coin": coin,
        "fetched_at": time.time(),
        "timeframes": {},
        "live": {},
        "book": {},
        "errors": [],
    }

    # --- Multi-timeframe candles ---
    for interval, lookback in intervals.items():
        try:
            candles = get_candles(
                coin=coin,
                interval=interval,
                lookback_minutes=lookback,
                include_forming=True,
            )
            closed = [c for c in candles if c.get("is_closed", True)]

            result["timeframes"][interval] = candle_features(closed)
            result["live"][interval] = forming_candle_features(candles, closed)

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            result["timeframes"][interval] = {"valid": False, "error": msg}
            result["live"][interval] = {"available": False, "error": msg}
            result["errors"].append(f"{interval}: {msg}")

    # --- Order book features ---
    try:
        result["book"] = orderbook_features(coin)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        result["book"] = {"book_valid": False, "error": msg}
        result["errors"].append(f"book: {msg}")

    return result

def compute_1m_features(coin: str) -> dict | None:
    """
    Lấy dữ liệu 1m đầy đủ: nến đóng + nến đang chạy + order book.
    Phục vụ phát hiện bơm xả và áp lực ngắn hạn.
    """
    try:
        # Lấy cả nến đóng và nến đang chạy
        candles = get_candles(
            coin=coin,
            interval="1m",
            lookback_minutes=180,
            include_forming=True,
        )

        closed = [c for c in candles if c.get("is_closed", True)]
        features = candle_features(closed)

        if not features.get("valid"):
            return None

        # Áp lực nến đang chạy (bơm / xả)
        live = forming_candle_features(candles, closed)

        # Order book
        book = orderbook_features(coin)

        return {
            # === Closed candle features ===
            "trend_1m": features["trend"],
            "momentum_1m": features["momentum_10"],
            "vol_spike_1m": min(features["volume_ratio"] / 5, 1.0),
            "volume_ratio_1m": features["volume_ratio"],
            "rsi_1m": features["rsi14"],
            "last_price": features["last_price"],

            # === Forming candle (bơm xả realtime) ===
            "forming_available": live.get("available", False),
            "forming_elapsed_fraction": live.get("elapsed_fraction"),
            "forming_move_pct": live.get("move_pct"),
            "forming_range_pct": live.get("range_pct"),
            "forming_volume_so_far": live.get("volume_so_far"),
            "forming_projected_volume": live.get("projected_volume"),
            "forming_projected_volume_ratio": live.get("projected_volume_ratio"),
            "forming_signal_reliable": live.get("signal_reliable", False),
            "forming_trades": live.get("trades"),

            # === Order book (bơm xả từ sổ lệnh) ===
            "imbalance": book.get("imbalance_usd", 0.0),
            "spread_bps": book.get("spread_bps"),
            "bid_depth_usd": book.get("bid_depth_usd", 0.0),
            "ask_depth_usd": book.get("ask_depth_usd", 0.0),
            "depth_10bps_usd": book.get("depth_10bps_usd", 0.0),
            "depth_25bps_usd": book.get("depth_25bps_usd", 0.0),
            "book_valid": book.get("book_valid", False),

            # Giữ tương thích cũ
            "funding": 0.0,
            "open_interest": 0.0,
        }

    except Exception as exc:
        print(f"HL FEATURE ERROR {coin}: {type(exc).__name__}: {exc}")
        return None

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
        ))

