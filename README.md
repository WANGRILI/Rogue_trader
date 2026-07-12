# RogueTrader: Multi-Agent LLM Crypto & Financial Trading Framework

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_zh.md">中文</a>
</p>

---

RogueTrader is an open-source multi-agent trading framework specializing in **both traditional financial assets and cryptocurrencies with on-chain data analysis**. Built on a LangGraph-based multi-agent architecture, it deploys specialized LLM-powered agents — from fundamental analysts, sentiment experts, technical analysts, to **on-chain data analysts** — that collaboratively evaluate market conditions and inform trading decisions through structured multi-agent debates.

> **What makes RogueTrader different:** Deep cryptocurrency-native analysis. Beyond standard price/volume/technicals, RogueTrader analyzes **on-chain power structures** — whale concentration, DeFi TVL flows, stablecoin supply dynamics, mining economics, Pi Cycle indicators, CME gap detection, and more. It treats crypto assets not just as price charts, but as complex on-chain economies with governance dynamics and capital flow patterns.

---

## Architecture Overview

### Agent Pipeline

```
User Input (Ticker + Date)
        │
        ▼
┌───────────────────────────────────────────┐
│  PHASE 1: ANALYST TEAM (parallel data gathering) │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────┐  │
│  │ Market   │ │ Social   │ │  News    │ │ Fundamentals │ │ On-Chain │  │
│  │ Analyst  │ │ Media    │ │ Analyst  │ │   Analyst    │ │ Analyst  │  │
│  │          │ │ Analyst  │ │          │ │              │ │   ★NEW   │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘ └──────────┘  │
└───────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────┐
│  PHASE 2: RESEARCH TEAM (structured debate) │
│  Bull Researcher ←→ Bear Researcher       │
│           ↓                               │
│     Research Manager (judge)              │
└───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────┐
│  PHASE 3: TRADER                       │
│  Synthesizes all reports → trade plan  │
└───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────┐
│  PHASE 4: RISK MANAGEMENT (three-way debate) │
│  Aggressive ←→ Conservative ←→ Neutral       │
│           ↓                               │
│     Portfolio Manager (final decision)     │
└───────────────────────────────────────┘
        │
        ▼
   FINAL DECISION: BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL
        │
        ▼
   Memory Reflection (BM25-based learning from outcomes)
```

### Agent Roles

