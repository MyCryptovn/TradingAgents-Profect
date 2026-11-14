"""Market-data adapter for TradingAgents crypto paper analysis.

Data sources:
- Hyperliquid 1d candles: primary historical OHLC/volume for the AI analysts.
- Kraken API: secondary/spot fallback source for price and market context.
- CoinMarketCap API: live market context (market cap, rank, 24h volume).
- Paper outcome measurement stays separate and uses Hyperliquid 1h candles.

Design:
- Requests are cached in-process to avoid redundant API calls.
- Requires COINMARKETCAP_API_KEY for live market cap/rank context.
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

import pandas as pd
from typing import Annotated
from langchain_core.tools import tool

USER_AGENT = "TradingAgents-Profect/marketdata"
MAX_HISTORY_ROWS = 720
CMC_API_URL = "https://pro-api.coinmarketcap.com/v1"
KRAKEN_API_URL = "https://api.kraken.com/0/public"


def _cmc_api_key() -> str:
    """Lấy API Key của CoinMarketCap từ biến môi trường."""
    return (
        os.getenv("COINMARKETCAP_API_KEY")
        or os.getenv("CMC_API_KEY")
        or os.getenv("COINMARKETCAP_DEMO_API_KEY")
        or ""
    ).strip()


def _get_cmc_json(path: str, params: dict[str, str] | None = None) -> dict:
    """Gọi CoinMarketCap API lấy dữ liệu JSON."""
    base = os.getenv("COINMARKETCAP_API_BASE_URL", CMC_API_URL).rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    key = _cmc_api_key()
    if not key:
        raise RuntimeError("COINMARKETCAP_API_KEY chưa được thiết lập trong Environment Variables.")

    headers = {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": key,
        "User-Agent": USER_AGENT,
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.load(response)
        
        status = payload.get("status", {})
        if status.get("error_code", 0) != 0:
            raise RuntimeError(f"CoinMarketCap API error: {status.get('error_message', 'Unknown error')}")
        return payload
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"CoinMarketCap HTTP error {exc.code}: {exc.reason}")


def _base_symbol(symbol: str) -> str:
    """Chuẩn hóa ký hiệu cơ bản từ input."""
    base = symbol.upper().strip()
    for sep in ("/", "-"):
        base = base.split(sep, 1)[0]
    if base.endswith("USD") and len(base) > 3:
        base = base[:-3]
    return {"XBT": "BTC", "XDG": "DOGE"}.get(base, base)


HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def _hl_coin_name(symbol: str) -> str:
    # Tên coin Hyperliquid phân biệt hoa thường (kPEPE), giữ nguyên định dạng hợp lệ.
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
    req = urllib.request.Request(
        HL_INFO_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)

    out = []
    for row in rows:
        if float(row["T"]) > now_ms:  # bỏ nến ngày đang chạy
            continue
        o, h, l, c = (float(row[k]) for k in ("o", "h", "l", "c"))
        if c <= 0:
            continue
        out.append({
            "Date": pd.to_datetime(int(row["t"]), unit="ms"),
            "Open": o, "High": h, "Low": l, "Close": c,
            "Volume": float(row["v"]) * c,
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


def _kraken_ticker(symbol: str) -> dict:
    """Lấy dữ liệu ticker spot bổ trợ từ Kraken API."""
    base = _base_symbol(symbol)
    # Kraken thường dùng mã XBT cho BTC và các cặp dạng XXBTZUSD hoặc tương đương
    kraken_pair = "XXBTZUSD" if base == "BTC" else f"X{base}ZUSD" if base in ["ETH", "LTC", "XRP"] else f"{base}USD"
    
    url = f"{KRAKEN_API_URL}/Ticker?pair={kraken_pair}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.load(response)
            result = data.get("result", {})
            # Lấy key đầu tiên trả về từ kết quả Kraken
            for k in result:
                pair_data = result[k]
                c_info = pair_data.get("c", [0]) # Last trade closed [price, lot volume]
                v_info = pair_data.get("v", [0, 0]) # Volume [today, 24h]
                return {
                    "kraken_price": float(c_info[0]) if c_info else 0.0,
                    "kraken_volume_24h": float(v_info[1]) if len(v_info) > 1 else 0.0,
                }
    except Exception:
        pass
    return {}


@lru_cache(maxsize=256)
def _history(symbol: str) -> pd.DataFrame:
    """
    Lấy lịch sử nến ngày, ưu tiên tuyệt đối Hyperliquid.
    """
    clean_sym = _hl_coin_name(symbol)
    try:
        return _hl_daily_history(clean_sym)
    except Exception as exc:
        raise RuntimeError(f"Không thể lấy lịch sử dữ liệu giá cho mã {symbol} từ Hyperliquid: {exc}")


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
    """Lấy thông tin thị trường kết hợp từ CoinMarketCap và Kraken bổ trợ."""
    clean_sym = _hl_coin_name(symbol)
    payload = _get_cmc_json("cryptocurrency/quotes/latest", {"symbol": clean_sym})
    data = payload.get("data", {})
    if not data or clean_sym not in data:
        raise RuntimeError(f"CoinMarketCap không trả về dữ liệu cho mã {symbol}")
    
    coin_data = data[clean_sym]
    quote = coin_data.get("quote", {}).get("USD", {})
    
    # Lấy thêm dữ liệu bổ trợ từ Kraken spot
    kraken_data = _kraken_ticker(symbol)

    return {
        "name": coin_data.get("name"),
        "current_price": quote.get("price"),
        "market_cap": quote.get("market_cap"),
        "total_volume": quote.get("volume_24h"),
        "price_change_percentage_24h": quote.get("percent_change_24h"),
        "cmc_rank": coin_data.get("cmc_rank"),
        "kraken_price": kraken_data.get("kraken_price"),
        "kraken_volume_24h": kraken_data.get("kraken_volume_24h"),
    }


@tool
def get_stock_data(
    symbol: Annotated[str, "crypto ticker such as BTC-USD"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Hyperliquid-backed crypto market data tool."""
    df = _history(symbol)
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    df = df[(df["Date"] >= start) & (df["Date"] < end)]
    if df.empty:
        raise RuntimeError(f"Không có dữ liệu crypto cho {symbol} trong khoảng thời gian yêu cầu")

    return (
        f"# Crypto market data for {symbol.upper()}\n"
        f"# Provider: Hyperliquid candles\n"
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
    """Calculate TradingAgents-compatible indicators from market data."""
    df = _indicators(_history(symbol))
    
    if df.empty or "Date" not in df.columns:
        raise RuntimeError(f"Indicator data for {symbol} is empty or missing 'Date' column")

    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    
    target_date = pd.Timestamp(curr_date)
    if df["Date"].dt.tz is not None and target_date.tz is None:
        target_date = target_date.tz_localize(df["Date"].dt.tz)
    
    df = df[df["Date"] <= target_date]
    if df.empty:
        raise RuntimeError(f"No indicator data for {symbol} on or before {curr_date}")

    names = [name.strip() for name in indicator.split(",") if name.strip()]
    if not names:
        raise RuntimeError("No indicator names were provided")
    
    available = [col for col in df.columns if col != "Date"]
    missing = [name for name in names if name not in df.columns]
  
    if missing:
        missing_str = ", ".join(f"'{m}'" for m in sorted(missing))
        available_str = ", ".join(sorted(available))
        raise RuntimeError(
            f"Unsupported indicator(s): [{missing_str}]. "
            f"Available indicators: [{available_str}]"
        )

    rows = df.tail(max(1, int(look_back_days)))
    
    lines = []
    for name in names:
        lines.extend([f"## {name} for {symbol}", ""])
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
    """Verified crypto snapshot using Hyperliquid historical + CoinMarketCap / Kraken live data."""
    df = _indicators(_history(symbol))
    cutoff = pd.Timestamp(curr_date)
    df = df[df["Date"] <= cutoff]
    if df.empty:
        raise RuntimeError(f"Không tìm thấy dữ liệu lịch sử cho {symbol}")

    latest = df.iloc[-1]
    live = _live_market(symbol)

    lines = [
        f"## Verified crypto market snapshot for {symbol.upper()}",
        "- Historical source: Hyperliquid 1d candles",
        "- Live context: CoinMarketCap API + Kraken Spot",
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
        "### Live Market Context (CMC & Kraken)",
        f"- CMC Last Price: {float(live.get('current_price') or 0):.10g}",
        f"- Kraken Spot Price: {float(live.get('kraken_price') or 0):.10g}",
        f"- Market cap: {float(live.get('market_cap') or 0):.10g}",
        f"- 24h volume (CMC): {float(live.get('total_volume') or 0):.10g}",
        f"- 24h volume (Kraken): {float(live.get('kraken_volume_24h') or 0):.10g}",
        f"- 24h change: {float(live.get('price_change_percentage_24h') or 0):.6g}%",
        f"- CMC Rank: {live.get('cmc_rank') or 'N/A'}",
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
    """Resolve crypto identity from live market endpoints."""
    base = _base_symbol(ticker)
    live = _live_market(ticker)
    return {
        "company_name": str(live.get("name") or base),
        "sector": "Crypto asset",
        "industry": "Cryptocurrency",
        "exchange": "CoinMarketCap & Kraken",
        "quote_type": "CRYPTOCURRENCY",
        "market_cap": str(live.get("market_cap") or ""),
        "volume_24h": str(live.get("total_volume") or ""),
        "pair": base,
    }
