"""Static contract checks for the real-market scanner safety boundary."""
from pathlib import Path

SOURCE = Path(__file__).with_name("market_scanner.py").read_text(encoding="utf-8")

REQUIRED = (
    "https://api.kraken.com/0/public/",
    "TIMEFRAMES = (15, 60, 240)",
    "rows[:-1]",
    "LIVE ORDERS: DISABLED",
    "Ranking is not a trade instruction",
    "TradingAgents deep analysis",
)

FORBIDDEN = (
    "private_key",
    "seed_phrase",
    "withdraw",
    "place_order",
    "create_order",
)

for text in REQUIRED:
    assert text in SOURCE, f"Missing scanner contract: {text}"

for text in FORBIDDEN:
    assert text not in SOURCE.lower(), f"Forbidden execution credential/order path found: {text}"

print("SCANNER CONTRACT: PASS")
print("LIVE TRADING GATE: DISABLED")