#### Analyst Team (Data Gathering)
| Agent | Responsibility | Tools |
|-------|---------------|-------|
| **Market Analyst** | Technical analysis using price data and indicators | `get_stock_data`, `get_indicators` |
| **Social Media Analyst** | Social sentiment and community metrics | `get_news`, `get_crypto_sentiment` |
| **News Analyst** | Global news, macro events, insider transactions | `get_news`, `get_global_news`, `get_insider_transactions` |
| **Fundamentals Analyst** | Company financials, valuation metrics | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` |
| **On-Chain Analyst ★** | On-chain metrics, whale activity, DeFi TVL, stablecoin flows, mining stats, Pi Cycle, NVT, funding rates, CME gaps, Fear & Greed | 10 specialized crypto tools (see below) |

#### Research Team (Debate)
- **Bull Researcher**: Argues the bullish case, uses BM25 memory to recall similar past situations
- **Bear Researcher**: Argues the bearish case, identifies risks and downside scenarios
- **Research Manager**: Judges the debate, synthesizes bull/bear perspectives into a coherent investment thesis

#### Execution & Risk
- **Trader**: Composes all analyst and researcher reports into a concrete trading plan (timing, sizing, direction)
- **Risk Management Trio** (Aggressive / Conservative / Neutral): Three-way debate evaluating portfolio risk from different perspectives
- **Portfolio Manager**: Final approval/rejection of any proposed transaction

---

## On-Chain Analysis Capabilities ★

The **On-Chain Analyst** is RogueTrader's key differentiator, with 10 specialized tools across 4 analysis dimensions:

### 1. Basic On-Chain Metrics
| Tool | Data Source | Description |
|------|-------------|-------------|
| `get_onchain_metrics` | CoinGecko | Market cap, circulating/total/max supply, FDV, ATH/ATL, multi-timeframe price changes |
| `get_nvt_ratio` | CoinGecko + Blockchain.com | Network Value to Transactions ratio (crypto's P/E ratio) — high = overvalued |

### 2. Whale & Market Maker Control
| Tool | Data Source | Description |
|------|-------------|-------------|
| `get_whale_activity` | CoinGecko (exchange tickers) | Exchange volume distribution, top-3 concentration, accumulation/distribution proxies |
| `get_stablecoin_flows` | DeFiLlama | Top-15 stablecoin circulating supplies — available capital for crypto markets |

### 3. DeFi & Layer Power Games
| Tool | Data Source | Description |
|------|-------------|-------------|
| `get_defi_tvl` | DeFiLlama | Chain-level or protocol-level Total Value Locked — capital flow between ecosystems |
| `get_mining_stats` | Blockchain.com | BTC hash rate, difficulty, transaction count, active addresses, miner revenue (7-day trends) |

### 4. Technical & Sentiment Indicators
| Tool | Data Source | Description |
|------|-------------|-------------|
| `get_pi_cycle_indicator` | CoinGecko / yfinance | 111-day MA × 350-day MA×2 — major cycle top/bottom detection |
| `get_crypto_fear_greed` | Alternative.me | 0-100 sentiment index with 7-day/30-day trend analysis |
| `get_funding_rate` | CoinGecko derivatives | Perpetual futures funding rates — crowded long/short detection |
| `get_cme_gap` | yfinance | CME Bitcoin futures weekend gap detection (~77% historical fill rate) |

### Analysis Philosophy

The On-Chain Analyst goes beyond surface metrics. Its system prompt instructs it to analyze:

- **Power Structure**: Who controls this asset? What are their incentives? Is it dominated by a small number of whales?
- **Macro Game Theory**: Regulatory stance across jurisdictions, nation-state accumulation patterns, monetary policy impact
- **Layer Power Games**: L1 dominance battles, L2 MEV and liquidity capture, cross-chain capital flows
- **Manipulation Risk**: Explicit assessment of whether the asset is vulnerable to pump-and-dump or rug-pull scenarios

---

## LLM Provider Support

RogueTrader supports **7 LLM providers** through a unified factory pattern:

| Provider | Models (Quick) | Models (Deep) | API Base |
|----------|---------------|---------------|----------|
| **DeepSeek** ★ (default) | `deepseek-chat` | `deepseek-reasoner` | `api.deepseek.com` |
| **OpenAI** | GPT-5.4 Mini/Nano, GPT-4.1 | GPT-5.4, GPT-5.2, GPT-5.4 Pro | `api.openai.com` |
| **Anthropic** | Claude Sonnet 4.6, Haiku 4.5 | Claude Opus 4.6, Sonnet 4.6 | `api.anthropic.com` |
| **Google** | Gemini 3 Flash, 2.5 Flash | Gemini 3.1 Pro, 2.5 Pro | Google AI |
| **xAI** | Grok 4.1 Fast | Grok 4, Grok 4.1 Fast | `api.x.ai` |
| **OpenRouter** | NVIDIA Nemotron, GLM 4.5 | Same (free tier) | `openrouter.ai` |
| **Ollama** | Qwen3, GPT-OSS, GLM-4.7 | Same (local) | `localhost:11434` |

> **Why DeepSeek is default:** It offers an OpenAI-compatible API at significantly lower cost, with strong reasoning capability from `deepseek-reasoner`. All DeepSeek calls go through the standard Chat Completions API (no Responses API dependency).

### Model Architecture

```
Deep Thinking LLM (deepseek-reasoner)
  ├── Research Manager (investment debate judge)
  └── Portfolio Manager (risk debate judge + final decision)

Quick Thinking LLM (deepseek-chat)
  ├── All 5 Analysts (market, social, news, fundamentals, onchain)
  ├── Bull/Bear Researchers
  ├── Trader
  └── Risk Management Trio (aggressive, conservative, neutral)
