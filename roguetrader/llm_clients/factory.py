"""
LLM 客户端工厂模块

根据提供商类型创建相应的 LLM 客户端实例。
"""

from typing import Optional

from .base_client import BaseLLMClient
from .openai_client import OpenAIClient
from .anthropic_client import AnthropicClient
from .google_client import GoogleClient


def create_llm_client(
    provider: str,
    model: str,
    base_url: Optional[str] = None,
    **kwargs,
) -> BaseLLMClient:
    """为指定的提供商创建 LLM 客户端。

    Args:
        provider: LLM 提供商（openai, anthropic, google, xai, ollama, openrouter）
        model: 模型名称/标识符
        base_url: API 端点的可选基础 URL
        **kwargs: 其他提供商特定的参数
            - http_client: 用于 SSL 代理或证书自定义的自定义 httpx.Client
            - http_async_client: 用于异步操作的自定义 httpx.AsyncClient
            - timeout: 请求超时时间（秒）
            - max_retries: 最大重试次数
            - api_key: 提供商的 API 密钥
            - callbacks: LangChain 回调

    Returns:
        配置好的 BaseLLMClient 实例

    Raises:
        ValueError: 如果提供商不支持
    """
    provider_lower = provider.lower()

    if provider_lower in ("openai", "ollama", "openrouter", "deepseek"):
        return OpenAIClient(model, base_url, provider=provider_lower, **kwargs)

    if provider_lower == "xai":
        return OpenAIClient(model, base_url, provider="xai", **kwargs)

    if provider_lower == "anthropic":
        return AnthropicClient(model, base_url, **kwargs)

    if provider_lower == "google":
        return GoogleClient(model, base_url, **kwargs)

    raise ValueError(f"Unsupported LLM provider: {provider}")
