# RogueTrader：多智能体 LLM 加密货币与金融交易框架

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_zh.md">中文</a>
</p>

---

RogueTrader 是一个开源的多智能体交易框架，专攻**传统金融资产**和**加密货币链上数据分析**。项目基于 LangGraph 多智能体架构，部署了多个 LLM 驱动的专业智能体——从基本面分析师、情绪分析师、技术分析师，到**链上数据专家**——各智能体通过结构化多轮辩论来协作评估市场状况，最终形成交易决策。

> **RogueTrader 的独特之处：** 深度的加密货币原生分析。在标准的价格/成交量/技术指标之外，RogueTrader 分析**链上权力结构**——鲸鱼持仓集中度、DeFi TVL 资金流向、稳定币供应动态、挖矿经济学、Pi Cycle 周期指标、CME 期货缺口检测等。它把加密资产看作复杂的链上经济体，而非仅仅是价格图表。

---

## 架构总览

### 智能体流水线

```
用户输入（交易标的 + 分析日期）
        │
        ▼
┌──────────────────────────────────────────────────┐
│  第一阶段：分析师团队（并行采集数据）                    │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐ │
│  │ 市场   │ │ 社交媒体│ │  新闻  │ │ 基本面   │ │  链上    │ │
│  │ 分析师 │ │ 分析师  │ │ 分析师 │ │ 分析师   │ │ 分析师★ │ │
│  └────────┘ └────────┘ └────────┘ └──────────┘ └──────────┘ │
└──────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────┐
│  第二阶段：研究团队（结构化辩论）         │
│  多头研究员 ←→ 空头研究员              │
│           ↓                          │
│     研究经理（裁判）                    │
└──────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────┐
│  第三阶段：交易员                       │
│  综合所有报告 → 制定交易计划            │
└──────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────┐
│  第四阶段：风控团队（三方辩论）           │
│  激进派 ←→ 保守派 ←→ 中立派            │
│           ↓                          │
│     投资组合经理（最终决策）             │
└──────────────────────────────────────┘
        │
        ▼
   最终决策：买入 / 增持 / 持有 / 减持 / 卖出
        │
        ▼
   记忆反思（基于 BM25 的结果学习）
```

### 智能体角色详解

#### 分析师团队（数据采集层）

