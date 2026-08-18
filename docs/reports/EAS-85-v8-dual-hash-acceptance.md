# EAS-85 v8 双哈希合同验收报告

## 结论

v8 从 `origin/main` 的 `53480044c9651aa895795cada5be7a0bf3973caf` 独立派生，保留 v7 已合入的元数据物化闸门，并新增 source/archive 与 runtime materialized 的逐字节双哈希边界。v6 与 v7 资产未修改。

## 当前冻结候选

| 边界 | SHA-256 |
| --- | --- |
| v8 source/archive 原始 `SKILL.md` | `a2c023166714a9b12d3918fdc61bc89423bf7a08ca28ff30b1b5c273da99eb51` |
| v8 runtime materialized `SKILL.md` | `554998de852d1e50909487086c2fd90fae91e26a01dd44f840f087f1514093f0` |

物化器合同为 `multica_daemon_frontmatter_normalizer/v8`。唯一批准的字节差异为 description 的 YAML 双引号，以及 closing frontmatter 后空行由一个变为两个；name、正文、版本和 envelope 均为 v8。manifest 对 source/archive/runtime 三个边界分别记录、分别校验，禁止互换或解析后绕过。

## 验证

- `python3 -m unittest tests.runtime.test_skill_bundle_v8`：14/14 通过。
- `python3 -m unittest tests.runtime.test_skill_bundle_v7`：12/12 通过，确认历史 v7 未受影响。
- `python3 -m compileall -q skills/east-v5-runtime-bootstrap-v8 tests/runtime/test_skill_bundle_v8.py`：通过。
- `git diff --check`：通过。

v8 测试覆盖两次干净 archive 解包与物化、五层哈希（source、archive、manifest、support files、runtime）、010→唯一 110→唯一 120 受限 Stub 链、控制 launcher 回放，以及 source/archive/runtime 漂移、未知物化器、非批准字节差异、v6/v7 envelope、旧 idempotency 重放等 fail-closed 拒绝。

## 发布边界

未发布新 skill、未启动真实 launcher 探针、未放行 120、未写正式库。最终 candidate head、archive、manifest、controller 与 support-files 哈希必须由工程助理在提交后的干净 head 重建并回写，再由 Sol 冻结。
