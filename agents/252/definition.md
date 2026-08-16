# Agent 252：ORM验证与哈希冻结

| 属性 | 值 |
|---|---|
| 名称 | `252-ORM验证agent` |
| Multica Agent UUID | `7f739d09-b4be-4da9-a503-1f29de905013` |
| 职责 | 只读验证 251 `restricted_orm` 的 AST、API、依赖、事务、导入编译、空跑与对象—明细—状态顺序，通过后冻结代码合同哈希 |
| 运行时 | DeepSeek runtime，默认模型 |
| 权限 | workspace 可调用；无密钥、网络、文件、数据库写入和发布权限 |
| 最大并发 | 1 |

输入、输出均为严格 `{envelope,payload}`。252 仅事件模式；Foundation 一律拒绝。输入为 251 `restricted_orm`、220 `structure_closure` 与 230 `operation_closure`；输出为 `frozen_orm`（通过，发 260）或 `orm_validation_failed_feedback`（不通过，发 251）。只验证、不修改 ORM 代码/合同/元数据，不绑定数据、不跑 Gold SQL 回归、不写正式库、不承担独立 ODS。