| 智能体 | 职责 | 可用工具 |
|--------|------|----------|
| **市场分析师** | 技术分析，价格数据和各种技术指标 | `get_stock_data`, `get_indicators` |
| **社交媒体分析师** | 社交情绪和社区指标 | `get_news`, `get_crypto_sentiment` |
| **新闻分析师** | 全球新闻、宏观事件、内部交易 | `get_news`, `get_global_news`, `get_insider_transactions` |
| **基本面分析师** | 公司财务、估值指标 | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` |
| **链上分析师 ★** | 链上指标、鲸鱼活动、DeFi TVL、稳定币流动、挖矿统计、Pi Cycle、NVT、资金费率、CME 缺口、恐惧贪婪指数 | 10 个专业加密工具（见下文） |

#### 研究团队（辩论层）
- **多头研究员**：论证看涨逻辑，使用 BM25 记忆系统回溯类似历史场景
- **空头研究员**：论证看跌逻辑，识别风险和下行情景
- **研究经理**：裁决辩论，综合多空双方观点形成投资判断

#### 执行与风控层
- **交易员**：将所有分析师和研究员的报告综合成具体交易计划（时机、仓位、方向）
- **风控三人组**（激进/保守/中立）：三方辩论，从不同视角审视组合风险
- **投资组合经理**：最终审批，否决或通过交易提案

---

## 链上分析能力 ★

**链上分析师**是 RogueTrader 的核心差异能力，拥有 10 个专业工具，横跨四个分析维度：

### 一、基础链上指标

| 工具 | 数据来源 | 说明 |
|------|----------|------|
| `get_onchain_metrics` | CoinGecko | 市值、流通/总/最大供应量、完全稀释估值、历史最高/最低价、多周期价格变动 |
| `get_nvt_ratio` | CoinGecko + Blockchain.com | NVT 比率（网络价值/交易量），相当于加密资产的市盈率。高 NVT = 高估，低 NVT = 低估 |

### 二、鲸鱼与做市商控制力分析

| 工具 | 数据来源 | 说明 |
|------|----------|------|
| `get_whale_activity` | CoinGecko（交易所 tickers） | 交易所成交量分布、前三名集中度、累积/分配模式的替代指标 |
| `get_stablecoin_flows` | DeFiLlama | 排名前 15 稳定币的流通供应量——反映可用于加密市场的"弹药" |

### 三、DeFi 与 Layer 权力博弈

| 工具 | 数据来源 | 说明 |
|------|----------|------|
| `get_defi_tvl` | DeFiLlama | 链级别或协议级别的 TVL——资金跨生态流动的方向 |
| `get_mining_stats` | Blockchain.com | BTC 算力、难度、交易数、活跃地址、矿工收入（含 7 日趋势） |

### 四、技术指标与市场情绪

| 工具 | 数据来源 | 说明 |
|------|----------|------|
| `get_pi_cycle_indicator` | CoinGecko / yfinance | 111 日均线 × 350 日均线×2 —— 历史上精准标记 BTC 周期顶部 |
| `get_crypto_fear_greed` | Alternative.me | 0-100 情绪指数，含 7 日/30 日趋势分析 |
| `get_funding_rate` | CoinGecko derivatives | 永续合约资金费率——多头拥挤（看跌信号）还是空头拥挤（看涨信号） |
| `get_cme_gap` | yfinance | CME 比特币期货周末缺口检测（约 77% 历史回补率） |

### 分析哲学

链上分析师的系统提示词要求其深入分析：

- **权力结构**：谁在控制这个资产？他们的激励是什么？是否被少数鲸鱼主导，存在砸盘或拉高出货风险？
- **宏观博弈**：主要司法管辖区的监管态度、国家级持仓、货币政策对机构采用的影响
- **Layer 权力博弈**：L1 公链的统治权争夺、L2 的 MEV 和流动性捕获、跨链资本流动
- **操纵风险评估**：明确评估该资产是否易受拉高出货或 Rug Pull 攻击

---

## LLM 提供商支持

RogueTrader 通过统一的工厂模式支持 **7 个 LLM 提供商**：

| 提供商 | 快速模型 | 深度模型 | API 地址 |
|--------|----------|----------|----------|
| **DeepSeek ★**（默认） | `deepseek-v4-flash` | `deepseek-v4-pro` | `api.deepseek.com` |
| **OpenAI** | GPT-5.4 Mini/Nano, GPT-4.1 | GPT-5.4, GPT-5.2, GPT-5.4 Pro | `api.openai.com` |
| **Anthropic** | Claude Sonnet 4.6, Haiku 4.5 | Claude Opus 4.6, Sonnet 4.6 | `api.anthropic.com` |
| **Google** | Gemini 3 Flash, 2.5 Flash | Gemini 3.1 Pro, 2.5 Pro | Google AI |
| **xAI** | Grok 4.1 Fast | Grok 4, Grok 4.1 Fast | `api.x.ai` |
| **OpenRouter** | NVIDIA Nemotron, GLM 4.5 | 同上（免费层） | `openrouter.ai` |
| **Ollama** | Qwen3, GPT-OSS, GLM-4.7 | 同上（本地运行） | `localhost:11434` |

> **为什么默认用 DeepSeek：** 兼容 OpenAI API 格式，成本远低于 GPT，同时使用 `deepseek-v4-pro` 处理深度推理，使用 `deepseek-v4-flash` 处理常规 Agent 任务。

### 双模型策略

```
深度思考模型（deepseek-v4-pro）
  ├── 研究经理（投资辩论裁判，需要深度综合）
  └── 投资组合经理（风控辩论裁判 + 最终决策）

快速思考模型（deepseek-v4-flash）
  ├── 全部 5 个分析师（数据采集 + 报告生成）
  ├── 多头/空头研究员（辩论发言）
  ├── 交易员（方案综合）
  └── 风控三人组（激进/保守/中立）
```

### Agent 配置文件

RogueTrader 支持一个面向小白用户的 Agent 配置文件：

```bash
configs/agents.yaml
```

你可以在这里调整每个 Agent 使用的模型路线和身份设定，而不需要改 Python 代码：

```yaml
agents:
  onchain_analyst:
    llm:
      tier: quick
      model: deepseek-v4-flash
    prompt:
      identity: |
        You are RogueTrader's Lead On-Chain Analyst specializing in crypto assets.
      focus: |
        Focus on whale behavior, DeFi liquidity, stablecoin flows, derivatives positioning, and manipulation risk.
      style: |
        Explain conclusions in practical trading language, not only raw metrics.
