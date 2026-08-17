# EAS-65 合同修补验收报告

## 结论

`release_candidate.payload.resume_qa_ref` 已与冻结的 `foundation_task_package` 一致：仅接受 `runtime_artifact_ref` 等价三元组 `{artifact_id, version, content_hash}` 或 `null`。210 的 `build_foundation_release` 保持既有对象直传；未加入字符串兼容、对象序列化、`qa_id` 提取或 storage locator。

## 冻结输入与哈希

- 实施基线：`4b83d9d513f01484c71b811512339fd7fe8943ec`。
- 祖先依据：EAS-60 `0c1c2228b7f44c531000316ada1d1d739381ef95`；EAS-63 `503f33d8611afc650bc64e4cc7af41e67ecbd701`。
- `release-candidate-package.schema.json` 修补前 SHA-256：`b5dee12c64b4f7341abe8766d5cca92be335dfa4caa53c743eef43c38ddc4eb3`；修补后：`8848ecb43068791f0cc48a914b68660bd9c9b3d57cee343ae9824fc21f21a433`。
- 脱敏 expansion fixture 文件 SHA-256：`b1e528fd4ef888a7c70501ea723e36fca84ee5cfc961def4cb85d001208a83dd`；其冻结任务包 content hash：`411f5ea3369d72041fbd2c550cb8ca21ba219a51d99f12a9a28469a110eec951`。

## 210 → 010 消费证据

使用 fixture 的非空引用 `{"artifact_id":"resume-qa-fixture","version":1,"content_hash":"dddd…dddd"}` 构建同一 Foundation expansion：

- 运行期 foundation task content hash：`504ba2667eeee723df9584fa9773886bb01c55b586b2db0edec09f1a42aaa0da`。
- 260 foundation regression content hash：`107a20e4605805179f4de31948f4379451c62d32aabee54dcae4d6d4aa1cde11`。
- 210 release candidate content hash：`ac9a98eff3d4915228bd0673e7a1f2c436673a58d1e019c2a833cedd0404742a`。
- 测试断言候选对象与任务包引用为同一对象，连续两次同输入装配的候选包完全相同，并经独立 010 Stub Schema/catalog 消费。

## 变更与覆盖

- `contracts/packages/release-candidate-package.schema.json`：使用已有 `artifact_ref_or_null` 定义替换 `string|null`。
- `fixtures/artifacts/foundation-task-package-expansion-resume-qa-valid.json`：新增不可逆脱敏的非空 expansion 三元组 fixture。
- `tests/agents/210/test_scheduler.py`：验证 fixture、210 无损直传、重复装配稳定、010 Stub 消费，以及输入 hash 漂移和未知字段拒绝。
- `tests/contracts/test_stage10_package_contracts.py`：验证 initial `null`、Foundation 非空三元组、裸字符串、缺字段、未知字段、非法 version/hash 和 event_data 非空引用的拒绝。

治理 manifest、package catalog、catalog Schema 与 input lock 的语义和定位未变；该 Schema 未纳入 `governed_manifest` 的内容哈希输入，故无需重算治理清单。

## 验证命令与结果

- `python3 -m unittest tests.contracts.test_stage10_package_contracts tests.agents.210.test_scheduler`：17 tests passed。
- `python3 scripts/v5.py check`：349 tests passed（同时完成 lint、type、Schema、治理与安全检查）。
- `git diff --check`：通过。

## 风险与后续

未发现合同阻断。EAS-40 集成目录未修改；待本修补合入 main 后，由其原实施链 rebase 并将冲突暴露测试替换为非空三元组成功消费的集成证据。
