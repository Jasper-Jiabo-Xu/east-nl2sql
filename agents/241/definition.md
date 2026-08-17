# Agent 241：初始数据生成与修改

## 基本信息

| 属性 | 值 |
|---|---|
| 名称 | `241-初始数据生成与修改agent` |
| Multica Agent UUID | `7df640f9-973f-4c46-8302-df1256f60146` |
| 职责 | 从结构闭包与操作闭包（事件）/ Foundation 画像（铺底）及只读快照生成并修改 `bound_data`，交 242 验证 |
| 运行时 | DeepSeek（`runtime_id=0e5e9dd9-5135-4937-bb03-92b77adb8395`，model 继承运行时默认） |
| 权限 | workspace 可调用；无密钥、无网络/正式库写入权限 |
| 最大并发 | 1 |
| Skill | `east-v5-runtime`（仓库内 SKILL，随 checkout 加载；无额外 workspace skill 绑定） |

## 合同和边界

- 输入、输出、反馈均为 `{envelope,payload}` 传输包；先校验 COMMON-ENVELOPE（内容哈希、父引用、input_hashes、attempt、mode），再校验业务 Schema。
- 事件模式消费 220 `structure_closure`、230 `operation_closure`、只读快照；Foundation 消费 210 完整 `foundation_task_package`、可选兼容 `foundation_profile`、220 同一任务引用的 `structure_closure`、CA-V0.3.0、TRG-V1.0.0 与快照，不读操作闭包、不出现 230/251/252。
- 唯一输出 `bound_data`（`payload.schema_version=v5.bound-data/v1`），字段逐条对应 DATA-PENDING-VALIDATION；`case_role` 仅取 `positive|hard_negative|background|foundation`。
- 242/260 反馈只能修改数据并生成新版本（`version+1`、`supersedes_ref` 指向上版三元组、`attempt_no+1`），旧包不可覆盖；第 3 次仍失败输出 `blocked_manual`。
- 硬代码校验记录边界、类型、引用、值血缘、目标条件与包 Schema；LLM 只通过 `proposed_data_groups` 提候选值。
- 不生成 ORM/SQL/操作顺序、不写沙箱或正式库、不调用旧生成器/策略器/装配器/registry、不自行验证并批准数据。

## 失败码（ContractError）

`TRANSPORT_PACKAGE_INVALID`、`ARTIFACT_TYPE_MISMATCH`、`SCHEMA_VALIDATION_FAILED:*`、`UNKNOWN_FIELD:*`、`CONTENT_HASH_DRIFT`、`STRUCTURE_CLOSURE_ENVELOPE_INVALID`、`OPERATION_CLOSURE_ENVELOPE_INVALID`、`OPERATION_CLOSURE_REQUIRED`、`FOUNDATION_OPERATION_CLOSURE_FORBIDDEN`、`FOUNDATION_PROFILE_IN_EVENT_MODE`、`STRUCTURE_CLOSURE_EMPTY`、`RECORD_TABLE_OUT_OF_CLOSURE`、`FIELD_OUT_OF_CLOSURE`、`CASE_ROLE_INVALID`、`EVENT_ROLE_IN_FOUNDATION_MODE`、`FOUNDATION_ROLE_IN_EVENT_MODE`、`NULL_VALUE_MISMATCH`、`VALUE_TYPE_MISMATCH`、`EXISTING_RECORD_ORPHAN`、`TEMPORARY_RECORD_ORPHAN`、`RECORD_LINK_ORPHAN`、`VALUE_PROVENANCE_EMPTY`、`GROUP_SUMMARY_MISMATCH`、`FEEDBACK_PACKAGE_REF_MISMATCH`、`FEEDBACK_LINEAGE_MISMATCH`、`REGRESSION_NOT_ROUTED_TO_241`、`REGRESSION_MODE_MISMATCH`、`ATTEMPT_OUT_OF_RANGE`、`ATTEMPT_LINEAGE_MISMATCH`、`PROPOSED_DATA_GROUPS_REQUIRED`、`MANIFEST_ARTIFACT_REF_MISMATCH`、`MANIFEST_LINEAGE_MISMATCH`、`MANIFEST_ISSUE_KEY_MISMATCH`、`MANIFEST_RUNTIME_BOUNDARY_VIOLATION`。

## 运行验收

脱敏最小 Fixture 真实覆盖：事件/Foundation 生成、可复算哈希、242 验证回退（version+1、supersedes、attempt=2）、260 回归回退（route=241、attempt=3 阻断）、输入拒绝（坏哈希、未知字段、孤儿引用、类型/空值/血缘/汇总不一致）、Foundation 拒绝操作闭包、242 下游 Stub 消费与 manifest 边界；运行期制品只留 V5 runtime 数据面。

## 实现位置

- 代码：`src/east_v5/agents/241/{generator,probe}.py`
- 包 Schema：`contracts/packages/{bound-data-package,data-validation-failed-feedback,sql-regression-failed-feedback,database-read-snapshot,bound-data-manifest}.schema.json` 与 `bound-data-manifest-template.json`
- 测试与 Fixture：`tests/agents/241/`