```

不需要配置所有 Agent。缺失字段会自动回退到代码内置默认值。做 A/B 测试时，可以通过 `ROGUETRADER_AGENT_CONFIG=/path/to/agents.yaml` 加载另一份配置。

---

## 记忆系统

RogueTrader 使用基于 **BM25 词法检索的记忆系统**（无需 embedding API，无 token 限制，零成本离线运行）：

```
FinancialSituationMemory（5 个实例）
  ├── bull_memory            → 多头研究员
  ├── bear_memory            → 空头研究员
  ├── trader_memory          → 交易员
  ├── invest_judge_memory    → 研究经理
  └── portfolio_manager_memory → 投资组合经理
```

**工作流程**：每次交易决策后，`Reflector` 分析实际结果（收益/亏损）与各智能体推理的差异，提取经验教训，存储 `(场景, 建议)` 键值对。后续分析时，各智能体通过 BM25 词法匹配检索最相似的 K 个历史场景，辅助当前决策。

---

## 项目结构

```
RogueTrader/
├── main.py                          # 快速入门入口
├── pyproject.toml                    # 包配置
├── requirements.txt                  # 依赖列表
├── README.md                         # 英文说明文档
├── README_zh.md                      # 本文件（中文说明文档）
│
├── roguetrader/                      # 核心 Python 包
│   ├── default_config.py             # 所有默认配置（默认使用 DeepSeek）
│   │
│   ├── graph/                        # LangGraph 编排层
│   │   ├── trading_graph.py          # 主编排器 —— RogueTraderGraph 类
│   │   ├── setup.py                  # GraphSetup —— 构建智能体工作流 DAG
│   │   ├── propagation.py            # Propagator —— 状态初始化与图调用参数
│   │   ├── conditional_logic.py      # ConditionalLogic —— 辩论路由逻辑
│   │   ├── reflection.py             # Reflector —— 交易后结果学习
│   │   └── signal_processing.py      # SignalProcessor —— 从最终报告中提取 BUY/HOLD/SELL
│   │
│   ├── agents/                       # 15 个智能体实现
│   │   ├── analysts/
│   │   │   ├── market_analyst.py     # 技术分析
│   │   │   ├── social_media_analyst.py  # 社交媒体情绪
│   │   │   ├── news_analyst.py       # 新闻与宏观事件
│   │   │   ├── fundamentals_analyst.py  # 公司基本面
│   │   │   └── onchain_analyst.py    # ★ 链上加密分析
│   │   ├── researchers/
│   │   │   ├── bull_researcher.py    # 看涨论证
│   │   │   └── bear_researcher.py    # 看跌论证
│   │   ├── managers/
│   │   │   ├── research_manager.py   # 投资辩论裁判
│   │   │   └── portfolio_manager.py  # 最终决策者
│   │   ├── risk_mgmt/
│   │   │   ├── aggressive_debator.py # 激进风险视角
│   │   │   ├── conservative_debator.py  # 保守风险视角
│   │   │   └── neutral_debator.py    # 中立风险视角
│   │   ├── trader/
│   │   │   └── trader.py             # 交易计划综合
│   │   └── utils/
│   │       ├── agent_utils.py        # 共享工具、语言指令
│   │       ├── agent_states.py       # AgentState、InvestDebateState、RiskDebateState
│   │       ├── memory.py             # BM25 FinancialSituationMemory 记忆系统
│   │       ├── core_stock_tools.py   # 股票价格/基本面工具
│   │       ├── technical_indicators_tools.py  # 标准技术指标工具
│   │       ├── fundamental_data_tools.py      # 财务报表工具
│   │       ├── news_data_tools.py             # 新闻/内部交易工具
│   │       ├── onchain_data_tools.py          # ★ 链上指标工具（9 个工具）
│   │       ├── crypto_indicator_tools.py      # ★ 加密特有指标（5 个工具）
│   │       └── crypto_sentiment_tools.py      # ★ 加密情绪工具（2 个工具）
│   │
│   ├── dataflows/                    # 数据管道层（5 个数据源）
│   │   ├── y_finance.py             # Yahoo Finance —— 股票、基本面、新闻
│   │   ├── alpha_vantage*.py         # Alpha Vantage —— 备选数据供应商
│   │   ├── onchain_data.py           # ★ CoinGecko + DeFiLlama + Blockchain.com + Alternative.me
│   │   ├── crypto_indicators.py      # ★ Pi Cycle、NVT、CME Gap、资金费率计算器
│   │   ├── crypto_sentiment.py       # ★ 聚合加密情绪管道
│   │   ├── interface.py              # 抽象数据供应商接口
│   │   ├── config.py                 # 运行时配置（从 default_config 更新）
│   │   └── utils.py                  # 数据处理工具
│   │
│   └── llm_clients/                  # 多提供商 LLM 抽象层
│       ├── factory.py                # create_llm_client() —— 提供商分发
│       ├── base_client.py            # BaseLLMClient 抽象基类
│       ├── openai_client.py          # OpenAI + DeepSeek + xAI + Ollama + OpenRouter
│       ├── anthropic_client.py       # Anthropic Claude
│       ├── google_client.py          # Google Gemini
│       ├── model_catalog.py          # 统一模型目录（6 个提供商 × 2 种模式）
│       ├── agent_registry.py         # 单个 Agent 的模型路由和身份配置
│       └── validators.py             # 模型验证逻辑
│
├── configs/
│   └── agents.yaml                   # 可选：单个 Agent 的身份和模型配置
│
├── cli/                              # 交互式终端界面
│   ├── main.py                       # 基于 Rich/Typer 的 TUI（8 步向导）
│   ├── models.py                     # 分析师选择类型
│   ├── config.py                     # CLI 配置
│   ├── utils.py                      # 各选择步骤的提示词函数
│   ├── announcements.py              # 社区公告获取
│   ├── stats_handler.py              # LLM/工具调用 token 追踪
│   └── static/welcome.txt            # ASCII 艺术欢迎画面
│
├── my_scripts/                       # ★ 个人运行脚本
│   └── roguetrader1.py              # DeepSeek 配置 + BTC 完整多智能体示例
│
├── my_results/                       # ★ 个人运行输出
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
│   └── 图状态日志/                  # 仅保留为历史旧输出
│
└── tests/                            # 测试文件
    ├── test_google_api_key.py
    ├── test_model_validation.py
    └── test_ticker_symbol_handling.py
