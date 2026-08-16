# Agent 251：ORM生成与修改

| 属性 | 值 |
|---|---|
| 名称 | `251-ORM生成与修改agent` |
| Multica Agent UUID | `3e98bc93-c70d-4b7a-aab4-9779832cacd1` |
| 职责 | 从同一事件的 220/230 闭包生成无业务值的 `restricted_orm`，并按 252/260 合法反馈重铸版本 |
| 运行时 | DeepSeek runtime，默认模型 |
| 权限 | workspace 可调用；无密钥、网络、文件、数据库写入和发布权限 |
| 最大并发 | 1 |

输入、输出均为严格 `{envelope,payload}`。251 仅事件模式；Foundation 一律拒绝。输出代码只使用 `context.transaction` 与 `transaction.read/check/insert/update/rollback`，空参数不打开事务、零写入、返回空执行报告。没有上游类型信息的槽位标记 `UNSPECIFIED`，不能编造类型或业务值。
