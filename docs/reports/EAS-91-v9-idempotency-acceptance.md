# EAS-91 v9 可复算幂等键验收报告

## 结论

v9 以独立 identity `east-v5-runtime-bootstrap-v9` 实现。控制与业务 launch key
由 `east-v5-launch-idempotency/v9` 的 canonical contract 计算；在写 outbox 及
调用 `multica issue create` 前均逐字节复算。调用方上报不匹配的 key 一律以
`RUNTIME_LAUNCH_IDEMPOTENCY_KEY_DERIVATION_DRIFT` 闭合拒绝。

## 物化合同

- key-contract 字段：版本、Skill name/version、manifest SHA-256、candidate head、
  root binding、run/trace/QA/attempt、from/to 和 input reference。
- manifest、build receipt、outbox entry 以及 `task_execution_receipt/v9` 均记录
  derivation contract 与 fingerprint。
- v9 packager 保持 archive、manifest、controller、support files、Skill source/runtime
  materialization 的独立 SHA-256 边界；最终候选 head 与五层哈希由工程助理在提交后
  用 `scripts/pack_skill.py --head <final-head>` 物化并回填交接包。

## 本地验证

命令：`python3 -m unittest tests.runtime.test_skill_bundle_v9 -v`

- 16/16 通过。
- 合法 control key 跨进程重放复用同一 control issue/task；业务链的同一合法 v9
  key 重放复用既有 task，launch 数不增加。
- 旧 control key 前缀 `dd5fc0f2`、`774d253e` 与旧 business key 前缀
  `6a2298ba`、`b0f40fb9` 都在 outbox、issue/task 和 120 前拒绝。
- v6/v7/v8 envelope 与 controller/runtime/manifest hash 漂移均在零 outbox、
  零 launcher record 前拒绝。

## 未包含的外部动作

本报告不替代合并后的平台 probe。根据冻结合同，工程助理须在最终提交、PR 和
probe-only 安装后，重新采集平台 before/after task 列表、实际两阶段 control/business
重放结果与 `010→110→唯一120` 证据；不得复用 EAS-88～EAS-90。
