## 结论

基于新 main `9bdf86b1c5f369a8ee05bb94ba2e47aeb04414de`，EAS-41 的脱敏全流程成功链已重放通过。EAS-68 的 `query_parameter_binding/v1` 和 `event_query_context/v2` 被真实生产、消费并纳入候选与回执血缘；未沿用旧 main 的制品哈希或回执。

所有数据库写入都发生在测试创建的隔离 SQLite formal store。260 先在其物理 copy 回归，并断言源 formal store 字节不变；随后仅 010 的固定提交器写入隔离 store。没有读取或持久化真实数据库、原始材料、模型响应、日志或凭据。

## 事件成功链

`010→110/QS→210→220→230→241/242 + 251/252→260(copy)→210(candidate)→010(receipt)`。

- 010 接受同一脱敏来源并固定路由至 110。
- 真实 140→150→160→170/180→110 双审输出产生 `query_parameter_binding`；`:account_value` 的值、JSON Pointer、字段、operator、SQLite 类型、evidence 与 binding hash 均被认证。
- 210 生成并绑定 `event_query_context/v2`；220、260 对 binding ref、父引用、输入哈希、版本、run/qa/trace/attempt 严格验证。
- 260 使用 `connection.execute(sql_gold, bindings)` 在 copy 回归，产生通过的 v2 回归包；copy 前后源 formal store 字节一致。
- 210 仅组装 `release_candidate`；010 验证全链 hash 后提交临时 formal store，生成 `release_receipt`。同候选二次提交返回同一回执，问题集写入数保持 1，数据库版本从 `fixture-db-v1` 增量至 `fixture-db-v2`。

## Foundation 与恢复

- Foundation initial：真实 `210→220→241→242→260(copy)→210→010`，010 固定编译器消费真实 260 批次并幂等提交。
- Foundation expansion：事件 `FOUNDATION_REQUIRED` 反馈经 210 形成显式 expansion 任务；`resume_qa_ref` 通过 210、260、候选及 010 Stub 保持值与 lineage，不调用 230、251、252 或废止组件。

## 关键重算哈希

详见 `docs/reports/integration/full/EAS-41-run-manifest.json`。本次事件关键哈希为：approved `fcbb00d0…`、binding `7f697df3…`、context `e6540792…`、regression `113e3d74…`、candidate `d1cf1e16…`。

## 验证

运行：

`python3 -m unittest tests.integration.full.test_full_release_e2e tests.integration.qs.test_question_sql_e2e tests.integration.data.test_event_dual_path tests.integration.foundation.test_foundation_e2e tests.agents.010.test_committer -v`

该命令覆盖成功、SQL/参数绑定拒绝、版本及哈希漂移、幂等、回滚、人工阻断和 Foundation 约束；运行制品均由测试清理。

另执行 `python3 scripts/v5.py check`：360 项仓库测试通过，包含 lint、类型、Schema 与安全检查。
