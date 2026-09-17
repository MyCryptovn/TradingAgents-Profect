"""Connect the broad real-market Top-10 scanner to TradingAgents for paper-only evaluation.

The scanner supplies candidates; TradingAgents supplies BUY/SELL/HOLD/REVIEW.
BUY/SELL are recorded as paper decisions only. No exchange credentials or live
order APIs are used. Outcomes are measured from subsequent Kraken public candles.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

TOP_N = 10
OUT_DIR = Path("tradingagents-paper-results")
KRAKEN_API = "https://api.kraken.com/0/public/"
HOLDING_INTERVAL_MINUTES = 60
OUTCOME_CANDLES = 4


@dataclass
class Decision:
    rank: int
    symbol: str
    tradingagents_ticker: str
    decision: str
    analyzed_at: str
    outcome_price: float | None = None
    entry_price: float | None = None
    return_pct: float | None = None
    outcome_status: str = "PENDING"
    error: str | None = None


def kraken_json(path: str, params: dict[str, str] | None = None) -> dict:
    url = KRAKEN_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "TradingAgents-Profect/paper-pipeline"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"Kraken API error: {payload['error']}")
    return payload["result"]


def run_scanner() -> list[str]:
    proc = subprocess.run(
        [sys.executable, "scripts/market_scanner.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = proc.stdout.splitlines()
    symbols: list[str] = []
    in_top = False
    for line in lines:
        if line.startswith("TOP ") and "CANDIDATES:" in line:
            in_top = True
            continue
        if in_top and line.startswith("NEXT GATE:"):
            break
        if in_top:
            match = re.match(r"^\s*\d+\s+(\S+)", line)
            if match:
                symbols.append(match.group(1))
    if not symbols:
        raise RuntimeError("Scanner produced no Top candidates")
    return symbols[:TOP_N]


def to_yahoo_crypto(symbol: str) -> str:
    base = symbol.split("/", 1)[0].upper()
    return f"{base}-USD"


def run_tradingagents(ticker: str, trade_date: str) -> str:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = dict(DEFAULT_CONFIG)
    config["llm_provider"] = os.getenv("TRADINGAGENTS_LLM_PROVIDER", config["llm_provider"])
    config["deep_think_llm"] = os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", config["deep_think_llm"])
    config["quick_think_llm"] = os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", config["quick_think_llm"])
    config["max_debate_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"))
    config["max_risk_discuss_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "1"))
    config["llm_max_retries"] = int(os.getenv("TRADINGAGENTS_LLM_MAX_RETRIES", "2"))
    graph = TradingAgentsGraph(
        selected_analysts=("market", "social", "news", "fundamentals"),
        debug=False,
        config=config,
    )
    _, signal = graph.propagate(ticker, trade_date, asset_type="crypto")
    return str(signal).strip().upper()


def latest_completed_ohlc(pair: str) -> list[list[float]]:
    result = kraken_json("OHLC", {"pair": pair, "interval": str(HOLDING_INTERVAL_MINUTES)})
    key = next((k for k in result if k != "last"), None)
    if not key:
        return []
    rows = result[key]
    return rows[:-1] if len(rows) > 1 else []


def evaluate_decision(pair: str, decision: str) -> tuple[float | None, float | None, str]:
    candles = latest_completed_ohlc(pair)
    if len(candles) < OUTCOME_CANDLES + 1:
        return None, None, "INSUFFICIENT_DATA"
    entry = float(candles[-OUTCOME_CANDLES - 1][4])
    outcome = float(candles[-1][4])
    if decision in {"BUY", "OVERWEIGHT"}:
        return entry, outcome, "MEASURED"
    if decision in {"SELL", "UNDERWEIGHT"}:
        return entry, outcome, "MEASURED"
    return None, None, "NON_EXECUTABLE_DECISION"


def main() -> int:
    if not os.getenv("GROQ_API_KEY") and os.getenv("TRADINGAGENTS_LLM_PROVIDER", "groq") == "groq":
        print("TRADINGAGENTS PAPER PIPELINE: GROQ_API_KEY is not configured")
        print("PAPER ORDERS: DISABLED UNTIL LLM PROVIDER IS CONFIGURED")
        return 0

    OUT_DIR.mkdir(exist_ok=True)
    candidates = run_scanner()
    trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    decisions: list[Decision] = []

    print("TRADINGAGENTS TOP-10 PAPER PIPELINE")
    print("DATA: KRAKEN PUBLIC MARKET SCANNER")
    print("AI: TRADINGAGENTS")
    print("PAPER BUY/SELL: ENABLED")
    print("REAL ORDERS: DISABLED")
    print(f"CANDIDATES: {len(candidates)}")

    for rank, symbol in enumerate(candidates, 1):
        ticker = to_yahoo_crypto(symbol)
        item = Decision(rank, symbol, ticker, "REVIEW", datetime.now(timezone.utc).isoformat())
        try:
            decision = run_tradingagents(ticker, trade_date)
            item.decision = decision
            pair = symbol.replace("/", "")
            if decision in {"BUY", "OVERWEIGHT", "SELL", "UNDERWEIGHT"}:
                entry, outcome, status = evaluate_decision(pair, decision)
                item.entry_price = entry
                item.outcome_price = outcome
                item.outcome_status = status
                if entry and outcome:
                    raw_return = outcome / entry - 1.0
                    item.return_pct = raw_return if decision in {"BUY", "OVERWEIGHT"} else -raw_return
            print(f"{rank:02d} {symbol:12} -> {decision:12} -> {item.outcome_status}")
        except Exception as exc:  # keep the remaining candidates testable
            item.error = f"{type(exc).__name__}: {exc}"
            item.outcome_status = "ERROR"
            print(f"{rank:02d} {symbol:12} -> ERROR -> {item.error}")
        decisions.append(item)

    payload = [asdict(item) for item in decisions]
    (OUT_DIR / "decisions.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    executed = [d for d in decisions if d.decision in {"BUY", "OVERWEIGHT", "SELL", "UNDERWEIGHT"}]
    measured = [d for d in executed if d.return_pct is not None]
    wins = [d for d in measured if d.return_pct > 0]
    print(f"PAPER DECISIONS: {len(executed)} BUY/SELL-like, {len(measured)} measurable")
    print(f"PAPER WIN RATE: {len(wins) / len(measured):.2%}" if measured else "PAPER WIN RATE: PENDING")
    print(f"RESULT FILE: {OUT_DIR / 'decisions.json'}")
    print("REVIEW DECISIONS: NEVER EXECUTED")
    print("LIVE TRADING GATE: DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
