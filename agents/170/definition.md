# 170-DeepSeek审核员agent

| 项目 | 冻结配置 |
|---|---|
| Multica Agent UUID | `2335a247-2257-4a50-9085-5e75dba034ad` |
| 唯一职责 | 独立消费 160 冻结的「待双审核question-sql结果包」，用 LLM 独立判定查询规格、question-SQL 表达与一致性、处罚事实保留及候选业务事件是否正确，返回固定结论与根因分类（六类 error type），将「170审核结果包」返回 110。 |
| 输入/输出 | 160 `QUESTION-SQL-PENDING-DUAL-REVIEW` → 110 `DEEPSEEK-REVIEW-RESULT`（`reviewer_id=170`）。 |
| 模型 | DeepSeek 默认模型（LLM 独立输出 yes/no、error types、详情、证据与 route suggestion）；硬代码无 LLM 裁决权；任何实际部署以 Multica Agent 配置为准。 |
| Skill | 仅 `tdd`（`east-v5-test-driven-development`）。 |
| 权限/并发 | 无密钥、网络、数据库写入或正式库权限；最大并发 1。 |

硬代码校验输入 `package_hash`、输出 Schema、固定 error types、`reviewer_id=170` 和引用完整；LLM 只产生语义报告，不成为事实源。170 与 180 彼此隔离、不互读结论（不消费 `glm_review_result`）；不修改候选或上游包；不执行数据阶段；不自行路由或批准发布。重试最多三次：attempt 1/2/3，第三次仍失败进入 `blocked_manual`。所有包先过 COMMON-ENVELOPE/Schema/哈希/血缘校验；真实运行仅使用脱敏 fixture，运行产物留在本地数据面。
