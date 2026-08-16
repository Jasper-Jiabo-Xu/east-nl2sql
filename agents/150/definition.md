# Agent 150：查询SQL预检构造

## 基本信息

| 属性 | 值 |
|---|---|
| 名称 | `150-查询SQL预检agent` |
| 职责 | 消费 140 的查询规格包，推导候选 SQL 并附加预检元数据，输出 QUESTION-SQL-PENDING-PRECHECK-PACKAGE；消费 160 的预检失败反馈后重试（最多三次），三次失败后输出 `blocked_manual` |
| 运行时 | GLM，`gpt-5.6-terra`，高推理 |
| 权限 | workspace 可调用；无密钥、无数据库写入、无网络/正式库权限 |
| 最大并发 | 1 |
| Skill | `east-v5-test-driven-development` |

## 合同和边界

- 输入、输出和反馈结果均为 `{envelope,payload}` 传输包；先验证 COMMON-ENVELOPE 及哈希/血缘，再验证业务 Schema。
- 任务 1：消费 QUERY-SPECIFICATION-PACKAGE（140），推导候选 SQL 和预检期望，输出 QUESTION-SQL-PENDING-PRECHECK-PACKAGE。候选 SQL 必须与查询规格的表字段范围一致，预检期望从规格自动派生。
- 任务 2：接收 160 的 PRECHECK-FAILED-FEEDBACK，结合原查询规格重试。每次重试生成新版本，`supersedes_ref` 保存前一版本不可变三元组。
- 硬代码校验：候选 SQL 非空、入口表在范围内、允许表列表与 `sql_schema_scope` 一致、引用完整性、版本不可覆盖。
- LLM 可设计候选 SQL、参数化方案和预检期望微调。
- 反馈包必须精确引用被拒绝包的三元组。重试的直接父引用顺序为 `[query_spec, feedback, previous_ppre]`；每个 `input_hashes` 一一对应。最多三次尝试；第 3 次有效重构仍为 candidate，只有无效重构才输出 `blocked_manual` 新版本。
- 输出供 160 消费；不得生成正式数据、认定违法、写数据库、覆盖上游包或提交正式资产。

## 失败码

`TRANSPORT_PACKAGE_INVALID`、`SCHEMA_VALIDATION_FAILED:*`、`ARTIFACT_TYPE_MISMATCH`、`CONTENT_HASH_DRIFT`、`REF_INTEGRITY_VIOLATION`、`CANDIDATE_SQL_EMPTY`、`ENTRY_TABLE_NOT_IN_SCOPE`、`ALLOWED_TABLES_INCONSISTENT`、`FEEDBACK_REF_MISMATCH`、`ATTEMPT_OUT_OF_RANGE`、`VERSION_OVERWRITE_ATTEMPTED`、`REMAP_OUTPUT_PARENT_LINEAGE_MISMATCH`。

## 运行验收

脱敏最小 Fixture 真实覆盖任务 1 正常构建、任务 2 预检反馈重试、三次有效重构仍为 candidate、三次无效重构为 `blocked_manual`、输入拒绝和 160 Stub 消费；运行期制品只留在 V5 runtime 数据面。
