"""
LLM 客户端基类模块

提供所有 LLM 提供商客户端的抽象基类和通用工具函数。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
import warnings


def normalize_content(response):
    """规范化 LLM 响应内容为纯字符串。

    多个提供商（OpenAI Responses API、Google Gemini 3）返回的内容
    可能是一个类型化块的列表，例如 [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}。
    下游代理期望 response.content 是字符串格式。此函数提取并连接文本块，
    丢弃推理/元数据块。
    """
    content = response.content
    if isinstance(content, list):
        texts = [
            item.get("text", "") if isinstance(item, dict) and item.get("type") == "text"
            else item if isinstance(item, str) else ""
            for item in content
        ]
        response.content = "\n".join(t for t in texts if t)
    return response


class BaseLLMClient(ABC):
    """LLM 客户端的抽象基类。"""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        self.model = model
        self.base_url = base_url
        self.kwargs = kwargs

    def get_provider_name(self) -> str:
        """返回在警告消息中使用的提供商名称。"""
        provider = getattr(self, "provider", None)
        if provider:
            return str(provider)
        return self.__class__.__name__.removesuffix("Client").lower()

    def warn_if_unknown_model(self) -> None:
        """当模型不在提供商的已知模型列表中时发出警告。"""
        if self.validate_model():
            return

        warnings.warn(
            (
                f"Model '{self.model}' is not in the known model list for "
                f"provider '{self.get_provider_name()}'. Continuing anyway."
            ),
            RuntimeWarning,
            stacklevel=2,
        )

    @abstractmethod
    def get_llm(self) -> Any:
        """返回配置好的 LLM 实例。"""
        pass

    @abstractmethod
    def validate_model(self) -> bool:
        """验证模型是否受此客户端支持。"""
        pass
