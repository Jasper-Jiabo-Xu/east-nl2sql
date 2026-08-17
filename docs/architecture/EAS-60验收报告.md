## EAS-60 Foundation 任务包冻结验收

结论：第二轮返工完成，待工程复核新 head。本变更将 Foundation 的完整运行期意图固定为 `foundation_task_package/v1`，并保留 `foundation_profile/v1` 作为可复算兼容投影；260 不接受 profile 作为输入。

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

- `python3 scripts/v5.py check`：298 tests，OK。
- `python3 -m unittest tests.agents.210.test_foundation tests.agents.220.test_closure tests.agents.241.test_generator tests.agents.242.test_validator tests.agents.260.test_regression tests.contracts.test_foundation_profile_package`：73 tests，OK；覆盖正常消费、任务引用漂移和数据库版本漂移，以及执行前重认证、物理隔离和 commit 前 delta 回滚。
- `git diff --check`：通过。
- 全文残留扫描确认不存在允许 260 直接消费 `foundation_profile` 的有效合同。

### 返工闭环（Sol 清单）

- `structure_closure`、`bound_data`、`verified_bound_data` package Schema 现以 `if/then` 按 mode 强制：Foundation 必须携带任务三元组，event_data 必须显式为 `null` 或不含闭包字段；260 以独立 Schema validator 检查完整 242 包与完整受控快照。
- 210 已有机器生产端：完整任务包生产后再投影 profile；`config/v5-architecture.json` 固定权威入口、兼容消费者与废止条件，`governance-manifest.json` 纳入任务包合同定位。
- 260 对记录逐项比较 target counts、distribution、hierarchy asset refs、表字段范围、禁止类型和临时/快照/记录链接引用完整性；快照 hash、verified data hash、任务/闭包/快照引用漂移均拒绝。
- 固定 `east-foundation-insert-compiler/v1` 只执行在传入的 SQLite copy。测试验证初始铺底和 expansion 完整链路、实际 delta、参数化 batch、下游 copy 冲突回滚，且 formal SQLite dump 的 SHA-256 不变。

新增返工测试：`tests/agents/210/test_foundation.py`、`tests/agents/260/test_regression.py`。后者覆盖 initial_seed、expansion、完整 242/快照 Schema、缺失字段、profile 直输、任务/数据库漂移、分布、层次、范围、禁止类型、引用完整性、copy 回滚与 formal-store 不变。

返工后机器哈希：`contracts/packages/bound-data-package.schema.json`=`58949326557fa46e58676f5e47705b2a54a94ffed1125fe1b283ca938e26dc4b`；`contracts/packages/verified-bound-data-package.schema.json`=`2cdbdb7f2426ffeb8d8e62fffc1a0d9bf83b08d4440d84d2a0a53fbeb48adf8d`；`governance-manifest.json`=`88b078f6abf650e74a1112f64a6c43842efcf83a3f7061d1c8c139bb21c3a536`。

### 第二轮返工：执行原子性与隔离

- 260 的 execution plan 现包含结构闭包、242 verified、242 validated 内容哈希和快照引用，并带 `plan_sha256`。执行前重新运行 verified package Schema 与 common-envelope 校验，复算 validated hash，逐项比较任务、闭包、快照与 verified 三元组；验证后的替换包或计划漂移均在写入前拒绝。
- 执行器在 `BEGIN IMMEDIATE` 前拒绝同一 SQLite connection，以及两个 connection 指向相同物理数据库文件的 copy/formal 组合。正式库未被作为写入目标。
- INSERT、delta 与 formal-store 完整性闸门均位于同一未提交事务中；只有全部通过才 commit。任一 `sqlite3` 或合同异常均 rollback。成功路径不虚报回滚，故 `rollback_verified=false`；失败路径由测试逐行核验 copy 回到执行前状态。
- 增加 verified 重新哈希替换、plan 哈希漂移、verified/snapshot 内容哈希漂移、无效层次引用、同 connection/同文件隔离和 trigger 造成 delta=2 后回滚的拒绝测试。
- 本轮最终证据：`python3 scripts/v5.py check` 为 298 tests，OK；上述定向命令为 73 tests，OK；`python3 scripts/v5.py schema`、`git diff --check 24de93b68e367f0e1f00bafb809e50cf8616b370` 与 `git diff --check` 均零输出。
