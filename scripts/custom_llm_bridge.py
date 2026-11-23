# ... existing code ...
from __future__ import annotations

import os
import sys
from typing import Any
from langchain_openai import ChatOpenAI


DEFAULT_PRIMARY_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-4-dragon")
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _build_single_gemini_client(
# ... existing code ...
    if hasattr(ta_graph, "config") and isinstance(ta_graph.config, dict):
        ta_graph.config["deep_think_llm"] = deep_name
        ta_graph.config["quick_think_llm"] = quick_name

    return ta_graph


def install_gemini_llm_hook(trading_graph_module: Any) -> None:
    """
    Chặn toàn bộ các điểm khởi tạo LLM trong TradingAgents (create_llm,
    ChatGoogleGenerativeAI, llm_clients) trước khi TradingAgentsGraph() chạy,
    đảm bảo 100% agent con đều dùng client Gemini có auto-fallback khi dính 503.
    """
    deep_name = (
        os.getenv("CUSTOM_DEEP_LLM")
        or os.getenv("TRADINGAGENTS_DEEP_THINK_LLM")
        or DEFAULT_PRIMARY_MODEL
    ).strip()
    quick_name = (
        os.getenv("CUSTOM_QUICK_LLM")
        or os.getenv("TRADINGAGENTS_QUICK_THINK_LLM")
        or DEFAULT_PRIMARY_MODEL
    ).strip()
    retries = int(os.getenv("TRADINGAGENTS_LLM_MAX_RETRIES", "3"))

    def _custom_create_llm(*args: Any, **kwargs: Any) -> Any:
        raw_model = str(kwargs.get("model") or (args[1] if len(args) > 1 else "")).lower()
        target = deep_name if "deep" in raw_model or "pro" in raw_model else quick_name
        return create_gemini_chat_model(model_name=target, max_retries=retries)

    # 1. Ghi đè create_llm trên trading_graph_module (nếu có)
    if hasattr(trading_graph_module, "create_llm"):
        trading_graph_module.create_llm = _custom_create_llm

    # 2. Ghi đè ChatGoogleGenerativeAI trực tiếp trên trading_graph_module (bản TradingAgents mới)
    if hasattr(trading_graph_module, "ChatGoogleGenerativeAI"):
        trading_graph_module.ChatGoogleGenerativeAI = lambda *a, **kw: _custom_create_llm(*a, **kw)

    # 3. Ghi đè trên các submodule llm_clients của tradingagents nếu đã được import
    for mod_name, mod in list(sys.modules.items()):
        if mod and mod_name.startswith("tradingagents."):
            if hasattr(mod, "create_llm"):
                setattr(mod, "create_llm", _custom_create_llm)
            if hasattr(mod, "ChatGoogleGenerativeAI"):
                setattr(mod, "ChatGoogleGenerativeAI", lambda *a, **kw: _custom_create_llm(*a, **kw))