```

---

## Memory System

RogueTrader uses a **BM25-based lexical memory** system (no embedding API costs, no token limits):

```
FinancialSituationMemory (5 instances)
  ├── bull_memory       → Bull Researcher
  ├── bear_memory       → Bear Researcher
  ├── trader_memory     → Trader
  ├── invest_judge_memory → Research Manager
  └── portfolio_manager_memory → Portfolio Manager
```

After each trading decision, the `Reflector` analyzes outcomes (returns/losses) against agent reasoning, extracts lessons learned, and stores `(situation, recommendation)` pairs. On subsequent analyses, agents retrieve the top-K most similar past situations via BM25 lexical matching to inform their current decision.

---

## Project Structure

```
RogueTrader/
├── main.py                          # Quick-start entry point
├── pyproject.toml                    # Package configuration
├── requirements.txt                  # Dependencies
├── README.md                         # This file
│
├── roguetrader/                      # Core Python package
│   ├── default_config.py             # All configuration defaults (DeepSeek by default)
│   │
│   ├── graph/                        # LangGraph orchestration
│   │   ├── trading_graph.py          # Main orchestrator — RogueTraderGraph class
│   │   ├── setup.py                  # GraphSetup — builds the agent workflow DAG
│   │   ├── propagation.py            # Propagator — state initialization & graph args
│   │   ├── conditional_logic.py      # ConditionalLogic — debate routing logic
│   │   ├── reflection.py             # Reflector — post-trade learning from outcomes
│   │   └── signal_processing.py      # SignalProcessor — extracts BUY/HOLD/SELL from final report
│   │
│   ├── agents/                       # 15 agent implementations
│   │   ├── analysts/
│   │   │   ├── market_analyst.py     # Technical analysis
│   │   │   ├── social_media_analyst.py  # Social sentiment
│   │   │   ├── news_analyst.py       # News & macro events
│   │   │   ├── fundamentals_analyst.py  # Company fundamentals
│   │   │   └── onchain_analyst.py    # ★ On-chain crypto analysis
│   │   ├── researchers/
│   │   │   ├── bull_researcher.py    # Bullish case argument
│   │   │   └── bear_researcher.py    # Bearish case argument
│   │   ├── managers/
│   │   │   ├── research_manager.py   # Investment debate judge
│   │   │   └── portfolio_manager.py  # Final decision maker
│   │   ├── risk_mgmt/
│   │   │   ├── aggressive_debator.py # Aggressive risk perspective
│   │   │   ├── conservative_debator.py  # Conservative risk perspective
│   │   │   └── neutral_debator.py    # Neutral risk perspective
│   │   ├── trader/
│   │   │   └── trader.py             # Trade plan synthesis
│   │   └── utils/
│   │       ├── agent_utils.py        # Shared utilities, language instructions
│   │       ├── agent_states.py       # AgentState, InvestDebateState, RiskDebateState
│   │       ├── memory.py             # BM25 FinancialSituationMemory
│   │       ├── core_stock_tools.py   # Stock price/fundamentals tools
│   │       ├── technical_indicators_tools.py  # Standard technical indicators
│   │       ├── fundamental_data_tools.py      # Financial statement tools
│   │       ├── news_data_tools.py             # News/insider transaction tools
│   │       ├── onchain_data_tools.py          # ★ On-chain metrics tools (9 tools)
│   │       ├── crypto_indicator_tools.py      # ★ Crypto-specific indicators (5 tools)
│   │       └── crypto_sentiment_tools.py      # ★ Crypto sentiment tools (2 tools)
│   │
│   ├── dataflows/                    # Data pipelines (5 sources)
│   │   ├── y_finance.py             # Yahoo Finance — stocks, fundamentals, news
│   │   ├── alpha_vantage*.py         # Alpha Vantage — alternative data vendor
│   │   ├── onchain_data.py           # ★ CoinGecko + DeFiLlama + Blockchain.com + Alternative.me
│   │   ├── crypto_indicators.py      # ★ Pi Cycle, NVT, CME Gap, Funding Rate calculators
│   │   ├── crypto_sentiment.py       # ★ Aggregated crypto sentiment pipeline
│   │   ├── interface.py              # Abstract data vendor interface
│   │   ├── config.py                 # Runtime config (updated from default_config)
│   │   └── utils.py                  # Data processing utilities
│   │
│   └── llm_clients/                  # Multi-provider LLM abstraction
│       ├── factory.py                # create_llm_client() — provider dispatch
│       ├── base_client.py            # BaseLLMClient abstract class
│       ├── openai_client.py          # OpenAI + DeepSeek + xAI + Ollama + OpenRouter
│       ├── anthropic_client.py       # Anthropic Claude
│       ├── google_client.py          # Google Gemini
│       ├── model_catalog.py          # Centralized model registry (6 providers × 2 modes)
│       └── validators.py             # Model validation logic
│
├── cli/                              # Interactive terminal UI
│   ├── main.py                       # Rich/Typer-based TUI (8-step wizard)
│   ├── models.py                     # Analyst selection types
│   ├── config.py                     # CLI-specific configuration
│   ├── utils.py                      # Prompt functions for each selection step
│   ├── announcements.py              # Community announcements fetcher
│   ├── stats_handler.py              # LLM/tool call token tracking
│   └── static/welcome.txt            # ASCII art splash screen
│
├── my_scripts/                       # ★ Personal run scripts
│   └── roguetrader1.py              # DeepSeek config + ETH on-chain analysis demo
│
├── my_results/                       # ★ Personal run outputs
│   ├── 运行结果/
│   │   └── 20260712_104835_BTC_USD/
│   │       ├── 运行索引.json
│   │       ├── 报告.md
│   │       ├── 状态.json
│   │       ├── 最终决策.json
│   │       ├── 运行配置.json
│   │       ├── 终端日志.log
│   │       └── 分段报告/
│   ├── 评估结果/
│   │   └── 20260712_110000_BTC_USD/
│   │       ├── 评估报告.md
│   │       ├── 评估结果.json
│   │       └── 反思候选.json
│   └── 图状态日志/                  # Historical legacy output only
│
└── tests/                            # Test files
    ├── test_google_api_key.py
    ├── test_model_validation.py
    └── test_ticker_symbol_handling.py
