"""
Hyperliquid-first Long/Short Market Scanner.

Architecture:
- Discover the complete available Hyperliquid perp universe first.
- Apply a lightweight market-data gate.
- Deep-analyze a bounded, diversified sample.
- Calculate independent LONG and SHORT scores.
- Keep quality filtering separate from directional scoring.
- Export structured JSON and backward-compatible console output.

READ ONLY. NO WALLET. NO LIVE ORDERS.
"""

from __future__ import annotations

import json
import math
import os
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.hl_feeder import (
    discover_perp_markets,
    compute_market_features,
)

# ============================================================
# CONFIGURATION
# ============================================================

OUT_DIR = Path("tradingagents-paper-results")
RESULT_FILE = OUT_DIR / "scanner.json"
OI_STATE_FILE = OUT_DIR / "hl_oi_snapshots.json"

TOP_N_PER_SIDE = 3

# Lightweight discovery gate.
MIN_DAY_VOLUME_USD = float(
    os.getenv("SCANNER_MIN_DAY_VOLUME_USD", "50000")
)

# Maximum markets receiving full candle + orderbook analysis.
MAX_DETAILED_MARKETS = int(
    os.getenv("SCANNER_MAX_DETAILED_MARKETS", "30")
)

# Detailed analysis sample:
# top liquid markets + a diversified sample of smaller markets.
TOP_LIQUID_SAMPLE = int(
    os.getenv("SCANNER_TOP_LIQUID_SAMPLE", "20")
)

MAX_SPREAD_BPS = float(
    os.getenv("SCANNER_MAX_SPREAD_BPS", "35")
)

MIN_DEPTH_10BPS_USD = float(
    os.getenv("SCANNER_MIN_DEPTH_10BPS_USD", "10000")
)

MIN_QUALITY_SCORE = float(
    os.getenv("SCANNER_MIN_QUALITY_SCORE", "45")
)

MIN_DIRECTION_SCORE = float(
    os.getenv("SCANNER_MIN_DIRECTION_SCORE", "60")
)

MIN_DIRECTION_GAP = float(
    os.getenv("SCANNER_MIN_DIRECTION_GAP", "6")
)

WATCHLIST_MIN_SCORE = float(
    os.getenv("SCANNER_WATCHLIST_MIN_SCORE", "52")
)

# Snapshot is useful only if it can be restored between workflow runs.
OI_MAX_AGE_SECONDS = 24 * 60 * 60

TIMEFRAME_WEIGHTS = {
    "1m": 0.10,
    "5m": 0.20,
    "15m": 0.35,
    "1h": 0.35,
}

TIMEFRAME_TREND_SCALES = {
    "1m": 0.002,
    "5m": 0.004,
    "15m": 0.008,
    "1h": 0.015,
}

TIMEFRAME_MOMENTUM_SCALES = {
    "1m": 0.004,
    "5m": 0.008,
    "15m": 0.015,
    "1h": 0.025,
}

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}

# ============================================================
# GENERAL HELPERS
# ============================================================


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


