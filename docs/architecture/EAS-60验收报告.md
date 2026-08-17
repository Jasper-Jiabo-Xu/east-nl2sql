## EAS-60 Foundation 任务包冻结验收

结论：通过。本变更将 Foundation 的完整运行期意图固定为 `foundation_task_package/v1`，并保留 `foundation_profile/v1` 作为可复算兼容投影；260 不接受 profile 作为输入。

### 机器合同与血缘

- producer/consumer：210 → 220、241、260，目录和 Schema 均固定。
- `foundation_task_ref` 是 `{artifact_id, version, content_hash}` 三元组。220 闭包、241 bound data、242 verified data 均逐棒保持相同值；260 对完整任务包、闭包、242 数据和受控快照逐项比对。
- 任务包强制冻结任务身份、模式、触发原因、目标数据库、对象/表字段范围、数量、分布、层次引用、禁止类型、恢复 QA 与 CA/TRG 版本，且拒绝未知字段。
- 兼容投影规则：`base_database_version=target_database_version`、`target_classes=target_object_types`、`target_counts=target_counts`、CA/TRG 原样复制，并包含完整包的 `foundation_task_ref`。

### 脱敏工件 SHA-256

- `contracts/packages/foundation-task-package.schema.json`：`20b17e38d47fc13d96c7b368669ba7b6cb2888aa4fdd7633db70f946184cc340`
- `fixtures/artifacts/foundation-task-package-valid.json`：`9be02ae3d23164f604223f34632f4f3b8b929e08ebad5fca15bdc5821d53ea33`
- `contracts/v5-runtime-packages.schema.json`：`94eacb7ec34bb89e1f295d3a421618cab6f8c20f5689ce1acc77071bc634160f`

### 验证证据

- `python3 scripts/v5.py check`：通过，287 tests。
- `python3 -m unittest tests.agents.260.test_regression`：通过，覆盖正常消费、任务引用漂移和数据库版本漂移。
- `git diff --check`：通过。
- 全文残留扫描确认不存在允许 260 直接消费 `foundation_profile` 的有效合同。

260 输入门禁还拒绝 scope 外表字段、目标数量不符、操作闭包、快照引用或数据库版本/哈希漂移、禁止记录类型以及不完整层次/分布资产。固定 INSERT 编译器仍仅用于数据库 copy，且门禁结果标识 `writes_formal_store=false`。