```

> **★ = 加密/链上新增功能** —— 这些是 RogueTrader 为加密货币分析流程新增的能力

---

## 安装

### 前置条件
- Python 3.10+
- conda（推荐）或 venv

### 快速安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/RogueTrader.git
cd RogueTrader

# 创建专用环境
conda create -n roguetrader python=3.13 -y
conda activate roguetrader

# 安装（推荐 editable 模式，方便开发）
pip install -e .
```

### API 密钥配置

RogueTrader 需要至少一个 LLM 提供商的 API 密钥。**默认使用 DeepSeek**：

```bash
export DEEPSEEK_API_KEY=你的密钥        # DeepSeek（默认，推荐）
# 或任选以下替代方案：
export OPENAI_API_KEY=...              # OpenAI
export ANTHROPIC_API_KEY=...           # Anthropic
export GOOGLE_API_KEY=...              # Google Gemini
export XAI_API_KEY=...                 # xAI Grok
export OPENROUTER_API_KEY=...          # OpenRouter
```

也可以复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
# 编辑 .env 填入你的密钥
```

### 验证当前工作副本

当前工作副本推荐使用锁定的 `uv` 环境验证：

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q cli roguetrader tests my_scripts main.py
uv run --frozen roguetrader --help
uv run --frozen roguetrader analyze --help
```

当前基线是 **24 个测试通过**。测试覆盖范围包括：模型/Provider 校验、Agent 注册配置、CLI 行为、输出路径规范化、链上分析师接线、Graph 初始化、OpenAI-compatible 客户端配置、本地 Parquet 数据按分析日期截断、ticker 处理、快速入口配置，以及最终交易信号的确定性提取。

---

## 使用方式

### CLI 交互式界面

```bash
# 启动交互式终端界面
roguetrader                    # 安装后的命令
# 或者
python -m cli.main             # 直接调用
```

CLI 将引导你完成 8 步配置向导：
1. 交易标的代码（如 `BTC-USD`、`ETH-USD`、`SPY`、`NVDA`）
2. 分析日期
3. 输出语言（中文/英文）
4. 分析师选择（市场、社交、新闻、基本面、链上）
5. 研究深度（辩论轮数：1-3）
6. LLM 提供商选择
7. 模型选择（快速思考 + 深度思考）
8. 提供商特定配置（推理强度、思考模式）

