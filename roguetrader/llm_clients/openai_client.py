"""
OpenAI 客户端模块

支持 OpenAI 原生模型、Ollama、OpenRouter、DeepSeek 和 xAI 提供商的客户端实现。
"""

import os
from typing import Any, Optional

from langchain_openai import ChatOpenAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model


class NormalizedChatOpenAI(ChatOpenAI):
    """带规范化内容输出的 ChatOpenAI。

    Responses API 返回内容为类型化块的列表
    （reasoning、text 等）。此版本将其规范化为字符串，
    以便下游处理保持一致。
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))

# 从用户配置转发到 ChatOpenAI 的参数
_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "reasoning_effort",
    "api_key", "callbacks", "http_client", "http_async_client",
)

# 提供商的基础 URL 和 API 密钥环境变量
_PROVIDER_CONFIG = {
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
}


class OpenAIClient(BaseLLMClient):
    """OpenAI、Ollama、OpenRouter 和 xAI 提供商的客户端。

    对于原生 OpenAI 模型，使用 Responses API (/v1/responses)，
    该 API 支持在所有模型系列（GPT-4.1、GPT-5）中使用 function tools 的 reasoning_effort。
    第三方兼容提供商（xAI、OpenRouter、Ollama）使用标准的 Chat Completions。
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """返回配置好的 ChatOpenAI 实例。"""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        # 提供商特定的基础 URL 和认证
        if self.provider in _PROVIDER_CONFIG:
            default_base_url, api_key_env = _PROVIDER_CONFIG[self.provider]
            llm_kwargs["base_url"] = self.base_url or default_base_url
            # 添加默认超时时间
            llm_kwargs["timeout"] = 60
            if api_key_env:
                api_key = os.environ.get(api_key_env)
                if not api_key:
                    print(f"WARNING: {api_key_env} is not set in environment variables! Check your .env file.")
                else:
                    llm_kwargs["api_key"] = api_key
            else:
                llm_kwargs["api_key"] = "ollama"
        elif self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # 转发用户提供的 kwargs
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # 原生 OpenAI：对所有模型系列使用 Responses API 以保持一致的行为。
        # 使用自定义 base_url 的第三方提供商（包括 DeepSeek）
        # 仅支持 Chat Completions。
        # 对于明确配置的 'deepseek' 提供商，不使用 Responses API
        if (self.provider == "openai" and self.base_url is None) or self.provider == "deepseek":
            if self.provider == "openai" and self.base_url is None:
                llm_kwargs["use_responses_api"] = True

        # 为所有请求添加默认超时时间，避免无限挂起。
        # Keep this hashable: langchain-openai may use timeout in cached client keys.
        llm_kwargs.setdefault("timeout", 30)
        return NormalizedChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """验证提供商的模型。"""
        return validate_model(self.provider, self.model)
