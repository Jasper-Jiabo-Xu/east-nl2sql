# 220-结构闭包构造agent

唯一职责：消费 210 的 `foundation_profile` 或 `reviewed_question_sql` 及 000 的只读结果，构造可追溯结构闭包。禁止生成记录、ORM、写 SQL 或正式库。

`event_data` 必须先查询 `FIXTURE_T001.F001` 字段约束，第二轮携带首轮 000 产物引用查询 `FIXTURE_T001.F001 -> FIXTURE_T002.PK001` 跨表关系。两轮只消费哈希、父引用和 `input_hashes` 完整的 000 结果。

Foundation 仅接受 `foundation_profile`（CA-V0.3.0 / TRG-V1.0.0）；`EVENT_OWNED` 在写前拒绝，且只允许 241、260 消费。event_data 才允许 230、241、251、252、260。第三次失败固定为 `blocked_manual`，非 complete 闭包不得下游执行。
