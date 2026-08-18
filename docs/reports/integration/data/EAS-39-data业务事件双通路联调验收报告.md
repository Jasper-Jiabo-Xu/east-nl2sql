# EAS-39 data 业务事件双通路联调验收报告（返工 · 证据补齐）

- issue_key: EAS-39（[V5-INT-DATA-001] data 业务事件双通路联调）
- 实施负责人: EAST DeepSeek 构建员（`7d50dabd-40f8-40a1-807f-041eeed941a9`）
- base_sha / checkout_ref: `5388c085a5a12214458bc3a5ee6084aca14ea847`（上一轮返工 head，Sol 复审对象）
- 报告日期: 2026-08-18

## 结论

**通过。** 按 Sol 冻结返工边界完成 `SQL_EXECUTION_ERROR` 路由语义修正与整链证据补齐：

- `route_target` 语义 = **210 校验后的下一业务目的地**；`SQL_EXECUTION_ERROR` 权威值为 `010`，与 V5 主链 `260→210→010` 一致。
- 260 真实 `SQL_EXECUTION_ERROR` 失败包现产出 `route_target=010`；210 直接消费、校验该包并确定性返回 010 路由。
- `sql_regression_failed_feedback` catalog 直接消费者统一为 `[210]`，未新增别名包、未改变其他错误码业务目的地。
- 五类失败路由均以「真实 260 产包 → 真实 210 消费并确定性路由」为整链证据，无手写反馈包。
- 补齐机器可读运行清单、三次重试/人工阻断、幂等重放；正式库未写入。
- 拒绝证据补齐（Sol 复审唯一未通过项）：基于真实 260 失败包新增 `payload.schema_version` 漂移与未知 payload 字段两类反例，重算 content_hash 后分别交给 210 Stub 与 `route_feedback`，断言 `210_STUB_SCHEMA_REJECTED` / `210_260_FEEDBACK_REJECTED`，证明拒绝源于 Schema 约束而非旧哈希。

## 返工代码改动（第一轮：路由语义，最小扩展）

- `src/east_v5/agents/260/regression.py:620`：`feedback()` 路由表新增 `"SQL_EXECUTION_ERROR": "010"`（原默认落 `210`）。
- `config/v5-package-catalog.json`：`sql_regression_failed_feedback` consumers `["150","241"]` → `["210"]`。
- `tests/agents/260/approved_210_stub.py:138`：210 直接消费接受的 `route_target` 集合加入 `"010"`。
- `tests/agents/260/test_regression.py`：SQL 错误路由断言 `210` → `010`。

## 返工代码改动（第二轮：拒绝证据补齐，最小扩展）

仅修改本 Issue 允许路径，未改动已验收的 260 路由实现、210 语义或 catalog：

- `tests/integration/data/test_event_dual_path.py`：`test_route_conflict_version_and_hash_drift_rejected` 新增 `payload.schema_version` 漂移与未知 payload 字段两类反例（均重算 content_hash，分别经 210 Stub 与 `route_feedback` 断言 `210_STUB_SCHEMA_REJECTED` / `210_260_FEEDBACK_REJECTED`）。
- `docs/reports/integration/data/run-manifest.json`：新增拒绝证据映射、更新 base_sha 与文件哈希。
- 本报告。

## 范围与拓扑

```
110(批准QS) → 210(reviewed) → 220(结构闭包) → 230(操作闭包)
                                              ├→ 241(bound_data) → 242(verified_bound_data)
                                              └→ 251(restricted_orm) → 252(frozen_orm)
                        260(database_copy_regression, 正式库 copy 沙箱)
                          └─ 失败包 route_target ∈ {241,251,010,210,manual} → 210 直接消费并确定性路由
                        210(release_candidate, 仅通过后组装) → 010
```

## 交付物（仓库相对路径）

- `tests/integration/data/test_event_dual_path.py` — 6 个联调测试。
- `fixtures/integration/data/approved-question-sql.json` — 批准 QS Fixture（110 输出）。
- `fixtures/integration/data/query-specification.json` — 查询规格（140 输出）。
- `fixtures/integration/data/database-read-snapshot.json` — 只读快照（EAS-19 输出）。
- `docs/reports/integration/data/run-manifest.json` — 机器可读运行清单。
- `docs/reports/integration/data/EAS-39-data业务事件双通路联调验收报告.md` — 本报告。

## 数量

