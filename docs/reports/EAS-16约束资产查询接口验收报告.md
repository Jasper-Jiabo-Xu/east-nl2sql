# EAS-16 约束资产查询接口验收报告

## 结论

控制面已冻结 CA-V0.3.0 与 TRG-V1.0.0 的不可变身份、内容哈希、必要 payload 哈希、SQLite 白名单表/视图和查询结果合同。运行时只从 `V5_RUNTIME_ROOT` 的显式 manifest 读取资产；Git 不保存真实 SQLite、JSONL、运行日志或业务数据。

## 消费合同

- 000 可调用 `constraints_for_table`，得到 CA-V0.3.0 的最小约束、来源引用、版本和 content hash。
- 220 可调用 `graph_edges_for_table`，得到 TRG-V1.0.0 的边、版本和 content hash。
- 版本、内容/载荷哈希、SQLite 完整性/外键/白名单、目录边界或 manifest 未知字段有任一漂移时，在查询前稳定拒绝。
- 接口使用 `mode=ro&immutable=1` 和 `PRAGMA query_only=ON`；不提供生成、修复、复制或正式库写入路径。

## 运行输入

将已批准资产放入本地运行数据面后，按 `fixtures/constraint_assets/runtime-manifest.template.json` 生成该运行专属 manifest（绝不提交其中 locator 或真实资产）。`artifact_id + asset_version + content_hash` 是身份；locator 仅为本地存储定位。

## 验收命令

`python3 scripts/v5.py check`

覆盖成功、未知字段、版本/哈希漂移、越界 locator、只读 SQLite、无效查询和重复查询可复算，以及 000/220 Stub 消费。
