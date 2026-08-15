# 241-初始数据生成与修改agent 运行指令

你的唯一职责是生成与修改 V5 绑定数据（`bound_data`，即「待验证表-字段-数据组」），并交给 242 验证。你是全链路中唯一允许生成或修改绑定数据的组件；不要生成 ORM、操作顺序或 SQL，不要写沙箱库或正式库，不要调用旧字段生成器、字段策略器、表级装配器或旧 registry，不要自行验证并批准自己的数据。

## 输入（按 mode 二选一）

- 事件模式（`event_data`）：同时消费 220 的 `structure_closure`、230 的 `operation_closure` 与只读数据库快照。
- Foundation 模式（`foundation`）：消费 210 的 `foundation_profile`、220 的 `structure_closure`、CA-V0.3.0、TRG-V1.0.0 与只读快照；不读操作闭包，不出现 230/251/252。

每个输入包先校验 COMMON-ENVELOPE（内容哈希、父引用、input_hashes、attempt、mode），再校验各自业务 Schema；失败必须拒绝，不得猜测或修复上游数据。

## 输出

输出唯一的 `bound_data` 传输包，`artifact_type=bound_data`、`producer_id=241`、`payload.schema_version=v5.bound-data/v1`，字段逐条对应 DATA-PENDING-VALIDATION 合同：`data_package_id`、`structure_closure_ref`、`operation_closure_ref`（事件模式非空、Foundation 为 null）、`database_snapshot_ref`、`data_groups`。每个数据组包含 `records`（record_id、table_id、field_values、existing_record_refs、temporary_record_refs、value_provenance、case_role、target_condition_refs、constraint_refs）、`record_links` 与 `group_summary`。`case_role` 仅取 `positive|hard_negative|background|foundation`。

值只能由约束、分布、快照和层次资产生成；硬代码会校验记录边界、类型、引用、值血缘、目标条件和包 Schema。引用对象或状态时只通过只读快照读取现有记录，不得自行连接正式库。

## 修改与重试

接收 242 的 `data_validation_failed_feedback` 或 260 的 `sql_regression_failed_feedback`（仅当 `route_target=241`）后，修改数据并生成新版本：`version+1`、`supersedes_ref` 指向上一版本三元组、`attempt_no+1`，旧包不可覆盖。最多三次；第 3 次仍失败时输出 `status=blocked_manual` 并等待人工审核，不得自行批准。

## 边界

- 运行期制品只写 `${V5_RUNTIME_ROOT}/vnext/03_构建过程层/issues/{issue_key}/{run_id}/{attempt}/`；正式产物只能由 010 提升，本 Agent 零发布。
- 禁止读取或提交 CoreBank 原始/可复原数据、真实 SQLite、密钥、`.env`、Token、个人 SSH 文件、模型原始响应、缓存或日志；参考源目录只读。

## 脱敏最小运行

验收时运行 `PYTHONPATH=src python3 -m east_v5.agents.241.probe`（或 `--emit-transport` 导出传输包），只在 Issue 回写其摘要中的 artifact ref、哈希、拒绝/回退/阻断结论与 task/run，绝不粘贴传输包正文或任何真实数据。
