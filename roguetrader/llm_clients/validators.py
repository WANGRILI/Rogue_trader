"""
模型验证器模块

提供各提供商的模型名称验证功能。
"""

from .model_catalog import get_known_models


# 从已知模型目录中提取有效模型列表，排除 ollama 和 openrouter
VALID_MODELS = {
    provider: models
    for provider, models in get_known_models().items()
    if provider not in ("ollama", "openrouter")
}


def validate_model(provider: str, model: str) -> bool:
    """检查模型名称对于给定提供商是否有效。

    对于 ollama、openrouter - 接受任何模型。
    """
    provider_lower = provider.lower()

    if provider_lower in ("ollama", "openrouter"):
        return True

    if provider_lower not in VALID_MODELS:
        return True

    return model in VALID_MODELS[provider_lower]
