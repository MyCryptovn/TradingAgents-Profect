"""Connect the broad real-market Top-10 scanner to TradingAgents for paper-only evaluation.

Crypto analysis uses a crypto-native overlay: Kraken exchange-native historical
OHLC plus live ticker/order-book context. The pinned TradingAgents source remains
unchanged. BUY/SELL are paper decisions only; live orders stay disabled.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

TOP_N = 10
OUT_DIR = Path("tradingagents-paper-results")
KRAKEN_API = "https://api.kraken.com/0/public/"
INTERVAL_MINUTES = 15
INTERVAL_SECONDS = INTERVAL_MINUTES * 60


@dataclass
class Decision:
    rank: int
    symbol: str
    tradingagents_ticker: str
    decision: str
    analyzed_at: str
    entry_time: str | None = None
    entry_price: float | None = None
    outcome_time: str | None = None
    outcome_price: float | None = None
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


def to_crypto_ticker(symbol: str) -> str:
    base = symbol.split("/", 1)[0].upper()
    if base == "XBT":
        base = "BTC"
    return f"{base}-USD"


def run_tradingagents(ticker: str, trade_date: str) -> str:
    """Run TradingAgents with crypto-native market tools over the pinned source."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.agents.analysts import market_analyst as market_analyst_module
    from tradingagents.graph import trading_graph as trading_graph_module
    from scripts.crypto_market_data import (
        crypto_identity,
        get_indicators as crypto_get_indicators,
        get_stock_data as crypto_get_stock_data,
        get_verified_market_snapshot as crypto_get_verified_snapshot,
    )
    from tradingagents.agents.utils.agent_utils import build_instrument_context

    # Overlay the stock-oriented tool names with crypto-native tools before the
    # graph is constructed. This keeps the pinned upstream source intact while
    # preventing crypto runs from touching Yahoo Finance market-data paths.
    market_analyst_module.get_stock_data = crypto_get_stock_data
    market_analyst_module.get_indicators = crypto_get_indicators
    market_analyst_module.get_verified_market_snapshot = crypto_get_verified_snapshot
    trading_graph_module.get_stock_data = crypto_get_stock_data
    trading_graph_module.get_indicators = crypto_get_indicators
    trading_graph_module.get_verified_market_snapshot = crypto_get_verified_snapshot

    original_resolve_context = TradingAgentsGraph.resolve_instrument_context

    def crypto_resolve_context(self, symbol: str, asset_type: str = "stock") -> str:
        if asset_type == "crypto":
            return build_instrument_context(symbol, asset_type, crypto_identity(symbol))
        return original_resolve_context(self, symbol, asset_type)

    TradingAgentsGraph.resolve_instrument_context = crypto_resolve_context

    config = dict(DEFAULT_CONFIG)
    config["llm_provider"] = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "groq")
    config["deep_think_llm"] = os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", config["deep_think_llm"])
    config["quick_think_llm"] = os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", config["quick_think_llm"])
    config["max_debate_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"))
    config["max_risk_discuss_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "1"))
    config["llm_max_retries"] = int(os.getenv("TRADINGAGENTS_LLM_MAX_RETRIES", "2"))

    # Fundamentals are intentionally omitted for crypto: company balance sheets
    # are not the correct data model. Macro/news/social analysts remain available.
    graph = TradingAgentsGraph(
        selected_analysts=("market", "social", "news"),
        debug=False,
        config=config,
    )
    # The paper pipeline has its own point-in-time outcome evaluator. Do not let
    # the upstream stock-oriented memory resolver make a Yahoo Finance call for
    # crypto while resolving old lessons.
    graph._resolve_pending_entries = lambda _ticker: None
    _, signal = graph.propagate(ticker, trade_date, asset_type="crypto")
    return str(signal).strip().upper()


def completed_ohlc(pair: str) -> list[list[float]]:
    result = kraken_json("OHLC", {"pair": pair, "interval": str(INTERVAL_MINUTES)})
    key = next((k for k in result if k != "last"), None)
    if not key:
        return []
    rows = result[key]
    return rows[:-1] if len(rows) > 1 else []


def first_future_candle(pair: str, analyzed_at: str) -> list[float] | None:
    analyzed_ts = datetime.fromisoformat(analyzed_at).timestamp()
    for row in completed_ohlc(pair):
        if float(row[0]) > analyzed_ts:
            return row
    return None


def evaluate_decision(
    pair: str, decision: str, analyzed_at: str
) -> tuple[str | None, float | None, str | None, float | None, str, float | None]:
    if decision not in {"BUY", "OVERWEIGHT", "SELL", "UNDERWEIGHT"}:
        return None, None, None, None, "NON_EXECUTABLE_DECISION", None
    candle = first_future_candle(pair, analyzed_at)
    if candle is None:
        return None, None, None, None, "PENDING_NEXT_CANDLE", None
    entry_time = datetime.fromtimestamp(float(candle[0]), tz=timezone.utc).isoformat()
    entry_price = float(candle[1])
    outcome_time = datetime.fromtimestamp(float(candle[0]) + INTERVAL_SECONDS, tz=timezone.utc).isoformat()
    outcome_price = float(candle[4])
    raw_return = outcome_price / entry_price - 1.0
    signed_return = raw_return if decision in {"BUY", "OVERWEIGHT"} else -raw_return
    return entry_time, entry_price, outcome_time, outcome_price, "MEASURED", signed_return


def wait_for_pending_candles(decisions: list[Decision]) -> None:
    """Wait briefly so the first post-decision candle can close when practical."""
    executable = [d for d in decisions if d.decision in {"BUY", "OVERWEIGHT", "SELL", "UNDERWEIGHT"}]
    if not executable:
        return
    now = time.time()
    waits = []
    for d in executable:
        ts = datetime.fromisoformat(d.analyzed_at).timestamp()
        next_open = math.floor(ts / INTERVAL_SECONDS) * INTERVAL_SECONDS + INTERVAL_SECONDS
        waits.append(max(0.0, next_open + INTERVAL_SECONDS - now))
    wait_seconds = min(max(waits), 1800.0)
    if wait_seconds > 0:
        print(f"WAITING FOR NEXT COMPLETED {INTERVAL_MINUTES}M CANDLE: {wait_seconds:.0f}s")
        time.sleep(wait_seconds)


def main() -> int:
    provider = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "groq")
    if provider == "groq" and not os.getenv("GROQ_API_KEY"):
        print("TRADINGAGENTS PAPER PIPELINE: GROQ_API_KEY is not configured")
        print("PAPER BUY/SELL: NOT RUN — LLM provider credential is required")
        print("LIVE TRADING GATE: DISABLED")
        return 0

    OUT_DIR.mkdir(exist_ok=True)
    candidates = run_scanner()
    trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    decisions: list[Decision] = []

    print("TRADINGAGENTS TOP-10 PAPER PIPELINE")
    print("DATA: KRAKEN SCANNER + KRAKEN HISTORICAL OHLC + KRAKEN LIVE CONTEXT")
    print("AI: TRADINGAGENTS")
    print("CRYPTO DATA MODE: YAHOO FINANCE BYPASSED")
    print("PAPER BUY/SELL: ENABLED")
    print("REAL ORDERS: DISABLED")
    print(f"CANDIDATES: {len(candidates)}")

    for rank, symbol in enumerate(candidates, 1):
        ticker = to_crypto_ticker(symbol)
        item = Decision(rank, symbol, ticker, "REVIEW", datetime.now(timezone.utc).isoformat())
        try:
            item.decision = run_tradingagents(ticker, trade_date)
            print(f"{rank:02d} {symbol:12} -> {item.decision}")
        except Exception as exc:
            item.error = f"{type(exc).__name__}: {exc}"
            item.outcome_status = "ERROR"
            print(f"{rank:02d} {symbol:12} -> ERROR -> {item.error}")
        decisions.append(item)

    wait_for_pending_candles(decisions)
    for item in decisions:
        if item.outcome_status == "ERROR":
            continue
        try:
            (
                item.entry_time,
                item.entry_price,
                item.outcome_time,
                item.outcome_price,
                item.outcome_status,
                signed_return,
            ) = evaluate_decision(item.symbol.replace("/", ""), item.decision, item.analyzed_at)
            if signed_return is not None:
                item.return_pct = signed_return
            print(f"{item.rank:02d} {item.symbol:12} -> {item.decision:12} -> {item.outcome_status}")
        except Exception as exc:
            item.error = f"{type(exc).__name__}: {exc}"
            item.outcome_status = "ERROR"

    payload = [asdict(item) for item in decisions]
    (OUT_DIR / "decisions.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    measured = [d for d in decisions if d.return_pct is not None]
    wins = [d for d in measured if d.return_pct > 0]
    executable = [d for d in decisions if d.decision in {"BUY", "OVERWEIGHT", "SELL", "UNDERWEIGHT"}]
    print(f"PAPER DECISIONS: {len(executable)} executable BUY/SELL-like decisions")
    print(f"PAPER MEASURED: {len(measured)}")
    print(f"PAPER WIN RATE: {len(wins) / len(measured):.2%}" if measured else "PAPER WIN RATE: PENDING")
    print(f"RESULT FILE: {OUT_DIR / 'decisions.json'}")
    print("REVIEW DECISIONS: NEVER EXECUTED")
    print("LIVE TRADING GATE: DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
