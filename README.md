# RogueTrader

<p align="center">
  <strong>多智能体市场研究、每日自动分析与结构化结果发布</strong>
</p>

<p align="center">
  <a href="README.md">中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="docs/control-panel.md">控制面板</a> ·
  <a href="docs/production-development.md">生产运维</a>
</p>

RogueTrader 将多智能体投资研究流程和项目自己的生产调度器结合起来：每天按设定时间串行分析多个标的，生成结构化决策，先写入本地 CSV，再独立推送到飞书群和飞书电子表格。

> 这是研究分析系统，不是自动实盘交易系统，也不构成投资建议。

![RogueTrader 控制面板——脱敏生产状态示例](docs/assets/control-panel-production.png)

_图片为脱敏示例状态，不包含真实凭据、个人路径或实际决策内容。_

## 核心能力

- **多智能体研究**：市场、社交、新闻、基本面和链上分析师协作，多空研究员与风控角色进行结构化辩论。
- **每日自动调度**：本机控制面板管理总开关、北京时间、分析标的和每个标的的独立开关。
- **多标的串行执行**：同一批标的依次运行，避免模型额度和数据源并发争用。
- **CSV-first 发布**：每个完整结果先幂等追加到本地 CSV；后续通知只读取刚落盘的同一条记录。
- **飞书双通道**：向飞书群发送简短卡片与完整决策摘要，同时向普通电子表格追加一行。
- **失败隔离**：消息或表格失败只重试对应通道，不会重新触发付费分析。
- **不可变生产版本**：生产环境运行固定 Git 标签、锁定依赖和独立虚拟环境，旧版本始终可回滚。
- **开发/生产隔离**：源码、状态、缓存、结果目录和 `.env` 分离，开发不会覆盖生产运行态。

## 工作流程

```text
本机控制面板
  └── Asia/Shanghai 每日调度
        └── 多标的串行分析
              └── 运行索引.json（完成标记）
                    └── 最终决策.json
                          └── 每日决策.csv（唯一事实来源）
                                ├── 本地消息包
                                ├── 飞书群通知
                                └── 飞书电子表格新行
```

发布器只消费完整结果。CSV、飞书消息和飞书表格分别记录状态，任何通知失败都不会反向影响分析任务。

## 快速操作

### 生产控制面板

```bash
ops/production-control-panel-service start
ops/production-control-panel-service status
ops/production-control-panel-service stop
```

启动后访问 <http://127.0.0.1:8765>。面板只监听本机回环地址；后台服务需要保持运行，每日任务才会按时触发。

### 手动分析

```bash
uv run --frozen python my_scripts/roguetrader0.py \
  --ticker BTC-USD \
  --date 2026-09-12 \
  --no-debug
```

手动入口支持修改标的、日期、分析师、模型和辩论轮数。完整参数见[开发与手动运行](docs/development.md)。

### 查看生产版本

```bash
./ops/release_manager.py status
./ops/release_manager.py list
.runtime/bin/run-version --check
```

发布与回滚流程见[生产/开发双模式](docs/production-development.md)。

## 结果结构

```text
my_results/
├── 运行结果/
│   └── <时间戳>_<标的>/
│       ├── 运行索引.json
│       ├── 最终决策.json
│       ├── 报告.md
│       ├── 状态.json
│       ├── 运行配置.json
│       ├── 终端日志.log
│       └── 分段报告/
└── 汇总/
    ├── 每日决策.csv
    └── 消息/
```

同一天的多个标的各占 CSV 一行，并通过 `event_id` 幂等去重。详细协议见[本地结果发布器](docs/local-publisher.md)。

## 生产与开发

| | 生产模式 | 开发模式 |
|---|---|---|
| 代码 | `.runtime/production/current` 指向不可变版本 | `.runtime/development/worktree` 的 `develop` 分支 |
| 环境 | 每个版本独立 `.venv` | 开发专用 `.venv` |
| 配置 | `.runtime/production/.env` | `.runtime/development/.env` |
| 状态 | `.runtime/production/control-panel` | 开发 worktree 内 `.runtime/control-panel` |
| 结果 | 项目根目录 `my_results/` | 开发 worktree 内 `my_results/` |

两个面板默认使用相同本机端口，因此同一时间只运行一个。生产状态和发布数据库不位于版本源码目录中，升级后会继续沿用。

## 安全边界

- `.env`、密钥、运行结果、CSV、SQLite 状态库和日志均被 Git 忽略。
- 控制面板不返回密钥，也不展示完整分析报告或决策正文。
- 飞书凭据只从当前运行环境的 `.env` 读取。
- 首次启用发布通道只建立历史基线，默认不回发旧结果。
- 对飞书表格的数据行排序或筛选不影响按 `event_id` 去重；不要移动表头或修改协议列。

## 分析引擎概览

```text
分析师团队
  市场 · 社交 · 新闻 · 基本面 · 链上
        ↓
多头研究员 ↔ 空头研究员 → 研究经理
        ↓
交易员
        ↓
激进风控 ↔ 保守风控 ↔ 中立风控 → 投资组合经理
        ↓
BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL
```

引擎基于 LangGraph，支持 DeepSeek、OpenAI、Anthropic、Google、xAI、OpenRouter 和本地 Ollama。角色、数据工具和记忆机制见[分析引擎](docs/analysis-engine.md)，模型及数据源见[模型与数据源](docs/providers-and-data.md)。

## 首次开发安装

需要 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --frozen
cp .env.example .env
./ops/release_manager.py bootstrap
./ops/release_manager.py setup-development
```

只把真实凭据写入本地 `.env`，不要提交。完整步骤见[开发与手动运行](docs/development.md)。

## 文档导航

- [控制面板](docs/control-panel.md)：开关、时间、标的和发布通道。
- [本地结果发布器](docs/local-publisher.md)：CSV-first、幂等、重试和飞书协议。
- [生产/开发双模式](docs/production-development.md)：不可变发布、激活与回滚。
- [分析引擎](docs/analysis-engine.md)：智能体角色、辩论流程和决策输出。
- [模型与数据源](docs/providers-and-data.md)：Provider、行情、链上数据和标的格式。
- [开发与手动运行](docs/development.md)：安装、测试、CLI 与本地数据模式。
- [变更记录](CHANGELOG.md)：生产版本的主要变化。

## 验证

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q cli roguetrader tests my_scripts main.py
```

当前测试覆盖调度配置、控制面板安全策略、运行目录、信号提取、CSV 幂等、历史基线、飞书消息、飞书表格以及发布重试。

## 许可证与来源

Apache License 2.0，详见 [LICENSE](LICENSE)。

RogueTrader 基于 Tauric Research 的 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 演进而来；归属与修改说明见 [NOTICE](NOTICE)。
