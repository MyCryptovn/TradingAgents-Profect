from __future__ import annotations

import os
from typing import Any
from langchain_openai import ChatOpenAI


DEFAULT_PRIMARY_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash"
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def create_gemini_chat_model(
    model_name: str | None = None,
    timeout: float = 45.0,
    max_retries: int = 2,
) -> ChatOpenAI:
    """
    Khởi tạo client Gemini độc lập qua cổng OpenAI-compatible của Google,
    bỏ qua whitelist cũ của vendor/TradingAgents.
    """
    api_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Thiếu GOOGLE_API_KEY hoặc GEMINI_API_KEY trong biến môi trường.")

    chosen = (
        model_name
        or os.getenv("CUSTOM_GEMINI_MODEL")
        or os.getenv("TRADINGAGENTS_QUICK_THINK_LLM")
        or DEFAULT_PRIMARY_MODEL
    ).strip()

    # Không truyền temperature/top_p vì gemini-3.5-flash-lite không hỗ trợ chỉnh tham số này
    return ChatOpenAI(
        model=chosen,
        api_key=api_key,
        base_url=GEMINI_OPENAI_BASE_URL,
        timeout=timeout,
        max_retries=max_retries,
    )


def patch_tradingagents_graph_llms(ta_graph: Any) -> Any:
    """
    Ghi đè trực tiếp deep_thinking_llm & quick_thinking_llm trên instance TradingAgentsGraph
    sau khi khởi tạo, giúp chạy đúng gemini-3.5-flash-lite mà không đụng vào vendor/.
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

    retries = int(os.getenv("TRADINGAGENTS_LLM_MAX_RETRIES", "2"))

    deep_llm = create_gemini_chat_model(model_name=deep_name, max_retries=retries)
    quick_llm = create_gemini_chat_model(model_name=quick_name, max_retries=retries)

    # Gắn vào cả object chính lẫn các thuộc tính nội bộ thường dùng của TradingAgentsGraph
    for attr in ("deep_thinking_llm", "deep_think_llm", "llm"):
        if hasattr(ta_graph, attr):
            setattr(ta_graph, attr, deep_llm)

    for attr in ("quick_thinking_llm", "quick_think_llm"):
        if hasattr(ta_graph, attr):
            setattr(ta_graph, attr, quick_llm)

    if hasattr(ta_graph, "config") and isinstance(ta_graph.config, dict):
        ta_graph.config["deep_think_llm"] = deep_name
        ta_graph.config["quick_think_llm"] = quick_name

    return ta_graph
    
def install_gemini_llm_hook(trading_graph_module: Any) -> None:
    """
    Chặn ngay hàm khởi tạo LLM bên trong trading_graph_module trước khi TradingAgentsGraph() chạy,
    đảm bảo 100% agent con đều nhận đúng gemini-3.5-flash-lite.
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
    retries = int(os.getenv("TRADINGAGENTS_LLM_MAX_RETRIES", "2"))

    def _custom_create_llm(provider: str, model: str, *args: Any, **kwargs: Any) -> Any:
        # Nếu vendor gọi cho deep_think thì trả deep_llm, ngược lại trả quick_llm
        target = deep_name if "deep" in str(model).lower() else quick_name
        return create_gemini_chat_model(model_name=target, max_retries=retries)

    # Ghi đè hàm tạo LLM của vendor (tên phổ biến trong TradingAgents là create_llm)
    if hasattr(trading_graph_module, "create_llm"):
        trading_graph_module.create_llm = _custom_create_llm
