# LLM 客户端一致性改进

[English](TODO.md) · [中文](TODO.zh.md)

## 待处理事项

### 1. `validate_model()` 尚未调用

- 在 `get_llm()` 中增加校验；未知模型只记录警告，不直接报错。

### 2. ~~参数处理不一致~~（已修复）

- GoogleClient 现在接收统一的 `api_key`，并映射为 `google_api_key`。

### 3. ~~接收但忽略 `base_url`~~（已修复）

- 所有客户端现在都会把 `base_url` 传递给对应的 LLM 构造器。

### 4. ~~使用 CLI 模型同步 `validators.py`~~（已修复）

- 已在 v0.2.2 同步。
