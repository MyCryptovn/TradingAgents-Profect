"""Crypto-native market-data adapter for TradingAgents paper analysis.

The pinned TradingAgents source is stock-oriented and normally uses Yahoo Finance.
For this crypto project, the market-data path is deliberately exchange-native:
- Kraken Spot public API: historical OHLC + live ticker/order-book context.
- pandas: deterministic technical indicators.
- No CoinGecko dependency in the hot analysis path, so public API rate limits
  cannot block a Top-10 paper run.

This module is an overlay; it does not modify the pinned upstream package.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Annotated

import pandas as pd
from langchain_core.tools import tool

KRAKEN_API = "https://api.kraken.com/0/public/"
USER_AGENT = "TradingAgents-Profect/crypto-data"
OHLC_INTERVAL_MINUTES = 1440
MAX_HISTORY_ROWS = 720


def _get_json(path: str, params: dict[str, str] | None = None) -> dict:
    url = KRAKEN_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"Kraken API error: {payload['error']}")
    return payload["result"]


def _base_symbol(symbol: str) -> str:
    base = symbol.upper().strip().replace("/USD", "").replace("-USD", "")
    return "BTC" if base == "XBT" else base


@lru_cache(maxsize=1)
def _asset_pairs() -> dict:
    return _get_json("AssetPairs")


def _resolve_usd_pair(symbol: str) -> str:
    base = _base_symbol(symbol)
    target = f"{base}/USD"
    target_alt = f"{base}USD"

    for key, item in _asset_pairs().items():
        wsname = str(item.get("wsname", "")).upper()
        altname = str(item.get("altname", "")).upper()
        if wsname == target or altname == target_alt or key.upper() == target_alt:
            return key

    return target_alt


def _history(symbol: str, days: int = 365) -> pd.DataFrame:
    pair = _resolve_usd_pair(symbol)
    requested_rows = max(220, min(int(days) + 30, MAX_HISTORY_ROWS))
    result = _get_json(
        "OHLC",
        {"pair": pair, "interval": str(OHLC_INTERVAL_MINUTES)},
    )
    key = next((k for k in result if k != "last"), None)
    rows = result.get(key, []) if key else []
    rows = rows[-requested_rows:]
    if len(rows) < 60:
        raise RuntimeError(
            f"Kraken returned only {len(rows)} completed candles for {symbol}; "
            "at least 60 are required"
        )

    records = []
    for row in rows:
        records.append(
            {
                "Date": pd.to_datetime(float(row[0]), unit="s", utc=True).tz_convert(None),
                "Open": float(row[1]),
                "High": float(row[2]),
                "Low": float(row[3]),
                "Close": float(row[4]),
                "Volume": float(row[6]),
            }
        )
    return (
        pd.DataFrame(records)
        .sort_values("Date")
        .drop_duplicates("Date")
        .reset_index(drop=True)
    )


def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"]
    out["close_10_ema"] = close.ewm(span=10, adjust=False).mean()
    out["close_50_sma"] = close.rolling(50).mean()
    out["close_200_sma"] = close.rolling(200).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macds"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macdh"] = out["macd"] - out["macds"]

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, math.nan)
    out["rsi"] = 100 - (100 / (1 + rs))

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    out["boll"] = mid
    out["boll_ub"] = mid + 2 * std
    out["boll_lb"] = mid - 2 * std

    tr = pd.concat(
        [
            out["High"] - out["Low"],
            (out["High"] - close.shift()).abs(),
            (out["Low"] - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(14).mean()

    volume_sum = out["Volume"].rolling(20).sum().replace(0, math.nan)
    out["vwma"] = (close * out["Volume"]).rolling(20).sum() / volume_sum
    return out


@tool
def get_stock_data(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Crypto-native replacement for TradingAgents' stock-data tool."""
    days = max(
        365,
        (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 30,
    )
    df = _history(symbol, days=days)
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    df = df[(df["Date"] >= start) & (df["Date"] < end)]
    if df.empty:
        raise RuntimeError(
            f"No Kraken crypto market data for {symbol} in requested range"
        )

    return (
        f"# Crypto market data for {symbol.upper()}\n"
        f"# Provider: Kraken Spot public OHLC\n"
        f"# Timeframe: 1D completed candles\n"
        f"# Records: {len(df)}\n\n"
        + df[["Date", "Open", "High", "Low", "Close", "Volume"]].to_csv(index=False)
    )


@tool
def get_indicators(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    indicator: Annotated[str, "technical indicator name"],
    curr_date: Annotated[str, "analysis date YYYY-MM-DD"],
    look_back_days: Annotated[int, "days of history"] = 30,
) -> str:
    """Calculate TradingAgents-compatible indicators from Kraken crypto OHLC."""
    df = _indicators(_history(symbol, days=max(365, look_back_days + 210)))
    cutoff = pd.Timestamp(curr_date)
    df = df[df["Date"] <= cutoff]
    if df.empty or indicator not in df.columns:
        raise RuntimeError(
            f"Unsupported or unavailable crypto indicator: {indicator}"
        )

    rows = df.tail(max(1, int(look_back_days)))
    lines = [f"## {indicator} for {symbol.upper()} (Kraken-native)", ""]
    for _, row in rows.iterrows():
        value = row[indicator]
        value_text = "N/A" if pd.isna(value) else f"{float(value):.8g}"
        lines.append(f"{row['Date'].strftime('%Y-%m-%d')}: {value_text}")
    return "\n".join(lines)


@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    curr_date: Annotated[str, "analysis date YYYY-MM-DD"],
    look_back_days: Annotated[int, "recent rows"] = 30,
) -> str:
    """Verified crypto snapshot: Kraken historical OHLC + live ticker."""
    df = _indicators(_history(symbol, days=365))
    cutoff = pd.Timestamp(curr_date)
    df = df[df["Date"] <= cutoff]
    if df.empty:
        raise RuntimeError(f"No verified Kraken historical data for {symbol}")

    latest = df.iloc[-1]
    pair = _resolve_usd_pair(symbol)
    live = _get_json("Ticker", {"pair": pair})
    ticker = next(iter(live.values()), None)

    lines = [
        f"## Verified crypto market snapshot for {symbol.upper()}",
        "- Historical source: Kraken Spot public OHLC",
        "- Exchange-native live context: Kraken public Ticker",
        f"- Kraken pair: {pair}",
        f"- Analysis cutoff: {curr_date}",
        "",
        "### Latest completed historical candle",
        f"- Date: {latest['Date'].strftime('%Y-%m-%d')}",
        f"- Open: {latest['Open']:.10g}",
        f"- High: {latest['High']:.10g}",
        f"- Low: {latest['Low']:.10g}",
        f"- Close: {latest['Close']:.10g}",
        f"- Volume: {latest['Volume']:.10g}",
    ]

    if ticker:
        bid = float(ticker["b"][0])
        ask = float(ticker["a"][0])
        last = float(ticker["c"][0])
        mid = (ask + bid) / 2
        spread_bps = ((ask - bid) / mid * 10000) if mid else 0.0
        lines += [
            "",
            "### Kraken live context",
            f"- Last: {last:.10g}",
            f"- Bid: {bid:.10g}",
            f"- Ask: {ask:.10g}",
            f"- Spread: {spread_bps:.2f} bps",
        ]

    lines += ["", "### Verified indicators"]
    for name in (
        "close_10_ema",
        "close_50_sma",
        "close_200_sma",
        "rsi",
        "boll",
        "boll_ub",
        "boll_lb",
        "macd",
        "macds",
        "macdh",
        "atr",
        "vwma",
    ):
        value = latest.get(name)
        lines.append(
            f"- {name}: {'N/A' if pd.isna(value) else f'{float(value):.10g}'}"
        )

    recent = df.tail(max(1, min(int(look_back_days), 30)))
    lines += ["", "### Recent completed closes"]
    for _, row in recent.iterrows():
        lines.append(
            f"- {row['Date'].strftime('%Y-%m-%d')}: {row['Close']:.10g}"
        )

    return "\n".join(lines)


def crypto_identity(ticker: str) -> dict[str, str]:
    """Resolve minimal crypto identity without any external metadata API."""
    base = _base_symbol(ticker)
    pair = _resolve_usd_pair(ticker)
    return {
        "company_name": base,
        "sector": "Crypto asset",
        "industry": "Cryptocurrency",
        "exchange": "Kraken",
        "quote_type": "CRYPTOCURRENCY",
        "market_cap": "",
        "volume_24h": "",
        "pair": pair,
    }
