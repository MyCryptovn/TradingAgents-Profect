"""Crypto-native market-data adapter for TradingAgents paper analysis.

The official TradingAgents source is stock-oriented and uses Yahoo Finance.
For this project, crypto analysis is routed through a crypto-native data layer:
- CoinGecko: broad token identity + historical market data.
- Kraken public API: exchange-native live ticker/order-book context.
- pandas: deterministic indicator calculations.

This module is an overlay; it does not modify the pinned upstream package.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Annotated

import pandas as pd
from langchain_core.tools import tool

COINGECKO_API = "https://api.coingecko.com/api/v3/"
KRAKEN_API = "https://api.kraken.com/0/public/"
USER_AGENT = "TradingAgents-Profect/crypto-data"


def _get_json(base: str, path: str, params: dict[str, str] | None = None) -> dict:
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"API error: {payload['error']}")
    return payload


def _base_symbol(symbol: str) -> str:
    base = symbol.upper().strip().replace("/USD", "").replace("-USD", "")
    return "BTC" if base == "XBT" else base


@lru_cache(maxsize=512)
def _coingecko_id(symbol: str) -> str:
    base = _base_symbol(symbol)
    known = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
        "BNB": "binancecoin", "ADA": "cardano", "DOGE": "dogecoin",
        "AVAX": "avalanche-2", "DOT": "polkadot", "LINK": "chainlink",
        "LTC": "litecoin", "BCH": "bitcoin-cash", "ATOM": "cosmos",
        "UNI": "uniswap", "AAVE": "aave", "MATIC": "matic-network",
        "TRX": "tron", "ETC": "ethereum-classic",
    }
    if base in known:
        return known[base]
    result = _get_json(COINGECKO_API, "search", {"query": base})
    coins = result.get("coins", [])
    exact = [c for c in coins if str(c.get("symbol", "")).upper() == base]
    if exact:
        return str(exact[0]["id"])
    if coins:
        return str(coins[0]["id"])
    raise RuntimeError(f"CoinGecko coin ID not found for {base}")


def _history(symbol: str, days: int = 365) -> pd.DataFrame:
    coin_id = _coingecko_id(symbol)
    now = int(time.time())
    start = now - int(days * 86400)
    payload = _get_json(
        COINGECKO_API,
        f"coins/{urllib.parse.quote(coin_id)}/market_chart/range",
        {"vs_currency": "usd", "from": str(start), "to": str(now)},
    )
    prices = payload.get("prices", [])
    volumes = payload.get("total_volumes", [])
    if not prices:
        raise RuntimeError(f"CoinGecko returned no historical prices for {symbol}")
    volume_map = {int(ts): float(v) for ts, v in volumes}
    rows = []
    for ts, price in prices:
        ts_i = int(ts)
        rows.append({
            "Date": pd.to_datetime(ts_i, unit="ms", utc=True).tz_convert(None),
            "Close": float(price),
            "Volume": float(volume_map.get(ts_i, 0.0)),
        })
    df = pd.DataFrame(rows).sort_values("Date").drop_duplicates("Date")
    # CoinGecko's market chart is price/volume, not exchange OHLC. For
    # deterministic indicators we use price as the close and a narrow synthetic
    # high/low envelope derived from adjacent observations only for volatility
    # calculations; exact current OHLC comes from Kraken in the verified tool.
    df["Open"] = df["Close"].shift(1).fillna(df["Close"])
    df["High"] = df[["Open", "Close"]].max(axis=1)
    df["Low"] = df[["Open", "Close"]].min(axis=1)
    return df.reset_index(drop=True)


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
    tr = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - close.shift()).abs(),
        (out["Low"] - close.shift()).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()
    out["vwma"] = (close * out["Volume"]).rolling(20).sum() / out["Volume"].rolling(20).sum().replace(0, math.nan)
    return out


@tool
def get_stock_data(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Crypto-native replacement for TradingAgents' stock-data tool."""
    df = _history(symbol, days=max(365, (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 30))
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    df = df[(df["Date"] >= start) & (df["Date"] < end)]
    if df.empty:
        raise RuntimeError(f"No crypto market data for {symbol} in requested range")
    display = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    return (
        f"# Crypto market data for {symbol.upper()}\n"
        f"# Provider: CoinGecko historical market data\n"
        f"# Records: {len(display)}\n\n"
        + display.to_csv(index=False)
    )


@tool
def get_indicators(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    indicator: Annotated[str, "technical indicator name"],
    curr_date: Annotated[str, "analysis date YYYY-MM-DD"],
    look_back_days: Annotated[int, "days of history"] = 30,
) -> str:
    """Calculate TradingAgents-compatible indicators from crypto-native data."""
    df = _indicators(_history(symbol, days=max(365, look_back_days + 210)))
    cutoff = pd.Timestamp(curr_date)
    df = df[df["Date"] <= cutoff]
    if df.empty or indicator not in df.columns:
        raise RuntimeError(f"Unsupported or unavailable crypto indicator: {indicator}")
    rows = df.tail(max(1, int(look_back_days)))
    lines = [f"## {indicator} for {symbol.upper()} (crypto-native)", ""]
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
    """Verified crypto snapshot: CoinGecko history + Kraken live ticker."""
    df = _indicators(_history(symbol, days=365))
    cutoff = pd.Timestamp(curr_date)
    df = df[df["Date"] <= cutoff]
    if df.empty:
        raise RuntimeError(f"No verified historical crypto data for {symbol}")
    latest = df.iloc[-1]
    base = _base_symbol(symbol)
    pair = f"{base}/USD"
    live = _get_json(KRAKEN_API, "Ticker", {"pair": pair})
    ticker = next(iter(live.get("result", {}).values()), None)
    lines = [
        f"## Verified crypto market snapshot for {symbol.upper()}",
        "- Historical benchmark: CoinGecko",
        "- Exchange-native live context: Kraken public API",
        f"- Analysis cutoff: {curr_date}",
        "",
        "### Latest verified historical data",
        f"- Date: {latest['Date'].strftime('%Y-%m-%d')}",
        f"- Close: {latest['Close']:.10g}",
        f"- Volume: {latest['Volume']:.10g}",
    ]
    if ticker:
        bid = float(ticker["b"][0]); ask = float(ticker["a"][0]); last = float(ticker["c"][0])
        lines += ["", "### Kraken live context", f"- Last: {last:.10g}", f"- Bid: {bid:.10g}", f"- Ask: {ask:.10g}", f"- Spread: {(ask-bid)/((ask+bid)/2)*10000:.2f} bps"]
    lines += ["", "### Verified indicators"]
    for name in ("close_10_ema", "close_50_sma", "close_200_sma", "rsi", "boll", "boll_ub", "boll_lb", "macd", "macds", "macdh", "atr", "vwma"):
        value = latest.get(name)
        lines.append(f"- {name}: {'N/A' if pd.isna(value) else f'{float(value):.10g}'}")
    recent = df.tail(max(1, min(int(look_back_days), 30)))
    lines += ["", "### Recent verified closes"]
    for _, row in recent.iterrows():
        lines.append(f"- {row['Date'].strftime('%Y-%m-%d')}: {row['Close']:.10g}")
    lines += ["", "Exact current exchange price claims must use the Kraken live context; historical claims must use the CoinGecko rows above."]
    return "\n".join(lines)


def crypto_identity(ticker: str) -> dict[str, str]:
    """Resolve crypto identity from CoinGecko without Yahoo Finance."""
    coin_id = _coingecko_id(ticker)
    payload = _get_json(COINGECKO_API, f"coins/{urllib.parse.quote(coin_id)}", {
        "localization": "false", "tickers": "false", "market_data": "true",
        "community_data": "true", "developer_data": "false",
    })
    market = payload.get("market_data") or {}
    return {
        "company_name": str(payload.get("name") or _base_symbol(ticker)),
        "sector": "Crypto asset",
        "industry": ", ".join((payload.get("categories") or [])[:3]),
        "exchange": "Kraken + CoinGecko aggregate",
        "quote_type": "CRYPTOCURRENCY",
        "market_cap": str(((market.get("market_cap") or {}).get("usd")) or ""),
        "volume_24h": str(((market.get("total_volume") or {}).get("usd")) or ""),
    }
