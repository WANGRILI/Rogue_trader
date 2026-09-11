# 模型与数据源

## LLM Provider

RogueTrader 支持以下 Provider：

| Provider | 环境变量 | 说明 |
|----------|----------|------|
| DeepSeek | `DEEPSEEK_API_KEY` | 默认 OpenAI-compatible 路径 |
| OpenAI | `OPENAI_API_KEY` | OpenAI 模型 |
| Anthropic | `ANTHROPIC_API_KEY` | Claude 模型 |
| Google | `GOOGLE_API_KEY` | Gemini 模型 |
| xAI | `XAI_API_KEY` | Grok 模型 |
| OpenRouter | `OPENROUTER_API_KEY` | OpenRouter 路由 |
| Ollama | 无云端密钥 | 本地模型服务 |

具体模型名称会随 Provider 更新。当前可选项以 CLI 和 `roguetrader/default_config.py` 为准，不在文档中复制一份容易过期的列表。

角色级配置位于 `configs/agents.yaml`，可以覆盖模型层级、身份、关注重点和表达风格。

## 市场与链上数据

| 数据源 | 用途 | 认证 |
|--------|------|------|
| Yahoo Finance / yfinance | 股票、ETF、期货和加密价格，部分基本面与新闻 | 通常无需 |
| CoinGecko | 加密市场、供应、交易所、衍生品和社交指标 | 免费层无需 |
| DeFiLlama | 公链与协议 TVL、稳定币供应 | 无需 |
| Blockchain.com | BTC 算力、难度、交易和矿工数据 | 无需 |
| Alternative.me | 加密恐惧与贪婪指数 | 无需 |
| Alpha Vantage | 备选股票数据 | 需要 API Key |

公开 API 可能限流、修订数据或返回当前口径。历史研究不能默认把它们视为严格 point-in-time 数据。

## 标的格式

项目使用 yfinance 兼容格式：

| 类型 | 格式 | 示例 |
|------|------|------|
| 加密货币/USD | `XXX-USD` | `BTC-USD`、`ETH-USD` |
| 加密货币/USDT | `XXX-USDT` | `BTC-USDT` |
| 美股或 ETF | `SYMBOL` | `NVDA`、`SPY` |
| 国际市场 | `SYMBOL.EXCHANGE` | `0700.HK`、`7203.T` |
| 期货 | `SYMBOL=F` | `GC=F`、`CL=F` |

链上工具会将常见加密标的映射为 CoinGecko coin ID；未命中内置映射时使用搜索回退。

## 本地历史数据

离线研究使用标准化后的 `processed/parquet`，并按分析日期截断。这样能减少在线 API 的当前数据混入历史分析。

本地数据仍需检查：

- 时间范围与时区；
- OHLCV 完整性；
- 重复或缺失区间；
- 标的映射；
- 拆分、复权或交易所口径；
- 请求日期之后的数据是否被排除。

相关命令见[开发与手动运行](development.md)。
