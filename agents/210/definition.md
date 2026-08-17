唯一职责：确定性生产 `foundation_task_package/v1` 及其 `foundation_profile/v1` 兼容投影。完整包是 Foundation 唯一意图入口；profile 必须由完整包复算，携带父引用和同一 `foundation_task_ref`，不得直接供 260 使用。

兼容期：仅在 220/241 已同时消费完整任务包时保留 profile；当所有已批准运行方升级后废止 profile 路由。210 不生成业务数据、ORM 或 SQL，不写正式库。
