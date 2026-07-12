"""
LLM调用统计回调处理器

此模块实现了LangChain的回调处理器，用于追踪:
- LLM调用次数
- 工具调用次数
- Token使用量（输入/输出）

使用方式:
    stats_handler = StatsCallbackHandler()
    graph = RogueTraderGraph(callbacks=[stats_handler])
    # ... 执行分析后 ...
    stats = stats_handler.get_stats()
    print(f"LLM calls: {stats['llm_calls']}, Tokens: {stats['tokens_in']}/{stats['tokens_out']}")
"""

import threading
from typing import Any, Dict, List, Union

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.messages import AIMessage


class StatsCallbackHandler(BaseCallbackHandler):
    """
    LangChain回调处理器 - 追踪LLM和工具调用统计

    线程安全的实现，使用锁保护共享状态。
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()  # 线程锁
        self.llm_calls = 0  # LLM调用次数
        self.tool_calls = 0  # 工具调用次数
        self.tokens_in = 0  # 输入token总数
        self.tokens_out = 0  # 输出token总数

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        **kwargs: Any,
    ) -> None:
        """当LLM开始时调用，增加LLM调用计数"""
        with self._lock:
            self.llm_calls += 1

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[Any]],
        **kwargs: Any,
    ) -> None:
        """当聊天模型开始时调用，增加LLM调用计数"""
        with self._lock:
            self.llm_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """
        从LLM响应中提取token使用量

        从AIMessage的usage_metadata字段获取输入/输出token数
        """
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return

        usage_metadata = None
        if hasattr(generation, "message"):
            message = generation.message
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata

        if usage_metadata:
            with self._lock:
                self.tokens_in += usage_metadata.get("input_tokens", 0)
                self.tokens_out += usage_metadata.get("output_tokens", 0)

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """当工具开始时调用，增加工具调用计数"""
        with self._lock:
            self.tool_calls += 1

    def get_stats(self) -> Dict[str, Any]:
        """
        返回当前统计信息

        返回:
            dict: 包含 llm_calls, tool_calls, tokens_in, tokens_out
        """
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
