# Agent 242：数据验证

## 基本信息

| 属性 | 值 |
|---|---|
| 名称 | `242-数据验证agent` |
| Multica Agent UUID | `4e801c18-7048-4227-a5c7-515f51a5e5ba` |
| 职责 | 只读验证 241 `bound_data`（单字段/表内多字段/跨表多字段与引用），通过后冻结数据哈希并发 `verified_bound_data`；不通过时聚合全部错误发 `data_validation_failed_feedback` |
| 运行时 | DeepSeek（`runtime_id=0e5e9dd9-5135-4937-bb03-92b77adb8395`，model 继承运行时默认） |
| 权限 | workspace 可调用；无密钥、网络、文件写入、正式库写入与发布权限 |
| 最大并发 | 1 |
| Skill | `east-v5-runtime`（仓库内 SKILL，随 checkout 加载；无额外 workspace skill 绑定） |

## 合同和边界

- 输入、输出、反馈均为 `{envelope,payload}` 传输包；先校验 COMMON-ENVELOPE（内容哈希、父引用、input_hashes、attempt、mode），再校验业务 Schema。
- 输入为 241 `bound_data` 与同一事件的 220 `structure_closure`；Foundation 与事件模式均支持，Foundation 不得出现操作闭包。
- 输出唯一两类包：通过 `verified_bound_data`（`payload.schema_version=v5.verified-bound-data/v1`，发 260）；不通过 `data_validation_failed_feedback`（`payload.schema_version=v5.data-validation-failed-feedback/v1`，发 241）。
- 只验证、不生成/修改数据，不生成 INSERT、不执行 ORM、不写沙箱或正式库、不调用旧生成器/策略器/装配器/registry、不承担独立 ODS。
- 验证范围来自受控 `ConstraintQueryService`（生产：`build_constraint_asset_resolver` 由已验证 runtime manifest 构造真实 `ConstraintAssetService` 并适配）按 `structure_closure` 逐表查询 CA-V0.2.0/CA-V0.3.0/TRG-V1.0.0 得到的「适用约束全集」，**不信任候选数据自报 `constraint_refs`，也不存在任意 Callable 铸造证明的接口**；adapter 只消费 EAS-58 冻结的服务端回执（`v5.constraint-asset-query-result/v2`：`query_method`/`table_code`/规范化 `query_parameters`/`artifact_id+asset_version+content_hash`/权威 `total`/页与游标/`returned_count`/`complete`/`records_hash`/`receipt_hash`），沿服务端单次 HMAC 游标走到闭合，`total`/`complete` 一律取自服务端、禁止 adapter 以 `len(records)`/`True` 自设，回执逐页与 `ConstraintAssetService` 控制面校验来源与方法映射；每个约束绑定 `constraint_id+scope+source_asset_version+canonical_rule_hash`，`canonical_rule_hash` 为服务端由批准资产记录内容产生的规范化哈希、逐项复算（不自证），`resolve` 返回 `(rule, content)` 并逐项复算；TRG 关系图被消费（closure `references` 的 `from→to` 表对必须被 `provider→consumer` 图边覆盖）；查询回执进入 `verified_bound_data` 输出合同并由 260 Stub 独立复核页链与计数。删条后重签、替换正文后重算、resolve 漂移、查询方法/来源映射错误、分页未闭合、缺 source、伪造空服务/非真实服务、游标重放均 fail closed。
- 按固定 validator registry 调用数据元/单字段、表内多字段、跨表多字段模块；聚合全部失败并定位到 `data_group/record/table/field/constraint`，禁止首错即停漏项。
- 通过后冻结完整数据内容、约束全集证明、模块结果、registry version、`validated_hash` 与时间；`validated_hash` 必须可复算且 260/发布阶段不得修改。
- 确定性身份：`validated_at` 默认继承来源 `created_at`，报告不含墙钟耗时；同一输入复算出相同 `artifact_id+version+content_hash`。
- 上游 `blocked_manual`、任一哈希/版本/父引用/模式漂移、未知字段、未知约束一律拒绝，不猜测、不修补。

