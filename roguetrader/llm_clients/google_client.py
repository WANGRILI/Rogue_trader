"""
Google Gemini 客户端模块

支持 Google Gemini 系列模型的客户端实现。
"""

from typing import Any, Optional

from langchain_google_genai import ChatGoogleGenerativeAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model


class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """带规范化内容输出的 ChatGoogleGenerativeAI。

    Gemini 3 模型返回内容为类型化块的列表。
    此版本将其规范化为字符串，以便下游处理保持一致。
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class GoogleClient(BaseLLMClient):
    """Google Gemini 模型的客户端。"""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """返回配置好的 ChatGoogleGenerativeAI 实例。"""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in ("timeout", "max_retries", "callbacks", "http_client", "http_async_client"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # 统一的 api_key 映射到提供商特定的 google_api_key
        google_api_key = self.kwargs.get("api_key") or self.kwargs.get("google_api_key")
        if google_api_key:
            llm_kwargs["google_api_key"] = google_api_key

        # 根据模型将 thinking_level 映射到相应的 API 参数
        # Gemini 3 Pro: low, high
        # Gemini 3 Flash: minimal, low, medium, high
        # Gemini 2.5: thinking_budget (0=禁用, -1=动态)
        thinking_level = self.kwargs.get("thinking_level")
        if thinking_level:
            model_lower = self.model.lower()
            if "gemini-3" in model_lower:
                # Gemini 3 Pro 不支持 "minimal"，改用 "low"
                if "pro" in model_lower and thinking_level == "minimal":
                    thinking_level = "low"
                llm_kwargs["thinking_level"] = thinking_level
            else:
                # Gemini 2.5: 映射到 thinking_budget
                llm_kwargs["thinking_budget"] = -1 if thinking_level == "high" else 0

        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """验证 Google 的模型。"""
        return validate_model("google", self.model)
