## 结论

本报告对应 EAS-15 PR #8 的返工提交。COMMON-ENVELOPE、运行期本地注册表、CLI 与脱敏下游 Stub 均只在临时 `runtime_root/vnext/03_构建过程层/issues/EAS-15/{run}/{attempt}` 中执行；未写入参考根、正式交付层或正式数据库。

## 冻结输入与关键哈希

| 文件 | SHA-256 |
| --- | --- |
| `contracts/common/common-envelope.schema.json` | `d13fba17cf19a08c166b67dc55a9b57cbbbeb59bde2d846a6606a03e6b54d6f1` |
| `src/east_v5/artifacts/registry.py` | `2dc8efd165af86ca7d6bfe14cbda0aacc97e2d53792d77de26a540ab8ccf8e1b` |
| `src/east_v5/artifacts/cli.py` | `166a16ec9434b6f3ffc13e5ea7911c90457be2d8c9656228fd6bbb1d4fc42886` |
| `fixtures/artifacts/common-envelope-invalid.json` | `02c6b910d5a978c5c06ddd17af110cbab219c3ebfce82d29ba8c5fdb84e93bf6` |
| `tests/artifacts/test_common_envelope.py` | `06beb7b3e034361fef02aa823ba2bb9d486b33c2e8c41a6989dad2baad83cf6e` |

| `requirements.txt` | `cb9e5058abe248be8579db704a1c9e30e655b30cb7d5dd093bebf97f44766591` |
| `src/east_v5/artifacts/schema.py` | `4f18659421e22e04a63bcca1405fbbcb8f941f318c3342d8ee109009a3925b60` |
| `tests/artifacts/test_common_envelope.py` | `f9019bf4936092b1d058b1cb71c724028daa0affae4912d2c1c35d66c1be375b` |
| `tests/test_governance.py` | `edf0cdf4f342eea7286061d46e90dba43c37b64263bb306ec1820953c1ab0b8a` |

`jsonschema==4.23.0` 是 CI 与本地的 Draft 2020-12 校验依赖。

## 可执行验证

`python3 scripts/v5.py check` 退出码 0，22/22 通过；`git diff --check` 退出码 0。

- locator：注册、解析与迁移均要求目标为存在的普通文件，且仅位于当前 EAS-15/run/attempt 目录；Git 根、参考根、正式交付层、其他 Issue/attempt 均为 `LOCATOR_OUT_OF_ATTEMPT_SCOPE` 或 `LOCATOR_OUT_OF_RUNTIME_ROOT`，发生在持久化前。迁移保持身份哈希不变。
- 重试：从 `config/workflow-policy.json` 读取唯一允许的 `TOOL_TRANSIENT`、`RUNTIME_TEMPORARY`、`LOCATOR_TEMPORARY`；attempt 1/2 为 `retryable`，无论此前是否已有失败，attempt 3 均为 `blocked_manual`。缺失/乱序为 `ATTEMPT_SEQUENCE_INVALID`，重复为 `ATTEMPT_REPLAY_FORBIDDEN`。
- 原子性：注册保存和审计注入失败均转为稳定 `RUNTIME_TEMPORARY`；locator 消失、身份冲突、孤儿/环引用均验证为零部分写入或保留既有状态；并发重放为单一不可变版本。
- CLI 端到端覆盖 `register`、`resolve`、`verify`（三元组）、`lineage`、`transition`、`validate-locator`、`migrate-locator` 和 `audit`。
- Schema：`jsonschema.Draft202012Validator` 在注册前和 `scripts/v5.py schema` 中实际执行；测试证明 Foundation/non-Foundation `qa_id`、未知字段、`$ref`、枚举、attempt 范围均由 Schema 本身拒绝。

## 错误码与 Fixture 覆盖

可执行拒绝 Fixture 覆盖未知/缺失字段、非法 mode/hash/schema/attempt；注册表测试覆盖 attempt 0/1/2/3/4、重复版本异内容 `IDENTITY_CONTENT_CONFLICT`、版本跳号 `VERSION_NOT_CONTIGUOUS`、非法 supersedes `SUPERSEDES_INVALID`、孤儿 `PARENT_ORPHAN`、直接与多层 parent 环 `PARENT_CYCLE`、多父成功、非法状态 `STATUS_TRANSITION_INVALID`、禁止 released `FORMAL_RELEASE_FORBIDDEN` 及 locator 失效/越界。

## 下游 Stub 与安全边界

EAS-16 Stub 注册并迁移 CA/TRG locator、保持父三元组；EAS-19 Stub 模拟 snapshot 冲突并验证既有记录不变；EAS-21 Stub 对 `PENALTY-SOURCE-PACKAGE` 完成通用信封 round-trip。三项均在临时 runtime copy，未读取真实 SQLite。

禁止调用计数：旧字段生成器、字段策略器、表级装配器、旧 registry、独立 ODS 运行调用均为 0（`python3 scripts/v5.py type` 通过）；参考根写入 0、正式交付层写入 0、正式库写入 0。
