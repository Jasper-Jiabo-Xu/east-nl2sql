## 结论

EAS-113 在控制面完成最小修补：Foundation 220 现在从已验证的完整 210 `foundation_task_package` 的 `target_table_field_scope` 确定性投影 `TABLE.FIELD` 字段种子。000 的 CA/TRG 结果只能扩展该集合；241 只验证、绝不补齐缺失字段。未产生运行期业务数据、正式库写入、ORM 或旧生成链调用。

## 变更与合同

- `src/east_v5/agents/220/closure.py`：增加严格的字段范围规范化、任务字段完整性校验和可复算闭包校验；层次引用仅由 000 `hierarchy_reference` 结果转换。
- `src/east_v5/agents/241/generator.py`：Foundation 消费前复验同一任务引用与完整字段范围，缺失字段固定拒绝 `FOUNDATION_TASK_FIELD_SCOPE_MISSING`。
- `tests/agents/220/test_closure.py`：覆盖 `T1:[A,B]`、`T2:[C]` 的完整种子、000 扩展、非法表/字段、重复字段、哈希/任务引用漂移、手工删字段和手工加未支持字段。
- `tests/agents/241/test_generator.py` 与 `tests/integration/foundation/test_foundation_e2e.py`：改为由真实 210 task 经 220 构造 closure；删除原手写 Foundation closure fixture，241 对任务内字段通过、任务外字段仍为 `FIELD_OUT_OF_CLOSURE`。
- `agents/contracts/220.md`、`agents/220/definition.md`、`agents/220/prompt.md`：冻结“任务字段不可删、资产只可扩展、不得 DDL 宽扩”的边界。

## 输入与可复算输出

- 脱敏 210 task fixture SHA-256：initial `898355f747a64ffacbed2374c815e0813c9ec79b5dac23f6c3842ed6bad55d98`；expansion `83ec67a17ced5c949e38f20fa989c28cfdf1c372ec187d3a9775d44dccc3eb64`。
- 更新后的脱敏集成输出已冻结于 `docs/reports/integration/foundation/EAS-40-运行清单.json`：initial closure `210fd9d00abc6d3903bbfc7dc060ce44f9d96b7d67ff609f6e2129c713ced83b`；expansion closure `7588b91ac31e2f4e4a73e3a26b08e09501a5ba98769b576bf6d5bb4c996bbdda`。
- 同输入双算字节级相等由 220 单测和 Foundation 集成测试覆盖。

## 验证

- `python3 -m unittest tests.agents.220.test_closure tests.agents.220.test_probe tests.agents.241.test_generator tests.integration.foundation.test_foundation_e2e`：45 passed。
- `python3 scripts/v5.py check`：370 passed。
- `git diff --check`：通过。

## 未执行项与风险

EAS-111 的真实 runtime manifest、000 查询 receipts、50/50 覆盖及 241 零写入预消费均属于合并后的受控运行数据面验证，本次未运行，未写正式库。旧 closure/resolver hash 仍应持续拒绝；工程助理在获授权合并后必须按 Issue 中冻结 lineage 重跑并形成第二份 DELIVERY-RECEIPT。
