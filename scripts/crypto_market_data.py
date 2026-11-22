"""Market-data adapter for TradingAgents crypto analysis. Hyperliquid only.

- Daily history: Hyperliquid candleSnapshot 1d.
- Live context: Hyperliquid metaAndAssetCtxs (mark price, 24h notional volume, OI, funding).
- Coinalyze and Kraken are NOT called here. They are context notes built in the pipeline.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Annotated

import pandas as pd
from langchain_core.tools import tool

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
USER_AGENT = "TradingAgents-Profect/hl-data"
MAX_HISTORY_ROWS = 720


def _hl_retry(fn, tries: int = 3):
    for attempt in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def hyper_post(body: dict):
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _hl_coin_name(symbol: str) -> str:
    # Hyperliquid coin names are case-sensitive (kPEPE), so no upper().
    base = symbol.strip().split("/", 1)[0].split("-", 1)[0]
    return {"XBT": "BTC", "XDG": "DOGE"}.get(base.upper(), base)


def _hl_daily_history(symbol: str) -> pd.DataFrame:
    coin = _hl_coin_name(symbol)
    now_ms = int(time.time() * 1000)
    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": "1d",
            "startTime": now_ms - 365 * 86400000,
            "endTime": now_ms,
        },
    }
    rows = _hl_retry(lambda: hyper_post(body))

    out = []
    for row in rows:
        if float(row["T"]) > now_ms:  # drop the candle still forming
            continue
        o, h, l, c = (float(row[k]) for k in ("o", "h", "l", "c"))
        if c <= 0:
            continue
        out.append({
            "Date": pd.to_datetime(int(row["t"]), unit="ms"),
            "Open": o, "High": h, "Low": l, "Close": c,
            "Volume": float(row["v"]) * c,  # approximate USD volume
        })

    df = pd.DataFrame(out)
    if len(df) < 60:
        raise RuntimeError(f"Hyperliquid history too short for {coin}: {len(df)} rows")
    return (
        df.sort_values("Date")
        .drop_duplicates("Date")
        .tail(MAX_HISTORY_ROWS)
        .reset_index(drop=True)
    )


@lru_cache(maxsize=256)
def _history(symbol: str) -> pd.DataFrame:
    try:
        return _hl_daily_history(symbol)
    except Exception as exc:
        print(f"[MarketData] Hyperliquid history failed for {symbol}: {exc}")
        raise RuntimeError(f"No Hyperliquid price history for {symbol}") from exc


@lru_cache(maxsize=256)
def _live_market(symbol: str) -> dict:
    coin = _hl_coin_name(symbol)
    if ":" in coin:
        raise RuntimeError(f"HIP-3 market {coin}: no live context from this endpoint")
    data = _hl_retry(lambda: hyper_post({"type": "metaAndAssetCtxs"}))
    names = [u["name"] for u in data[0]["universe"]]
    if coin not in names:
        raise RuntimeError(f"{coin} not found in Hyperliquid universe")
    ctx = data[1][names.index(coin)]
    mark = float(ctx["markPx"])
    oi_units = float(ctx["openInterest"])
    return {
        "name": coin,
        "current_price": mark,
        "open_interest_usd": oi_units * mark,
        "total_volume": float(ctx["dayNtlVlm"]),
        "funding": float(ctx["funding"]),
        "premium": ctx.get("premium"),
    }


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


def crypto_identity(ticker: str) -> dict[str, str]:
    """Identity for the prompt: Hyperliquid-native, no external ID lookup."""
    coin = _hl_coin_name(ticker)
    try:
        live = _live_market(ticker)
    except Exception as exc:
        print(f"HL IDENTITY FALLBACK {ticker}: {type(exc).__name__}: {exc}")
        live = {}
    return {
        "company_name": coin,
        "sector": "Crypto asset",
        "industry": "Cryptocurrency",
        "exchange": "Hyperliquid",
        "quote_type": "CRYPTOCURRENCY",
        "market_cap": "",
        "volume_24h": str(live.get("total_volume", "")),
        "pair": coin,
    }


@tool
def get_stock_data(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Hyperliquid-backed daily crypto price data."""
    df = _history(symbol)
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    df = df[(df["Date"] >= start) & (df["Date"] < end)]
    if df.empty:
        raise RuntimeError(f"No Hyperliquid data for {symbol} in requested range")
    return (
        f"# Crypto market data for {symbol.upper()}\n"
        f"# Provider: Hyperliquid candleSnapshot 1d\n"
        f"# Records: {len(df)}\n\n"
        + df[["Date", "Open", "High", "Low", "Close", "Volume"]].to_csv(index=False)
    )


