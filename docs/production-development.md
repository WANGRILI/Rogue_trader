# RogueTrader 生产/开发双模式

[中文](production-development.md) · [English](production-development.en.md)

## 架构边界

RogueTrader 自己负责调度、分析、结果发布和版本管理，不依赖外部调度系统。项目根目录是稳定控制面，开发代码位于独立 Git worktree，生产代码来自不可变版本：

```text
生产控制面板
  -> .runtime/production/current
  -> .runtime/releases/<version>  （固定提交 + 独立 .venv）
  -> 项目根目录 my_results/       （生产结果与汇总 CSV）

开发控制面板
  -> .runtime/development/worktree （develop 分支 + 独立 .venv）
  -> 开发 worktree/my_results/     （开发结果与汇总 CSV）
```

`.runtime/`、密钥、运行数据和缓存均不进入 Git。每个已发布版本保留自己的源码、依赖锁和虚拟环境，因此后续开发不会改变老版本。

## 当前状态与运行

```bash
./ops/release_manager.py status
./ops/release_manager.py list
.runtime/bin/run-version --check
ops/production-control-panel-service status
```

生产控制面板只监听 `127.0.0.1:8765`，调度配置位于 `.runtime/production/control-panel/`，投递状态位于 `.runtime/production/publisher/`：

```bash
ops/production-control-panel-service start
ops/production-control-panel-service stop
```

开发运行：

```bash
ops/run-development --ticker BTC-USD --date 2026-09-09
```

开发结果和缓存位于开发 worktree 内，不会写入生产目录。

显式校验或执行旧版本：

```bash
.runtime/bin/run-version --version v1.0.0 --check
.runtime/bin/run-version --version v1.0.0
```

第二条会发起真实分析并消耗外部 API 配额。

## 新版本发布

所有新功能在 `.runtime/development/worktree` 的 `develop` 分支开发。测试完成后创建新的生产标签和不可变环境：

```bash
git tag -a production/<version> <candidate-commit> -m "RogueTrader production <version>"
./ops/release_manager.py install <version> production/<version>
./ops/release_manager.py verify <version> --import-check
.runtime/bin/run-version --version <version> --check
./ops/release_manager.py activate <version>
ops/production-control-panel-service start
```

`install` 会创建固定提交的 worktree、独立 `.venv` 和独立数据缓存。`activate` 先验证版本，再原子切换 `current` 指针，并把原版本保存在 `previous`。生产控制面板的调度配置和投递数据库不在版本目录中，因此升级后保持连续。

## 回滚

先停止生产控制面板，再切回上一版并重新启动：

```bash
ops/production-control-panel-service stop
./ops/release_manager.py rollback
ops/production-control-panel-service start
.runtime/bin/run-version --check
```

也可以激活任一已安装版本：

```bash
./ops/release_manager.py activate v1.0.0
```

回滚只切换项目内的版本指针，不删除新版本、历史结果或投递记录。

## 密钥和数据

- 项目根目录 `.env` 保持为本地基线，不进入 Git。
- 生产发布读取 `.runtime/production/.env`。
- 开发 worktree 读取 `.runtime/development/.env`，与生产文件互不覆盖。
- 显式同步命令为 `sync-production-env` 和 `sync-development-env`，覆盖前自动备份。
- 生产结果与 `my_results/汇总/每日决策.csv` 位于项目根目录；每个版本拥有独立数据缓存。
- 飞书凭据只从当前环境的 `.env` 读取，控制面板只返回配置状态，不返回密钥值。

## 兼容性约定

- 版本号只能安装一次；修复必须使用新版本号，禁止覆盖旧版本。
- 发布目录内的受跟踪源码被修改后，启动器拒绝执行。
- 每个版本保留自己的源码、`uv.lock` 和 `.venv`。
- `my_scripts/roguetrader1.py` 是稳定兼容入口，不承载日常业务迭代。
- 每日分析入口是 `my_scripts/daily_analysis.py`；生产面板按标的调用版本内的 `roguetrader0.py`。