```

> **★ = crypto/on-chain addition** — the features added for RogueTrader's crypto-focused workflow

---

## Installation

### Prerequisites
- Python 3.10+
- conda (recommended) or venv

### Quick Setup

```bash
# Clone
git clone https://github.com/yourusername/RogueTrader.git
cd RogueTrader

# Create environment
conda create -n roguetrader python=3.13 -y
conda activate roguetrader

# Install package (editable mode recommended for development)
pip install -e .
```

### API Keys

RogueTrader requires at least one LLM provider API key. The **default is DeepSeek**:

```bash
export DEEPSEEK_API_KEY=your_key_here     # DeepSeek (default)
# OR any of these alternatives:
export OPENAI_API_KEY=...                 # OpenAI
export ANTHROPIC_API_KEY=...              # Anthropic
export GOOGLE_API_KEY=...                 # Google Gemini
export XAI_API_KEY=...                    # xAI Grok
export OPENROUTER_API_KEY=...             # OpenRouter
```

Alternatively, copy `.env.example` to `.env`:

```bash
cp .env.example .env
# Edit .env with your keys
```

### Verification

This working copy is expected to run with the locked `uv` environment:

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q cli roguetrader tests my_scripts main.py
uv run --frozen roguetrader --help
uv run --frozen roguetrader analyze --help
```

The current baseline is **19 passing tests**. These tests cover provider/model validation, CLI behavior, output path normalization, on-chain analyst wiring, graph initialization, OpenAI-compatible client configuration, local Parquet data cutoffs, ticker handling, quick-start configuration, and deterministic signal extraction.

---

## Usage

### CLI (Interactive)

```bash
# Launch the interactive TUI
roguetrader                    # installed command
# OR
python -m cli.main             # direct invocation
```