### 自定义脚本与高级运行方式

当前工作副本在 `my_scripts/` 下还包含一套个人/定制化运行脚本。这类脚本直接调用 Python API，适合在本地复现实验运行，并预先写好配置、分析师选择、交易标的和分析日期。

例如，`my_scripts/roguetrader1.py` 会从项目根目录加载 `.env`，配置 DeepSeek，设置中文输出，默认运行市场、社交、新闻、基本面和链上 5 个分析师，并运行 BTC 分析。推荐从项目根目录运行，确保结构化结果统一写入项目根目录的 `my_results/运行结果/`。

#### 运行前准备

```bash
conda activate roguetrader
```

#### 方式 A：直接运行（输出显示在终端）

```bash
uv run --frozen python my_scripts/roguetrader1.py
```

- 输出会实时显示在终端。
- 本次运行会自动创建 `my_results/运行结果/<时间戳>_<标的>/`。
- 该目录会包含 `运行索引.json`、`报告.md`、`状态.json`、`最终决策.json`、`运行配置.json`、`分段报告/` 和 `终端日志.log`。
- 这是推荐的完整多智能体运行方式。

### 本地 processed/parquet 工作流

离线或本地数据检查可以使用 `my_scripts/roguetrader_local_data.py`。当前运行时数据策略是：

- `raw2` 是上游 source of truth。
- RogueTrader 运行时工具只读取标准化后的 `processed/parquet`。
- 本地 OHLCV 摘要会按请求的分析日期截断，避免历史分析偷看未来数据。

不调用 LLM/API 的 smoke test：

```bash
uv run --frozen python my_scripts/roguetrader_local_data.py \
  --skip-roguetrader \
  --ticker BTC-USD \
  --date 2014-11-30 \
  --source manual_or_investing \
  --timeframe 1d \
  --days 30
```

该命令会在 `my_results/运行结果/<时间戳>_<标的>/` 下生成 `运行索引.json`、`报告.md`、`状态.json`、`最终决策.json` 和 `运行配置.json`。

#### 方式 B：额外保存一份 Shell 转录日志

```bash
uv run --frozen python my_scripts/roguetrader1.py > my_results/rogue_btc_0713.log 2>&1
```

- 终端不会实时显示内容，标准输出和错误信息都会额外写入 `my_results/rogue_btc_0713.log`。
- `>` 会覆盖同名旧文件；如果想追加写入，使用 `>>`。
- 这只是额外的 Shell 转录。标准完整结果仍以 `my_results/运行结果/<时间戳>_<标的>/` 为准。

#### 方式 C：边看终端输出，边额外保存 Shell 转录

```bash
uv run --frozen python my_scripts/roguetrader1.py 2>&1 | tee my_results/rogue_btc_0713.log
```

- 屏幕上可以实时观察运行进度。
- 同时会额外保存一份 Shell 转录到 `my_results/rogue_btc_0713.log`。
- 通常不需要手动 `tee`，因为标准运行目录内已经自动写入 `终端日志.log`。

如果希望 Python 输出尽量不被缓冲，可以使用 `-u`：

```bash
uv run --frozen python -u my_scripts/roguetrader1.py 2>&1 | tee my_results/rogue_btc_0713.log
```

#### 方式 D：后台运行，关闭终端也尽量不中断

```bash
nohup uv run --frozen python -u my_scripts/roguetrader1.py > my_results/rogue_btc_0713.log 2>&1 &
```

查看后台任务：

```bash
jobs
ps aux | grep roguetrader1
```

实时查看输出文件：

```bash
tail -f my_results/rogue_btc_0713.log
```

停止后台任务时，先通过 `ps aux | grep roguetrader1` 找到进程号，再执行：

```bash
kill 进程号
```

#### 常用命令速查

| 场景 | 命令 |
|------|------|
| 完整运行 | `uv run --frozen python my_scripts/roguetrader1.py` |
| 额外保存 Shell 转录 | `uv run --frozen python my_scripts/roguetrader1.py > my_results/报告名.log 2>&1` |
| 边看边额外保存 | `uv run --frozen python -u my_scripts/roguetrader1.py 2>&1 \| tee my_results/报告名.log` |
| 后台运行 | `nohup uv run --frozen python -u my_scripts/roguetrader1.py > my_results/报告名.log 2>&1 &` |
| 查看后台输出 | `tail -f my_results/报告名.log` |
| 停止前台脚本 | `Ctrl+C` |
| 停止后台脚本 | `ps aux \| grep roguetrader1` 后 `kill 进程号` |

