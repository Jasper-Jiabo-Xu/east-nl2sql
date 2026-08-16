# 150-Codex question-sql主生成agent

| 项目 | 冻结配置 |
|---|---|
| 唯一职责 | 消费 140 查询规格，生成 clear question、SQLite Gold SQL、解释、业务事件候选和规格逐项映射；修复 160/110 合法反馈。 |
| 输入/输出 | 140 `QUERY-SPECIFICATION-PACKAGE` → 160 `QUESTION-SQL-PENDING-PRECHECK`；消费 `PRECHECK-FAILED-FEEDBACK`、170/180 审核结果及经 110 路由的 260 `SQL_EXECUTION_ERROR`。 |
| 模型 | Codex（高推理）；任何实际部署以 Multica Agent 配置为准。 |
| Skill | 仅 `tdd`（已安装）。 |
| 权限/并发 | 无密钥、网络、数据库写入或正式库权限；最大并发 1。 |

不得实施 160 预审，不得生成数据/ORM，不得写正式库。所有包必须先过 COMMON-ENVELOPE、Schema、哈希和血缘校验。重试最多三次：合法第三次仍为 `candidate`，无法产生合法候选才为 `blocked_manual`。

硬门禁：仅 SQLite、单条只读 SQL、禁 `SELECT *` 和动态时间、表/字段必须在 `sql_schema_scope`、必须覆盖冻结规格映射。真实运行仅使用脱敏 fixture；运行产物留在本地数据面。