The CLI walks you through an 8-step wizard:
1. Ticker symbol (e.g., `BTC-USD`, `ETH-USD`, `SPY`, `NVDA`)
2. Analysis date
3. Output language (English / Chinese)
4. Analyst selection (market, social, news, fundamentals, onchain)
5. Research depth (debate rounds: 1-3)
6. LLM provider selection
7. Model selection (quick + deep thinking)
8. Provider-specific configuration (reasoning effort, thinking mode)

### Custom Scripts and Advanced Run Modes

This working copy also includes personal/custom run scripts under `my_scripts/`. These scripts call the Python API directly and are useful for reproducible local runs with pre-filled configuration, analyst selection, ticker, and analysis date.

For example, `my_scripts/roguetrader1.py` loads `.env` from the project root, configures DeepSeek, sets Chinese output, selects the on-chain analyst, and runs an ETH analysis. Before running scripts from `my_scripts/`, install the project in editable mode (`pip install -e .`) so the `roguetrader` package imports correctly.

#### Preparation

```bash
conda activate roguetrader
cd my_scripts
```

#### Mode A: Run directly and print to terminal

```bash
python roguetrader1.py
```

- Output is printed directly to the terminal.
- Terminal output is not automatically preserved after the terminal is closed.
- Best for quick smoke tests.

### Local Processed Parquet Workflow

For offline/local-data checks, use `my_scripts/roguetrader_local_data.py`. The runtime policy is:

- `raw2` is treated as the upstream source of truth.
- RogueTrader runtime tools read only standardized `processed/parquet`.
- Local OHLCV summaries are cut off at the requested analysis date to avoid look-ahead bias.

Smoke test without any LLM/API call:

```bash
uv run --frozen python my_scripts/roguetrader_local_data.py \
  --skip-roguetrader \
  --ticker BTC-USD \
  --date 2014-11-30 \
  --source manual_or_investing \
  --timeframe 1d \
  --days 30
```

This writes `运行索引.json`, `报告.md`, `状态.json`, `最终决策.json`, and `运行配置.json` under `my_results/运行结果/<timestamp>_<ticker>/`.

#### Mode B: Save the full terminal output to a local file

```bash
python roguetrader1.py > ../my_results/rogue_eth_0519.log 2>&1
```

- Nothing is printed live in the terminal; stdout and stderr are both written to `../my_results/rogue_eth_0519.log`.
- `>` overwrites an existing file with the same name; use `>>` to append instead.
- Best when you need a complete local log for one run.

#### Mode C: Print live and save at the same time (recommended)

```bash
python roguetrader1.py 2>&1 | tee ../my_results/rogue_eth_0519.log
```

- You can watch progress in the terminal in real time.
- The full terminal output is also saved under `my_results/`.
- Best for long-running RogueTrader multi-agent analysis jobs.

To reduce Python output buffering, use `-u`:

```bash
python -u roguetrader1.py 2>&1 | tee ../my_results/rogue_eth_0519.log
```

#### Mode D: Run in the background

```bash
nohup python -u roguetrader1.py > ../my_results/rogue_eth_0519.log 2>&1 &
```

Check background jobs:

```bash
jobs
ps aux | grep roguetrader1
```

Follow the output file:

```bash
tail -f ../my_results/rogue_eth_0519.log
```

To stop a background run, find the process ID with `ps aux | grep roguetrader1`, then run:

```bash
kill <pid>
```

#### Command Cheat Sheet

| Scenario | Command |
|----------|---------|
| Quick test | `python roguetrader1.py` |
| Save full log | `python roguetrader1.py > ../my_results/report_name.log 2>&1` |
| Print and save | `python -u roguetrader1.py 2>&1 \| tee ../my_results/report_name.log` |
| Background run | `nohup python -u roguetrader1.py > ../my_results/report_name.log 2>&1 &` |
| Follow background output | `tail -f ../my_results/report_name.log` |
| Stop foreground run | `Ctrl+C` |
| Stop background run | `ps aux \| grep roguetrader1`, then `kill <pid>` |

#### Customizing Script Parameters

To customize a run, edit `my_scripts/roguetrader1.py` and adjust:

