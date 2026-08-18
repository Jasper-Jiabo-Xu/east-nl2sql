# EAS-67 合同修补验收报告

## 结论

已新增由 210 生产的 `event_query_context`（`v5.event-query-context/v1`）。它将 140 的 `sql_schema_scope` 与 110/210 已批准 SQL 和 mapping 确定性投影为按 `spec_item` 排序的精确 `TABLE.FIELD` 集合。220 的新入口只读取该投影；260 同时认证 210 reviewed 包、context 与原始 140 `question_sql` query-spec，并将三者引用写入回归包。Foundation 路径未变。

## 冻结基线与合同哈希

- 基线：`bd73dcfb85842f93ac3e4cf470611c7a9ca6e471`。
- `config/v5-package-catalog.json`：`284afa656f54f7c6f86a2e4dc0be2587acca49c9869d6ec5f1bc121ad977cb8b`。
- `contracts/packages/event-query-context-package.schema.json`：`4636fea0c5c9d17a27c92b713e03e918778511e9f9aa2f5fff61aeae64a4127a`。
- `contracts/packages/regression-passed-data-orm.schema.json`：`69523e86024337bc102d89ca5c760b818e1ebaff2981e171b59ff801db4519ba`。
- `contracts/v5-package-catalog.schema.json`：`4fe90b174ee553d43b3c96b1627c103e364fe95c9c89106375de198a984c8cec`。

## 真实消费者证据

210 `begin_event(approved, query_spec)` 生成 reviewed 包和 context，并向 220 同时下发两项引用。220 校验 context 的三项来源引用、父引用、hash、run/qa/trace/attempt 后用 `field_projection` 构造 closure；260 校验相同 lineage，并在通过时把 `reviewed_question_sql_ref`、`event_query_context_ref`、原始 `query_spec_ref` 写入 copy regression。260 只在副本数据库执行，未写正式库。

稳定拒绝包括：空/重复 spec_item、空字段集、未知或越界字段、无法唯一还原的别名、SQL 无可解析字段、context hash/父引用/上下文漂移、reviewed/query-spec 引用漂移。

## 回归

- `python3 scripts/v5.py check`：354 tests passed。
- `python3 -m unittest tests.agents.210.test_scheduler tests.agents.220.test_closure tests.agents.260.test_regression tests.contracts.test_stage10_package_contracts`：通过。
- `git diff --check`：通过。

## 边界

未读取或写入正式库；未修改 Foundation、241/242/230/251/252 职责，以及 EAS-41 保留的全链目录。
