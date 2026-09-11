# 开发与手动运行

## 前置条件

- Python 3.10+
- `uv`
- 至少一个可用的 LLM Provider，或本地 Ollama

## 初始化项目

```bash
uv sync --frozen
cp .env.example .env
```

只在本地 `.env` 中填写真实凭据。`.env` 已被 Git 忽略，不应通过命令行输出或复制到文档。

验证基础环境：

```bash
uv run --frozen roguetrader --help
uv run --frozen roguetrader analyze --help
uv run --frozen python -m unittest discover -s tests -v
```

## 创建隔离开发环境

项目根目录作为稳定控制面，功能开发位于独立 worktree：

```bash
./ops/release_manager.py bootstrap
./ops/release_manager.py setup-development
ops/run-development --check
```

开发路径为 `.runtime/development/worktree`，环境文件为 `.runtime/development/.env`。

## 开发控制面板

```bash
cd .runtime/development/worktree
ops/control-panel-service start
ops/control-panel-service status
ops/control-panel-service stop
```

生产和开发面板默认都监听 `127.0.0.1:8765`，同一时间只运行一个。

## 手动完整分析

```bash
uv run --frozen python my_scripts/roguetrader0.py \
  --ticker ETH-USD \
  --date 2026-09-12 \
  --analysts market,onchain \
  --max-debate-rounds 1 \
  --no-debug
```

常用参数：

- `--ticker`：yfinance 兼容标的；
- `--date`：`YYYY-MM-DD`；
- `--analysts`：逗号分隔的分析师；
- `--output-language`：`Chinese` 或 `English`；
- `--max-debate-rounds`：多空辩论轮数；
- `--max-recur-limit`：LangGraph 递归上限；
- `--quick-model`、`--deep-model`：本次运行的模型覆盖。

不要为了保存日志额外改脚本。标准运行目录已经包含 `终端日志.log`。

## CLI

```bash
uv run --frozen roguetrader
```

交互式 CLI 会引导选择标的、日期、语言、分析师、研究深度、Provider 和模型。

## 本地 processed/parquet 模式

历史或离线检查优先使用标准化的 `processed/parquet` 数据，并按请求日期截断，避免读取未来数据：

```bash
uv run --frozen python my_scripts/roguetrader_local_data.py \
  --skip-roguetrader \
  --ticker BTC-USD \
  --date 2014-11-30 \
  --source manual_or_investing \
  --timeframe 1d \
  --days 30
```

`--skip-roguetrader` 不调用 LLM，适合检查本地数据路径和输出协议。

## 测试

```bash
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q cli roguetrader tests my_scripts main.py
node --check roguetrader/control_panel/static/app.js
git diff --check
```

需要真实凭据或产生外部费用的测试必须显式执行，不属于默认测试集。

## 代码入口

| 路径 | 用途 |
|------|------|
| `my_scripts/roguetrader0.py` | 带参数的手动分析入口 |
| `my_scripts/daily_analysis.py` | 每日分析业务入口 |
| `my_scripts/roguetrader1.py` | 兼容已有调度调用的稳定入口 |
| `roguetrader/control_panel/` | 本机控制面板和调度器 |
| `roguetrader/publisher/` | CSV-first 发布与飞书通道 |
| `ops/release_manager.py` | 不可变版本安装、激活和回滚 |