#### 定制脚本参数

若要修改运行内容，可编辑 `my_scripts/roguetrader1.py` 中的参数：

- `selected_analysts`：选择分析师，如 `market`、`social`、`news`、`fundamentals`、`onchain`。
- `config["output_language"]`：设置输出语言，如 `Chinese` 或 `English`。
- `config["max_debate_rounds"]`：设置多空研究员辩论轮数。
- `config["max_recur_limit"]`：设置 LangGraph 递归限制，复杂分析可适当调大。
- `rt.propagate("BTC-USD", "2026-07-13")`：修改交易标的和分析日期。

### Python API

#### 快速入门（默认 DeepSeek 配置）

```python
from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.default_config import DEFAULT_CONFIG

# 默认配置已经使用 DeepSeek，无需额外设置
rt = RogueTraderGraph(debug=True, config=DEFAULT_CONFIG.copy())
_, decision = rt.propagate("ETH-USD", "2026-05-19")
print(decision)  # BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL
```

#### 链上分析（纯加密视角）

```python
from roguetrader.graph.trading_graph import RogueTraderGraph
from roguetrader.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["output_language"] = "Chinese"       # 中文报告
config["max_debate_rounds"] = 2             # 2 轮辩论

# 仅使用链上分析师，专注加密原生分析
rt = RogueTraderGraph(
    debug=True,
    config=config,
    selected_analysts=["onchain"]
)
_, decision = rt.propagate("BTC-USD", "2026-05-19")
print(decision)
```

#### 全分析师阵容

```python
rt = RogueTraderGraph(
    debug=True,
    config=config,
    selected_analysts=["market", "social", "news", "fundamentals", "onchain"]
)
_, decision = rt.propagate("ETH-USD", "2026-05-19")
```

#### 切换 LLM 提供商

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

# 本地模型（Ollama）
config["llm_provider"] = "ollama"
config["deep_think_llm"] = "qwen3:latest"
config["quick_think_llm"] = "qwen3:latest"
```

#### 结果反思与学习

```python
rt = RogueTraderGraph(debug=True, config=config)

# 初始分析
_, decision = rt.propagate("ETH-USD", "2026-05-19")

# 实际收益出来后，教给智能体
rt.reflect_and_remember(returns_losses=+3.2)  # +3.2% 收益
# 所有 5 个记忆实例都会被更新，存储经验教训
```

---

## 配置参考

所有配置项定义在 `roguetrader/default_config.py` 中：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `llm_provider` | `deepseek` | LLM 提供商：openai, anthropic, google, xai, openrouter, ollama, deepseek |
| `deep_think_llm` | `deepseek-v4-pro` | 深度思考模型（用于研究经理、投资组合经理） |
| `quick_think_llm` | `deepseek-v4-flash` | 快速思考模型（用于分析师、研究员、交易员、风控辩手） |
| `backend_url` | `https://api.deepseek.com` | API 端点（留空则按提供商自动设置） |
| `output_language` | `English` | 报告语言。设为 `Chinese` 则输出中文。内部辩论始终使用英文以保证推理质量 |
| `max_debate_rounds` | `1` | 多头 vs 空头辩论轮数 |
| `max_risk_discuss_rounds` | `1` | 三方风控辩论轮数 |
| `max_recur_limit` | `20` | LangGraph 递归限制 |
| `data_vendors.onchain_data` | `coingecko` | 链上数据源：coingecko, defillama, blockchain_com |
| `data_vendors.crypto_indicators` | `local` | 加密指标计算方式：local（从数据源本地计算） |
| `data_vendors.crypto_sentiment` | `coingecko` | 加密情绪数据源：coingecko, alternative_me |

### 提供商专属配置

| 配置项 | 可选值 | 适用提供商 |
|--------|--------|------------|
| `google_thinking_level` | `high`, `minimal`, `None` | Google Gemini |
| `openai_reasoning_effort` | `low`, `medium`, `high`, `None` | OpenAI |
| `anthropic_effort` | `low`, `medium`, `high`, `None` | Anthropic Claude |

