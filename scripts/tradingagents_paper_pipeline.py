"""Connect the broad real-market Top-10 scanner to TradingAgents for paper-only evaluation.

Crypto analysis uses a CoinGecko-native overlay for historical OHLC/market data
and live market context. Kraken remains only the separate paper-outcome evaluator.
The pinned TradingAgents source remains unchanged. BUY=LONG/SELL=SHORT are paper decisions only;
live orders stay disabled.
"""

from pathlib import Path
import re
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

# === CẤU HÌNH LỌC ALTCOIN NHỎ ===
SMALL_ALT_VOLUME_USD = 500_000      # volume 24h dưới mức này coi là alt nhỏ
SMALL_ALT_TAKER_BUY = 0.52            # yêu cầu taker buy cao hơn
NORMAL_TAKER_BUY = 0.50

TOP_N = 10
OUT_DIR = Path("tradingagents-paper-results")
KRAKEN_API = "https://api.kraken.com/0/public/"
INTERVAL_MINUTES = 60
INTERVAL_SECONDS = INTERVAL_MINUTES * 60
HYPER_API = "https://api.hyperliquid.xyz/info"


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


def hyper_post(body: dict):
    req = urllib.request.Request(
        HYPER_API,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "TradingAgents/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def hyper_candles(coin: str, interval: str = "1h", count: int = 200):
    ms = {"5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}[interval]
    end = int(time.time() * 1000)
    start = end - count * ms
    rows = hyper_post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end}})
    return [
        {"time": r["t"], "open": float(r["o"]), "high": float(r["h"]),
         "low": float(r["l"]), "close": float(r["c"]), "volume": float(r["v"])}
        for r in rows
    ]
    
SCAN_INFO = {}
REASONS = {}
SHORT_NOTE = (
    " TRADING HORIZON: short-term paper, max hold about 24 hours, hard stop about 3 percent. "
    "No fixed take-profit required; exit on reversal or horizon. "
    "BUY = LONG, SELL = SHORT, HOLD = no new position. "
    "Prefer action when 1m + 15m and flow align; do not require perfect agreement on every timeframe."
)
SHORT_RULES = (
    " POSITION MODEL: Perpetual / paper futures. BUY = LONG, SELL = SHORT, HOLD = flat. "
    "ENTRY BUY (LONG) if at least 3 of 4 are true: "
    "(1) RSI14 on 5m or 15m below 72, "
    "(2) trend15 from scanner above 0 OR trend1h above 0, "
    "(3) taker buy share at least 52 percent, "
    "(4) order book bid volume >= ask volume and Hyperliquid funding is not extremely positive. "
     "Strong bonus if 1m trend is also positive and vol_spike_1m is elevated. "
    "If fewer than 3 of the 4 conditions hold, rate HOLD. "
    "ENTRY SELL (SHORT) if at least 3 of 4 are true: "
    "(1) RSI14 on 5m or 15m above 28, "
    "(2) trend15 below 0 OR trend1h below 0, "
    "(3) taker buy share"
    "(4) order book ask volume >= bid volume and Hyperliquid funding is not extremely negative. "
    "If fewer than 3 of the 4 conditions hold, rate HOLD. "
    "Hard stop about 3 percent. Let winners run under reversal guard."
    " Avoid chasing when 1m is already strongly extended against the higher timeframe. ")