- `selected_analysts`: choose analysts such as `market`, `social`, `news`, `fundamentals`, `onchain`.
- `config["output_language"]`: choose `Chinese` or `English` report output.
- `config["max_debate_rounds"]`: set the Bull/Bear researcher debate rounds.
- `config["max_recur_limit"]`: set the LangGraph recursion limit; increase for more complex runs.
- `rt.propagate("ETH-USD", "2026-05-19")`: change the ticker and analysis date.

### Python API

#### Quick Start (Default DeepSeek Config)

```python
from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.default_config import DEFAULT_CONFIG

# Default config already uses DeepSeek
rt = RogueTraderGraph(debug=True, config=DEFAULT_CONFIG.copy())
_, decision = rt.propagate("ETH-USD", "2026-05-19")
print(decision)  # BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL
```

#### On-Chain Analysis (Crypto-Focused)

```python
from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["output_language"] = "Chinese"
config["max_debate_rounds"] = 2

# Use only on-chain analyst for crypto-native analysis
rt = RogueTraderGraph(
    debug=True,
    config=config,
    selected_analysts=["onchain"]
)
_, decision = rt.propagate("BTC-USD", "2026-05-19")
print(decision)
```

#### Full Analyst Suite

```python
rt = RogueTraderGraph(
    debug=True,
    config=config,
    selected_analysts=["market", "social", "news", "fundamentals", "onchain"]
)
_, decision = rt.propagate("ETH-USD", "2026-05-19")
```

#### Switching LLM Providers

```python
config = DEFAULT_CONFIG.copy()

# OpenAI
config["llm_provider"] = "openai"
config["deep_think_llm"] = "gpt-5.4"
config["quick_think_llm"] = "gpt-5.4-mini"

# Anthropic
config["llm_provider"] = "anthropic"
config["deep_think_llm"] = "claude-opus-4-6"
config["quick_think_llm"] = "claude-sonnet-4-6"

# Google
config["llm_provider"] = "google"
config["deep_think_llm"] = "gemini-2.5-pro"
config["quick_think_llm"] = "gemini-2.5-flash"

# Local (Ollama)
config["llm_provider"] = "ollama"
config["deep_think_llm"] = "qwen3:latest"
config["quick_think_llm"] = "qwen3:latest"
```

#### Learning from Outcomes (Reflection)

```python
rt = RogueTraderGraph(debug=True, config=config)

# Initial analysis
_, decision = rt.propagate("ETH-USD", "2026-05-19")

# After you know the actual return, teach the agents
rt.reflect_and_remember(returns_losses=+3.2)  # +3.2% return
# This updates all 5 memory instances with lessons learned
```

---

## Configuration Reference

All configuration lives in `roguetrader/default_config.py`:

| Config Key | Default | Description |
|------------|---------|-------------|
| `llm_provider` | `deepseek` | LLM provider: openai, anthropic, google, xai, openrouter, ollama, deepseek |
| `deep_think_llm` | `deepseek-reasoner` | Model for complex reasoning (Research Manager, Portfolio Manager) |
| `quick_think_llm` | `deepseek-chat` | Model for routine tasks (analysts, researchers, trader, risk debators) |
| `backend_url` | `https://api.deepseek.com` | API endpoint (auto-set per provider if blank) |
| `output_language` | `English` | Report language. Use `Chinese` for Chinese output. Internal debates always English |
| `max_debate_rounds` | `1` | Bull vs Bear debate rounds |
| `max_risk_discuss_rounds` | `1` | Three-way risk debate rounds |
| `max_recur_limit` | `20` | LangGraph recursion limit |
| `data_vendors.onchain_data` | `coingecko` | On-chain data source: coingecko, defillama, blockchain_com |
| `data_vendors.crypto_indicators` | `local` | Crypto indicator calculation: local (computed from data sources) |
| `data_vendors.crypto_sentiment` | `coingecko` | Crypto sentiment source: coingecko, alternative_me |

### Provider-Specific Options

| Config Key | Values | Applies To |
|------------|--------|------------|
| `google_thinking_level` | `high`, `minimal`, `None` | Google Gemini |
| `openai_reasoning_effort` | `low`, `medium`, `high`, `None` | OpenAI |
| `anthropic_effort` | `low`, `medium`, `high`, `None` | Anthropic Claude |

