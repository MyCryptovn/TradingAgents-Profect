name: TradingAgents Groq Comparison (Manual Test)

on:
  workflow_dispatch:
  schedule:
    - cron: "15 /6 * * *"
permissions:
  contents: write

concurrency:
  group: tradingagents-groq-paper
  cancel-in-progress: false

jobs:
  paper-analysis-groq:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install pinned TradingAgents source
        run: pip install -e vendor/TradingAgents

      - name: Validate crypto-native adapter
        run: python -m py_compile scripts/crypto_market_data.py scripts/tradingagents_paper_pipeline.py scripts/market_scanner.py scripts/paper_ledger_groq.py

      - name: Run Top-N TradingAgents paper pipeline (Groq)
        env:
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          COINGECKO_API_KEY: ${{ secrets.COINGECKO_API_KEY }}
          COINGECKO_API_BASE_URL: https://api.coingecko.com/api/v3/
          TRADINGAGENTS_LLM_PROVIDER: groq
          TRADINGAGENTS_DEEP_THINK_LLM: openai/gpt-oss-120b
          TRADINGAGENTS_QUICK_THINK_LLM: openai/gpt-oss-120b
          TRADINGAGENTS_MAX_DEBATE_ROUNDS: "1"
          TRADINGAGENTS_MAX_RISK_ROUNDS: "1"
          TRADINGAGENTS_LLM_MAX_RETRIES: "6"
          TRADINGAGENTS_MEMORY_LOG_PATH: ${{ github.workspace }}/paper-results-groq/trading_memory.md
        run: python -m scripts.tradingagents_paper_pipeline

      - name: Verify crypto-native routing and live trading gate
        run: |
          grep -R -q "REAL ORDERS: DISABLED" scripts/tradingagents_paper_pipeline.py
          grep -R -q "LIVE TRADING GATE: DISABLED" scripts/tradingagents_paper_pipeline.py
          python - <<'PY'
          import json
          from pathlib import Path
          p = Path("tradingagents-paper-results/decisions.json")
          rows = json.loads(p.read_text())
          successful = [r for r in rows if r.get("outcome_status") != "ERROR"]
          if not successful:
              raise SystemExit("No successful analyses.")
          print(f"Successful: {len(successful)}/{len(rows)}")
          PY

      - name: Record paper ledger (Groq)
        run: python scripts/paper_ledger_groq.py record

      - name: Score paper ledger (Groq)
        run: python scripts/paper_ledger_groq.py score

      - name: Commit ledger
        run: |
          git config user.name "paper-bot-groq"
          git config user.email "paper-bot-groq@users.noreply.github.com"
          git add paper-ledger
          git diff --staged --quiet || (git commit -m "update groq paper ledger [skip ci]" && git pull --rebase && git push)

      - name: Upload Groq paper report
        uses: actions/upload-artifact@v4
        with:
          name: tradingagents-groq-paper-results
          path: tradingagents-paper-results/
          retention-days: 14
