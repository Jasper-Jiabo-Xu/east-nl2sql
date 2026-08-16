# 160-确定性预审agent

| 项目 | 冻结配置 |
|---|---|
| 唯一职责 | 消费 150 待预审 question-SQL，执行 SQL/Schema/类型/引用/固定格式确定性检查；合格时冻结同一版本给 170/180，失败时返回精确修复信息给 150。 |
| 输入/输出 | 150 `QUESTION-SQL-PENDING-PRECHECK` → 170/180 `QUESTION-SQL-PENDING-DUAL-REVIEW`（通过）；150 `PRECHECK-FAILED-FEEDBACK`（失败）。 |
| 模型 | 硬编码审核、无 LLM 裁决权；任何实际部署以 Multica Agent 配置为准。 |
| Skill | 仅 `tdd`（如适用，`east-v5-test-driven-development`）。 |
| 权限/并发 | 无密钥、网络、数据库写入或正式库权限；最大并发 1。 |

硬编码审核，不做语义裁决；不得改写候选。通过包必须冻结候选、完整上下文、`review_round` 和 `package_hash`，170/180 完全相同。重试最多三次：attempt 1/2/3，第三次仍失败进入 `blocked_manual`。所有包必须先过 COMMON-ENVELOPE、Schema、哈希和血缘校验；真实运行仅使用脱敏 fixture，运行产物留在本地数据面。
