# 架构快照状态

本目录同时保存现行人工裁决记录和迁移前历史快照。2026-08-13 起，运行期机器权威入口为仓库根目录的：

- `governance-manifest.json`
- `config/v5-architecture.json`
- `config/v5-package-catalog.json`
- `contracts/v5-runtime-packages.schema.json`

现行可读架构为 `docs/architecture/V5双通路与Foundation架构.md` 与 `docs/architecture/EAST-V5双通路.drawio`。

## 文件状态

| 文件 | 状态 | 用途 |
|---|---|---|
| `V5最终共识与Issue迁移合同.md` | 现行人工裁决记录 | 解释 CA-V0.3.0、TRG-V1.0.0 与 Issue 迁移背景；机器执行仍以治理 manifest 为准 |
| `V5项目上下文总览.md` | 已迁移可读总览 | 新会话导航，不替代机器合同 |
| `V5项目文件树与归档规范.md` | 已迁移逻辑归档参考 | 描述运行数据面；Git 实际目录以 `config/artifact-layout.json` 为准 |
| `EAST数据集大图-V5-人工.drawio` | 历史快照 | 禁止作为现行拓扑或 Agent 合同 |
| `agent划分与任务输入输出清单-V2.xlsx` | 历史快照 | 禁止作为现行包字段、生产者或消费者合同 |

历史文件不得无痕覆盖或删除；需要恢复历史背景时可读取，但实施、测试和验收不得依赖其冲突内容。