---

## 数据来源

| 数据源 | 用途 | 认证要求 |
|--------|------|----------|
| **Yahoo Finance** (`yfinance`) | 股票价格、基本面、新闻、技术指标 | 无需（免费） |
| **CoinGecko API** | 加密市场数据、OHLC、交易所成交量、衍生品、社交指标、热门币种 | 无需（免费层） |
| **DeFiLlama API** | 公链 TVL、协议 TVL、稳定币供应 | 无需（免费） |
| **Blockchain.com API** | BTC 链上统计（算力、难度、交易数、活跃地址、矿工收入） | 无需（免费） |
| **Alternative.me API** | 加密恐惧与贪婪指数 | 无需（免费） |
| **Alpha Vantage** | 备选股票数据供应商 | 需要 API 密钥 |

> 所有加密数据源使用**免费层，无需 API 密钥**，但受速率限制。CoinGecko 数据使用 `lru_cache` 缓存以减少重复调用。

---

## 交易标的价格式

RogueTrader 使用 **yfinance 兼容的代码格式**：

| 资产类型 | 格式 | 示例 |
|----------|------|------|
| 加密货币（USD） | `XXX-USD` | `BTC-USD`, `ETH-USD`, `SOL-USD`, `DOGE-USD` |
| 加密货币（USDT） | `XXX-USDT` | `BTC-USDT`, `ETH-USDT` |
| 美股 | `SYMBOL` | `SPY`, `NVDA`, `AAPL`, `TSLA` |
| 国际市场 | `SYMBOL.EXCHANGE` | `CNC.TO`, `7203.T`, `0700.HK` |
| 期货 | `SYMBOL=F` | `GC=F`（黄金）, `CL=F`（原油） |

链上分析师会自动将 yfinance 格式的代码转换为 CoinGecko 的 coin_id（如 `BTC-USD` → `bitcoin`，`ETH-USD` → `ethereum`）。已预置 30+ 种加密货币的映射表，未匹配的代码会回退到 CoinGecko 搜索 API。

---

## 底层实现

### LangGraph 工作流

框架基于 **LangGraph** 构建，使用有向无环图结构：

```
START → [按顺序执行选中的分析师]
           ↓
     多头研究员 ⇄ 空头研究员  （条件循环：辩论轮数控制）
           ↓
     研究经理
           ↓
     交易员
           ↓
     激进派 → 保守派 → 中立派  （条件循环：风控轮数控制）
           ↓
     投资组合经理 → END
```

- 每个分析师节点条件循环到其工具节点，直到采集到所需数据
- 多头/空头研究员交替发言，辩论轮数耗尽后进入研究经理
- 风控三人组轮转，风控轮数耗尽后进入投资组合经理
- 所有状态累积在共享的 `AgentState` TypedDict 中

### 数据流架构

```
用户输入（标的 + 日期）
    │
    ▼
┌─────────────┐    ┌──────────────────┐
│  Dataflows  │───▶│  Agent Tools     │
│  （原始数据） │    │  （LangChain @tool）│
└─────────────┘    └──────────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │   Analysts   │
                   │  （LLM + 工具）│
                   └──────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ Researchers  │
                   │ + Managers   │
                   │ （深度 LLM）  │
                   └──────────────┘
                          │
                          ▼
                    最终交易决策
```

- **Dataflows** 是纯 Python 函数调用外部 API——不依赖 LLM
- **Agent Tools** 用 LangChain 的 `@tool` 装饰器封装 dataflows，供 LLM 函数调用
- **Analysts** 使用快速思考 LLM + 绑定工具来采集和分析数据
- **Managers** 使用深度思考 LLM 来做综合、判断和决策

---

## 本实例的本地定制

这个本地工作副本包含在上游代码库之外的一系列修改：

### 配置变更
- **默认 LLM**：从 OpenAI GPT 改为 **DeepSeek**（`deepseek-v4-pro` + `deepseek-v4-flash`）
- **默认后端 URL**：`https://api.deepseek.com`
- 在 LLM 工厂中新增 `deepseek` 作为识别的提供商（走 OpenAI 兼容 API 路径）

