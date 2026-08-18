## 结论

EAS-72 attempt 2 保持 blocked，未重放其 task、未补写 artifact/receipt、未创建 110/120。本次只补齐 EAS-70 的受治理 task bootstrap，不改变 010、110、120 的业务职责，不写正式库。

## 变更

- `scripts/runtime_bootstrap.py` 是 task 启动前唯一的可执行 preflight entrypoint。
- `src/east_v5/runtime/bootstrap.py` 校验 candidate head、三项代码 hash、root binding、entrypoint 可执行与安全本地 root；失败不暴露物理路径。
- `task_input_envelope/v1` 新增强制 `execution_bootstrap`；adapter 未取得 matching `BootstrapEvidence` 时拒绝运行。
- `config/v5-runtime-bootstrap.json` 将 010/110/120 绑定为 governed-checkout wrapper 消费者，并冻结 controller 与业务职责边界。

## 010/110/120 配置证据

attempt 2 前的三个平台 Agent 均为 `skills=[]`、`runtime_config={}`、`custom_env_key_count=0`，故其配置摘要为同一 canonical SHA-256：`d1944aaf5bae0c09b24a47e7682b933be34457d5c2e1db10a71193e4c180897d`。

新配置源码 SHA-256=`0a522389ebef32431b0760f4e12b31f05a55a2e47a329a63c67dae79e3b46c8a`；当前 adapter/bootstrap/runner SHA-256 分别为 `8f2929d84fa29c5bec86c48b1b64353f1ff16ac5ea22891ed696ee2b2e8b86e6`、`9e4eec2430e3e761a64b2c6d13f02f281c2c5825ef7d6fbb54ebd37413f7ae68`、`74561823367739458ffb99e9e00c948986c59491fcdd89b7efe85ce066eab589`。三 Agent 的实际 after hash 必须由工程助理在新 candidate commit 后写入 attempt 3 启动证据，并与 candidate head 和 preflight 输出逐项比对。未完成安装或其中任一 hash 不一致时不得创建业务 task。

## 零部分推进覆盖

- bootstrap 缺失：`RUNTIME_BOOTSTRAP_MISSING`；
- 未经 preflight 的 adapter：`RUNTIME_BOOTSTRAP_UNVERIFIED`；
- 错误 candidate head：`RUNTIME_BOOTSTRAP_CANDIDATE_HEAD_DRIFT`；
- 代码 hash 漂移：`RUNTIME_BOOTSTRAP_CODE_HASH_DRIFT`；
- entrypoint 缺失或不可编译：`RUNTIME_BOOTSTRAP_ENTRYPOINT_MISSING` / `RUNTIME_BOOTSTRAP_ENTRYPOINT_NOT_EXECUTABLE`；
- registry 不可解析：`RUNTIME_INPUT_RESOLUTION_REJECTED`，无 artifact-registry 文件、receipt 或 next dispatch。

## 验证

`python3 -m unittest tests.runtime.test_adapter -v`：7/7 通过。

`python3 scripts/v5.py check`：360 tests 通过。

## 下一步（工程助理）

提交本地工件形成新 candidate head；从同一 `root_binding_id` 物化 sanctioned fixture，实际安装/调用 wrapper，创建全新的 attempt=3。每条边回传真实 issue/task/agent/runtime UUID、preflight 加载证据、artifact/receipt 三元组、registry read-back、消费结论与 launcher 生成的下一 task 证据。未满足时保持 fail-closed，不以评论、mention 或 dispatch intent 作为顺序边。
