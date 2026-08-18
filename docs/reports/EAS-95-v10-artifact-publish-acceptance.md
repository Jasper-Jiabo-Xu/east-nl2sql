# EAS-95 v10 制品原子发布验收报告

## 结论

v10 以全新 `east-v5-runtime-bootstrap-v10` identity 修复 daemon-local
制品发布闭包。原有 v6/v7/v8/v9 目录未改动。`stage=committed` 仅在同一
daemon-local publication 已原子落盘、独立解释器 read-only resolve 成功、且
唯一下游 task 已读回之后返回。

## 根因与修复

v9 先写 launch outbox/创建下游，再写 artifact registry；而 registry 根又只由
调用方传入的 runtime root 决定。因此不同 task workdir 的解析器可看见 receipt
或 outbox 却不可见 payload/registry。

v10 要求 0700 daemon root 及其 0600 root-binding marker。每个绑定下的单一
`artifact-publication-v10.json` 通过 fsync + replace 同时保存 records、payload、
receipt、outbox 与 journal。对外创建 issue/task 前，controller 使用新解释器在
该根执行只读 `resolve-preflight`；失败会清除 outbox，且不会创建下游 issue/task。

## 哈希与身份边界

- Skill：`east-v5-runtime-bootstrap-v10` / `v10`
- envelope：`task_input_envelope/v10`、`launcher_control_envelope/v10`
- bootstrap：`east-v5-runtime-bootstrap/v10`
- key contract：`east-v5-launch-idempotency/v10`
- packager receipt、manifest、source archive 与 runtime materialization 均由
  `tests.runtime.test_skill_bundle_v10` 重算并验证；最终 head 哈希由交付助理提交后
  机械生成，实施阶段不伪造最终 hash。

## 验证

`python3 -m unittest tests.runtime.test_skill_bundle_v10 -v`

覆盖全新 010→110→120、不同进程同 key 重放零新增、不同 task workdir 消费同一
daemon publication、v6/v7/v8/v9 envelope/key/hash 拒绝、registry 冲突、缺失依赖、
绑定/版本/父链漂移、launch failure 零 outbox、控制入口重放及私有 descriptor。
所有 fixture 均为不可逆脱敏 fixture；未写正式库，也未放行 120 后链路。