### 加密/链上新增功能
- **链上分析师智能体** (`onchain_analyst.py`) —— 四维分析框架的完整智能体
- **16 个链上/加密工具** 横跨 3 个工具模块：
  - `onchain_data_tools.py` —— 9 个工具（市场数据、鲸鱼活动、DeFi TVL、稳定币流、挖矿统计、Pi Cycle、NVT、恐惧贪婪、资金费率、CME 缺口）
  - `crypto_indicator_tools.py` —— 5 个工具（Pi Cycle、NVT Ratio、CME Gap、Funding Rate、Fear & Greed）
  - `crypto_sentiment_tools.py` —— 2 个工具（聚合加密情绪、热门币种）
- **3 个加密数据流模块**：
  - `onchain_data.py` —— CoinGecko + DeFiLlama + Blockchain.com + Alternative.me 整合（400+ 行代码）
  - `crypto_indicators.py` —— Pi Cycle、NVT Ratio、CME Gap、资金费率计算器
  - `crypto_sentiment.py` —— 聚合加密情绪管道
- **Ticker 映射表**：30+ 加密货币代码 → CoinGecko coin_id 的映射，附搜索 API 回退

### 个人脚本与结果
- `my_scripts/roguetrader1.py` —— DeepSeek 配置 + BTC 完整多智能体演示
- `my_results/` —— 历史分析记录和 JSON 格式的完整状态日志

---

## 依赖包

```
langgraph >= 0.4.8              # 智能体工作流编排
langchain-openai >= 0.3.23      # OpenAI 兼容 LLM 客户端（DeepSeek、xAI 等）
langchain-anthropic >= 0.3.15   # Anthropic Claude 客户端
langchain-google-genai >= 2.1.5 # Google Gemini 客户端
langchain-experimental >= 0.3.4
yfinance >= 0.2.63              # 股票/加密价格数据
stockstats >= 0.6.5             # 技术指标计算
pandas >= 2.3.0                 # 数据处理
pyarrow >= 16.0.0               # 本地 processed/parquet 运行时数据
requests >= 2.32.4              # HTTP 客户端（加密 API）
rank-bm25 >= 0.2.2              # BM25 词法搜索（记忆系统）
rich >= 14.0.0                  # 终端 UI（CLI）
typer >= 0.21.0                 # CLI 框架
questionary >= 2.1.0            # 交互式提示
redis >= 6.2.0                  # 可选：记忆持久化
python-dotenv >= 1.0.0          # 环境变量加载
PyYAML >= 6.0.2                 # Agent YAML 配置
```

---

## 当前可用性判断

目前已经验证：

- 锁定的 `uv` 环境可通过 `uv run --frozen` 使用。
- CLI 命令和帮助路径正常：`roguetrader` / `roguetrader analyze`。
- `RogueTraderGraph` 可以用链上分析师和 OpenAI-compatible Provider 初始化。
- 本地 processed/parquet 摘要会按分析日期截断，避免历史分析中的未来数据泄漏。
- `--skip-roguetrader` 模式下，本地报告和结构化状态文件可以生成，并已统一写入 `my_results/运行结果/` 下的中文路径。
- 直接调用 `RogueTraderGraph.propagate()` 的端到端运行也会写入单独的规范运行目录，包含索引、报告、状态、结构化最终决策、运行配置和分段报告。
- 离线信号评估已统一写入 `my_results/评估结果/` 下的中文路径。
- 最终交易信号中已经包含明确决策时，会用确定性规则提取，不再额外调用一次 LLM。

仍需注意的边界：

- 完整多智能体端到端分析仍需要可用的 LLM API Key 或本地 Ollama endpoint。
- CoinGecko、DeFiLlama、Blockchain.com、Alternative.me、yfinance 等在线数据源可能返回当前/实时数据；它们尚不能保证是严格 point-in-time 的历史回测数据。
- 做历史/离线评估时，本地 processed/parquet 路径更可靠。
- 该项目目前应视为研究分析框架，不是自动实盘执行系统，也不构成投资建议。

---

## 贡献

欢迎贡献——尤其期待以下方向：

- 更多链上数据源（Glassnode、Dune Analytics、Arkham 等）
- 新的加密特有指标
- 多资产组合优化
- 回测系统集成
- Bug 修复和文档改进

## 许可证

Apache License 2.0 — 详见 [LICENSE](LICENSE)。

RogueTrader 是基于 [Tauric Research](https://tauric.ai/) 的 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 项目修改而来的独立衍生版本。上游归属和修改说明详见 [NOTICE](NOTICE)。
