# EAS-44 验收报告

日期：2026-08-13

## 结论

EAS-44 的 Git 控制面迁移已实现：非 Foundation 双通路、Foundation 专用链、Agent/包职责、运行时 Schema、Prompt/Skill、固定 Foundation INSERT 编译器、架构图与自动一致性校验采用同一冻结口径。16 个受影响 Multica Issue 已追加幂等覆盖条款。

本分支以 EAS-14 PR #6 的治理骨架为前置基线；合并 EAS-44 前应先合并该前置 PR，或由维护者确认等价基线。

## 冻结输入

- 人工裁决日期：2026-08-11；项目负责人：Jiabo Xu。
- 约束资产：CA-V0.3.0。
- 带类型引用图：TRG-V1.0.0。
- 治理 manifest：`governance-manifest.json`，内容哈希 `8b28baa6263fa7935b76675abf57586c00a296615fcdd09dac80dcce64a1fae7`。

## 关键制品哈希

| 制品 | SHA-256 |
|---|---|
| `config/v5-architecture.json` | `bb09a18ac0be0f5c75b738911f86830bb0c5aaf25a86822d4e4dbec4ccca2dbb` |
| `config/v5-package-catalog.json` | `76693d1f094cad6a768fa33d527e85edc8108647409dfd0153c8bb42a3819c5e` |
| `contracts/v5-runtime-packages.schema.json` | `96a6a36a23865a59b25bcaf8dfd687d8ed53c14cd55bb4335208c03cadda98e9` |
| `src/east_v5/foundation/compiler.py` | `dfc68a4db6d7932f837b9eefdb67e3bf286132865a0b369ee2866fa25447e8a2` |
| `docs/architecture/EAST-V5双通路.drawio` | `f8299ca16d41f00d11eb6afc76fafcd89a0cc524a37c0f2f51ab576751bcaa70` |

## 测试证据

执行 `python3 scripts/v5.py check`：10/10 通过。覆盖：

- 架构/包 fan-out 漂移和活跃合同残留扫描；
- 编译器字节级确定性、参数化绑定和拓扑排序；
- EVENT_OWNED、依赖缺失、环依赖、未知字段、非法标识符、非标量值写前拒绝；
- SQLite 下游 Stub 真实消费、外键顺序、失败事务整体回滚；
- 治理 Schema、manifest、根目录隔离、版本漂移和安全扫描。

执行 `git diff --check`：通过。

## Issue 迁移证据

`scripts/migrate_eas44_issue_contracts.py --apply` 已迁移 EAS-13、16、18、20、29—36、39—42；随后 dry-run 返回 `changed=[]`，证明 16 个目标均已存在唯一迁移标记。脚本保留历史正文，仅追加 2026-08-13 现行覆盖条款。

## 安全与目录边界

- 仅在 `multica repo checkout` 创建的专用 Git checkout 实施。
- 未复制参考源的旧实现、数据库、KB 或候选产物。
- 未读取或提交 CoreBank 原始数据、真实 SQLite、模型原始响应、日志、密钥或 `.env`。
- 编译器只生成批次，不连接数据库；测试仅使用内存 SQLite 脱敏 Fixture。

## 人工验收点

- 先处理前置 EAS-14 PR #6，再审阅本 PR 的 diff 与 CI。
- 人工确认后由 EAS-45 统一调度 EAS-15 及后续最小依赖集合；本实现不自动合并、不发布、不写正式库。
