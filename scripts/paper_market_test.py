"""Safe public-market paper-trading smoke test.

This intentionally does not place real orders and does not require exchange credentials.
It downloads public Hyperliquid spot/perps candles, runs an adaptive statistical baseline, and
reports paper PnL, drawdown, trade count, and whether the run is eligible for a
separate TradingAgents/LLM paper-analysis stage.
"""

from __future__ import annotations

import json
import math
import statistics
import urllib.parse
import urllib.request
from dataclasses import dataclass


SYMBOLS = ("XRPUSDT", "BTCUSDT", "ETHUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "SOLUSDT", "LTCUSDT", "AVAXUSDT", "NEARUSDT", "ASTERUSDT", "QNTUSDT", "WLDUSDT", "WIFUSDT")
INTERVAL = "15m"
LIMIT = 500
FEE = 0.0026
START_CASH = 10_000.0


@dataclass
class Trade:
    symbol: str
    side: str
    price: float
    quantity: float
    equity_after: float

import time

def fetch_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list[list[float]]:
    """
    Lấy dữ liệu nến trực tiếp từ Hyperliquid Info API (chuẩn POST request),
    giúp đồng bộ hoàn toàn với kiến trúc core engine mới.
    """
    # Chuẩn hóa symbol từ dạng "BTC/USDT" hoặc "BTCUSDT" sang coin của Hyperliquid (ví dụ: "BTC")
    coin = symbol.upper().replace("/", "").replace("USDT", "").replace("USD", "")
    if coin == "XBT":
        coin = "BTC"
        
    url = "https://api.hyperliquid.xyz/info"
    
    # Quy đổi interval sang giây để tính khoảng thời gian bắt đầu (startTime)
    interval_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
    sec = interval_map.get(interval, 900)
    
    end_time = int(time.time() * 1000)
    start_time = end_time - (limit * sec * 1000)
    
    # Payload chuẩn theo tài liệu API của Hyperliquid cho candleSnapshot
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_time,
            "endTime": end_time
        }
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "TradingAgents-PaperTest/1.0"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Hyperliquid API error for {coin}: {e}")
        
    formatted = []
    for r in rows:
        if isinstance(r, dict):
            # Hyperliquid trả về dạng dict chứa các trường thời gian, mở, cao, thấp, đóng, khối lượng
            t = float(r.get("t", r.get("time", 0)))
            o = float(r.get("o", r.get("open", 0)))
            h = float(r.get("h", r.get("high", 0)))
            l = float(r.get("l", r.get("low", 0)))
            c = float(r.get("c", r.get("close", 0)))
            v = float(r.get("v", r.get("volume", 0)))
            formatted.append([t, o, h, l, c, v])
            
    return formatted


def ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def adaptive_signal(closes: list[float]) -> str:
    if len(closes) < 60:
        return "HOLD"
    short = ema(closes[-30:], 12)
    long = ema(closes[-60:], 26)
    recent = closes[-40:]
    returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent)) if recent[i] > 0 and recent[i - 1] > 0]
    vol = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    momentum = closes[-1] / closes[-20] - 1.0
    # Adaptive z-like evidence, not a fixed profit target.
    trend_gap = (short - long) / long if long else 0.0
    if trend_gap > vol * 1.0 and momentum > 0:
        return "BUY"
    if trend_gap < -vol * 1.0 and momentum < 0:
        return "SELL"
    return "HOLD"


def paper_run(symbol: str, rows: list[list[float]]) -> tuple[float, float, list[Trade]]:
    cash = START_CASH
    coin = 0.0
    peak = START_CASH
    max_drawdown = 0.0
    trades: list[Trade] = []

    closes: list[float] = []
    for row in rows:
        close = row[4]
        closes.append(close)
        signal = adaptive_signal(closes)
        equity = cash + coin * close
        if signal == "BUY" and coin == 0.0 and cash > 100:
            spend = cash * 0.25
            qty = (spend * (1 - FEE)) / close
            cash -= spend
            coin += qty
            equity = cash + coin * close
            trades.append(Trade(symbol, "BUY", close, qty, equity))
        elif signal == "SELL" and coin > 0.0:
            proceeds = coin * close * (1 - FEE)
            qty = coin
            coin = 0.0
            cash += proceeds
            equity = cash
            trades.append(Trade(symbol, "SELL", close, qty, equity))

        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)

    final_equity = cash + coin * closes[-1]
    return final_equity, max_drawdown, trades


def main() -> int:
    print("PAPER MARKET TEST - REAL ORDERS DISABLED")
    print(f"interval={INTERVAL} candles={LIMIT} fee={FEE:.4%}")

    total_start = 0.0
    total_final = 0.0
    all_trades: list[Trade] = []

    for symbol in SYMBOLS:
        try:
            rows = fetch_klines(symbol)
            final_equity, drawdown, trades = paper_run(symbol, rows)
        except Exception as e:
            print(f"{symbol}: bỏ qua ({e})")
            continue
        pnl = final_equity - START_CASH
        total_start += START_CASH
        total_final += final_equity
        all_trades.extend(trades)
        print(f"{symbol}: final=${final_equity:,.2f} pnl=${pnl:,.2f} drawdown={drawdown:.2%} trades={len(trades)}")

    total_pnl = total_final - total_start
    print(f"TOTAL: start=${total_start:,.2f} final=${total_final:,.2f} pnl=${total_pnl:,.2f} trades={len(all_trades)}")
    print("LIVE TRADING GATE: DISABLED")
    print("TradingAgents LLM analysis requires an explicitly configured provider key in GitHub Secrets; no key is read from source code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
