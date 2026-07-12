"""
Anthropic Claude 客户端模块

支持 Anthropic Claude 系列模型的客户端实现。
"""

from typing import Any, Optional

from langchain_anthropic import ChatAnthropic

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

# 从用户配置转发到 ChatAnthropic 的参数
_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "api_key", "max_tokens",
    "callbacks", "http_client", "http_async_client", "effort",
)


class NormalizedChatAnthropic(ChatAnthropic):
    """带规范化内容输出的 ChatAnthropic。

    带有扩展思考或工具使用的 Claude 模型返回的内容为
    类型化块的列表。此版本将其规范化为字符串，
    以便下游处理保持一致。
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude 模型的客户端。"""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """返回配置好的 ChatAnthropic 实例。"""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return NormalizedChatAnthropic(**llm_kwargs)

    def validate_model(self) -> bool:
        """验证 Anthropic 的模型。"""
        return validate_model("anthropic", self.model)
