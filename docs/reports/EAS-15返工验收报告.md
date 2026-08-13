## 结论

本报告对应 EAS-15 PR #8 的返工提交。COMMON-ENVELOPE、运行期本地注册表、CLI 与脱敏下游 Stub 均只在临时 `runtime_root` 中执行；未写入参考根、正式交付层或正式数据库。

## 冻结输入与关键哈希

| 文件 | SHA-256 |
| --- | --- |
| `contracts/common/common-envelope.schema.json` | `d13fba17cf19a08c166b67dc55a9b57cbbbeb59bde2d846a6606a03e6b54d6f1` |
| `src/east_v5/artifacts/registry.py` | `2dc8efd165af86ca7d6bfe14cbda0aacc97e2d53792d77de26a540ab8ccf8e1b` |
| `src/east_v5/artifacts/cli.py` | `166a16ec9434b6f3ffc13e5ea7911c90457be2d8c9656228fd6bbb1d4fc42886` |
| `fixtures/artifacts/common-envelope-invalid.json` | `02c6b910d5a978c5c06ddd17af110cbab219c3ebfce82d29ba8c5fdb84e93bf6` |
| `tests/artifacts/test_common_envelope.py` | `06beb7b3e034361fef02aa823ba2bb9d486b33c2e8c41a6989dad2baad83cf6e` |

## 可执行验证

`python3 scripts/v5.py check` 退出码 0，21/21 通过；`git diff --check` 退出码 0。

- locator：注册、解析与迁移均要求目标为存在的普通文件且位于 `runtime_root` 内；越界为 `LOCATOR_OUT_OF_RUNTIME_ROOT`，不存在为 `LOCATOR_MISSING`，均发生在持久化前。迁移保持身份哈希不变。
- 重试：`STORAGE_FAILURE`、`AUDIT_FAILURE`、`LOCATOR_MISSING` 作为唯一临时错误进入按 attempt 隔离的 retry state；attempt 1/2 为 `retryable`，attempt 3 为 `blocked_manual`，其余错误为 `RETRY_ERROR_NOT_TRANSIENT`。
- 原子性：注册保存失败、locator 消失、身份冲突、孤儿/环引用均验证为零部分写入或保留既有状态；并发重放为单一不可变版本。
- CLI 端到端覆盖 `register`、`resolve`、`verify`（三元组）、`lineage`、`transition`、`validate-locator`、`migrate-locator` 和 `audit`。
- Schema：Draft 2020-12 明列三种 mode，并以 `if/then/else` 声明非 Foundation 的 `qa_id` 必填；Foundation 允许独立任务为空或恢复任务携带 `qa_id`。

## 错误码与 Fixture 覆盖

可执行拒绝 Fixture 覆盖未知/缺失字段、非法 mode/hash/schema/attempt；注册表测试覆盖 attempt 0/1/2/3/4、重复版本异内容 `IDENTITY_CONTENT_CONFLICT`、版本跳号 `VERSION_NOT_CONTIGUOUS`、非法 supersedes `SUPERSEDES_INVALID`、孤儿 `PARENT_ORPHAN`、直接与多层 parent 环 `PARENT_CYCLE`、非法状态 `STATUS_TRANSITION_INVALID`、禁止 released `FORMAL_RELEASE_FORBIDDEN` 及 locator 失效/越界。

## 下游 Stub 与安全边界

EAS-16 Stub 注册并迁移 CA/TRG locator、保持父三元组；EAS-19 Stub 模拟 snapshot 冲突并验证既有记录不变；EAS-21 Stub 对 `PENALTY-SOURCE-PACKAGE` 完成通用信封 round-trip。三项均在临时 runtime copy，未读取真实 SQLite。

禁止调用计数：旧字段生成器、字段策略器、表级装配器、旧 registry、独立 ODS 运行调用均为 0（`python3 scripts/v5.py type` 通过）；参考根写入 0、正式交付层写入 0、正式库写入 0。
