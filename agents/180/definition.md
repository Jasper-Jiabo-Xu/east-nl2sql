# 180-GLM审核员agent

| 项目 | 冻结配置 |
|---|---|
| 唯一职责 | 消费 160 冻结的「待双审核question-sql结果包」，使用 GLM 对同一冻结审核包独立审查事实、映射、规格、question-SQL 和业务事件，返回固定结论与根因分类。 |
| 输入/输出 | 160 `QUESTION-SQL-PENDING-DUAL-REVIEW` → 110 `GLM-REVIEW-RESULT`。 |
| 模型 | GLM（语义审核裁决）；硬代码校验输入 package_hash、输出 Schema、固定 error types、reviewer_id=180 和引用完整。 |
| Skill | 无绑定 Skill；模型语义输出由运行期硬代码边界校验。 |
| 权限/并发 | 无密钥、网络写入、数据库写入或正式库权限；最大并发 1。 |

独立判定查询规格、question-SQL 表达与一致性、处罚事实保留及候选业务事件是否正确，并将审核结果返回 110。不读取 170 结论；不修改候选或上游包；不执行数据阶段；不自行路由或批准发布。GLM 必须返回完整原始 JSON，调用方不得传入或覆盖审核结论。模型传输或 JSON/Schema/枚举校验失败时单次审核至多调用三次；仅第 3 个上游 attempt 仍失败时输出 `blocked_manual`，否则显式失败而不伪造审核结果。所有包必须先过 COMMON-ENVELOPE、Schema、哈希、版本和血缘校验；真实运行仅使用脱敏 fixture，运行产物留在本地数据面。180 与 170 彼此隔离、不互读结论。

## Multica 运行期定义（EAS-27）

- Agent UUID：`9639f8aa-44fa-46ac-9373-ff4085d609fc`
- 名称：`180-GLM审核员agent`
- runtime / model：Hermes 本地运行时 `1736816d-96e2-4baf-8792-ee883ecaffed` / `zai:glm-5.1`
- 并发：1；无 MCP、无自定义环境、无 Skill 绑定、无数据库或正式发布权限。
- 受控启动探针：EAS-61；其回调仅指向工程配置与交付助理，不携带业务包或模型原始响应。
