# EAS-40 Foundation 铺底端到端联调验收报告

- 实施负责人：EAST DeepSeek 构建员（`7d50dabd-40f8-40a1-807f-041eeed941a9`）
- 冻结基线：`763a93100e2f69e4dda0d7869a35d34d90c74d39`（含 EAS-65 合同修补的 main）
- 联调拓扑：`210 → 220 → 241 → 242 → 260 → 210 → 010`（Foundation，不经过 230/251/252）
- 固定编译器：`east-foundation-insert-compiler/v1`
- 测试文件：`tests/integration/foundation/test_foundation_e2e.py`
- 冻结输入：`fixtures/integration/foundation/foundation-task-initial.json`、`foundation-task-expansion.json`
- 机器可读清单：`docs/reports/integration/foundation/EAS-40-运行清单.json`

## 结论

Foundation 铺底端到端链路可一键复现、全绿。initial/expansion 两条入口走同一机器路径；event 侧缺失对象状态（missing_object_state）正确回路由为显式 Foundation 任务；Foundation 运行全程 230/251/252 及旧生成器/装配器调用数为零；禁止类型在编译与回归两层被拒绝；260 以确定性参数化 SQL 批次在正式库物理隔离 copy 上回归、正式库字节级不变；210 组装 Foundation 发布候选并保留 `resume_qa_ref`（`null` 与非空三元组均覆盖）、不正式提交。

原 `EAS-40-RESUME-QA-REF-TYPE-CONFLICT` 已由 Sol 裁决并随 EAS-65（PR #39）合入 main 解决：`release_candidate.payload.resume_qa_ref` 修为 `runtime_artifact_ref|null`，210 对象无损直传，本报告已同步为「非空三元组原值保留并被 010 Stub 严格消费」的正例。

## 范围与交付物

| 路径 | 内容 |
| --- | --- |
| `tests/integration/foundation/test_foundation_e2e.py` | 端到端集成测试（7 例全过） |
| `fixtures/integration/foundation/foundation-task-initial.json` | initial_seed 最小冻结输入 |
| `fixtures/integration/foundation/foundation-task-expansion.json` | expansion 最小冻结输入 |
| `docs/reports/integration/foundation/EAS-40-运行清单.json` | 机器可读运行清单（输入/输出哈希） |
| `docs/reports/integration/foundation/EAS-40-验收报告.md` | 本中文报告 |

全部运行产物为脱敏常量，不包含真实数据库、业务数据、ORM、模型原始响应或日志；正式库不写入。

## 验收边界逐条核验

### 1. initial/expansion 与 event 触发 missing_object_state 两种入口

- `test_initial_seed_and_expansion_share_machine_path`：initial（空库 → 1 记录）与 expansion（快照基线 1 → 目标 2，增量 1）均通过 260 回归，`regression_status == passed`，`database_state_delta` 满足 `before/after/delta/passed`。
- `test_event_missing_object_state_routes_to_explicit_foundation_task`：event 数据引用快照中不存在的对象状态 → 260 产出 `FOUNDATION_REQUIRED` 反馈（`route_target=210`）→ 210 `route_feedback` 返回 `requires_explicit_foundation_task=true` → 闭环后以 expansion 任务走 Foundation 链路补齐该对象状态，回归通过。

### 2. 230/251/252 与旧生成器/装配器调用次数为零

- `test_forbidden_agents_and_legacy_components_zero_invocation`：
  - `verify_architecture` 通过，`foundation.forbidden_agents == ["230","251","252"]`；
  - 六个 `prohibited_runtime_components`（legacy_field_generator/legacy_field_policy/legacy_table_assembler/legacy_registry/independent_ods_generator/independent_ods_validator）在 `src/east_v5` 控制面零命中；
  - 用 `sys.setprofile` 调用追踪包裹整条 Foundation 链路，`east_v5.agents.230/251/252` 前缀模块调用数为零。

### 3. 生成对象池/静态状态池/必要初始状态，拒绝禁止类型

- `test_prohibited_record_types_rejected`：
  - 已验数据带 `record_type=transaction`（命中 `prohibited_record_types`）→ 260 输入验证抛 `FOUNDATION_PROHIBITED_TYPE_HIT`；
  - 编译器对 `event_owned_tables` 命中表抛 `FOUNDATION_EVENT_OWNED_REJECTED`。

### 4. 260 确定性参数化 SQL 批次在正式库 copy 回归

- `test_260_deterministic_parameterized_batch_in_isolated_copy`：
  - `sql_statements[].sql` 含 `?` 占位符，`rendered_sql_for_audit` 不含 `?`（仅审计展示，不执行）；
  - `sandbox_execution_report.committed == true`，`compiler == east-foundation-insert-compiler/v1`；
  - 同输入两次运行 `foundation_write_batch` 与 `foundation_write_batch_hash` 字节级一致；
  - 正式库 count 保持 0（未写入），隔离 copy count 为 1（仅 delta 落入 copy）。

### 5. 210 组装 Foundation 发布候选并保留 resume_qa_ref、不正式提交

- `test_210_assembles_release_candidate_retaining_null_resume_qa_ref`：
  - `build_foundation_release` 产出 `release_mode=foundation` 的 `release_candidate`，`resume_qa_ref=null` 保留，`approved_question_sql_ref`/`event_regression_passed_ref` 为 `null`，`foundation_regression_report_ref` 指向 260 报告；
  - 经冻结 010 Stub（`consume_stub("release_candidate","010",…)`）严格消费，返回 `content_hash`；
  - 正式库 count 保持 0，未发生正式提交。
- `test_non_null_resume_qa_ref_retained_by_value_and_strictly_consumed_by_010`：
  - 任务包 `resume_qa_ref={artifact_id,version,content_hash}` 经 `build_foundation_release` **原值保留**（同一对象引用，无序列化/ID 提取）；
  - 发布候选与任务包的 `resume_qa_ref` 恒等且等于输入三元组；同输入两次装配产出同一 `release_candidate`（幂等）；
  - 冻结 010 Stub 严格消费该非空三元组发布候选；
  - 反例：`resume_qa_ref` 退化为裸字符串时，010 Stub 严格拒绝（`RELEASE_CANDIDATE_STUB_REJECTED`）。

## 未解析项

无。原 `resume_qa_ref` 类型冲突已由 Sol 裁决并经 EAS-65 修补解决。

## 复现命令

```bash
python3 -m pytest tests/integration/foundation/test_foundation_e2e.py --import-mode=importlib -q
```

结果：`7 passed`。
