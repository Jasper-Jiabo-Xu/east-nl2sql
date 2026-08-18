# EAS-67 合同修补验收报告

## 结论

已新增由 210 生产的 `event_query_context`（`v5.event-query-context/v1`）。它将 140 的 `sql_schema_scope` 与 110/210 已批准 SQL 和 mapping 确定性投影为按 `spec_item` 排序的精确 `TABLE.FIELD` 集合。220 的新入口只读取该投影；260 同时认证 210 reviewed 包、context 与原始 140 `question_sql` query-spec，并将三者引用写入回归包。Foundation 路径未变。

## 冻结基线与合同哈希

- 基线：`bd73dcfb85842f93ac3e4cf470611c7a9ca6e471`。
- `config/v5-package-catalog.json`：`284afa656f54f7c6f86a2e4dc0be2587acca49c9869d6ec5f1bc121ad977cb8b`。
- `contracts/packages/event-query-context-package.schema.json`：`4636fea0c5c9d17a27c92b713e03e918778511e9f9aa2f5fff61aeae64a4127a`。
- `contracts/packages/regression-passed-data-orm.schema.json`：`23941a50a5522386f4f3629ad21a35dac42ed5d807918d3190383cd685a46222`。
- `contracts/v5-package-catalog.schema.json`：`4fe90b174ee553d43b3c96b1627c103e364fe95c9c89106375de198a984c8cec`。

## 真实消费者证据

210 `begin_event(approved, query_spec)` 直接接收真实 140 与真实 110，先校验 110 payload 的 query-spec ref 与直接 140 ref 一致，再生成 reviewed 和 context。context 的 payload refs、parent refs 与 input hashes 按 `[140, 110, reviewed]` 精确绑定；110 的 envelope parent/input refs、payload、producer、mode、status 均未改变。220 的公共 context validator 还要求 reviewed 信封的唯一 parent 精确等于 context 的 `source_question_sql_ref`，并依赖严格信封校验其 input hash；220 只用 `field_projection` 构造 closure。260 在打开副本前复用该 validator，并在通过时把 `reviewed_question_sql_ref`、`event_query_context_ref`、原始 `query_spec_ref` 写入 copy regression。260 只在副本数据库执行，未写正式库。

220 与 260 已删除无 context 兼容入口、旧 mapping 推断、query-spec 改写、伪造 parent ref 与 `allowed_fields[0]` fallback；210 也不再调度未携带 context 的事件。稳定拒绝包括：缺 context、伪 `event_data` query-spec、空/重复 spec_item、空字段集、未知或越界字段、无法唯一还原的别名、SQL 无可解析字段、context hash/parent/ref/version/run/qa/trace/attempt 漂移、reviewed/query-spec 引用漂移，以及在 context 自洽地重算 projection/parent/input/content hash 后替换 `source_question_sql_ref`。220 与 260 均覆盖该篡改；260 断言正式库副本字节保持不变。EAS-39 保留五类真实失败路由；其中 `SQL_EXECUTION_ERROR→010` 以合法 140+110→210 输入和只在 260 copy SELECT 阶段缺失的 SQLite collation 复现。

## 回归

- `python3 scripts/v5.py check`：357 tests passed。
- `python3 -m unittest tests.agents.210.test_scheduler tests.agents.220.test_closure tests.agents.260.test_regression tests.integration.data.test_event_dual_path`：49 tests passed。
- `git diff --check`：通过。

## 边界

未读取或写入正式库；未修改 Foundation、241/242/230/251/252 职责，以及 EAS-41 保留的全链目录。
