## EAS-112 TRG 真实消费合同修补验收报告

结论：通过本地冻结合同验收。修补仅改变 TRG 的生产消费者、脱敏测试 fixture、测试与本报告；未修改 `TRG-V1.0.0`、`contracts/constraint_assets/approved-assets.json`、正式库、241/260/210/010、旧生成器、旧 registry 或 ORM。

## 冻结输入

- 基线：`1715b85457eb5e6295311a1d76bb88dfff4eda04`
- 合同：`EAS-111-TRG-CONSUMER-REPAIR-V1`
- 验收 JSON canonical SHA-256：`e0c1fc08a049930dad0fff3a4c8de281f0e3ebe6285ae9938554f4b59de19e8d`
- TRG content hash：`a3480f669bd9e97db78a8fec96fac7e317b43b9ed6f222d5c920bc227eaf3b6a`
- TRG edges payload SHA-256：`51a4b2a64f95ece7e1239a06a10c6a1cd6d79e4baf7699107f042ff0e707a45c`

## 消费者修补

- `ConstraintAssetService.graph_edges_for_table` 仅接受真实 `source_table`、`source_field`、`target_table`、`target_field`、`edge_type`；按 `source→target` 过滤与排序，只增加服务端 `canonical_edge_hash`。
- `242` resolver 对同一真实字段合同、端点表前缀、显式 `PROVIDER_TO_CONSUMER` 方向及 provider/consumer 字段一致性复核，并以 `(source_table, target_table)` 供引用覆盖检查。
- 不提供旧 `provider_table_code` / `consumer_table_code` 格式的兼容层；该格式稳定拒绝。

## 真实消费证据

测试临时复制已经冻结的交付资产到 Git 外运行根，并在复制前后复算所有 payload hash；不写正式库、测试结束即清理运行根。

- 真实 TRG 537/537 边均可解析；覆盖图的全部 72 张表（包含当前 29 表范围），每表查询错误数为 0。
- 生产 `ConstraintAssetService`（000/220 查询边界）两次全量读取结果一致，返回 537 个唯一 `canonical_edge_hash`。
- 生产 `ConstraintAssetService` + `ConstraintAssetQueryService` + 242 `AssetBoundResolver` 在无候选业务数据的结构预检中两次得到一致 universe；图查询闭合，未使用 Stub-only 证据。
- CA-V0.2.0 数据库状态仅为 `3508 CANDIDATE / 0 APPROVED`；72 表 `field_rules_for_table` receipt 均为 `complete=true,total=0`。报告未将候选规则声称为已验证或已批准。

## 拒绝与不变量

测试覆盖并稳定拒绝：旧合成 provider/consumer-only 记录、缺失端点、表/字段前缀矛盾、显式方向矛盾、provider 字段矛盾、`canonical_edge_hash` 漂移；既有版本未知、资产 content/payload hash 漂移测试持续通过。`git diff` 对冻结 TRG 和 approved-assets 路径为空。

## 验证命令

```text
python3 -m unittest tests.constraint_assets.test_service tests.agents.242.test_validator -v
36 tests, OK

python3 scripts/v5.py check
368 tests, OK

git diff --check
OK
```

风险：当前修补仅解开结构闭包的真实图消费合同；EAS-111 后续真实 000→220 全量重跑、合并授权与 EAS-107 放行仍须依冻结回调合同由工程助理与 Sol 处理。
