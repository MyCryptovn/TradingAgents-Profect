"""CoinGecko market-data adapter for TradingAgents paper analysis.

The pinned TradingAgents source is stock-oriented and normally uses Yahoo Finance.
For this crypto test path, the analyst market data comes from CoinGecko.

Design:
- CoinGecko API is the primary AI market-data provider.
- Optional COINGECKO_API_KEY is supported (Demo or Pro via API base URL).
- CoinGecko requests are cached in-process so repeated TradingAgents tool calls
  do not hammer the API.
- 429 responses use Retry-After/exponential backoff instead of failing instantly.
- Paper outcome measurement remains separate from the AI data path.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Annotated

import pandas as pd
from langchain_core.tools import tool

COINGECKO_PUBLIC_API = "https://api.coingecko.com/api/v3/"
COINGECKO_PRO_API = "https://pro-api.coingecko.com/api/v3/"
USER_AGENT = "TradingAgents-Profect/coingecko-data"
MAX_HISTORY_ROWS = 720


def _api_base() -> str:
    return os.getenv("COINGECKO_API_BASE_URL", COINGECKO_PUBLIC_API).rstrip("/") + "/"


def _api_key() -> str:
    return (
        os.getenv("COINGECKO_API_KEY")
        or os.getenv("CG_API_KEY")
        or os.getenv("COINGECKO_DEMO_API_KEY")
        or ""
    ).strip()


def _get_json(
    path: str,
    params: dict[str, str] | None = None,
    *,
    retries: int = 4,
) -> dict:
    base = _api_base()
    url = base + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)

    key = _api_key()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if key:
        if "pro-api.coingecko.com" in base:
            headers["x-cg-pro-api-key"] = key
        else:
            headers["x-cg-demo-api-key"] = key

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.load(response)
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(f"CoinGecko API error: {payload['error']}")
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt >= retries:
                raise
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(2 ** attempt, 16)
            except ValueError:
                delay = min(2 ** attempt, 16)
            print(
                f"COINGECKO RATE LIMIT: HTTP 429; retrying in {delay:.1f}s "
                f"(attempt {attempt + 1}/{retries})"
            )
            time.sleep(delay)
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(min(2 ** attempt, 8))

    raise RuntimeError(f"CoinGecko request failed: {last_error}")


def _base_symbol(symbol: str) -> str:
    base = symbol.upper().strip().replace("/USD", "").replace("-USD", "")
    return "BTC" if base == "XBT" else base


@lru_cache(maxsize=1)
def _coins_list() -> list[dict]:
    payload = _get_json("coins/list", {"include_platform": "false"})
    if not isinstance(payload, list):
        raise RuntimeError("CoinGecko coins/list returned an unexpected payload")
    return payload


@lru_cache(maxsize=256)
def _resolve_coin_id(symbol: str) -> str:
    base = _base_symbol(symbol).lower()

    # Stable, high-confidence aliases for the most common scanner symbols.
    aliases = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "bnb": "binancecoin",
        "sol": "solana",
        "xrp": "ripple",
        "ada": "cardano",
        "doge": "dogecoin",
        "avax": "avalanche-2",
        "dot": "polkadot",
        "matic": "matic-network",
        "pol": "polygon-ecosystem-token",
        "link": "chainlink",
        "ltc": "litecoin",
        "trx": "tron",
        "atom": "cosmos",
        "uni": "uniswap",
        "etc": "ethereum-classic",
        "xlm": "stellar",
        "bch": "bitcoin-cash",
        "near": "near",
        "apt": "aptos",
        "arb": "arbitrum",
        "op": "optimism",
        "fil": "filecoin",
        "inj": "injective-protocol",
        "sui": "sui",
        "pepe": "pepe",
        "aave": "aave",
        "mkr": "maker",
    }
    if base in aliases:
        return aliases[base]

    matches = [
        item
        for item in _coins_list()
        if str(item.get("symbol", "")).lower() == base
    ]
    if not matches:
        raise RuntimeError(f"CoinGecko coin id not found for {symbol}")

    # Prefer an exact id/name match before falling back to the first symbol match.
    for item in matches:
        if str(item.get("id", "")).lower() == base:
            return str(item["id"])
    return str(matches[0]["id"])


@lru_cache(maxsize=256)
def _history(symbol: str) -> pd.DataFrame:
    """Build a daily price/volume history from one CoinGecko market-chart request.

    CoinGecko's daily market-chart data is used as the stable free/demo-compatible
    history source. OHLC fields are reconstructed from consecutive daily prices;
    volume comes from CoinGecko's total_volumes series.
    """
    coin_id = _resolve_coin_id(symbol)
    payload = _get_json(
        f"coins/{urllib.parse.quote(coin_id, safe='')}/market_chart",
        {
            "vs_currency": "usd",
            "days": "365",
            "interval": "daily",
        },
    )
    prices = payload.get("prices") or []
    volumes = payload.get("total_volumes") or []
    if len(prices) < 60:
        raise RuntimeError(
            f"CoinGecko returned insufficient price history for {symbol}: {len(prices)} rows"
        )

    volume_map: dict[pd.Timestamp, float] = {}
    for timestamp, value in volumes:
        dt = pd.to_datetime(float(timestamp), unit="ms", utc=True).tz_convert(None)
        volume_map[dt.normalize()] = float(value)

    rows = []
    previous_close: float | None = None
    for timestamp, value in prices:
        close = float(value)
        if close <= 0:
            continue
        dt = pd.to_datetime(float(timestamp), unit="ms", utc=True).tz_convert(None)
        open_price = previous_close if previous_close is not None else close
        rows.append(
            {
                "Date": dt,
                "Open": open_price,
                "High": max(open_price, close),
                "Low": min(open_price, close),
                "Close": close,
                "Volume": volume_map.get(dt.normalize(), 0.0),
            }
        )
        previous_close = close

    df = (
        pd.DataFrame(rows)
        .sort_values("Date")
        .drop_duplicates("Date")
        .reset_index(drop=True)
    )
    if len(df) < 60:
        raise RuntimeError(f"CoinGecko history too short for {symbol}")
    return df.tail(MAX_HISTORY_ROWS).reset_index(drop=True)

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


@lru_cache(maxsize=256)
def _live_market(symbol: str) -> dict:
    coin_id = _resolve_coin_id(symbol)
    rows = _get_json(
        "coins/markets",
        {
            "vs_currency": "usd",
            "ids": coin_id,
            "sparkline": "false",
            "price_change_percentage": "24h",
        },
    )
    if not rows:
        raise RuntimeError(f"CoinGecko returned no live market row for {symbol}")
    return rows[0]


@tool
def get_stock_data(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """CoinGecko-backed replacement for TradingAgents' stock-data tool."""
    df = _history(symbol)
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    df = df[(df["Date"] >= start) & (df["Date"] < end)]
    if df.empty:
        raise RuntimeError(
            f"No CoinGecko crypto market data for {symbol} in requested range"
        )

    return (
        f"# Crypto market data for {symbol.upper()}\n"
        f"# Provider: CoinGecko API\n"
        f"# Timeframe: CoinGecko market-chart daily data; OHLC reconstructed from daily closes\n"
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
    """Calculate TradingAgents-compatible indicators from CoinGecko data."""
    df = _indicators(_history(symbol))
    cutoff = pd.Timestamp(curr_date)
    df = df[df["Date"] <= cutoff]
    if df.empty or indicator not in df.columns:
        raise RuntimeError(
            f"Unsupported or unavailable crypto indicator: {indicator}"
        )

    rows = df.tail(max(1, int(look_back_days)))
    lines = [f"## {indicator} for {symbol.upper()} (CoinGecko-native)", ""]
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
    """Verified crypto snapshot using CoinGecko historical + live market data."""
    df = _indicators(_history(symbol))
    cutoff = pd.Timestamp(curr_date)
    df = df[df["Date"] <= cutoff]
    if df.empty:
        raise RuntimeError(f"No verified CoinGecko historical data for {symbol}")

    latest = df.iloc[-1]
    live = _live_market(symbol)

    lines = [
        f"## Verified crypto market snapshot for {symbol.upper()}",
        "- Historical source: CoinGecko API",
        "- Live context: CoinGecko coins/markets",
        f"- CoinGecko coin id: {_resolve_coin_id(symbol)}",
        f"- Analysis cutoff: {curr_date}",
        "",
        "### Latest completed historical candle",
        f"- Date: {latest['Date'].strftime('%Y-%m-%d')}",
        f"- Open: {latest['Open']:.10g}",
        f"- High: {latest['High']:.10g}",
        f"- Low: {latest['Low']:.10g}",
        f"- Close: {latest['Close']:.10g}",
        f"- Volume: {latest['Volume']:.10g}",
        "",
        "### CoinGecko live context",
        f"- Last: {float(live.get('current_price') or 0):.10g}",
        f"- Market cap: {float(live.get('market_cap') or 0):.10g}",
        f"- 24h volume: {float(live.get('total_volume') or 0):.10g}",
        f"- 24h change: {float(live.get('price_change_percentage_24h') or 0):.6g}%",
        "",
        "### Verified indicators",
    ]

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
    """Resolve crypto identity from the CoinGecko market endpoint."""
    base = _base_symbol(ticker)
    pair = _resolve_coin_id(ticker)
    live = _live_market(ticker)
    return {
        "company_name": str(live.get("name") or base),
        "sector": "Crypto asset",
        "industry": "Cryptocurrency",
        "exchange": "CoinGecko",
        "quote_type": "CRYPTOCURRENCY",
        "market_cap": str(live.get("market_cap") or ""),
        "volume_24h": str(live.get("total_volume") or ""),
        "pair": pair,
    }