---

## Data Sources

| Source | Used For | Authentication |
|--------|----------|----------------|
| **Yahoo Finance** (`yfinance`) | Stock prices, fundamentals, news, technical indicators | None (free) |
| **CoinGecko API** | Crypto market data, OHLC, exchange volumes, derivatives, social metrics, trending | None (free tier) |
| **DeFiLlama API** | Chain TVL, protocol TVL, stablecoin supplies | None (free) |
| **Blockchain.com API** | BTC on-chain stats (hash rate, difficulty, transactions, active addresses, miner revenue) | None (free) |
| **Alternative.me API** | Crypto Fear & Greed Index | None (free) |
| **Alpha Vantage** | Alternative stock data vendor | API key required |

> All crypto data sources use **free tiers with no API key required**, though rate limits apply. CoinGecko data is cached with `lru_cache` to minimize API calls.

---

## Ticker Format

RogueTrader uses **yfinance-compatible ticker symbols**:

| Asset Type | Format | Examples |
|------------|--------|----------|
| Crypto (USD) | `XXX-USD` | `BTC-USD`, `ETH-USD`, `SOL-USD`, `DOGE-USD` |
| Crypto (USDT) | `XXX-USDT` | `BTC-USDT`, `ETH-USDT` |
| US Stocks | `SYMBOL` | `SPY`, `NVDA`, `AAPL`, `TSLA` |
| International | `SYMBOL.EXCHANGE` | `CNC.TO`, `7203.T`, `0700.HK` |
| Futures | `SYMBOL=F` | `GC=F` (Gold), `CL=F` (Crude Oil) |

The On-Chain Analyst automatically resolves yfinance tickers to CoinGecko coin IDs (e.g., `BTC-USD` → `bitcoin`, `ETH-USD` → `ethereum`). 30+ cryptocurrencies are pre-mapped; unknown tickers fall back to CoinGecko search API.

---

## Under the Hood

### LangGraph Workflow

The framework is built on **LangGraph** with a directed acyclic graph structure:

```
START → [Selected Analysts in sequence]
           ↓
     Bull Researcher ⇄ Bear Researcher  (conditional loop: debate rounds)
           ↓
     Research Manager
           ↓
     Trader
           ↓
     Aggressive → Conservative → Neutral  (conditional loop: risk rounds)
           ↓
     Portfolio Manager → END
```

- Each analyst node conditionally loops to its tool node until all needed data is gathered
- Bull/Bear researchers alternate until debate rounds are exhausted, then proceed to Research Manager
- Risk management trio rotates until risk rounds are exhausted, then proceeds to Portfolio Manager
- All state accumulates in a shared `AgentState` TypedDict

### Data Flow Architecture

```
User ticker + date
    │
    ▼
┌─────────────┐    ┌──────────────────┐
│  Dataflows  │───▶│  Agent Tools     │
│  (raw data) │    │  (LangChain @tool)│
└─────────────┘    └──────────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │   Analysts   │
                   │  (LLM + tools)│
                   └──────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ Researchers  │
                   │ + Managers   │
                   │ (deep LLM)   │
                   └──────────────┘
                          │
                          ▼
                   FINAL DECISION
```

- **Dataflows** are pure Python functions calling external APIs — no LLM dependency
- **Agent Tools** wrap dataflows as LangChain `@tool` decorators for LLM function calling
- **Analysts** use Quick Thinking LLM + bound tools to gather and analyze data
- **Managers** use Deep Thinking LLM to synthesize, judge, and decide

---

## Local Customizations (This Instance)

This local working copy includes modifications beyond the upstream codebase:

### Configuration Changes
- **Default LLM**: Changed from OpenAI GPT to **DeepSeek** (`deepseek-reasoner` + `deepseek-chat`)
- **Default backend URL**: `https://api.deepseek.com`
- Added `deepseek` as a recognized provider in the LLM factory (uses OpenAI-compatible API path)

