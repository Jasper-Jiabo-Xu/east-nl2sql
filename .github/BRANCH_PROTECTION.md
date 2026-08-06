# main 分支保护与 05 发布门禁配置

> EAST NL2SQL V5 协作与发布安全规则 —— 可复现的 GitHub 配置说明。
> 仓库管理员按本文在 GitHub Settings 中逐项配置；配置完成后截图留档。

## 1. 前提

- 仓库：`Jasper-Jiabo-Xu/east-nl2sql`
- 保护分支：`main`
- 配置入口：GitHub 仓库 → Settings → Branches → Add branch protection rule
- 权限要求：仓库管理员（admin）

## 2. main 分支保护规则

### 2.1 Branch name pattern

```
main
```

### 2.2 必须启用的保护

| 规则 | 设置 | 原因 |
| --- | --- | --- |
| **Require a pull request before merging** | ON | Agent 不得直接推送到 main；所有变更必须经过 PR |
| └ Require approvals | **2** | 至少两位审批者（含一名人类管理员） |
| └ Dismiss stale pull request approvals when new commits are pushed | ON | 新提交后旧审批失效 |
| └ Require review from Code Owners | ON（若有 CODEOWNERS） | 关键目录需指定所有者审批 |
| **Require status checks to pass before merging** | ON | PR 必须通过 CI 检查 |
| └ Require branches to be up to date before merging | ON | 防止合并冲突绕过检查 |
| **Require conversation resolution before merging** | ON | 所有 PR 评论线程必须解决 |
| **Require signed commits** | ON | 提交可追溯 |
| **Require linear history** | ON | 禁止 merge commit，仅允许 squash/rebase |
| **Do not allow bypassing the above settings** | ON | 包括管理员在内均受约束 |
| **Restrict who can push to matching branches** | ON | 仅限指定的发布管理员 |
| **Allow force pushes** | OFF | 禁止 force push |
| **Allow deletions** | OFF | 禁止删除分支 |

### 2.3 状态检查要求（若配置 CI）

CI 工作流必须包含以下检查，且在 branch protection 的 "Status checks that are required" 中勾选：

- `security-scan`：安全扫描（禁止 API Key、.env、Token、真实数据、LLM 原始缓存）
- `manifest-verify`：若 PR 涉及 `05_新版本交付层/`，验证 manifest 完整性

## 3. Agent 推送禁令

**Agent 绝不允许直接推送 main 分支。** 所有 Agent 产出的代码变更流程：

1. Agent 在过程层分支（如 `feature/EAS-XXX`）上工作
2. 推送该分支到 GitHub
3. 创建 Pull Request 到 `main`
4. PR 经双人审批、CI 通过后合并
5. 发布类 PR 还需满足第 4 节的额外门禁

Agent 运行时不得持有 main 分支的推送权限。若通过 API Token 操作，Token 应限定为仅创建 PR 而非直接推送 main。

## 4. 05_新版本交付层 发布门禁

发布到 `05_新版本交付层/` 的 PR 在满足第 2 节基础保护外，还必须通过以下额外门禁：

### 4.1 发布前检查清单

| 门禁项 | 要求 | 验证方式 |
| --- | --- | --- |
| **来源追溯** | 所有交付物可追溯到 `01_来源冻结层/` 中的冻结文件或已批准人工决策 | PR 模板中逐项引用 `source_ref` |
| **Manifest 哈希** | 每个交付文件提供 `sha256`；整体交付提供 `delivery_content_hash` 和 `package_id` | 与 `发布清单.json` 交叉校验 |
| **安全扫描** | 禁止 API Key、.env、Token、个人凭据、CoreBank 真实数据、LLM 原始缓存 | 自动扫描或人工审查 |
| **验收报告** | 对应阶段的审核/验证/回归报告全部通过 | PR 模板引用报告路径和结论 |
| **人工批准** | 项目负责人或指定审批人明确批准 | PR review approval |
| **PR 合并** | 通过受保护的 PR 合并进入 main；不直接推送 | GitHub branch protection 强制执行 |

### 4.2 发布流程

```text
过程层候选物（03_构建过程层/）
  → 通过全部验收 + 安全扫描
  → 生成发布清单（含文件哈希 + package_id + delivery_content_hash）
  → 创建 PR（填写 PR 模板全部字段）
  → 人工审批（至少 1 名项目负责人）
  → CI 检查通过
  → Squash merge 到 main
  → 交付层资产生效
```

### 4.3 禁止的发布行为

- 直接推送 `05_新版本交付层/` 中的文件到 main
- 修改已发布版本的既有文件而不创建新版本
- 发布未经过双审核、硬编码验证或回归的 question-SQL / data / Foundation 产物
- 发布包含 API Key、.env、Token、CoreBank 数据或 LLM 原始缓存的任何文件
- 绕过 PR 模板和发布清单直接合并

## 5. 配置验证

完成 GitHub 配置后，执行以下验证：

1. 尝试直接推送 main → 应被拒绝
2. 尝试 force push main → 应被拒绝
3. 创建不含审批的 PR → 应无法合并
4. 创建包含 `05_新版本交付层/` 变更的 PR → PR 模板中发布门禁部分必须全部打勾

## 6. 恢复与例外

- 紧急修复需绕过保护时，必须由两名管理员同时批准，并在发布总账中记录原因、时间和审批人。
- 例外记录保存在 `00_治理与合同/发布规范/` 中，作为审计凭据。
