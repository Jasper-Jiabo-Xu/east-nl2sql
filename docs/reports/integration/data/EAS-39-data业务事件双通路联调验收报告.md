# EAS-39 data 业务事件双通路联调验收报告

- issue_key: EAS-39（[V5-INT-DATA-001] data 业务事件双通路联调）
- 实施负责人: EAST DeepSeek 构建员（`7d50dabd-40f8-40a1-807f-041eeed941a9`）
- base_sha: `4b83d9d513f01484c71b811512339fd7fe8943ec`
- 报告日期: 2026-08-17

## 结论

**通过。** 业务事件模式下 `210→220→230→{241→242, 251→252}→260→210` 双通路端到端联调全部通过：

- 批准 QS Fixture（110 双审核通过包）经 210 产出独立 `reviewed_question_sql`，220 建立结构闭包、230 建立操作闭包。
- 241 与 251 消费**同一**操作闭包，三个 INSERT 槽位（`slot_fixture_t001_f001`、`slot_fixture_t001_f002`、`slot_fixture_t002_pk001`）一一绑定。
- 242 冻结数据哈希、252 冻结 ORM code_hash；260 仅在正式库 copy 上绑定/回归，正式库字节不变（`formal_unchanged=True`）。
- 210 仅在回归通过后组装 `FORMAL-RELEASE-CANDIDATE`（`release_mode=event_data`），010 下游可消费，未正式提交。
- 五类路由（DATA_VALUE_ERROR / ORM_PLAN_ERROR / SQL_EXECUTION_ERROR / FOUNDATION_REQUIRED / MANUAL_REVIEW_REQUIRED）均有真实证据。

## 范围与拓扑

```
110(批准QS) → 210(reviewed) → 220(结构闭包) → 230(操作闭包)
                                              ├→ 241(bound_data) → 242(verified_bound_data)
                                              └→ 251(restricted_orm) → 252(frozen_orm)
                        260(database_copy_regression, 正式库 copy 沙箱) → 210(release_candidate)
```

## 交付物（仓库相对路径）

- `tests/integration/data/test_event_dual_path.py` — 5 个联调测试。
- `fixtures/integration/data/approved-question-sql.json` — 批准 QS Fixture（110 输出）。
- `fixtures/integration/data/query-specification.json` — 查询规格（140 输出）。
- `fixtures/integration/data/database-read-snapshot.json` — 只读快照（EAS-19 输出）。
- `docs/reports/integration/data/EAS-39-data业务事件双通路联调验收报告.md` — 本报告。

所有文件仅位于允许路径 `tests/integration/data/`、`fixtures/integration/data/`、`docs/reports/integration/data/`，无碰撞、无敏感数据。

## 数量

- 联调测试：5 个（unittest 口径），全部通过。
- 全量回归：`scripts/v5.py check` → lint/type/schema/security 通过，`Ran 347 tests ... OK`（含本联调 5 例）。
- 联调路径真实产出包：reviewed_question_sql、structure_closure、operation_closure、bound_data、verified_bound_data、restricted_orm、frozen_orm、database_copy_regression、release_candidate，共 9 类。

## 输入/输出哈希与关键事实

- `reviewed_artifact_id` = `210-reviewed-eas39-approved`
- 结构闭包 tables = `['FIXTURE_T001', 'FIXTURE_T002']`，fields = `['FIXTURE_T001.F001', 'FIXTURE_T001.F002', 'FIXTURE_T002.PK001']`
- 操作闭包步骤 = `op-001:READ, op-002:CHECK, op-003:INSERT, op-004:INSERT`
- 绑定数据分组摘要 = `{FIXTURE_T001: 2, FIXTURE_T002: 2}`，positive 2 / hard_negative 2
- verified `validated_hash` = `10ca99e735913148…`
- frozen ORM `validated_hash`（code_hash 冻结）= `10ae73f3c47cc71c…`
- 绑定槽位 = `slot_fixture_t001_f001, slot_fixture_t001_f002, slot_fixture_t002_pk001`
- 回归 `regression_status` = `passed`，`formal_unchanged` = `True`
- 回归包 envelope `content_hash` = `aa5af549f450c3a1…`
- release_candidate_id = `210-release-event-regression-bound-data-eas39-run:verified-bound-data`，`release_mode` = `event_data`

## 测试摘要

1. `test_dual_path_chain_260_copy_regression_and_210_release_candidate`：全链路 + 正式库字节不变 + 010 可消费 release_candidate。
2. `test_release_candidate_requires_passed_regression`：失败反馈不得组装为发布候选（`210_EVENT_REGRESSION_REJECTED`）。
3. `test_five_frozen_error_routes_and_conflicts_are_rejected`：冻结路由表五码 + 冲突拒绝（route 冲突、第三次非 manual、manual 非第三次）。
4. `test_realistic_260_failure_productions_route_correctly`：260 真实产出的五类失败反馈路由（DATA_VALUE_ERROR→241、SQL_EXECUTION_ERROR→210、ORM_PLAN_ERROR→251、FOUNDATION_REQUIRED→210、MANUAL_REVIEW_REQUIRED→manual）。
5. `test_210_stub_rejects_schema_title_impersonation_and_hash_drift`：拒绝 Schema title 冒充 artifact_type、错误 mode/schema 配对、内容哈希漂移。

## 下游消费结果

- 260 回归包经独立 210 Stub（`tests/agents/260/approved_210_stub.py`）消费 → `decision=accepted`。
- 210 release_candidate 经独立 010 合同 Stub（`contracts/test_stage10_package_contracts.consume_stub`）消费 → 通过 catalog producer/consumer 与 Schema 校验。

## 未解析项

- 无。冻结映射（`database_copy_regression` 唯一 artifact_type，`event_data`→`regression-passed-data-orm`、`foundation`→`foundation-regression-report`）在本联调中保持稳定，未新增/移除 catalog ID。

## 风险与边界

- 本联调只验证 event_data 双通路；foundation 通路由既有 `tests/agents/260/test_regression.py` 与 `tests/agents/210/test_scheduler.py` 覆盖，不在本 Issue 范围内重复。
- 真实 SQLite / 原始模型响应 / 日志均未进入 Git；运行时产物留在本地数据面。
- 本报告不含正式库写入证据之外的任何可复原 CoreBank 数据。
