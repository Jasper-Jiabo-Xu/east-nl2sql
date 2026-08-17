260 是双模式、copy-only 的确定性回归边界，不生成或修改候选数据、冻结 ORM 或正式库。

- event_data：只接收完整 `QUESTION-SQL-DUAL-REVIEW-PASSED`（110）、140 查询规格、242 已验证数据、252 冻结 ORM 与 EAS-19 快照。逐包校验信封、Schema、内容哈希、父引用和版本；在 SQLite copy 执行受限 ORM 与 Gold SQL，按批准 Fixture 身份核验正例命中、反例排除、条件/码值覆盖、行数 target+tolerance、分组、DISTINCT 与 JOIN 倍率。
- foundation：只接收完整 210 `foundation_task_package`、同一三元组 220 结构闭包、242 `verified_bound_data` 与受控快照。保留 EAS-60 的固定参数化 INSERT 编译器、写前物理隔离、事务内 delta 与真实 rollback；验证分布、层次、引用完整性、范围和禁止类型。
- 失败输出严格 `sql_regression_failed_feedback`，按 241/251/210 路由；第三次（包括 SQL 执行错误）强制 `MANUAL_REVIEW_REQUIRED/manual`。210 Stub 必须实际消费事件成功包与失败反馈。

禁止 profile 直输、自由 SQL、Foundation ORM/230/251/252、旧字段生成器/策略器/表级装配器，以及任何正式库写入。
