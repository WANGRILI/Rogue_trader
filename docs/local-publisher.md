# RogueTrader 本地结果发布器

结果发布器与分析流程相互独立。它只在检测到完整结果后读取结构化决策，先追加本地 CSV，再让本地消息包、飞书群消息和飞书普通电子表格消费刚刚落盘的同一条 CSV 记录。所有发布步骤都不调用 LLM。

```text
完整运行结果 → CSV 写入并 fsync → 按 event_id 从 CSV 回读
                                      ├── 本地消息包
                                      ├── 飞书群消息
                                      └── 飞书电子表格
```

## 输出

默认输出位于当前环境自己的 `my_results/汇总/`：

```text
my_results/汇总/
├── 每日决策.csv
└── 消息/
    └── <event_id>.json
```

投递状态位于当前运行环境的 `publisher/publisher.sqlite3`；生产模式使用 `.runtime/production/publisher/publisher.sqlite3`。生成结果、CSV、消息包、状态库和锁文件均已被 Git 忽略。

CSV 每个完成的分析占一行，包含：

```text
event_id, run_id, trade_date, generated_at, ticker, action,
action_source, confidence, risk_level, time_horizon, entry_plan,
stop_loss, take_profit, key_reasons, invalidations,
decision_summary, source_schema_version, publication_schema_version
```

字符串按标准 CSV 规则转义，并防护以 `= + - @` 开头的公式注入内容。

## 完整性和幂等性

- 只有同时存在 `运行索引.json` 和 `最终决策.json` 的目录才可发布。
- `运行索引.json` 是写出流程最后创建的完成标记。
- 索引与决策中的标的、日期和动作必须一致。
- `INCOMPLETE` 结果不会发布。
- `event_id` 由运行 ID 和决策内容稳定计算。
- CSV 是唯一的本地发布事实来源。写入并 `fsync` 成功后，发布器会按 `event_id` 回读并校验这条逻辑记录；下游通道不再直接消费原始 JSON。
- CSV 写入前会检查 `event_id`，重复执行脚本不会产生重复行。
- 每个本地消息包使用 `event_id` 作为文件名，重复执行不会重复创建。
- CSV 失败时，本地消息、飞书群消息和飞书表格均不会执行。CSV 成功后，三个下游目标分别记录状态和重试，任一失败都不会回滚 CSV 或重新运行付费分析。
- SQLite 已登记的下游失败任务可以只从 CSV 恢复，即使对应原始结果文件后来不可用，也不需要再次运行分析。
- 自动服务只处理它从完整运行结果登记的新事件。手工增加、删除或修改 CSV 行不会自动触发推送；临时历史维护由人工显式处理。
- 决策摘要允许包含换行，因此不得用 `tail -n 1` 等物理行方式消费 CSV，必须使用标准 CSV 解析器并按 `event_id` 定位逻辑记录。

## 脚本

以下命令在隔离开发工作树中执行。

预览消息，不写任何文件：

```bash
ops/preview-result-message my_results/运行结果/<运行目录>
```

追加 CSV，并从已落盘 CSV 生成本地消息包：

```bash
ops/publish-result my_results/运行结果/<运行目录>
```

脚本会识别输入目录所属的 `my_results`，默认把汇总写回同一个结果根目录。因此显式传入旧生产结果时，不会把它混入开发环境的 CSV；传入开发结果时也不会写入生产目录。

`--message-only` 为兼容旧命令名称而保留，但仍会先确保对应 CSV 记录已经落盘；不存在绕过 CSV 直接生成消息的模式。

只更新 CSV：

```bash
ops/update-decision-csv my_results/运行结果/<运行目录>
```

扫描新增结果：

```bash
ops/publish-new-results
```

首次扫描只把已有结果登记为历史基线，不会突然推送全部历史日报。只有显式传入 `--backfill` 才会发布历史结果：

```bash
ops/publish-new-results --backfill
```

也可以指定另一个结果根目录：

```bash
ops/publish-new-results --results-root /path/to/my_results
```

## 飞书群通知

在目标飞书群中添加“自定义机器人”，安全设置选择“加签”。把 Webhook 和加签密钥只写入隔离开发工作树的 `.env`：

```dotenv
ROGUETRADER_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
ROGUETRADER_FEISHU_SIGNING_SECRET=...
```

不要把真实值写入 `.env.example`、命令参数、聊天消息或 Git。控制面板只返回“已配置/未配置”，不会返回以上值。

