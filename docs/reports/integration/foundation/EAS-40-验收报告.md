# EAS-40 Foundation 铺底端到端联调验收报告

- 实施负责人：EAST DeepSeek 构建员（`7d50dabd-40f8-40a1-807f-041eeed941a9`）
- 冻结基线：`763a93100e2f69e4dda0d7869a35d34d90c74d39`（含 EAS-65 合同修补的 main）
- 联调拓扑：`210 → 220 → 241 → 242 → 260 → 210 → 010`（Foundation，不经过 230/251/252）
- 固定编译器：`east-foundation-insert-compiler/v1`
- 测试文件：`tests/integration/foundation/test_foundation_e2e.py`
- 冻结输入：`fixtures/integration/foundation/foundation-task-initial.json`、`foundation-task-expansion.json`
- 机器可读清单：`docs/reports/integration/foundation/EAS-40-运行清单.json`

## 结论

Foundation 铺底端到端链路可一键复现、全绿。initial/expansion 两条入口走同一机器路径；**event 缺失对象状态（missing_object_state）的恢复链已真实闭合**：260 产出 `FOUNDATION_REQUIRED` 反馈 → 210 路由取得真实 `feedback_ref` 三元组 → 以该三元组构造 `foundation_mode=expansion` 任务（进入 `resume_qa_ref` 与父引用/`input_hashes`）→ 跑完 210→220→241→242→260→210→010 → 发布候选 `resume_qa_ref` 与路由 feedback 三元组完全一致并被 010 Stub 严格消费、正式库未写入。

其余边界不变：230/251/252 及旧生成器/装配器调用数为零；禁止类型在编译与回归两层被拒绝；260 以确定性参数化 SQL 批次在正式库物理隔离 copy 上回归、正式库字节级不变；initial `null` 正例与裸字符串拒绝保留。

本版本为 Sol 返工裁决后的重做 head：expansion Fixture 改非空三元组、event 闭环由「预构造任务」改为「由 `feedback_ref` 真实构造」，非空正例不再基于 `initial_seed` 冒充 expansion。

## 范围与交付物

| 路径 | 内容 |
| --- | --- |
| `tests/integration/foundation/test_foundation_e2e.py` | 端到端集成测试（7 例全过） |
| `fixtures/integration/foundation/foundation-task-initial.json` | initial_seed 最小冻结输入（`resume_qa_ref=null`） |
| `fixtures/integration/foundation/foundation-task-expansion.json` | expansion 最小冻结输入（`resume_qa_ref` 非空三元组） |
| `docs/reports/integration/foundation/EAS-40-运行清单.json` | 机器可读运行清单（输入/输出哈希、血缘） |
| `docs/reports/integration/foundation/EAS-40-验收报告.md` | 本中文报告 |

全部运行产物为脱敏常量，不包含真实数据库、业务数据、ORM、模型原始响应或日志；正式库不写入。

## 验收边界逐条核验

### 1. initial/expansion 与 event 触发 missing_object_state 两种入口

- `test_initial_seed_and_expansion_share_machine_path`：initial（空库 → 1 记录）与 expansion（快照基线 1 → 目标 2，增量 1）均通过 260 回归，`regression_status == passed`，`database_state_delta` 满足 `before/after/delta/passed`。
- `test_event_missing_object_state_routes_to_explicit_foundation_task`（**真实血缘闭环**）：
  1. event 数据引用快照中不存在的对象状态 → 260 `run_event` 产出 `FOUNDATION_REQUIRED` 反馈（`route_target=210`）；
  2. 210 `route_feedback` 返回 `requires_explicit_foundation_task=true` 与真实 `feedback_ref` 三元组；
  3. 由 `feedback_ref` 构造 `foundation_mode=expansion` 任务：`resume_qa_ref == feedback_ref`，且 `feedback_ref` 进入 `parent_artifact_refs`、其 `content_hash` 进入 `input_hashes`；
  4. 该任务真实跑完 210→220→241→242→260→210→010，回归通过；
  5. 发布候选 `resume_qa_ref == feedback_ref == routed["feedback_ref"]`，010 Stub 严格消费，正式库 count 保持 0。

### 2. 230/251/252 与旧生成器/装配器调用次数为零

- `test_forbidden_agents_and_legacy_components_zero_invocation`：
  - `verify_architecture` 通过，`foundation.forbidden_agents == ["230","251","252"]`；
  - 六个 `prohibited_runtime_components` 在 `src/east_v5` 控制面零命中；
  - `sys.setprofile` 调用追踪包裹整条 Foundation 链路，`east_v5.agents.230/251/252` 前缀模块调用数为零。

### 3. 生成对象池/静态状态池/必要初始状态，拒绝禁止类型

- `test_prohibited_record_types_rejected`：
  - 已验数据带 `record_type=transaction` → 260 输入验证抛 `FOUNDATION_PROHIBITED_TYPE_HIT`；
  - 编译器对 `event_owned_tables` 命中表抛 `FOUNDATION_EVENT_OWNED_REJECTED`。

### 4. 260 确定性参数化 SQL 批次在正式库 copy 回归

- `test_260_deterministic_parameterized_batch_in_isolated_copy`：
  - `sql_statements[].sql` 含 `?` 占位符，`rendered_sql_for_audit` 不含 `?`；
  - `sandbox_execution_report.committed == true`，`compiler == east-foundation-insert-compiler/v1`；
  - 同输入两次运行 `foundation_write_batch` 与 `foundation_write_batch_hash` 字节级一致；
  - 正式库 count 保持 0（未写入），隔离 copy count 为 1（仅 delta 落入 copy）。

### 5. 210 组装 Foundation 发布候选并保留 resume_qa_ref、不正式提交

- `test_210_assembles_release_candidate_retaining_null_resume_qa_ref`（initial `null` 正例）：
  - `build_foundation_release` 产出 `release_mode=foundation` 的 `release_candidate`，`resume_qa_ref=null` 保留，`foundation_regression_report_ref` 指向 260 报告；经冻结 010 Stub 严格消费；正式库未写。
- `test_non_null_resume_qa_ref_retained_by_value_and_strictly_consumed_by_010`（expansion 非空正例，非 initial_seed 冒充）：
  - 使用携带非空三元组 `eas40-resume-qa` 的 `foundation-mode=expansion` Fixture；
  - `resume_qa_ref` 经 `build_foundation_release` **原值保留**（同一对象引用，无序列化/ID 提取）；同输入两次装配幂等；010 Stub 严格消费；
  - 反例：`resume_qa_ref` 退化为裸字符串被 010 Stub 拒绝（`RELEASE_CANDIDATE_STUB_REJECTED`）。

## 未解析项

无。

## 复现命令

```bash
python3 -m pytest tests/integration/foundation/test_foundation_e2e.py --import-mode=importlib -q
python3 scripts/v5.py check
git diff --check
```

结果：定向 E2E `7 passed`；`scripts/v5.py check` 349 项全过；`git diff --check` 零输出。
