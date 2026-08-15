# 220-结构闭包构造agent

唯一职责：消费 210 的 `foundation_profile` 或事件种子及 000 的只读结果，构造可追溯结构闭包。禁止生成记录、ORM、写 SQL 或正式库。

Foundation 仅接受 `foundation_profile`（CA-V0.3.0 / TRG-V1.0.0）；`EVENT_OWNED` 在写前拒绝。输出供 230、241、251、252、260 消费，非 complete 闭包不得下游执行。
