# Agent 130：EAST可观察事实构造

## 基本信息

| 属性 | 值 |
|------|-----|
| Agent 名称 | 130-EAST可观察事实构造agent |
| 编号 | 130 |
| 职责 | 迭代调用000，将处罚事实映射为EAST可直接/间接观察的事实、表字段路径和可观察边界 |
| 模型 | `glm-5.1` |
| Runtime | GLM（`be8a07a2-4dce-465a-8ca5-b0119faa70cd`） |
| 最大并发任务 | 1 |
| 可见性 | workspace |

## 输入

| 来源 | 包类型 | 说明 |
|------|--------|------|
| 120-监管处罚不可丢失事实构造agent | PENALTY-FACT-PACKAGE | 监管处罚不可丢失事实包 |
| 000-资产检索agent | CONSTRAINT-ASSET-PACKAGE | 约束资产查询结果包 |
| 110-question-sql阶段调度agent | 170审核结果包/180审核结果包 | 审核路由回退：EAST映射错误 |

## 输出

| 消费者 | 包类型 | 说明 |
|--------|--------|------|
| 000-资产检索agent | CONSTRAINT-QUERY-REQUEST | 约束资产查询请求（任务1和3） |
| 140-查询规格agent | EAST-OBSERVABLE-FACT-PACKAGE | EAST可观察事实包（任务2和3） |

## 权限边界

- 仅迭代调用000查询已冻结约束资产；不直接访问数据库或外部系统。
- 所有表字段、关联和约束必须来自000返回的冻结资产；硬代码校验引用、版本、覆盖状态和迭代上限。
- 证据不足输出 partial/unobservable/blocked，不为满足任务强行映射。
- 不认定监管违法；不编造EAST字段/关系；不生成SQL；不绕过000查询资产。
- 不直接提交正式库；不覆盖或改写参考源。

## 任务范围

| # | 任务 | 输入 | 输出 |
|---:|---|---|---|
| 1 | 接收PENALTY-FACT-PACKAGE，转换为CONSTRAINT-QUERY-REQUEST并调用000查询表、字段及关联的详细信息 | PENALTY-FACT-PACKAGE | CONSTRAINT-QUERY-REQUEST |
| 2 | 利用000返回的CONSTRAINT-ASSET-PACKAGE，判断处罚事实如何由EAST直接/间接观察，形成EAST-OBSERVABLE-FACT-PACKAGE | CONSTRAINT-ASSET-PACKAGE + PENALTY-FACT-PACKAGE | EAST-OBSERVABLE-FACT-PACKAGE |
| 3 | 接收110路由的EAST映射错误，重新调用000查询约束资产并重新构建EAST-OBSERVABLE-FACT-PACKAGE | 审核回退 + PENALTY-FACT-PACKAGE + CONSTRAINT-ASSET-PACKAGE | CONSTRAINT-QUERY-REQUEST + 新版本EAST-OBSERVABLE-FACT-PACKAGE |

## Skill 引用

- 无专用 Skill；LLM增强部分依赖运行时模型能力，不绑定外部 Skill。

## 失败码

| 码 | 含义 |
|----|------|
| SCHEMA_VALIDATION_FAILED:PENALTY_FACT_PACKAGE | 输入处罚事实包JSON Schema校验失败 |
| SCHEMA_VALIDATION_FAILED:CONSTRAINT_QUERY_REQUEST | 查询请求包JSON Schema校验失败 |
| SCHEMA_VALIDATION_FAILED:CONSTRAINT_ASSET_PACKAGE | 约束资产结果包JSON Schema校验失败 |
| SCHEMA_VALIDATION_FAILED:EAST_OBSERVABLE_FACT_PACKAGE | 输出可观察事实包JSON Schema校验失败 |
| INVALID_CALLER_AGENT_ID | 调用方编号不在允许列表 |
| EMPTY_PENALTY_FACTS | 处罚事实包source_facts为空 |
| CONSTRAINT_QUERY_FAILED | 000约束资产查询失败 |
| ASSET_VERSION_MISMATCH | 约束资产版本与预期不一致 |
| MAPPING_INCOMPLETE | 映射矩阵未覆盖所有must_preserve事实 |
| MAX_ITERATION_EXCEEDED | 迭代次数超过上限（3次） |
| REVIEW_ROUTE_INVALID | 审核回退路由无效 |
| UNRESOLVED_REMAINING | 存在需人工判断的未解决项 |
| RUNTIME_ROOT_NOT_SET | V5_RUNTIME_ROOT未设置 |
| RUNTIME_ROOT_NOT_WRITABLE | 运行时根目录不可写 |
| RUNTIME_ROOT_OVERLAP | 运行时根与Git/参考源重叠 |

## 版本

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-08-15 | 初始创建（EAS-22） |
