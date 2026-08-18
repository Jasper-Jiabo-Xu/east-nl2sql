## EAS-70 task bootstrap 合同

`task_input_envelope/v1` 的 `execution_bootstrap` 是可执行加载合同，不是给业务 Agent 的自然语言提示。它包含 candidate base/head、adapter/bootstrap/runner 三个 SHA-256、daemon-local resolver context 以及由该 context 重算的 `root_binding_id`。

平台 task 启动时必须在已挂载的受治理 checkout 中执行：

```text
python3 scripts/runtime_bootstrap.py preflight --envelope-file <受控信封文件>
```

成功前不得导入或调用 `RuntimeAdapter`。该命令只输出 candidate head、三个代码 hash、binding 与固定 entrypoint；不输出物理 root、参考源、业务 payload 或 registry locator。

`RuntimeBootstrap` 顺序验证：checkout HEAD、clean checkout、adapter/bootstrap/runner hash、runner 可编译、root binding 和本地数据根边界。任一步失败均为稳定 fail-closed，且不会登记 artifact、生成 receipt 或创建下游 task。

`RuntimeAdapter` 还要求同一信封的不可变 `BootstrapEvidence`；缺失或漂移时返回 `RUNTIME_BOOTSTRAP_UNVERIFIED` 或 `RUNTIME_BOOTSTRAP_EVIDENCE_DRIFT`。因此业务 Agent 只能生产/消费冻结包；原子登记、read-back、receipt、`launch_next_task` 与 task UUID 读取仍由 adapter/controller 独占。

受治理配置 `config/v5-runtime-bootstrap.json` 固定覆盖 010、110、120，职责为 `unchanged`。工程助理在新 candidate 已提交后读取该配置，给三项 Agent 的 task runtime 安装同一 governed-checkout wrapper；这不是把 checkout/adapter 调用再写入 Agent Prompt，也不把 project local_directory 当数据 root。