def normalized(value: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return clamp(value / scale)


def atomic_json_write(path: Path, payload: Any) -> None:
    """Avoid leaving partially written JSON files."""
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary = tempfile.mkstemp(
        prefix=path.stem + "_",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )

        os.replace(temporary, path)

    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_oi_state() -> dict:
    try:
        data = json.loads(OI_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


# ============================================================
# OI SNAPSHOTS
# ============================================================


def calculate_oi_change(
    market: dict,
    previous_state: dict,
    now: float,
) -> float | None:
    """
    Compare current OI with a previous timestamped observation.

    OI change is not invented when history is unavailable.
    """
    coin = market["coin"]
    current = safe_float(market.get("open_interest"))

    if current <= 0:
        return None

    previous = previous_state.get(coin)

    if not isinstance(previous, dict):
        return None

    old_value = safe_float(previous.get("open_interest"))
    old_time = safe_float(previous.get("timestamp"))

    age = now - old_time

    if old_value <= 0:
        return None

    if age < 60 or age > OI_MAX_AGE_SECONDS:
        return None

    return current / old_value - 1.0


def update_oi_state(
    markets: list[dict],
    previous_state: dict,
    now: float,
) -> dict:
    updated = dict(previous_state)

    for market in markets:
        coin = market["coin"]
        oi = safe_float(market.get("open_interest"))

        if oi <= 0:
            continue

        updated[coin] = {
            "open_interest": oi,
            "timestamp": now,
        }

    # Prevent unbounded state growth.
    cutoff = now - 7 * 24 * 60 * 60

    return {
        coin: value
        for coin, value in updated.items()
        if isinstance(value, dict)
        and safe_float(value.get("timestamp")) >= cutoff
    }


# ============================================================
# UNIVERSE AND DEEP-SCAN SELECTION
# ============================================================


def select_deep_scan_markets(
    markets: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Discover all markets first, then select a diversified sample.

    The sample is not limited to only the largest coins.
    """
    eligible = []
    rejected = []

    for market in markets:
        coin = str(market.get("coin") or "")
        volume = safe_float(market.get("day_volume_usd"))
        price = safe_float(market.get("mark_price"))

        reasons = []

        if not coin:
            reasons.append("MISSING_MARKET_ID")

        if price <= 0:
            reasons.append("INVALID_MARK_PRICE")

        if volume < MIN_DAY_VOLUME_USD:
            reasons.append("DAY_VOLUME_BELOW_DISCOVERY_FLOOR")

        if reasons:
            rejected.append({
                "coin": coin,
                "day_volume_usd": volume,
                "reasons": reasons,
            })
            continue

        eligible.append(market)

    eligible.sort(
        key=lambda x: safe_float(x.get("day_volume_usd")),
        reverse=True,
    )

    limit = max(1, MAX_DETAILED_MARKETS)
    top_count = min(
        max(1, TOP_LIQUID_SAMPLE),
        limit,
        len(eligible),
    )

    selected = eligible[:top_count]
    remaining = eligible[top_count:]

    slots = limit - len(selected)

    if slots > 0 and remaining:
        # Deterministically distribute the remaining slots
        # across the rest of the eligible volume distribution.
        if len(remaining) <= slots:
            selected.extend(remaining)
        else:
            indexes = [
                round(i * (len(remaining) - 1) / max(slots - 1, 1))
                for i in range(slots)
            ]

            seen = set()

            for index in indexes:
                market = remaining[index]
                coin = market["coin"]

                if coin not in seen:
                    selected.append(market)
                    seen.add(coin)

    return selected, rejected


# ============================================================
# DATA QUALITY
# ============================================================


def check_freshness(
    feature: dict,
    timeframe: str,
    now_ms: float,
) -> bool:
    close_time = safe_float(feature.get("last_candle_close_time"))

    if close_time <= 0:
        return False

    max_age = INTERVAL_MS[timeframe] * 3 + 60_000

    return 0 <= now_ms - close_time <= max_age


def assess_quality(
    market: dict,
    features: dict,
    now_ms: float,
) -> dict:
    reasons = []
    timeframes = features.get("timeframes", {})
    book = features.get("book", {})

    # The 5m, 15m and 1h data are mandatory.
    required = ("5m", "15m", "1h")

    for timeframe in required:
        row = timeframes.get(timeframe, {})

        if not row.get("valid"):
            reasons.append(f"MISSING_{timeframe.upper()}_DATA")
            continue

        if not check_freshness(row, timeframe, now_ms):
            reasons.append(f"STALE_{timeframe.upper()}_DATA")

    # 1m is important for short-term entries but may be unavailable.
    one_minute = timeframes.get("1m", {})

    if not one_minute.get("valid"):
        reasons.append("MISSING_1M_DATA")
    elif not check_freshness(one_minute, "1m", now_ms):
        reasons.append("STALE_1M_DATA")

    if not book.get("book_valid"):
        reasons.append("INVALID_ORDERBOOK")

    spread = book.get("spread_bps")

    if spread is None:
        reasons.append("MISSING_SPREAD")
    elif safe_float(spread, 999999) > MAX_SPREAD_BPS:
        reasons.append("SPREAD_TOO_WIDE")

    depth = safe_float(book.get("depth_10bps_usd"))

    if depth < MIN_DEPTH_10BPS_USD:
        reasons.append("INSUFFICIENT_10BPS_DEPTH")

    volume = safe_float(market.get("day_volume_usd"))

    # Quality score is separate from Long/Short direction.
    spread_score = 0.0

    if spread is not None:
        spread_score = clamp(
            1.0 - safe_float(spread) / MAX_SPREAD_BPS,
            0.0,
            1.0,
        )

    depth_score = clamp(
        math.log10(max(depth, 1.0) / MIN_DEPTH_10BPS_USD + 1)
        / 2.0,
        0.0,
        1.0,
    )

    volume_score = clamp(
        math.log10(max(volume, 1.0) / MIN_DAY_VOLUME_USD + 1)
        / 2.0,
        0.0,
        1.0,
    )

    completeness = sum(
        1 for tf in ("1m", "5m", "15m", "1h")
        if timeframes.get(tf, {}).get("valid")
    ) / 4.0

    quality_score = (
        30.0 * spread_score
        + 30.0 * depth_score
        + 20.0 * volume_score
        + 20.0 * completeness
    )

    if quality_score < MIN_QUALITY_SCORE:
        reasons.append("QUALITY_SCORE_TOO_LOW")

    return {
        "quality_score": round(quality_score, 2),
        "spread_bps": (
            round(safe_float(spread), 4)
            if spread is not None else None
        ),
        "depth_10bps_usd": round(depth, 2),
        "day_volume_usd": round(volume, 2),
        "data_completeness": round(completeness, 2),
        "passed": not reasons,
        "reasons": reasons,
    }


# ============================================================
# DIRECTIONAL FEATURES
# ============================================================


def timeframe_direction(row: dict, timeframe: str) -> float:
    """
    Return a directional value from -1 to +1.

    Positive means bullish pressure.
    Negative means bearish pressure.
    """
    if not row.get("valid"):
        return 0.0

    trend = safe_float(row.get("trend"))
    momentum = safe_float(row.get("momentum_10"))

    trend_signal = normalized(
        trend,
        TIMEFRAME_TREND_SCALES[timeframe],
    )

    momentum_signal = normalized(
        momentum,
        TIMEFRAME_MOMENTUM_SCALES[timeframe],
    )

    return clamp(
        0.55 * trend_signal + 0.45 * momentum_signal
    )


def directional_composite(features: dict) -> tuple[float, dict]:
    timeframes = features.get("timeframes", {})

    components = {}
    total_weight = 0.0
    composite = 0.0

    for timeframe, weight in TIMEFRAME_WEIGHTS.items():
        row = timeframes.get(timeframe, {})
        signal = timeframe_direction(row, timeframe)

        components[timeframe] = round(signal, 4)

        if row.get("valid"):
            composite += signal * weight
            total_weight += weight

    if total_weight <= 0:
        return 0.0, components

    return clamp(composite / total_weight), components


# ============================================================
# LONG AND SHORT SCORING
# ============================================================


def calculate_direction_scores(
    market: dict,
    features: dict,
    oi_change_pct: float | None,
) -> dict:
    """
    Independent directional scoring.

    Neither direction is inferred solely from the opposite score.
    """
    composite, timeframe_signals = directional_composite(features)

    timeframes = features.get("timeframes", {})
    book = features.get("book", {})

    imbalance = clamp(
        safe_float(book.get("imbalance_usd"))
    )

    funding = safe_float(market.get("funding"))

    # Positive funding mildly penalizes Long and favors Short.
    # Negative funding mildly penalizes Short and favors Long.
    funding_signal = clamp(funding / 0.0005)

    volume_ratios = [
        safe_float(timeframes.get(tf, {}).get("volume_ratio"))
        for tf in ("5m", "15m")
        if timeframes.get(tf, {}).get("valid")
    ]

    volume_ratio = (
        sum(volume_ratios) / len(volume_ratios)
        if volume_ratios else 0.0
    )

    # Volume confirms a direction only when price direction exists.
    volume_bonus = min(
        6.0,
        max(0.0, (volume_ratio - 1.0) * 3.0),
    )

    long_score = 50.0 + 32.0 * composite
    short_score = 50.0 - 32.0 * composite

    long_score += 8.0 * imbalance
    short_score -= 8.0 * imbalance

    long_score -= 4.0 * funding_signal
    short_score += 4.0 * funding_signal

    if composite > 0:
        long_score += volume_bonus
    elif composite < 0:
        short_score += volume_bonus

    # Avoid chasing extreme late moves.
    rsi_5m = safe_float(
        timeframes.get("5m", {}).get("rsi14"),
        50.0,
    )

    rsi_15m = safe_float(
        timeframes.get("15m", {}).get("rsi14"),
        50.0,
    )

    rsi_reference = (rsi_5m + rsi_15m) / 2.0

    if rsi_reference > 82:
        long_score -= 7.0

    if rsi_reference < 18:
        short_score -= 7.0

    # OI change is a contextual confirmation, not a standalone signal.
    oi_confirmation = 0.0

    if oi_change_pct is not None:
        oi_confirmation = clamp(
            oi_change_pct / 0.05
        )

        if composite > 0 and oi_confirmation > 0:
            long_score += min(3.0, oi_confirmation * 3.0)

        elif composite < 0 and oi_confirmation > 0:
            short_score += min(3.0, oi_confirmation * 3.0)

    long_score = clamp(long_score, 0.0, 100.0)
    short_score = clamp(short_score, 0.0, 100.0)

    if (
        long_score >= MIN_DIRECTION_SCORE
        and long_score - short_score >= MIN_DIRECTION_GAP
    ):
        direction = "LONG"

    elif (
        short_score >= MIN_DIRECTION_SCORE
        and short_score - long_score >= MIN_DIRECTION_GAP
    ):
        direction = "SHORT"

    else:
        direction = "WATCH"

    return {
        "direction": direction,
        "long_score": round(long_score, 2),
        "short_score": round(short_score, 2),
        "direction_gap": round(
            abs(long_score - short_score), 2
        ),
        "directional_composite": round(composite, 4),
        "timeframe_signals": timeframe_signals,
        "volume_ratio_confirmation": round(volume_ratio, 4),
        "rsi_reference": round(rsi_reference, 2),
        "funding": funding,
        "oi_change_pct": (
            round(oi_change_pct * 100, 4)
            if oi_change_pct is not None else None
        ),
        "oi_confirmation": round(oi_confirmation, 4),
        "orderbook_imbalance": round(imbalance, 4),
    }


# ============================================================
# CANDIDATE ANALYSIS
# ============================================================


def analyze_market(
    market: dict,
    previous_oi: dict,
    now: float,
) -> dict:
    coin = str(market["coin"])

    features = compute_market_features(coin)

    oi_change = calculate_oi_change(
        market,
        previous_oi,
        now,
    )

    quality = assess_quality(
        market,
        features,
        now * 1000,
    )

    scores = calculate_direction_scores(
        market,
        features,
        oi_change,
    )

    row = {
        "symbol": coin,
        "dex": market.get("dex") or "native",
        "is_native": bool(market.get("is_native")),
        "mark_price": safe_float(market.get("mark_price")),
        "day_volume_usd": safe_float(
            market.get("day_volume_usd")
        ),
        "open_interest": safe_float(
            market.get("open_interest")
        ),
        "previous_day_price": safe_float(
            market.get("previous_day_price")
        ),
        "funding": safe_float(market.get("funding")),
        "quality": quality,
        "scores": scores,
        "features": features,
        "analyzed_at": utc_now(),
    }

    row["passed_quality"] = quality["passed"]

    if not quality["passed"]:
        row["status"] = "REJECTED"
        row["rejection_reasons"] = quality["reasons"]

    elif scores["direction"] == "LONG":
        row["status"] = "LONG_CANDIDATE"
        row["rejection_reasons"] = []

    elif scores["direction"] == "SHORT":
        row["status"] = "SHORT_CANDIDATE"
        row["rejection_reasons"] = []

    elif max(
        scores["long_score"],
        scores["short_score"],
    ) >= WATCHLIST_MIN_SCORE:
        row["status"] = "WATCHLIST"
        row["rejection_reasons"] = []

    else:
        row["status"] = "NO_CLEAR_DIRECTION"
        row["rejection_reasons"] = [
            "DIRECTIONAL_SCORE_BELOW_WATCHLIST_THRESHOLD"
        ]

    return row


# ============================================================
# RANKING AND REPORTING
# ============================================================


def rank_candidates(rows: list[dict]) -> tuple[list, list, list]:
    valid = [
        row for row in rows
        if row.get("passed_quality")
    ]

    long_rows = [
        row for row in valid
        if row["scores"]["direction"] == "LONG"
    ]

    short_rows = [
        row for row in valid
        if row["scores"]["direction"] == "SHORT"
    ]

    watchlist = [
        row for row in valid
        if row["status"] == "WATCHLIST"
    ]

    long_rows.sort(
        key=lambda x: (
            x["scores"]["long_score"],
            x["quality"]["quality_score"],
        ),
        reverse=True,
    )

    short_rows.sort(
        key=lambda x: (
            x["scores"]["short_score"],
            x["quality"]["quality_score"],
        ),
        reverse=True,
    )

    watchlist.sort(
        key=lambda x: max(
            x["scores"]["long_score"],
            x["scores"]["short_score"],
        ),
        reverse=True,
    )

    return (
        long_rows[:TOP_N_PER_SIDE],
        short_rows[:TOP_N_PER_SIDE],
        watchlist[:20],
    )


def format_candidate_line(rank: int, row: dict, direction: str) -> str:
    scores = row["scores"]
    quality = row["quality"]

    score = (
        scores["long_score"]
        if direction == "LONG"
        else scores["short_score"]
    )

    return (
        f"{rank:02d} {row['symbol']}/USD "
        f"direction={direction} "
        f"score={score:.2f} "
        f"long_score={scores['long_score']:.2f} "
        f"short_score={scores['short_score']:.2f} "
        f"mtf_score={score / 100:.4f} "
        f"vol24h=${row['day_volume_usd']:,.0f} "
        f"spread={quality['spread_bps']:.3f} "
        f"depth10bps=${quality['depth_10bps_usd']:,.0f}"
    )


def print_report(
    all_rows: list[dict],
    long_rows: list[dict],
    short_rows: list[dict],
    watchlist: list[dict],
    rejected: list[dict],
    discovery_count: int,
    eligible_count: int,
    detailed_count: int,
    discovery_rejected: list[dict],
) -> None:
    print("\n============================================")
    print("HYPERLIQUID LONG / SHORT MARKET SCANNER")
    print("MODE: READ ONLY / PAPER ONLY")
    print("LIVE TRADING: DISABLED")
    print("============================================")

    print(f"MARKETS DISCOVERED: {discovery_count}")
    print(f"MARKETS ABOVE VOLUME FLOOR: {eligible_count}")
    print(f"MARKETS DEEP ANALYZED: {detailed_count}")

    print(
        f"QUALITY PASSED: "
        f"{sum(1 for x in all_rows if x['passed_quality'])}"
    )

    print(f"REJECTED: {len(rejected) + len(discovery_rejected)}")

    print("\nTOP LONG CANDIDATES:")

    if not long_rows:
        print("NONE")

    for rank, row in enumerate(long_rows, 1):
        print(format_candidate_line(rank, row, "LONG"))

    print("\nTOP SHORT CANDIDATES:")

    if not short_rows:
        print("NONE")

    for rank, row in enumerate(short_rows, 1):
        print(format_candidate_line(rank, row, "SHORT"))

    print("\nWATCHLIST:")

    for rank, row in enumerate(watchlist, 1):
        print(
            f"{rank:02d} {row['symbol']} "
            f"long={row['scores']['long_score']:.2f} "
            f"short={row['scores']['short_score']:.2f}"
        )

    print("\nREJECTED REASONS:")

    for row in (discovery_rejected + rejected)[:40]:
        print(
            f"REJECT {row.get('coin', row.get('symbol'))}: "
            f"{','.join(row.get('reasons', row.get('rejection_reasons', [])))}"
        )

    # Backward-compatible output for the existing paper pipeline.
    # Native symbols only: the old CoinGecko ticker adapter does not
    # yet safely understand DEX-prefixed HIP-3 market identifiers.
    compatibility = []

    for row in long_rows + short_rows:
        if row.get("is_native"):
            compatibility.append(row)

    compatibility.sort(
        key=lambda x: max(
            x["scores"]["long_score"],
            x["scores"]["short_score"],
        ),
        reverse=True,
    )

    compatibility = compatibility[:10]

    print("\nTOP CANDIDATES:")

    if not compatibility:
        print("NONE")

    for rank, row in enumerate(compatibility, 1):
        direction = row["scores"]["direction"]
        print(format_candidate_line(rank, row, direction))

    print("NEXT GATE: TRADINGAGENTS PAPER ANALYSIS")


# ============================================================
# MAIN
# ============================================================


def main() -> int:
    started = time.time()
    now = time.time()

    print("HYPERLIQUID MARKET DISCOVERY")
    print("READ ONLY: TRUE")
    print("LIVE ORDERS: DISABLED")

    try:
        markets = discover_perp_markets()
    except Exception as exc:
        print(
            f"FATAL DISCOVERY ERROR: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    if not markets:
        print("FATAL: No Hyperliquid perpetual markets discovered")
        return 1

    # All discovered markets are counted before any selection.
    selected, discovery_rejected = select_deep_scan_markets(
        markets
    )

    eligible_count = len(markets) - len(discovery_rejected)

    print(f"DISCOVERED: {len(markets)}")
    print(f"ELIGIBLE: {eligible_count}")
    print(f"DEEP SCAN SAMPLE: {len(selected)}")

    previous_oi = load_oi_state()

    rows = []
    rejected = []

    for index, market in enumerate(selected, 1):
        coin = market["coin"]

        print(
            f"\n[{index}/{len(selected)}] "
            f"ANALYZING {coin} "
            f"VOL24H=${safe_float(market.get('day_volume_usd')):,.0f}"
        )

        try:
            row = analyze_market(
                market,
                previous_oi,
                now,
            )

            rows.append(row)

            scores = row["scores"]

            print(
                f"RESULT {coin}: "
                f"LONG={scores['long_score']:.2f} "
                f"SHORT={scores['short_score']:.2f} "
                f"QUALITY={row['quality']['quality_score']:.2f} "
                f"STATUS={row['status']}"
            )

            if not row["passed_quality"]:
                rejected.append({
                    "coin": coin,
                    "reasons": row["quality"]["reasons"],
                })

        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"

            print(f"ANALYSIS ERROR {coin}: {message}")

            rejected.append({
                "coin": coin,
                "reasons": ["ANALYSIS_ERROR"],
                "error": message,
            })

    long_rows, short_rows, watchlist = rank_candidates(rows)

    updated_oi = update_oi_state(
        markets,
        previous_oi,
        now,
    )

    # The file is local to the runner. Persist it between workflow
    # runs using GitHub Actions cache or an artifact restore step.
    atomic_json_write(OI_STATE_FILE, updated_oi)

    payload = {
        "schema_version": 1,
        "scanner": "hyperliquid_long_short",
        "generated_at": utc_now(),
        "mode": "READ_ONLY_PAPER",
        "live_trading_enabled": False,
        "universe": {
            "discovered": len(markets),
            "above_volume_floor": eligible_count,
            "deep_analyzed": len(selected),
            "max_deep_scan": MAX_DETAILED_MARKETS,
            "min_day_volume_usd": MIN_DAY_VOLUME_USD,
        },
        "config": {
            "max_spread_bps": MAX_SPREAD_BPS,
            "min_depth_10bps_usd": MIN_DEPTH_10BPS_USD,
            "min_quality_score": MIN_QUALITY_SCORE,
            "min_direction_score": MIN_DIRECTION_SCORE,
            "min_direction_gap": MIN_DIRECTION_GAP,
        },
        "summary": {
            "quality_passed": sum(
                1 for row in rows if row["passed_quality"]
            ),
            "long_candidates": len(long_rows),
            "short_candidates": len(short_rows),
            "watchlist": len(watchlist),
            "rejected": len(rejected) + len(discovery_rejected),
            "analysis_errors": sum(
                1 for row in rejected
                if "error" in row
            ),
        },
        "top_long": long_rows,
        "top_short": short_rows,
        "watchlist": watchlist,
        "analyzed_markets": rows,
        "rejected": rejected,
        "discovery_rejected": discovery_rejected,
        "oi_state_file": str(OI_STATE_FILE),
        "duration_seconds": round(time.time() - started, 2),
    }

    atomic_json_write(RESULT_FILE, payload)

    print_report(
        rows,
        long_rows,
        short_rows,
        watchlist,
        rejected,
        len(markets),
        eligible_count,
        len(selected),
        discovery_rejected,
    )

    print(f"\nRESULT FILE: {RESULT_FILE}")
    print(f"OI STATE FILE: {OI_STATE_FILE}")
    print(f"DURATION: {time.time() - started:.1f}s")
    print("LIVE TRADING GATE: DISABLED")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