发送不含分析结果的测试卡片：

```bash
ops/test-feishu-notification
```

测试成功后，可在控制面板中开启自动推送。首次开启只为现有结果建立飞书基线，不会补发历史消息。也可以显式发送一个完整结果；同一 `event_id` 已成功发送时不会重复发送：

```bash
ops/send-feishu-result my_results/运行结果/<运行目录>
```

飞书卡片从已落盘 CSV 记录生成，保留决策摘要的换行和 Markdown 层次，最长 6,000 字。飞书失败不会影响本地 CSV。自动重试依次等待 1 分钟、5 分钟、15 分钟、1 小时、3 小时和 6 小时，并在首次失败 24 小时后停止，以免发送过期的每日决策。

日常发送保持幂等。如果明确需要重新发送一份已成功投递的结果，可使用：

```bash
ops/send-feishu-result my_results/运行结果/<运行目录> --force
```

## 自动运行

控制面板服务会同时启动一个独立的本地发布轮询线程，每 15 秒检查一次当前环境的 `my_results/运行结果/`。检测到新完成结果后先生成 CSV 记录，再执行下游投递；它不会监视或自动上传人工编辑的 CSV 行。分析失败或发布失败不会导致另一方重复执行。

控制面板显示本地发布状态，以及两个飞书通道是否已配置、是否启用、最近测试、最近送达和待重试数量。它不读取或返回 Webhook、加签密钥、App Secret、表格定位信息或消息正文。

## 飞书电子表格同步

这里使用普通飞书电子表格，而不是每天上传并覆盖一个 CSV 文件。每个新 CSV 逻辑记录按标的追加一行，所以同一交易日的 `BTC-USD`、`ETH-USD` 等标的会自然形成多行，便于筛选、排序和导出 CSV。本地 `每日决策.csv` 始终保留为审计副本。

表格列优先展示 `trade_date`、`ticker`、`action`、置信度、风险和执行计划；`run_id`、`event_id` 与协议版本位于后部。发布器同时使用本地 SQLite 状态和远端 `event_id` 列去重。即使本地状态库丢失，已经存在于目标表格的事件也不会再次追加。

为 RogueTrader 使用独立的飞书自建应用，并在开发工作树的 `.env` 中配置：

```dotenv
ROGUETRADER_FEISHU_APP_ID=cli_...
ROGUETRADER_FEISHU_APP_SECRET=...
ROGUETRADER_FEISHU_SPREADSHEET_TOKEN=...
ROGUETRADER_FEISHU_SHEET_ID=...
```

电子表格 URL 通常类似 `https://.../sheets/<spreadsheet_token>?sheet=<sheet_id>`，分别取路径中的 spreadsheet token 和查询参数中的 sheet ID。应用需要开启“查看、评论、编辑和管理电子表格”（`sheets:spreadsheet`）权限，并需要能访问目标文档。权限变更后要按飞书后台要求发布或启用新的应用版本并完成管理员审批；然后在目标电子表格的分享设置中把该应用添加为可编辑协作者，最后重新测试连接。

测试连接并在空白表格中初始化固定表头，不追加测试数据：

```bash
ops/test-feishu-sheet
```

测试成功后可在控制面板开启“飞书表格同步”。首次开启只为现有结果建立该通道的基线，不自动回填历史。若需要明确同步某个已有结果：

```bash
ops/sync-feishu-sheet-result my_results/运行结果/<运行目录>
```

普通同步保持幂等；`--force` 只重置本地投递状态，远端仍会按 `event_id` 阻止重复行：

```bash
ops/sync-feishu-sheet-result my_results/运行结果/<运行目录> --force
```

飞书表格同步与群消息分别重试，任何失败都不会重新运行分析。表格通道沿用 1 分钟、5 分钟、15 分钟、1 小时、3 小时和 6 小时的退避，并在首次失败 24 小时后停止。

### 表格中的人工操作

- 可以安全地对第一行以下的数据区域排序、筛选、冻结或调整样式；远端查重按 `event_id` 精确匹配，不依赖行号或当前排序。
- 新追加记录可能暂时显示在数据区底部，需要时可以再次应用排序；这不表示同步失败。
- 不要把第一行表头排入数据区，也不要重命名、删除或调换协议列。
- 不要修改 `event_id` 单元格，否则会破坏远端幂等判断。

官方接口参考：[获取 tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)、[追加数据](https://open.feishu.cn/document/server-docs/docs/sheets-v3/data-operation/append-data)。
