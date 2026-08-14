# EAS-21 验收报告

日期：2026-08-14

## 结论

EAS-21（Agent 120 监管处罚不可丢失事实构造）核心实现与平台层补齐完成：确定性事实提取、包合同、拒绝路径、下游消费 Stub 和安全边界已通过验证；Multica Agent 120 定义已在平台创建并记录 UUID；三个脱敏 Fixture 的真实最小运行证据已采集。

## 冻结输入

- 实施分支：`feat/agent-120-penalty-fact-extraction`，PR #11
- Base：`main` @ `8345a1d`
- Head：`1ea9569`
- 预检 RECEIPT：`e9b96845-e884-4f37-9de1-87fc33f6c95a`（工程助理签发）

## 变更清单

| 类别 | 文件 | 说明 |
|------|------|------|
| 包目录 | `config/v5-package-catalog.json` | penalty_source/fact 包注册 |
| JSON Schema | `contracts/packages/penalty-source-package.schema.json` | 来源包合同 |
| JSON Schema | `contracts/packages/penalty-fact-package.schema.json` | 事实包合同 |
| 实现代码 | `src/east_v5/agents/east_120/__init__.py` | 模块导出 |
| 实现代码 | `src/east_v5/agents/east_120/extractor.py` | 确定性提取 + 验证 + LLM 增强接口 |
| 合同文档 | `agents/contracts/120.md` | 合同摘要（原有） |
| Agent 定义 | `agents/120/definition.md` | Agent 定义、模型、权限、失败码（新增） |
| Agent 定义 | `agents/120/prompt.md` | 系统提示词全文（新增） |
| 测试 | `tests/agents/east_120/test_extractor.py` | 48 项测试 |
| 脱敏 Fixture | `fixtures/penalty/matched.json` | 合成 matched 场景 |
| 脱敏 Fixture | `fixtures/penalty/list_only.json` | 合成 list_only 场景 |
| 脱敏 Fixture | `fixtures/penalty/text_only.json` | 合成 text_only 场景 |

## Multica Agent 120 平台定义

| 属性 | 值 |
|------|-----|
| Agent UUID | `22533152-db59-4a1b-8d01-5f251c618e6b` |
| 名称 | 120-监管处罚不可丢失事实构造agent |
| 模型 | `claude-sonnet-4-6` |
| Runtime | Claude（`0e5e9dd9-5135-4937-bb03-92b77adb8395`） |
| 最大并发 | 3 |
| 可见性 | workspace |
| 创建时间 | 2026-08-14T07:56:21Z |

## 测试证据

### 冻结验收链

执行 `python3 scripts/v5.py check`：70/70 通过（48 Agent 120 新增 + 22 既有回归）。

执行 `python3 scripts/v5.py security/schema/type`：全部通过。

### 最小真实运行证据

执行 `python3 scripts/agent_120_minimal_evidence.py`，使用 3 个脱敏 Fixture：

| Fixture | 事实数 | 不确定项 | 输入校验 | 输出校验 |
|---------|--------|----------|----------|----------|
| matched.json | 7 | 0 | PASS | PASS |
| list_only.json | 8 | 1 | PASS | PASS |
| text_only.json | 7 | 0 | PASS | PASS |

matched 场景提取事实明细：
- fact-001 subject(yes) 示例人甲
- fact-002 behavior(yes) 违反信贷管理规定
- fact-003 regulatory_conclusion(yes) 《示例法》第X条
- fact-004 result(yes) 警告并罚款人民币30万元
- fact-005 regulatory_conclusion(conditional) 示例监管局
- fact-006 time(yes) 2023-06-15
- fact-007 regulatory_conclusion(yes) 示例罚决字〔2023〕第1号

事实包 content_hash：`f21d5e2f349dfc03054db890c94ff62a43f05fa9e33691ce0a44b558448acd04`

### 测试覆盖

- SourcePackageValidationTests（12）：schema 版本、join_status、sha256、span 重复、未知字段、required 字段、list_only/text_only/matched/empty_content。
- FactPackageValidationTests（7）：schema 版本、fact_id 重复、fact_type 枚举、must_preserve 枚举、evidence snippet 非空、未知字段、完整 fact 包。
- ExtractionTests（7）：三种 join_status 提取、fact_id 连续性、fact_type 合法性、must_preserve 正确性、empty_content。
- BuildFactPackageTests（6）：LLM 事实合并、span 引用校验、不确定性标注、外部证据、证据冲突、list_only 不确定性。
- FactExtractorClassTests（4）：端到端 matched/list_only、re_extract_from_review、span ref 校验。
- CrossSpanAndMultiTypeTests（3）：跨 span 事实多引用、全 fact_type 覆盖、amount 类型。
- DownstreamStubTests（3）：130/140/170 消费 Stub 验证。
- EnvelopeIntegrationTests（1）：common-envelope 包装。
- EnumAndBoundaryTests（5）：枚举合法性、fact_id 格式、无主体→unknown_subject、结构化事实完整性。

## 安全与目录边界

- 3 个 Fixture 均为合成数据（"示例×"、`example.gov.cn`、`demo-*`、占位伪 sha256），无 CoreBank/真实处罚可复原内容。
- PR 13 文件无 `.db/.sqlite/.env/密钥/日志`（`git diff --name-only` 命中 0）。
- 未读取或提交 CoreBank 原始数据、真实 SQLite、模型原始响应、日志、密钥或 `.env`。
- 新增 `agents/120/` 定义文件和 `scripts/agent_120_minimal_evidence.py` 待工程助理提交至 PR。

## 未决项

- `agents/120/definition.md`、`agents/120/prompt.md`、`scripts/agent_120_minimal_evidence.py`、本验收报告和 manifest 模板需工程助理追加 commit 至 PR #11 分支。
- Agent 120 的真实 Multica 任务触发（从 Issue 分派到 Agent 执行端到端）需上游 010/110 就绪后验证。
