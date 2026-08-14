# Agent 120：监管处罚不可丢失事实构造

## 基本信息

| 属性 | 值 |
|------|-----|
| Agent 名称 | 120-监管处罚不可丢失事实构造agent |
| 编号 | 120 |
| 职责 | 从冻结处罚来源包提取不可丢失事实，以可追溯互联网证据补充规则、时点和业务解释 |
| 模型 | `claude-sonnet-4-6` |
| Runtime | Claude（`0e5e9dd9-5135-4937-bb03-92b77adb8395`） |
| 最大并发任务 | 3 |
| 可见性 | workspace |

## 输入

| 来源 | 包类型 | 说明 |
|------|--------|------|
| 110-question-sql阶段调度agent | PENALTY-SOURCE-PACKAGE | 010 冻结的监管处罚来源包 |
| 110 路由的 170/180 审核反馈 | DEEPSEEK-REVIEW-RESULT / GLM-REVIEW-RESULT | 事实包遗漏或理解错误时触发重新提取 |

## 输出

| 消费者 | 包类型 | 说明 |
|--------|--------|------|
| 130-EAST可观察事实构造agent, 140-查询规格agent | PENALTY-FACT-PACKAGE | 监管处罚不可丢失事实包 |

## 权限边界

- 只读源包；产出为候选事实包，不直接提交正式库。
- 互联网检索仅用于补充监管规则、适用范围、业务含义、处罚背景；必须保留 URL、发布机构、发布日期、访问日期、适用时点和证据片段。
- 硬代码校验 source span、quote hash、固定 fact types、证据来源和包 Schema。
- 不覆盖或改写处罚原文；不做 EAST 表字段映射；不生成 question/SQL。

## Skill 引用

- 无专用 Skill；LLM 增强部分依赖运行时模型能力，不绑定外部 Skill。

## 失败码

| 码 | 含义 |
|----|------|
| SCHEMA_VERSION_UNSUPPORTED:PENALTY_SOURCE | 来源包 schema 版本不匹配 |
| SCHEMA_VERSION_UNSUPPORTED:PENALTY_FACT | 事实包 schema 版本不匹配 |
| SCHEMA_VALIDATION_FAILED:PENALTY_SOURCE_PACKAGE | 来源包 JSON Schema 校验失败 |
| SCHEMA_VALIDATION_FAILED:PENALTY_FACT_PACKAGE | 事实包 JSON Schema 校验失败 |
| SOURCE_SPAN_MISSING_ID | span 缺失 ID |
| SOURCE_SPAN_ID_INVALID | span ID 格式不合法 |
| SOURCE_SPAN_DUPLICATE_ID | span ID 重复 |
| SOURCE_SPAN_TEXT_UNRESOLVABLE | span 文本无法解析 |
| LIST_ONLY_HAS_TEXT_REF | list_only 源包不应携带 text_source_ref |
| FULL_TEXT_CONTENT_INCONSISTENT | full_text_status 与 full_text_raw 不一致 |
| FACT_ID_DUPLICATE | 事实 ID 重复 |
| FACT_TYPE_INVALID | 事实类型不在枚举范围内 |
| MUST_PRESERVE_INVALID | must_preserve_in_question 值不合法 |
| EVIDENCE_SNIPPET_EMPTY | 外部证据片段为空 |
| SPAN_REF_NOT_FOUND | span 引用在源包中不存在 |
| SPAN_REF_NOT_IN_SOURCE | 事实引用的 span 不在源包中 |
| QUOTE_HASH_MISMATCH | 事实原文无法追溯到源包 |
| PAYLOAD_NOT_OBJECT | 输入不是 JSON 对象 |

## 版本

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-08-14 | 初始创建（EAS-21） |
