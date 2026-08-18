## 结论

EAS-72 attempt 2 保持 blocked，未重放其 task、未补写 artifact/receipt、未创建 110/120。本次只补齐 EAS-70 的受治理 task bootstrap，不改变 010、110、120 的业务职责，不写正式库。

## 变更

- `skills/east-v5-runtime-bootstrap-v1/` 提供精简 `SKILL.md`、单一 manifest、supporting runner、claim verifier 与 adapter/bootstrap/runner/schema 的 hash-frozen 副本。
- `src/east_v5/runtime/bootstrap.py` 要求 `execution_bootstrap.skill_bundle` 包含 Skill 名称、版本和 manifest hash；任一缺失/漂移均在业务处理前拒绝。
- `config/v5-runtime-bootstrap.json` 将 010/110/120 绑定为 `skill_bundle` 消费者，并冻结 controller 与业务职责边界。

## 010/110/120 配置证据

attempt 2 前的三个平台 Agent 均为 `skills=[]`、`runtime_config={}`、`custom_env_key_count=0`，故其配置摘要为同一 canonical SHA-256：`d1944aaf5bae0c09b24a47e7682b933be34457d5c2e1db10a71193e4c180897d`。

新配置、Skill manifest、Skill `SKILL.md` 与所有 supporting-file hash 由 `skills/east-v5-runtime-bootstrap-v1/manifest.json` 冻结。三 Agent 的实际 after hash 必须由工程助理在新 candidate commit 后写入 3/3 claim preflight 证据，并覆盖 Agent UUID、runtime UUID、instructions hash、完整 enabled Skill ID 集合及 Skill manifest hash。未完成 additive binding 或其中任一 hash 不一致时不得创建业务 task。

## 零部分推进覆盖

- bootstrap 缺失：`RUNTIME_BOOTSTRAP_MISSING`；
- 未经 preflight 的 adapter：`RUNTIME_BOOTSTRAP_UNVERIFIED`；
- 错误 candidate head：`RUNTIME_BOOTSTRAP_CANDIDATE_HEAD_DRIFT`；
- 代码 hash 漂移：`RUNTIME_BOOTSTRAP_CODE_HASH_DRIFT`；
- entrypoint 缺失或不可编译：`RUNTIME_BOOTSTRAP_ENTRYPOINT_MISSING` / `RUNTIME_BOOTSTRAP_ENTRYPOINT_NOT_EXECUTABLE`；
- registry 不可解析：`RUNTIME_INPUT_RESOLUTION_REJECTED`，无 artifact-registry 文件、receipt 或 next dispatch。
- Skill manifest/支持文件/绑定/provider 漂移：`RUNTIME_SKILL_*`，无业务 artifact、receipt 或下游 task。

## 验证

`python3 -m unittest tests.runtime.test_adapter tests.runtime.test_skill_bundle -v`：12/12 通过。

`python3 scripts/v5.py check`：360 tests 通过。

## 下一步（工程助理）

提交本地工件形成新 candidate head；由工程助理导入不可变 workspace Skill、对 010/110/120 additive binding 并完成 3/3 claim preflight。通过后才创建全新的 attempt=3。每条边回传真实 issue/task/agent/runtime UUID、Skill 加载证据、artifact/receipt 三元组、registry read-back、消费结论与 launcher 生成的下一 task 证据。未满足时保持 fail-closed，不以评论、mention 或 dispatch intent 作为顺序边。
