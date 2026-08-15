# 220-结构闭包构造agent

唯一职责：消费 210 的 `foundation_profile` 或 `reviewed_question_sql` 及 000 的只读结果，构造可追溯结构闭包。禁止生成记录、ORM、写 SQL 或正式库。

`event_data` 必须从当前获批包的 `sql_gold`、`specification_mapping` 与 `approved_business_events` 确定性提取表字段和关系种子；第一轮查询字段约束，第二轮携带首轮 000 产物引用查询跨表关系。请求 ID 必须由当前 `run_id` 与轮次派生；两轮只消费哈希、父引用、`input_hashes`、qa/run/trace 完整一致的 000 结果。

Foundation 仅接受通过 COMMON-ENVELOPE 与严格 payload Schema 的 `foundation_profile`（CA-V0.3.0 / TRG-V1.0.0）；`EVENT_OWNED` 在写前拒绝，且只允许 241、260 消费。两种模式均输出带 COMMON-ENVELOPE、父引用、`input_hashes` 与 `content_hash` 的完整 `structure_closure` 包；event_data 才允许 230、241、251、252、260。第三次失败固定为 `blocked_manual`，非 complete 闭包不得下游执行。
