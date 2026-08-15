# Agent 130：EAST 可观察事实构造

## 基本信息

| 属性 | 值 |
|---|---|
| 名称 | `130-EAST可观察事实构造agent` |
| Multica Agent UUID | `32074806-be1d-45e0-becb-a03bb3737750` |
| 职责 | 把 120 的处罚事实经 000 冻结资产映射为 EAST 可观察事实；仅输出风险筛查边界 |
| 运行时 | Codex，`gpt-5.6-terra`，高推理 |
| 权限 | workspace 可调用；无密钥、无数据库写入、无网络/正式库权限 |
| 最大并发 | 1 |
| Skill | `east-v5-test-driven-development` |

## 合同和边界

- 输入、输出和审核结果均为 `{envelope,payload}` 传输包；先验证 COMMON-ENVELOPE 及哈希/血缘，再验证业务 Schema。
- 任务 1 只生成 `CONSTRAINT-QUERY-REQUEST`，`target_asset_types` 仅取冻结枚举；`previous_request_refs` 仅取不可变三元组。
- 任务 2 只消费 000 的 `CONSTRAINT-ASSET-PACKAGE`；没有命中时以 `NO_EAST_ASSET` 显式说明不可观察，不伪造表或字段。
- 任务 3 仅接受完整的 170/180 审核结果（`OBSERVABLE_MAPPING_ERROR` 且 route=130），扩大范围调用 000；第 3 次未命中输出合规的 `blocked_manual` 新版本。
- 审核包必须精确引用被审核的 observable 三元组；000 结果必须引用当前 request 三元组，并与其 run/qa/trace/attempt 一致。只有显式事实 ID + source_ref + 约束证据三者闭合的资产记录可提高覆盖率。
- `east-observable-fact-manifest` 将输出三元组、直接输入、run/qa/trace、attempt/status 与 runtime 相对定位固化；拒绝越界定位。
- 输出供 140/150 消费；不得生成 question/SQL、认定违法、写数据库、覆盖上游包或提交正式资产。

## 失败码

`TRANSPORT_PACKAGE_INVALID`、`SCHEMA_VALIDATION_FAILED:*`、`ARTIFACT_TYPE_MISMATCH`、`CONTENT_HASH_DRIFT`、`REVIEW_NOT_ROUTED_TO_130`、`ATTEMPT_OUT_OF_RANGE`、`PENALTY_FACTS_EMPTY`。

## 运行验收

脱敏最小 Fixture 覆盖成功、输入拒绝、审核回退、第 3 次人工阻断以及 140/150 Stub 消费；运行期制品只留在 V5 runtime 数据面。
