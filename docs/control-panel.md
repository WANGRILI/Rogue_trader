# RogueTrader 控制面板

控制面板是 RogueTrader 自己的本地调度与结果发布服务。开发和生产使用不同代码、配置、状态库及结果目录，互不覆盖。

## 能力与行为

- 总开关：开启或关闭项目自己的每日调度。
- 每日时间：使用 `Asia/Shanghai`（北京时间）的 24 小时制时间。
- 标的管理：新增、移除，以及分别启用或停用每个标的。
- 执行状态：显示下一次执行、当前标的和最近结果。
- 串行执行：同一批标的逐个运行，避免争抢资源或 API 限额。
- 不补跑：新启用或重启服务时，只安排下一个未来时刻。
- 结果发布：完整分析结束后先生成并回读汇总 CSV，本地消息包、飞书群通知和飞书表格只消费这条已落盘记录。

新增或删除标的只影响下一批任务；已经启动的批次使用启动时的标的快照。

## 生产模式

从稳定项目根目录管理生产服务：

```bash
ops/production-control-panel-service start
ops/production-control-panel-service status
ops/production-control-panel-service stop
```

生产服务始终加载 `.runtime/production/current` 指向的不可变版本，结果写入项目根目录 `my_results/`。生产调度状态位于 `.runtime/production/control-panel/`，发布状态位于 `.runtime/production/publisher/`；它们不会因版本升级而丢失。

启动后访问 <http://127.0.0.1:8765>。服务必须保持运行，设定的每日任务才会自动执行。

## 开发模式

开发服务从隔离 worktree 启动：

```bash
cd .runtime/development/worktree
ops/control-panel-service start
ops/control-panel-service status
ops/control-panel-service stop
```

开发状态和结果都保留在开发 worktree 内。生产与开发面板默认使用同一个本机端口，因此同一时间只运行一个；发布前应先停止开发面板，再启动生产面板。

如需在终端前台调试：

```bash
ops/run-control-panel
```

## 数据与安全边界

控制面板只监听本机回环地址，HTTP 服务不提供外网监听选项。它只公开固定静态资源和控制 API，不提供任意文件读取能力。

状态目录包含：

- `config.json`：开关、时间和标的列表；
- `runs.json`：最近执行状态；
- `logs/`：各标的的进程日志；
- `service.log`、`service.pid`：后台服务日志和进程号；
- 相邻的 `publisher/publisher.sqlite3`：投递幂等与重试状态。

整个 `.runtime/` 已被 Git 忽略；`.env`、API 密钥、分析结果和运行日志都不会进入提交。页面不读取或展示 `.env`、密钥或分析报告正文。

“飞书群通知”和“飞书表格同步”与每日分析总开关相互独立。凭据缺失或连接测试失效时无法开启。推送失败只重试推送，不会重复分析。

飞书表格每个标的每天追加一行，并按 `event_id` 查重。排序和筛选数据行不影响追加或去重，但不能移动表头、修改协议列或改写 `event_id`。

结果格式、CSV-first 协议和手动脚本见 [`local-publisher.md`](local-publisher.md)。

## 实际执行命令

每个启用标的会在当前环境中依次执行：

```bash
.venv/bin/python my_scripts/roguetrader0.py \
  --ticker <SYMBOL> \
  --date <北京时间的计划日期> \
  --no-debug
```

这会调用真实数据源和模型 API，并把分析结果写到当前环境的 `my_results/`。打开总开关前，应确认当前环境的密钥、模型和额度配置正确。
