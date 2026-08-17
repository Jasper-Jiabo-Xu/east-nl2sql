# EAS-38 question-SQL 阶段端到端联调验收报告

## 结论

question-SQL 阶段端到端联调 **通过**。从冻结脱敏处罚来源包出发，完整闭环
`120 → 130 ↔ 000 → 140 → 150 → 160 → 170/180 → 110 → 210 → 220` 已真实生产—消费打通：

- 正常路径经 160 确定性预审冻结同版双审包，170/180 双 YES 后由 110 组装批准包，210 消费并调度 220，未误启动 data 阶段。
- 六类 error type 各自产生失败 Fixture，110 按最上游受损包路由（120/130/140/150）正确。
- 双审不一致、超时、非法输出均被硬代码边界拦截；三次失败或任一审核 `blocked_manual` 进入人工阻断。
- 170/180 彼此隔离、输入同一冻结 `package_hash`。
- 答案侧字段未进入 benchmark 输入；来源与可观察边界全程不丢失；报告可从冻结 Fixture 一键复现且不改动任何已验证输入。

## 范围与边界

- 冻结基线：`4b83d9d513f01484c71b811512339fd7fe8943ec`（main，`git status --porcelain` 为空）。
- 仅新增本 Issue 指定路径：`tests/integration/qs/`、`fixtures/integration/qs/`、`docs/reports/integration/qs/`。
- 未修改任何已验证上游 Agent、合同、Schema、Fixture；未写正式库；未外发原始/可复原数据。
- 000 以经冻结 Schema 校验的最小脱敏 `constraint-asset-package` 形式消费（不触碰真实 CoreBank SQLite）。

## 数量

| 项目 | 数量 |
|------|------|
| 端到端正常路径 | 1（双 YES → 210 → 220） |
| 六类 error type 失败 Fixture | 6 |
| 160 预审失败 → 150 修复 | 1 |
| 110 最上游路由（跨审核/单审核混合） | 2 |
| 双审超时/非法输出/模型失败 | 3 |
| 三次失败 / blocked_manual 人工阻断 | 2 |
| 答案侧字段隔离、来源/可观察边界、可复现 | 3 |
| 集成测试合计 | **14** |
| 链上既有单元测试（000/120/110/130/140/150/160/170/180） | 51+48+7+12+20+16+10+22+12 = **198** |

## 输入输出哈希（冻结 Fixture，一键复现）

正常路径确定性哈希（`TIME=2026-08-17T00:00:00+00:00`，`run-qs/QA-QS/trace-qs`）：

| 包 | artifact_id | version | content_hash |
|----|-------------|---------|--------------|
| PENALTY-FACT-PACKAGE (120) | `penalty-qs` | 1 | `a921c267b2848dee54bb30a5d16aa58e03a46a9d00310e4ae4a857e4305bc930` |
| EAST-OBSERVABLE-FACT-PACKAGE (130) | `130-observable-run-qs` | 1 | `b0e8132215487902381b259db8f2f874870dcbf8da6af319fe0e2387c1ce4ab1` |
| QUERY-SPECIFICATION-PACKAGE (140) | `140-qspec-run-qs` | 1 | `44f80e543042e7b481668730cad4bff6abd0649a61867939900cf6a5bde86e52` |
| QUESTION-SQL-PENDING-PRECHECK (150) | `150-question-sql-run-qs` | 1 | `0cec0d3303d44f5cd37a119171da910a56f9b61d1429faf36a3a6c0e0a80891c` |
| QUESTION-SQL-PENDING-DUAL-REVIEW (160) | `150-run-qs-QA-QS-dual-review` | 1 | `78d43c479a85b5e84cef4063fb14e455e75a8bb11723f57788b7df3792a583a0` |
| DEEPSEEK-REVIEW-RESULT (170) | `…-deepseek-review` | 1 | `46c768a5eeee2dc711b53162786c58f69d35cf4ac9cb3f71c13ec241a55b58c2` |
| GLM-REVIEW-RESULT (180) | `…-v1-glm-review` | 1 | `2f4487f64df46c1e60377bcc84385f8ae17e02e365ba6dad1cfc61f41d1fc75c` |
| QUESTION-SQL-DUAL-REVIEW-PASSED (110) | `…-dual-review-passed` | 1 | `53377cd151578db1f2e553473ccd60c3612ab62f6a9042ba17032e84cbc03276` |

