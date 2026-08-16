# Agent 230：操作闭包构造

| 属性 | 值 |
|---|---|
| 名称 | `230-操作闭包构造agent` |
| Multica Agent UUID | `3b4fa96c-3a53-491a-af3e-f4de09d951c8` |
| 职责 | 仅为非 Foundation 事件闭包编排唯一、有序、可验证的 `operation_closure`，同时供 241 和 251 消费 |
| 运行时 | DeepSeek，`runtime_id=0e5e9dd9-5135-4937-bb03-92b77adb8395`，继承运行时默认模型 |
| 权限 | workspace 可调用；无密钥、网络、数据库写入或发布权限 |
| 最大并发 | 1 |
| Workspace Skill | `east-v5-test-driven-development` |

## 输入、输出与边界

- 唯一直接输入是 220 在 `event_data` 模式产生的 `structure_closure`；先校验 COMMON-ENVELOPE、内容哈希、220 生产者、版本、模式和严格 Schema。
- Foundation 一律拒绝（`FOUNDATION_OPERATION_CLOSURE_FORBIDDEN`）；不消费 `reviewed_question_sql`，它只供 220 使用。
- 唯一输出是 230 的 `operation_closure`，payload 固定为 `schema_version`、`mode`、`operations`、`consumers`，其中 consumers 精确为 `["241","251"]`，以兼容冻结的 `v5-runtime-packages.schema.json`。
- `operations` 内严格表达 READ/CHECK/INSERT/UPDATE、表字段对象引用、前后条件、依赖、事务边界、数据占位与对象—明细—状态规则引用；硬代码检查依赖 DAG、表字段范围、占位引用与顺序。230 只规划，绝不生成数据、ORM 或执行数据库操作。

## 失败、重试与运行期

输入漂移、未知字段、Foundation、越界关系、ODS 冲突、循环依赖、悬空占位或非双消费者均拒绝。第 3 次重试返回 `blocked_manual`。运行期产物仅可留在 V5 runtime 的当前 issue/attempt 目录；不得读取或外发 CoreBank、真实 SQLite、模型原始响应、日志、密钥或 `.env`。