def run_scanner() -> list[str]:
    print(">>> STARTING SCANNER...")

    proc = subprocess.run(
        [sys.executable, "-u", "-m", "scripts.market_scanner"],
        capture_output=True,
        text=True,
        timeout=1800,
    )

    print("=== SCANNER STDOUT ===")
    print(proc.stdout[-8000:] if proc.stdout else "No stdout")

    print("=== SCANNER STDERR ===")
    print(proc.stderr[-8000:] if proc.stderr else "No stderr")

    print(f"=== RETURN CODE: {proc.returncode} ===")

    if proc.returncode != 0:
        raise RuntimeError(
            f"Market scanner failed with code {proc.returncode}"
        )

    result_file = Path(
        "tradingagents-paper-results/scanner.json"
    )

    if not result_file.exists():
        raise RuntimeError(
            "Scanner did not create scanner.json"
        )

    try:
        data = json.loads(
            result_file.read_text(encoding="utf-8")
        )
    except Exception as e:
        raise RuntimeError(
            f"Cannot read scanner.json: {e}"
        ) from e

    if not isinstance(data, dict):
        raise RuntimeError(
            "scanner.json must contain a JSON object"
        )

    top_long = data.get("top_long", [])
    top_short = data.get("top_short", [])

    if not isinstance(top_long, list):
        raise RuntimeError("top_long must be a list")

    if not isinstance(top_short, list):
        raise RuntimeError("top_short must be a list")

    print(
        "Scanner JSON loaded. "
        f"Long={len(top_long)}, Short={len(top_short)}"
    )

    output_lines = (proc.stdout or "").splitlines()

    symbols = []
    seen = set()

    for row in top_long + top_short:
        if not isinstance(row, dict):
            continue

        raw_symbol = row.get("symbol")

        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            print(f"SKIP invalid scanner row: {row}")
            continue

        raw_symbol = raw_symbol.strip()

        if raw_symbol.endswith("/USD"):
            sym = raw_symbol
        else:
            sym = f"{raw_symbol}/USD"

        if sym in seen:
            continue

        scores = row.get("scores", {})

        if not isinstance(scores, dict):
            raise RuntimeError(
                f"Invalid scores structure for {sym}"
            )

        direction = scores.get("direction", "UNKNOWN")

        try:
            long_score = float(scores["long_score"])
            short_score = float(scores["short_score"])
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(
                f"Missing or invalid Long/Short scores for {sym}: {e}"
            ) from e

        # Lấy nguyên dòng kết quả của scanner.
        # Dòng này chứa mtf_score, vol24h, spread,
        # depth10bps và các thông tin lọc khác.
        pattern = re.compile(
            rf"^\s*\d+\s+{re.escape(sym)}\s+",
            re.IGNORECASE,
        )

        scanner_line = next(
            (
                line.strip()
                for line in output_lines
                if pattern.search(line)
            ),
            None,
        )

        if scanner_line is None:
            raise RuntimeError(
                f"Cannot find complete scanner output line for {sym}. "
                "Check TOP CANDIDATES output format."
            )

        # Không cho phép pipeline âm thầm nhận mtf_score = 0.
        if "mtf_score=" not in scanner_line:
            raise RuntimeError(
                f"Scanner output for {sym} is missing mtf_score"
            )

        if "spread=" not in scanner_line:
            raise RuntimeError(
                f"Scanner output for {sym} is missing spread"
            )

        if "vol24h=" not in scanner_line:
            raise RuntimeError(
                f"Scanner output for {sym} is missing vol24h"
            )

        SCAN_INFO[sym] = scanner_line

        seen.add(sym)
        symbols.append(sym)

        print(
            f"SCANNER HANDOFF: {sym} "
            f"direction={direction} "
            f"long_score={long_score:.2f} "
            f"short_score={short_score:.2f}"
        )

        print(f"SCAN INFO: {SCAN_INFO[sym]}")

    if not symbols:
        raise RuntimeError(
            "Scanner produced no valid Top candidates"
        )

    print(f"FINAL SCANNER SYMBOLS: {symbols}")

    return symbols[:TOP_N]


    
def to_crypto_ticker(symbol: str) -> str:
    base = symbol.split("/", 1)[0].upper()
    base = {"XBT": "BTC", "XDG": "DOGE"}.get(base, base)
    return f"{base}-USD"

