# EAS-16 约束资产查询接口验收报告

## 结论

控制面已冻结 CA-V0.2.0（单字段与码表）、CA-V0.3.0（多字段）与 TRG-V1.0.0（带类型引用图）三类的不可变身份、内容哈希、必要 payload 哈希、SQLite 白名单表/视图和查询结果合同。运行时只从 `V5_RUNTIME_ROOT` 的显式 manifest 读取资产；Git 不保存真实 SQLite、JSONL、运行日志或业务数据。

## 消费合同

- 000 可调用 `constraints_for_table`，得到 CA-V0.3.0 的最小约束、来源引用、版本和 content hash。
- 220 可调用 `graph_edges_for_table`，得到 TRG-V1.0.0 的边、版本和 content hash。
- 242 可消费同一 `constraints_for_table` 输出，对已绑定记录做约束校验，并取回冻结的 content hash。
- 252 可消费 `graph_edges_for_table` 输出，对引用图边冻结确定性哈希。
- 版本、内容/载荷哈希、SQLite 完整性/外键/白名单、目录边界或 manifest 未知字段有任一漂移时，在查询前稳定拒绝。
- 接口使用 `mode=ro&immutable=1` 和 `PRAGMA query_only=ON`；不提供生成、修复、复制或正式库写入路径。

## CA-V0.2.0 reconciliation（返工补齐）

- 新增版本化控制面 `contracts/constraint_assets/reconciliation-manifest.json`（schema `reconciliation-manifest.schema.json`），按 Sol 2026-08-13 冻结与人工裁决 `daac427c…` 登记 CA-V0.2.0 = 单字段约束资产与码表。
- 冻结 hash（唯一权威事实源，任何漂移即拒绝，不自修）：
  - `single_field_final.sqlite` = `f137be03a3814428b76507373d2032705dcacb1ff804adbddd96e397da5c1d6f`
  - `装配视图与码表引用域/本地码表.sqlite` = `3f9f05b47e6f4bfaada37abe06e17286228bf351edbd0a34ade2b09a2d5e82bd`
  - `装配视图与码表引用域/国标与附件4码表.sqlite` = `0932aecbdb4ed263ff5d8887cf1f833ecf76b220aec4fda42613df0b905de274`
  - 资产 content hash = `6cb0f385adae4e3e9e24558dc432132b43c437e8c2db5ce329c46bf68e8c4188`
- 旧 `CA-V0.2.0-foundation/manifest/asset_manifest.json` 保留为历史来源事实：`publication_status=approved_partial_not_released`、`not_in_scope` 含 `SINGLE_FIELD_CONSTRAINTS`；不回写、不重建、不改动约束内容。
- 0 字节且未登记入 manifest 的审计占位文件不在获批输入范围，已排除。

## 运行输入

将已批准资产放入本地运行数据面后，按 `fixtures/constraint_assets/runtime-manifest.template.json` 生成该运行专属 manifest（绝不提交其中 locator 或真实资产）。`artifact_id + asset_version + content_hash` 是身份；locator 仅为本地存储定位。

## 验收命令

`python3 scripts/v5.py check`

覆盖成功、未知字段、版本/哈希漂移、越界 locator、只读 SQLite、无效查询和重复查询可复算，以及 000/220/242/252 Stub 消费与 CA-V0.2.0 reconciliation 登记/拒绝路径。
