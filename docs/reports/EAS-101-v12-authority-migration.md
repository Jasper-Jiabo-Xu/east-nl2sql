## EAS-101 v11 → v12 权威迁移记录

本记录只描述 Git 控制面工件；不安装或绑定 Skill，不运行业务 task，不写正式库。

| 项目 | v11 | v12 |
| --- | --- | --- |
| Skill identity | `east-v5-runtime-bootstrap-v11` | `east-v5-runtime-bootstrap-v12` |
| graph schema | `east-v5-full-runtime-graph/v11` | `east-v5-full-runtime-graph/v12` |
| state / envelope | v11 | v12 |
| authority baseline | 无逐行批准矩阵 | `config/authority-matrix-v2.json`，17/17 `approved_exact` |
| legacy status | 原工件保持不变 | 仅 additive gate；不从本 Issue 安装、解绑或改平台 |

## 冻结链

- v1 审计工件 SHA-256：`3448bbd828d8bef764d1aa252645128edf3e5a8861d2ebd36fe7265842c982d5`，保持原字节，不在本仓库重写。
- Jiabo A 裁决：`29bd102b-ba2e-4afb-bea5-cb87bcd6862b`；Sol v12-A freeze：`37252aa2-1490-4b8d-9db5-a89ac9faaede`。
- 身份/runtime 基线：EAS-70 `42f91f92-9453-4e7a-a38e-30d629ae07d6`。
- 140 仅有的 v1 记录更正：`7314bbf8…` 被替换为批准值 `1fddd3bcd5380b4b7779ae634a51bf7de99c9d50dd12d5581cbffcba720b8172`；证据为 EAS-45 附件、执行回执和 Sol 验收，见 v2 的 `matrix_correction`。

v12 controller 在首次 preflight 前同时验证 embedded matrix、graph 和 manifest 的 17 个 UUID/runtime/instruction hash，并验证每行完整 enabled-Skill inventory。其期望集合严格为该行 `approved_skill_bindings` 经 `config/skill-identity-resolver-v1.json` 解析出的 workspace Skill UUID，加新 v12 identity UUID；顺序会规范化。authority matrix 继续只保存逻辑 Skill 名，resolver 单独冻结逻辑名→平台 UUID 映射且受 manifest 文件哈希保护；claims 只携带 UUID，不能提交映射。缺失批准 TDD、逻辑名出现在平台 inventory、非 TDD 行出现 TDD、legacy bootstrap/v11、重复或任何额外 Skill 均拒绝。task-time claim 重复同一验证；resolver 缺失、哈希漂移、未知/重复/非 UUID 映射也在写状态前拒绝。任一矩阵、指令、runtime、claim 或 Skill 漂移均拒绝且不持久化 task。

## Supersession

v12 是新的独立 Skill/manifest/config；v11 未被编辑、删除或替换。后续安装仅可在 v12 additive gate 通过后进行，并且不得将 010/110/120 的 legacy bootstrap 或未溯源 TDD 追认为 v12 批准 Skill。