@tool
def get_indicators(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    indicator: Annotated[str, "one or comma-separated indicator names"],
    curr_date: Annotated[str, "analysis date YYYY-MM-DD"],
    look_back_days: Annotated[int, "days of history"] = 30,
) -> str:
    """Calculate indicators from Hyperliquid daily candles."""
    df = _indicators(_history(symbol)).sort_values("Date").reset_index(drop=True)
    target = pd.Timestamp(curr_date)
    df = df[df["Date"] <= target]
    if df.empty:
        raise RuntimeError(f"No indicator data for {symbol} on or before {curr_date}")

    names = [n.strip() for n in indicator.split(",") if n.strip()]
    if not names:
        raise RuntimeError("No indicator names were provided")
    missing = [n for n in names if n not in df.columns]
    if missing:
        available = ", ".join(sorted(c for c in df.columns if c != "Date"))
        raise RuntimeError(f"Unsupported indicator(s): {missing}. Available: [{available}]")

    rows = df.tail(max(1, int(look_back_days)))
    lines = []
    for name in names:
        lines.extend([f"## {name} for {symbol} (Hyperliquid-native)", ""])
        for _, row in rows.iterrows():
            value = row[name]
            text = "N/A" if pd.isna(value) else f"{float(value):.8g}"
            lines.append(f"{row['Date'].strftime('%Y-%m-%d')}: {text}")
        lines.append("")
    return "\n".join(lines).rstrip()


@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    curr_date: Annotated[str, "analysis date YYYY-MM-DD"],
    look_back_days: Annotated[int, "recent rows"] = 30,
) -> str:
    """Verified snapshot: Hyperliquid daily history + Hyperliquid live context."""
    df = _indicators(_history(symbol))
    df = df[df["Date"] <= pd.Timestamp(curr_date)]
    if df.empty:
        raise RuntimeError(f"No verified Hyperliquid history for {symbol}")

    latest = df.iloc[-1]
    try:
        live = _live_market(symbol)
        live_lines = [
            f"- Mark price: {live['current_price']:.10g}",
            f"- Open interest (USD): {live['open_interest_usd']:,.0f}",
            f"- 24h notional volume (USD): {live['total_volume']:,.0f}",
            f"- Funding (per hour): {live['funding']:+.6f}",
        ]
    except Exception as exc:
        live_lines = [f"- Live context unavailable: {type(exc).__name__}"]

    lines = [
        f"## Verified market snapshot for {symbol.upper()}",
        "- Historical source: Hyperliquid candleSnapshot 1d",
        "- Live source: Hyperliquid metaAndAssetCtxs",
        f"- Analysis cutoff: {curr_date}",
        "",
        "### Latest completed daily candle",
        f"- Date: {latest['Date'].strftime('%Y-%m-%d')}",
        f"- Open: {latest['Open']:.10g}",
        f"- High: {latest['High']:.10g}",
        f"- Low: {latest['Low']:.10g}",
        f"- Close: {latest['Close']:.10g}",
        f"- Volume (approx USD): {latest['Volume']:.10g}",
        "",
        "### Hyperliquid live context",
        *live_lines,
        "",
        "### Indicators",
    ]
    for name in ("close_10_ema", "close_50_sma", "close_200_sma", "rsi",
                 "boll", "boll_ub", "boll_lb", "macd", "macds", "macdh", "atr", "vwma"):
        value = latest.get(name)
        lines.append(f"- {name}: {'N/A' if pd.isna(value) else f'{float(value):.10g}'}")

    recent = df.tail(max(1, min(int(look_back_days), 30)))
    lines += ["", "### Recent completed closes"]
    for _, row in recent.iterrows():
        lines.append(f"- {row['Date'].strftime('%Y-%m-%d')}: {row['Close']:.10g}")
    return "\n".join(lines)