### Crypto/On-Chain Additions
- **On-Chain Analyst agent** (`onchain_analyst.py`) — full agent with 4-dimension analysis framework
- **16 on-chain/crypto tools** across 3 tool modules:
  - `onchain_data_tools.py` — 9 tools (market data, whale activity, DeFi TVL, stablecoin flows, mining stats, Pi Cycle, NVT, Fear & Greed, funding rates, CME gaps)
  - `crypto_indicator_tools.py` — 5 tools (Pi Cycle, NVT Ratio, CME Gap, Funding Rate, Fear & Greed)
  - `crypto_sentiment_tools.py` — 2 tools (aggregated crypto sentiment, trending coins)
- **3 crypto dataflow modules**:
  - `onchain_data.py` — CoinGecko + DeFiLlama + Blockchain.com + Alternative.me integration (400+ lines)
  - `crypto_indicators.py` — Pi Cycle, NVT Ratio, CME Gap, Funding Rate calculators
  - `crypto_sentiment.py` — Aggregated crypto sentiment pipeline
- **Ticker mapping**: 30+ crypto ticker → CoinGecko coin_id mappings with search API fallback

### Personal Scripts & Results
- `my_scripts/roguetrader1.py` — DeepSeek config + ETH on-chain analysis demo
- `my_results/` — Historical analysis traces and full state logs in JSON

---

## Package Dependencies

```
langgraph >= 0.4.8          # Agent workflow orchestration
langchain-openai >= 0.3.23  # OpenAI-compatible LLM client (DeepSeek, xAI, etc.)
langchain-anthropic >= 0.3.15  # Anthropic Claude client
langchain-google-genai >= 2.1.5  # Google Gemini client
langchain-experimental >= 0.3.4
yfinance >= 0.2.63          # Stock/crypto price data
stockstats >= 0.6.5         # Technical indicators
pandas >= 2.3.0             # Data manipulation
pyarrow >= 16.0.0           # Local processed Parquet runtime data
requests >= 2.32.4          # HTTP client for crypto APIs
rank-bm25 >= 0.2.2          # BM25 lexical search for memory
rich >= 14.0.0              # Terminal UI (CLI)
typer >= 0.21.0             # CLI framework
questionary >= 2.1.0        # Interactive prompts
redis >= 6.2.0              # Optional: memory persistence
python-dotenv >= 1.0.0      # Environment variable loading
```

---

## Current Readiness

What is currently verified:

- Locked `uv` environment works with `uv run --frozen`.
- CLI command/help paths work as `roguetrader` / `roguetrader analyze`.
- `RogueTraderGraph` initializes with the on-chain analyst and OpenAI-compatible providers.
- Local processed Parquet summaries avoid look-ahead bias by cutting data at the analysis date.
- Local report/state generation works in `--skip-roguetrader` mode and now writes to normalized Chinese output paths under `my_results/运行结果/`.
- Direct `RogueTraderGraph.propagate()` runs now also write a single normalized run directory with an index, report, state, structured decision JSON, config, and section reports.
- Offline signal evaluation writes to normalized Chinese output paths under `my_results/评估结果/`.
- Final decision extraction avoids an extra LLM call when the report already contains an explicit decision.

Known boundaries:

- Full end-to-end multi-agent analysis still requires a working LLM provider key or local Ollama endpoint.
- Online on-chain APIs such as CoinGecko, DeFiLlama, Blockchain.com, Alternative.me, and yfinance may return current/live data; they are not yet guaranteed point-in-time historical datasets for backtests.
- Local processed Parquet is the safer path for historical/offline evaluation.
- This is a research framework, not an execution engine or financial advice system.

---

## Contributing

Contributions are welcome — especially:

- Additional on-chain data sources (Glassnode, Dune Analytics, Arkham, etc.)
- New crypto-specific indicators
- Multi-asset portfolio optimization
- Backtesting integration

## License

Apache License 2.0 — see [LICENSE](LICENSE).

RogueTrader is an independent modified derivative of the [TradingAgents](https://github.com/TauricResearch/TradingAgents) project by [Tauric Research](https://tauric.ai/). See [NOTICE](NOTICE) for upstream attribution and modification notes.