- 160 冻结双审包 `package_hash = 386b1bd4235e6fd5dd806a867d2cf650429c310bebd63f104aed1d13a4f78281`（170/180 输入同一哈希）。
- 事实构造结果：脱敏来源包确定性提取 7 条事实；映射 3 条到冻结资产（EAST_D001.F001/F002/F003），其余 4 条显式 `NO_EAST_ASSET` 不可观察，`coverage_status=partial`（不伪造表/字段）。

## 测试摘要

运行方式（全绿）：

```
PYTHONPATH=src python3 -m pytest tests/integration/qs/test_question_sql_e2e.py -q   # 14 passed
PYTHONPATH=src python3 -m pytest tests/agents/{000,130,140,150}/test_extractor.py tests/agents/110/test_scheduler.py tests/agents/160/test_precheck.py tests/agents/170/test_review.py tests/agents/180/test_reviewer.py -q
```

| 验收边界 | 覆盖测试 | 结果 |
|----------|----------|------|
| 至少一个端到端 pass | `test_end_to_end_happy_path_reaches_210_and_220` | 通过（→210→220） |
| 每个 error type 一个失败 Fixture | `test_each_error_type_failure_fixture_routes_correctly`（6 类） | 通过 |
| 160 预审失败 | `test_160_precheck_failure_feedback_and_150_repair` | 通过（PC-SQL-005 → 反馈 → 150 修复 attempt2） |
| 双审不一致 | `test_most_upstream_route_across_reviewers` | 通过（130 vs 150 → 130） |
| 双审超时 / 非法输出 | `test_180_timeout_blocks_manual_on_third_attempt`、`test_180_illegal_output_raises_retry_exhausted_before_third_attempt`、`test_170_model_failure_blocks_manual_after_three` | 通过 |
| 170/180 独立且输入同一冻结哈希 | `test_170_180_independent_and_same_frozen_hash` | 通过 |
| 110 按最上游受损包路由 | `test_180_mixed_errors_route_to_most_upstream` | 通过（FACT+QUESTION_SQL → 120） |
| 三次失败进入人工阻断 | `test_third_round_no_routes_to_manual`、`test_reviewer_blocked_manual_routes_to_manual` | 通过 |
| 答案侧字段不进 benchmark 输入 | `test_answer_fields_not_in_benchmark_inputs` | 通过 |
| 来源与可观察边界全程不丢失 | `test_source_and_observability_boundary_preserved` | 通过 |
| 报告可一键复现且不改动输入 | `test_reproducible_and_no_input_mutation` | 通过 |

## 下游消费结果

- **220**：`DataStageCoordinator.begin_event(approved)["dispatches"][0]["target"] == "220"`（结构闭包），210 未误启动 data 阶段。
- **170/180 Stub**：160 冻结包经 `consume_170_180_stub` 语义等价校验；170/180 均以同一 `package_hash` 与 `reviewed_package_ref` 消费。
- **210**：批准包携带 `penalty_fact_package`/`observable_fact_package` 引用与 140 规格一致，来源与可观察边界未丢失。

## 未解析项

- 无待定接口、无静默漏项、无未解释失败。
- 已知且非阻断：180 与 110 对 `QUESTION_FACT_OMISSION` 的 route map 存在冻结口径差异（180→120，110/170→150）。本联调以 110 调度器冻结 `ERROR_ROUTE` 为路由权威，未改动任何上游实现，仅记录观察。

## 风险

- 000 未触碰真实 CoreBank SQLite，本联调以冻结 Schema 校验的最小脱敏资产包消费；真实上游完成后需按合同重跑 130 合同测试。
- 180 的 GLM 语义输出以 `ScriptedGLM` 固定脱敏报告驱动（无模型外发）；真实 GLM/DeepSeek 运行时由各 Agent 主 Issue 的受控探针验收，不在本联调范围。

## 一键复现

```
cd <east-nl2sql checkout @4b83d9d513f01484c71b811512339fd7fe8943ec>
PYTHONPATH=src python3 -m pytest tests/integration/qs/test_question_sql_e2e.py -q
```

所有输入均来自 `fixtures/integration/qs/`（`penalty-source-sanitized.json`、`constraint-asset-approved.json`、`error-type-failure-reports.json`），测试过程不改动任何已验证输入（`test_reproducible_and_no_input_mutation` 断言输入深拷贝前后一致）。