## 失败码（ContractError）

`TRANSPORT_PACKAGE_INVALID`、`UNKNOWN_FIELD:BOUND_DATA`、`UNKNOWN_FIELD:STRUCTURE_CLOSURE`、`BOUND_DATA_ENVELOPE_INVALID`、`STRUCTURE_CLOSURE_ENVELOPE_INVALID`、`UPSTREAM_BLOCKED_MANUAL`、`SCHEMA_VERSION_UNSUPPORTED`、`STRUCTURE_CLOSURE_REFERENCE_MISMATCH`、`FOUNDATION_OPERATION_CLOSURE_FORBIDDEN`、`OPERATION_CLOSURE_REQUIRED`、`SCHEMA_VALIDATION_FAILED:*`、`INPUT_MUTATED`、`QUERY_RECEIPT_INVALID`、`QUERY_RECEIPT_INCOMPLETE`、`QUERY_RECEIPT_SOURCE_MISMATCH`、`RULE_CONTENT_HASH_DRIFT`、`GRAPH_REFERENCE_UNCOVERED`、`UNIVERSE_PROOF_INVALID`、`UNIVERSE_CLOSURE_MISMATCH`、`UNIVERSE_SOURCE_SET_INVALID`、`UNIVERSE_SOURCE_REF_INVALID`、`UNIVERSE_SOURCE_DRIFT`、`UNIVERSE_SOURCE_VERSION_INVALID`、`UNIVERSE_RULE_HASH_INVALID`、`UNIVERSE_DUPLICATE_CONSTRAINT`、`UNKNOWN_CONSTRAINT`、`VALIDATION_REJECTED`、`VALIDATION_NOT_FAILED`、`CONTENT_HASH_DRIFT`、`UNKNOWN_RULE_KIND`、`EXPRESSION_INVALID`、`FIELD_RULE_ENDPOINT_REQUIRED`、`VIOLATION_LOCATION_MISSING`、`VIOLATION_LOCATION_UNRESOLVABLE`、`ASSET_QUERY_SERVICE_REQUIRED`、`ASSET_QUERY_CHAIN_GAP`、`ASSET_QUERY_RECEIPT_INVALID`、`ASSET_PAYLOAD_HASH_DRIFT`、`ASSET_QUERY_CURSOR_INVALID`。

## 运行验收

用真实 `ConstraintAssetService` + 脱敏 SQLite/JSONL/runtime-manifest fixture（`fixtures/constraint_assets/` 脱敏模板 + `sanitized_fixture.py` 确定性构造）跑通 `build_constraint_asset_resolver → 242 → 260`，**不以 `FixtureQueryService` 或任意 Protocol 实现自证**。覆盖：事件/Foundation 通过冻结、可复算哈希与 260 下游 Stub 消费、字段/表内/跨表单层与多层失败聚合反馈、错误聚合非首错即停、输入深比较零修改、哈希/未知字段/未知约束/未知规则种类/上游阻断/闭包引用漂移拒绝、registry 版本冻结与下游消费；`>LIMIT`（122 条）数据集分页遍历闭合、三 source 矩阵无缺失无重复；删条后重签、替换正文、resolve 漂移、缺 source、分页未闭合、伪造空服务/非真实服务、游标重放均拒绝；运行期制品只留 V5 runtime 数据面。

## 实现位置

- 代码：`src/east_v5/agents/242/{resolver,validator,probe,sanitized_fixture}.py`（`resolver.py` 为 EAS-58 服务端回执消费、权威 `canonical_rule_hash` 绑定与 `AssetBoundResolver`）
- 包 Schema：`contracts/packages/verified-bound-data-package.schema.json`（`data-validation-failed-feedback.schema.json` 与 `bound-data-package.schema.json` 复用 EAS-31/EAS-44 冻结）
- 测试与下游 Stub：`tests/agents/242/`
