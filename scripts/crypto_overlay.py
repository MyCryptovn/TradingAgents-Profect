"""Overlay cho TradingAgents: chỉ dùng Hyperliquid, chặn Yahoo Finance.

Không sửa vendor/. Gọi apply_overlay() một lần trước khi dựng TradingAgentsGraph,
rồi gọi verify() để chắc không còn tool Yahoo nào được gắn.
"""
import sys

from langchain_core.tools import tool

from scripts import crypto_market_data as md

# Tool cổ phiếu -> tool Hyperliquid (cùng tên hàm, khác nguồn dữ liệu)
STOCK_TO_CRYPTO = {
    "get_stock_data": md.get_stock_data,
    "get_indicators": md.get_indicators,
    "get_verified_market_snapshot": md.get_verified_market_snapshot,
}

# Tool chỉ có nghĩa với cổ phiếu hoặc đi qua Yahoo: thay bằng thông báo "không có dữ liệu"
BLOCKED_TOOLS = (
    "get_YFin_data_online",
    "get_stock_stats_indicators_window",
    "get_stockstats_indicator",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_insider_transactions",
    "get_news",
    "get_global_news",
)

_BLOCKED_IDS: set[int] = set()


def _make_blocked(name: str):
    def _blocked(query: str = "") -> str:
        return (
            f"NO DATA: {name} uses Yahoo Finance (stock-only) and is disabled "
            "for crypto analysis. Use Hyperliquid tools instead."
        )
    _blocked.__name__ = name
    obj = tool(name)(_blocked)
    _BLOCKED_IDS.add(id(obj))
    return obj


def apply_overlay() -> dict:
    """Gắn lại mọi tên tool cổ phiếu trong các module tradingagents đã nạp.

    Trả về {tên_tool: [danh_sách_module_đã_gắn]} để log.
    """
    # Nạp graph và agents trước, để mọi module đã được import
    import tradingagents.graph.trading_graph  # noqa: F401

    replacements = dict(STOCK_TO_CRYPTO)
    for name in BLOCKED_TOOLS:
        replacements[name] = _make_blocked(name)

    patched: dict[str, list[str]] = {}
    for modname, mod in list(sys.modules.items()):
        if not modname.startswith("tradingagents") or mod is None:
            continue
        for old, new in replacements.items():
            if hasattr(mod, old) and getattr(mod, old) is not new:
                setattr(mod, old, new)
                patched.setdefault(old, []).append(modname)
    return patched


def verify() -> list[str]:
    """Trả về danh sách tool Yahoo còn bị gắn. Rỗng nghĩa là đạt."""
    left = []
    for modname, mod in list(sys.modules.items()):
        if not modname.startswith("tradingagents") or mod is None:
            continue
        for name in BLOCKED_TOOLS:
            obj = getattr(mod, name, None)
            if obj is not None and id(obj) not in _BLOCKED_IDS:
                left.append(f"{modname}.{name}")
    return left