def flow_note(ticker):
    pair = ticker.replace("-", "")
    try:
        res = kraken_json("Trades", {"pair": pair})
        key = next(k for k in res if k != "last")
        trades = res[key][-500:]
        buy = sum(float(t[0]) * float(t[1]) for t in trades if t[3] == "b")
        sell = sum(float(t[0]) * float(t[1]) for t in trades if t[3] == "s")
        span = (float(trades[-1][2]) - float(trades[0][2])) / 60
        book = next(iter(kraken_json("Depth", {"pair": pair, "count": "20"}).values()))
        bids = sum(float(x[1]) for x in book["bids"])
        asks = sum(float(x[1]) for x in book["asks"])
        return f" MONEY FLOW (Kraken, last {span:.0f} minutes): taker buy ${buy:,.0f} vs taker sell ${sell:,.0f}, buy share {buy / max(buy + sell, 1):.0%}. Order book top 20 levels: bid volume {bids:,.0f} vs ask volume {asks:,.0f}."
    except Exception:
        return ""
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
    
    # Safe import candle_note
    try:
        from scripts.candle_note import candle_note
    except ModuleNotFoundError:
        def candle_note(ticker: str) -> str:
            return f"[STUB] Candle note missing for {ticker}"
    try:
        from scripts.hl_note import hl_note
    except ModuleNotFoundError:
        def hl_note(ticker: str) -> str:
            return ""
    try:
        from scripts.coinglass_note import coinglass_note
    except ModuleNotFoundError:
        def coinglass_note(ticker: str) -> str:
            return ""
    try: 
        from scripts.coinalyze_note import coinalyze_note
    except Exception:
        def coinalyze_note(ticker: str) -> str:
            return ""

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
            from scripts.hl_rich_note import hl_rich_note
            # Rule khác nhau cho alt nhỏ và coin thường
            if is_small_alt(symbol.replace("-", "/")):
                rules = (
                    " POSITION MODEL: This is perpetual / paper futures style. BUY means open or hold a LONG. SELL means open or hold a SHORT. HOLD means no new position. Do not treat SELL as only exiting a long; SELL is a valid directional short entry when bearish conditions are met "
                    " ENTRY RULES FOR SMALL ALT (STRICT): Rate BUY only if ALL are true: "
                    "1) RSI14 on 1m/5m/15m below < 72, "
                    "2) trend15 positive AND trend_1m also positive, "
                    "3) taker buy share >= 52%, "
                    "4) order book bid volume clearly higher than ask, "
                    "5) Hyperliquid funding not strongly positive. "
                    "If ANY condition fails → HOLD. "
                    "Prefer SELL when taker buy share <= 42% and both 1m + 15m are weak. "
                    "Max risk about 1 percent of capital per trade. Hard stop-loss about 3 percent. No fixed take-profit required; exit on reversal or horizon."
                )
            else:
                rules = SHORT_RULES
            return (build_instrument_context(symbol, asset_type, crypto_identity(symbol)) + SHORT_NOTE + flow_note(symbol) + candle_note(symbol) + hl_note(symbol) + hl_rich_note(symbol) + coinalyze_note(symbol)  + " SCANNER SNAPSHOT: " + SCAN_INFO.get(symbol.replace("-", "/"), "") + rules)
        return original_resolve_context(self, symbol, asset_type)

    TradingAgentsGraph.resolve_instrument_context = crypto_resolve_context

    config = dict(DEFAULT_CONFIG)
    config["llm_provider"] = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "google")
    config["deep_think_llm"] = os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", config["deep_think_llm"])
    config["quick_think_llm"] = os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", config["quick_think_llm"])
    config["max_debate_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "3"))
    config["max_risk_discuss_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "3"))
    config["llm_max_retries"] = int(os.getenv("TRADINGAGENTS_LLM_MAX_RETRIES", "6"))

    # Fundamentals are intentionally omitted for crypto: company balance sheets
    # are not the correct data model. Macro/news/social analysts remain available.
    graph = TradingAgentsGraph(
        selected_analysts=("market",),
        debug=False,
        config=config,
    )
    # The paper pipeline has its own point-in-time outcome evaluator. Do not let
    # the upstream stock-oriented memory resolver make a Yahoo Finance call for
    # crypto while resolving old lessons.
    graph._resolve_pending_entries = lambda _ticker: None
    state, signal = graph.propagate(ticker, trade_date, asset_type="crypto")
    REASONS[ticker] = str(state.get("final_trade_decision", ""))[:1500]
    print(f"REASON {ticker}: {REASONS[ticker][:600]}")
    return str(signal).strip().upper()


def completed_ohlc(pair: str) -> list[list[float]]:
    coin = pair.split("-")[0]
    rows = hyper_candles(coin, "1h", 200)
    out = [[r["time"] / 1000, r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in rows]
    return out[:-1] if len(out) > 1 else []


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
    wait_seconds = min(max(waits), 60.0)
    if wait_seconds > 0:
        print(f"WAITING FOR NEXT COMPLETED {INTERVAL_MINUTES}M CANDLE: {wait_seconds:.0f}s")
        time.sleep(wait_seconds)

def is_small_alt(symbol: str) -> bool:
    """Nhận diện altcoin nhỏ dựa trên volume 24h từ scanner."""
    scan_line = SCAN_INFO.get(symbol, "")
    # Ví dụ dòng scanner: ... vol24h=$1,234,567 ...
    import re
    m = re.search(r"vol24h=\$([0-9,]+)", scan_line)
    if not m:
        return True   # không lấy được volume → bỏ cho an toàn
    try:
        vol = float(m.group(1).replace(",", ""))
        return vol < SMALL_ALT_VOLUME_USD
    except Exception:
        return True

def run_with_retry(ticker, trade_date):
    for attempt in range(3):
        try:
            return run_tradingagents(ticker, trade_date)
        except Exception as exc:
            if attempt < 2 and ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)):
                print(f"Gemini 429, chờ 65 giây rồi thử lại ({attempt + 1}/2)")
                time.sleep(65)
                continue
            raise


def main() -> int:
    provider = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "google")
    if provider == "google" and not os.getenv("GOOGLE_API_KEY"):
        print("TRADINGAGENTS PAPER PIPELINE: GOOGLE_API_KEY is not configured")
        print("PAPER BUY = LONG/SELL = SHORT: NOT RUN — LLM provider credential is required")
        print("LIVE TRADING GATE: DISABLED")
        return 0

    OUT_DIR.mkdir(exist_ok=True)
    candidates = run_scanner()
    try:
        open_syms = [t["symbol"] for t in json.load(open(os.getenv("LEDGER_FILE", "paper-ledger/ledger.json")))["trades"] if t["status"] == "open" and t["action"] != "HOLD"]
    except Exception:
        open_syms = []
    candidates = candidates + [s for s in open_syms if s not in candidates]

    # ========== BỘ LỌC RỦI RO + CHẤT LƯỢNG ==========
    MAX_OPEN_POSITIONS = 3
    MIN_MTF_SCORE = 0.28
    MAX_SPREAD_BPS = 45.0

    filtered: list[str] = []
