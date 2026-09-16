"""Safe public-market paper-trading smoke test.

This intentionally does not place real orders and does not require exchange credentials.
It downloads public Binance spot candles, runs an adaptive statistical baseline, and
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


SYMBOLS = ("BTCUSDT", "ETHUSDT")
INTERVAL = "15m"
LIMIT = 500
FEE = 0.001
START_CASH = 10_000.0


@dataclass
class Trade:
    symbol: str
    side: str
    price: float
    quantity: float
    equity_after: float


def fetch_klines(symbol: str) -> list[list[float]]:
    params = urllib.parse.urlencode({"symbol": symbol, "interval": INTERVAL, "limit": LIMIT})
    url = f"https://api.binance.com/api/v3/klines?{params}"
    with urllib.request.urlopen(url, timeout=20) as response:
        raw = json.load(response)
    return [[float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])] for row in raw]


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
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes[-40:]))]
    vol = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    momentum = closes[-1] / closes[-20] - 1.0
    # Adaptive z-like evidence, not a fixed profit target.
    trend_gap = (short - long) / long if long else 0.0
    if trend_gap > vol * 2.0 and momentum > 0:
        return "BUY"
    if trend_gap < -vol * 2.0 and momentum < 0:
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

    total_start = START_CASH * len(SYMBOLS)
    total_final = 0.0
    all_trades: list[Trade] = []

    for symbol in SYMBOLS:
        rows = fetch_klines(symbol)
        final_equity, drawdown, trades = paper_run(symbol, rows)
        pnl = final_equity - START_CASH
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
