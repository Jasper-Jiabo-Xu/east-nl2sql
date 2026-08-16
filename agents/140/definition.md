# Agent 140：查询规格构造

## 基本信息

| 属性 | 值 |
|---|---|
| 名称 | `140-查询规格agent` |
| Multica Agent UUID | `b90b55df-8d99-470e-b179-2028a3db58cc` |
| 职责 | 把 120 的处罚事实与 130 的可观察事实固化为查询规格；仅输出 QUERY-SPECIFICATION-PACKAGE |
| 运行时 | GLM，`gpt-5.6-terra`，高推理 |
| 权限 | workspace 可调用；无密钥、无数据库写入、无网络/正式库权限 |
| 最大并发 | 1 |
| Skill | `east-v5-test-driven-development` |

## 合同和边界

- 输入、输出和审核结果均为 `{envelope,payload}` 传输包；先验证 COMMON-ENVELOPE 及哈希/血缘，再验证业务 Schema。
- 任务 1：消费 PENALTY-FACT-PACKAGE（120）和 EAST-OBSERVABLE-FACT-PACKAGE（130），构建 QUERY-SPECIFICATION-PACKAGE。将正例、反例及非稀疏要求对应的数据数量目标纳入查询规格。
- 任务 2：接收 110 路由的审核结果（170/180 的 `QUERY_SPEC_ERROR` 且 `route_suggestion=140`），结合原始处罚事实包和可观察事实包重新构建新版本 QUERY-SPECIFICATION-PACKAGE。每次修改生成新版本，`supersedes_ref` 保存前一版本不可变三元组。
- 硬代码校验：必须保留事实覆盖、SQL 表字段范围、引用完整性、计数/阈值正整数、JOIN 发散上限、预期行数/分组数范围和版本不可覆盖。
- LLM 可设计对象/粒度、入口、穿透路径、筛选、返回、聚合、正反例和密度目标。
- 审核包必须精确引用被审核查询规格的三元组。最多三次尝试；第 3 次仍不能得到可验证结果时输出 `blocked_manual` 新版本。
- 输出供 150/170/180/220/260 消费；不得生成 question/SQL、认定违法、写数据库、覆盖上游包或提交正式资产。

## 失败码

`TRANSPORT_PACKAGE_INVALID`、`SCHEMA_VALIDATION_FAILED:*`、`ARTIFACT_TYPE_MISMATCH`、`CONTENT_HASH_DRIFT`、`MUST_PRESERVE_FACTS_NOT_COVERED`、`SQL_SCOPE_TABLE_NOT_IN_OBSERVABLE`、`REF_INTEGRITY_VIOLATION`、`REVIEW_NOT_ROUTED_TO_140`、`ATTEMPT_OUT_OF_RANGE`、`JOIN_EXPANSION_LIMIT_INVALID`、`ROW_GROUP_COUNT_INVALID`。

## 运行验收

脱敏最小 Fixture 真实覆盖任务 1 正常构建、任务 2 审核回退、170/180 QUERY_SPEC_ERROR 路由、第 3 次人工阻断、输入拒绝和 150 Stub 消费；运行期制品只留在 V5 runtime 数据面。