sầààaqwasq    for sym in candidates:
        # 1) Giới hạn số lệnh mở — vẫn cho phân tích lệnh đang mở
        if len(open_syms) >= MAX_OPEN_POSITIONS and sym not in open_syms:
            print(f"SKIP {sym}: max open positions ({MAX_OPEN_POSITIONS})")
            continue

        scan_line = SCAN_INFO.get(sym, "") or SCAN_INFO.get(sym.replace("-", "/"), "")

        # 2) mtf_score (lệnh đang mở thì không chặn)
        import re
        m = re.search(r"mtf_score=([0-9.]+)", scan_line)
        score = float(m.group(1)) if m else 0.0
        if score < MIN_MTF_SCORE and sym not in open_syms:
            print(f"SKIP {sym}: mtf_score {score:.4f} < {MIN_MTF_SCORE}")
            continue

        # 3) Spread quá rộng → book xấu, hay HOLD/lỗ
        sp = re.search(r"spread=([0-9.]+)", scan_line)
        if sp and sym not in open_syms and float(sp.group(1)) > 45.0:
            print(f"SKIP {sym}: spread {float(sp.group(1)):.1f}bps > 45")
            continue
        
        # 4) Alt nhỏ: KHÔNG bỏ — chỉ log (rule chặt nằm trong resolve)
        if is_small_alt(sym):
            print(f"SMALL ALT: {sym} → rule chặt hơn trong resolve")

        filtered.append(sym)

    candidates = filtered
    
    trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    decisions: list[Decision] = []

    print("TRADINGAGENTS TOP-10 PAPER PIPELINE")
    print("DATA: KRAKEN SCANNER + COINGECKO AI MARKET DATA + HYPERLIQUID NOTE + COINALYZE NOTE + KRAKEN PAPER OUTCOME")
    print("AI: TRADINGAGENTS")
    print(f"LLM PROVIDER: {provider}")
    print("CRYPTO DATA MODE: COINGECKO API + YAHOO FINANCE BYPASSED + HL public info for funding/book/candles + COINALYZE API")
    print("PAPER BUY/SELL: ENABLED")
    print("REAL ORDERS: DISABLED")
    print(f"CANDIDATES: {len(candidates)}")

    for rank, symbol in enumerate(candidates, 1):
        ticker = to_crypto_ticker(symbol)
        item = Decision(rank, symbol, ticker, "REVIEW", datetime.now(timezone.utc).isoformat())
        try:
            item.decision = run_with_retry(ticker, trade_date)
            print(f"{rank:02d} {symbol:12} -> {item.decision}")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            item.error = f"{type(exc).__name__}: {exc}"
            item.outcome_status = "ERROR"
            print(f"{rank:02d} {symbol:12} -> ERROR -> {item.error}")
        decisions.append(item)
        time.sleep(75)

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
    for p in payload:
        p["scan"] = SCAN_INFO.get(p["symbol"], "")
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
