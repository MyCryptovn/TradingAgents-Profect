"""Real-market paper validation using Kraken public spot OHLC data only.

No API key, wallet, or order endpoint is used. This validates public-market
connectivity and a look-ahead-safe paper execution baseline. It is NOT a
profitability claim for TradingAgents/LLMs and never places live orders.
"""

from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

PAIRS = {
    "XBTUSD": "BTC/USD",
    "ETHUSD": "ETH/USD",
    "SOLUSD": "SOL/USD",
    "XRPUSD": "XRP/USD",
}
INTERVALS = (15, 60)
FEE = 0.001
SLIPPAGE = 0.0005
START_CASH = 10_000.0
MIN_ROWS = 180
LOOKBACK = 120


@dataclass
class Result:
    pair: str
    interval: int
    strategy_equity: float
    benchmark_equity: float
    max_drawdown: float
    trades: int
    latest_age_minutes: float


def fetch_ohlc(pair: str, interval: int) -> list[list[float]]:
    query = urllib.parse.urlencode({"pair": pair, "interval": interval})
    url = f"https://api.kraken.com/0/public/OHLC?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "TradingAgents-Profect/market-validation"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"Kraken {pair} {interval}m error: {payload['error']}")
    result = payload["result"]
    pair_key = next(key for key in result if key != "last")
    rows = result[pair_key]
    parsed = [[float(v) for v in row[:7]] for row in rows]
    if len(parsed) < MIN_ROWS:
        raise RuntimeError(f"{pair} {interval}m: only {len(parsed)} candles")
    # Kraken can include the currently forming candle. Exclude it so the test
    # only makes decisions from completed candles.
    return parsed[:-1]


def ema(values: list[float], period: int) -> float:
    alpha = 2.0 / (period + 1)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


def evidence_score(closes: list[float]) -> float:
    if len(closes) < 60:
        return 0.0
    fast = ema(closes[-30:], 12)
    slow = ema(closes[-60:], 26)
    recent = closes[-40:]
    returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
    vol = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    trend_gap = (fast - slow) / slow if slow else 0.0
    return trend_gap / vol if vol > 0 else 0.0


def adaptive_signal(closes: list[float]) -> str:
    if len(closes) < LOOKBACK + 1:
        return "HOLD"
    current = evidence_score(closes)
    history = []
    start = max(60, len(closes) - LOOKBACK)
    for end in range(start, len(closes)):
        history.append(evidence_score(closes[:end]))
    if len(history) < 40:
        return "HOLD"
    ordered = sorted(history)
    low = ordered[max(0, int(len(ordered) * 0.20) - 1)]
    high = ordered[min(len(ordered) - 1, int(len(ordered) * 0.80))]
    momentum = closes[-1] / closes[-20] - 1.0
    if current >= high and momentum > 0:
        return "BUY"
    if current <= low and momentum < 0:
        return "SELL"
    return "HOLD"


def run(pair: str, interval: int, rows: list[list[float]]) -> Result:
    cash = START_CASH
    coin = 0.0
    peak = START_CASH
    max_drawdown = 0.0
    trades = 0
    closes: list[float] = []

    # Signal on completed candle t; execute at candle t+1 open.
    for index in range(len(rows) - 1):
        close = rows[index][4]
        next_open = rows[index + 1][1]
        closes.append(close)
        decision = adaptive_signal(closes)
        if decision == "BUY" and coin == 0.0:
            spend = cash * 0.25
            fill = next_open * (1.0 + SLIPPAGE)
            coin = spend * (1.0 - FEE) / fill
            cash -= spend
            trades += 1
        elif decision == "SELL" and coin > 0.0:
            fill = next_open * (1.0 - SLIPPAGE)
            cash += coin * fill * (1.0 - FEE)
            coin = 0.0
            trades += 1
        equity = cash + coin * next_open
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)

    final_close = rows[-1][4]
    final = cash + coin * final_close
    benchmark = START_CASH * final_close / rows[0][1]
    latest_age_minutes = max(0.0, (time.time() - rows[-1][0]) / 60.0)
    return Result(pair, interval, final, benchmark, max_drawdown, trades, latest_age_minutes)


def main() -> int:
    print("REAL-MARKET PAPER VALIDATION")
    print("DATA SOURCE: KRAKEN PUBLIC SPOT OHLC")
    print("LIVE ORDERS: DISABLED")
    print(
        f"pairs={','.join(PAIRS)} intervals={','.join(map(str, INTERVALS))}m "
        f"fee={FEE:.4%} slippage={SLIPPAGE:.2%} execution=next-open"
    )

    for interval in INTERVALS:
        for pair, label in PAIRS.items():
            rows = fetch_ohlc(pair, interval)
            result = run(pair, interval, rows)
            strategy_pnl = result.strategy_equity / START_CASH - 1.0
            benchmark_pnl = result.benchmark_equity / START_CASH - 1.0
            print(
                f"{label:7} {interval:3}m strategy={strategy_pnl:+.2%} "
                f"buy_hold={benchmark_pnl:+.2%} alpha={strategy_pnl - benchmark_pnl:+.2%} "
                f"maxDD={result.max_drawdown:.2%} trades={result.trades} "
                f"latest_age={result.latest_age_minutes:.1f}m"
            )

    print("\nGATE: Public real-market data and paper execution mechanics were exercised.")
    print("GATE: Signals use adaptive rolling evidence; there is no fixed take-profit target.")
    print("GATE: Decisions use completed candles and execute at the next candle open.")
    print("GATE: This does NOT prove TradingAgents/LLM profitability.")
    print("GATE: Live trading remains DISABLED until sustained paper evidence is reviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
