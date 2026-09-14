# Models and Data Sources

[中文](providers-and-data.md) · [English](providers-and-data.en.md)

## LLM Providers

RogueTrader supports the following providers:

| Provider | Environment variable | Notes |
|---|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` | Default OpenAI-compatible path |
| OpenAI | `OPENAI_API_KEY` | OpenAI models |
| Anthropic | `ANTHROPIC_API_KEY` | Claude models |
| Google | `GOOGLE_API_KEY` | Gemini models |
| xAI | `XAI_API_KEY` | Grok models |
| OpenRouter | `OPENROUTER_API_KEY` | OpenRouter routing |
| Ollama | No cloud key | Local model service |

Model catalogs change over time. The current CLI and `roguetrader/default_config.py` are authoritative; this document intentionally avoids duplicating a list that would become stale.

Per-role settings live in `configs/agents.yaml` and can override model tier, identity, analytical focus, and communication style.

## Market and On-chain Data

| Source | Use | Authentication |
|---|---|---|
| Yahoo Finance / yfinance | Equity, ETF, futures, and crypto prices; selected fundamentals and news | Usually none |
| CoinGecko | Crypto markets, supply, exchanges, derivatives, and social metrics | Free tier requires no key |
| DeFiLlama | Chain and protocol TVL; stablecoin supply | None |
| Blockchain.com | BTC hash rate, difficulty, transactions, and miner data | None |
| Alternative.me | Crypto Fear & Greed Index | None |
| Alpha Vantage | Alternative equity data | API key |

Public APIs may be rate-limited, revise data, or expose current definitions. Historical research must not assume strict point-in-time behavior without validation.

## Symbol Formats

The project uses yfinance-compatible symbols:

| Type | Format | Examples |
|---|---|---|
| Crypto / USD | `XXX-USD` | `BTC-USD`, `ETH-USD` |
| Crypto / USDT | `XXX-USDT` | `BTC-USDT` |
| US equity or ETF | `SYMBOL` | `NVDA`, `SPY` |
| International market | `SYMBOL.EXCHANGE` | `0700.HK`, `7203.T` |
| Futures | `SYMBOL=F` | `GC=F`, `CL=F` |

On-chain tools map common crypto symbols to CoinGecko coin IDs and fall back to search when no built-in mapping exists.

## Local Historical Data

Offline research uses normalized `processed/parquet` data truncated at the requested analysis date. This reduces accidental leakage of current online data into historical analysis.

Local datasets still require checks for:

- time coverage and timezone;
- OHLCV completeness;
- duplicate or missing intervals;
- symbol mapping;
- split, adjustment, or exchange conventions;
- exclusion of observations after the requested date.

See [Development and Manual Runs](development.en.md) for commands.