- 联调测试：6 个（unittest 口径），全部通过。
- 全量回归：`scripts/v5.py check` → lint/type/schema/security 通过，`Ran 347 tests ... OK`。
- 联调路径真实产出包：reviewed_question_sql、structure_closure、operation_closure、bound_data、verified_bound_data、restricted_orm、frozen_orm、database_copy_regression、release_candidate，共 9 类。

## 输入/输出哈希与关键事实

- `reviewed_artifact_id` = `210-reviewed-eas39-approved`
- 结构闭包 tables = `['FIXTURE_T001', 'FIXTURE_T002']`，fields = `['FIXTURE_T001.F001', 'FIXTURE_T001.F002', 'FIXTURE_T002.PK001']`
- 操作闭包步骤 = `op-001:READ, op-002:CHECK, op-003:INSERT, op-004:INSERT`
- 绑定槽位 = `slot_fixture_t001_f001, slot_fixture_t001_f002, slot_fixture_t002_pk001`
- verified `validated_hash` = `10ca99e73591314823201a527efd5b89e279bc2a77437d8e4c42081c4342ca8d`
- frozen ORM `validated_hash` = `10ae73f3c47cc71c25d6d15d292493bf475fa3b1a863da235574928db9541779`
- 回归包 envelope `content_hash` = `aa5af549f450c3a1bbd03e84db46da5eddabb4728abc3e48010602ca8bac9e98`
- `regression_status` = `passed`，`formal_unchanged` = `True`
- release_candidate_id = `210-release-event-regression-bound-data-eas39-run:verified-bound-data`，`release_mode` = `event_data`

## 测试摘要（真实产包 → 真实消费）

1. `test_dual_path_chain_260_copy_regression_and_210_release_candidate`：全链路 + 正式库字节不变 + 010 可消费 release_candidate。
2. `test_release_candidate_requires_passed_regression`：真实 260 失败反馈不得组装为发布候选（`210_EVENT_REGRESSION_REJECTED`）。
3. `test_five_real_failure_routes_consumed_by_210_and_routed`：五类失败均用真实 260 `run_event` 产包 → 210 Stub 直接消费（`kind=feedback`）→ `route_feedback` 确定性返回 `241/251/010/210/manual`。
4. `test_retry_escalation_manual_block_and_idempotent_replay`：DATA_VALUE_ERROR 第 1/2 次 → 241，第 3 次 → MANUAL_REVIEW_REQUIRED/manual；同一失败包重复消费/路由结果一致（幂等重放）。
5. `test_route_conflict_version_and_hash_drift_rejected`：route_target 冲突、retry_count/attempt_no 漂移（attempt 隔离）、内容哈希漂移、210 不认识的业务目的地、`payload.schema_version` 漂移（`v2`）与未知 payload 字段均被拒绝；后两者重算 content_hash 后分别由 210 Stub 与 `route_feedback` 拒绝（`210_STUB_SCHEMA_REJECTED` / `210_260_FEEDBACK_REJECTED`），证明拒绝源于 Schema 约束而非旧哈希。
6. `test_210_stub_rejects_schema_title_impersonation_and_hash_drift`：拒绝 Schema title 冒充 artifact_type、错误 mode/schema 配对、内容哈希漂移。

## 下游消费结果

- 260 回归包与五类失败包经独立 210 Stub（`tests/agents/260/approved_210_stub.py`）消费 → `decision=accepted`。
- 210 release_candidate 经独立 010 合同 Stub（`contracts/test_stage10_package_contracts.consume_stub`）消费 → 通过 catalog producer/consumer 与 Schema 校验。

## 未解析项

- 无。冻结映射（`database_copy_regression` 唯一 artifact_type，`event_data`→`regression-passed-data-orm`、`foundation`→`foundation-regression-report`）保持稳定，未新增/移除 catalog ID；`sql_regression_failed_feedback` 消费者收敛为 210。

## 风险与边界

- 本联调只验证 event_data 双通路；foundation 通路由既有 `tests/agents/260/test_regression.py` 与 `tests/agents/210/test_scheduler.py` 覆盖，不在本 Issue 范围内重复。
- 150/110 的 `sql_regression_route_record` 阶段不在本次最小扩展路径内，未改动。
- 真实 SQLite / 原始模型响应 / 日志均未进入 Git；运行时产物留在本地数据面。
- 本报告不含正式库写入证据之外的任何可复原 CoreBank 数据。
