"""Real-market paper validation using public Binance spot candles only.

No API key, wallet, or order endpoint is used. This is a market-data/execution
baseline test, not a claim that the LLM strategy is profitable.
"""

from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
INTERVALS = ("15m", "1h")
LIMIT = 1000
FEE = 0.001
SLIPPAGE = 0.0005
START_CASH = 10_000.0
MIN_ROWS = 200


@dataclass
class Result:
    symbol: str
    interval: str
    strategy_equity: float
    benchmark_equity: float
    max_drawdown: float
    trades: int
    latest_age_minutes: float


def fetch_klines(symbol: str, interval: str) -> list[list[float]]:
    query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": LIMIT})
    url = f"https://api.binance.com/api/v3/klines?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "TradingAgents-Profect/market-validation"})
    with urllib.request.urlopen(request, timeout=30) as response:
        rows = json.load(response)
    parsed = [[float(v) for v in row[:6]] for row in rows]
    if len(parsed) < MIN_ROWS:
        raise RuntimeError(f"{symbol} {interval}: only {len(parsed)} candles")
    return parsed


def ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1 - alpha) * value
    return value


def signal(closes: list[float]) -> str:
    if len(closes) < 60:
        return "HOLD"
    fast = ema(closes[-30:], 12)
    slow = ema(closes[-60:], 26)
    recent = closes[-40:]
    returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
    vol = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    momentum = closes[-1] / closes[-20] - 1.0
    trend_gap = (fast - slow) / slow if slow else 0.0
    evidence = trend_gap / vol if vol > 0 else 0.0
    if evidence > 2.0 and momentum > 0:
        return "BUY"
    if evidence < -2.0 and momentum < 0:
        return "SELL"
    return "HOLD"


def run(symbol: str, interval: str, rows: list[list[float]]) -> Result:
    cash = START_CASH
    coin = 0.0
    peak = START_CASH
    drawdown = 0.0
    trades = 0
    closes: list[float] = []

    for row in rows:
        close = row[4]
        closes.append(close)
        decision = signal(closes)
        equity = cash + coin * close
        if decision == "BUY" and coin == 0.0:
            spend = cash * 0.25
            fill = close * (1 + SLIPPAGE)
            coin = spend * (1 - FEE) / fill
            cash -= spend
            trades += 1
        elif decision == "SELL" and coin > 0.0:
            fill = close * (1 - SLIPPAGE)
            cash += coin * fill * (1 - FEE)
            coin = 0.0
            trades += 1
        equity = cash + coin * close
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak else 0.0)

    final = cash + coin * closes[-1]
    benchmark = START_CASH * closes[-1] / closes[0]
    age_minutes = max(0.0, (time.time() * 1000 - rows[-1][6] if len(rows[-1]) > 6 else time.time() * 1000 - rows[-1][0]) / 60000)
    return Result(symbol, interval, final, benchmark, drawdown, trades, age_minutes)


def main() -> int:
    print("REAL-MARKET PAPER VALIDATION")
    print("LIVE ORDERS: DISABLED")
    print(f"symbols={','.join(SYMBOLS)} intervals={','.join(INTERVALS)} candles={LIMIT} fee={FEE:.4%} slippage={SLIPPAGE:.2%}")

    results: list[Result] = []
    for interval in INTERVALS:
        for symbol in SYMBOLS:
            rows = fetch_klines(symbol, interval)
            result = run(symbol, interval, rows)
            results.append(result)
            strategy_pnl = result.strategy_equity / START_CASH - 1
            benchmark_pnl = result.benchmark_equity / START_CASH - 1
            print(
                f"{symbol:8} {interval:3} strategy={strategy_pnl:+.2%} "
                f"buy_hold={benchmark_pnl:+.2%} alpha={strategy_pnl - benchmark_pnl:+.2%} "
                f"maxDD={result.max_drawdown:.2%} trades={result.trades}"
            )

    print("\nGATE: This validates live public market data, fees/slippage assumptions and paper execution mechanics.")
    print("GATE: It does NOT prove TradingAgents/LLM profitability and does NOT enable live trading.")
    print("GATE: Live trading remains DISABLED until sustained paper evidence is reviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
