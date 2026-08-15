# Agent 000：资产检索

## 基本信息

| 属性 | 值 |
|------|-----|
| Agent 名称 | 000-资产检索agent |
| 编号 | 000 |
| 职责 | 把明确的自然语言查询请求转换为查询计划，经硬编码安全闸门查询已冻结约束资产并返回可追溯结果 |
| 模型 | `glm-5.1` |
| Runtime | GLM（`be8a07a2-4dce-465a-8ca5-b0119faa70cd`） |
| 最大并发任务 | 1 |
| 可见性 | workspace |

## 输入

| 来源 | 包类型 | 说明 |
|------|--------|------|
| 130-EAST可观察事实构造agent | CONSTRAINT-QUERY-REQUEST | 约束资产查询请求（caller_agent_id=130） |
| 220-结构闭包构造agent | CONSTRAINT-QUERY-REQUEST | 约束资产查询请求（caller_agent_id=220） |

## 输出

| 消费者 | 包类型 | 说明 |
|--------|--------|------|
| 130-EAST可观察事实构造agent | CONSTRAINT-ASSET-PACKAGE | 约束资产查询结果包 |
| 220-结构闭包构造agent | CONSTRAINT-ASSET-PACKAGE | 约束资产查询结果包 |

## 权限边界

- 只读查询已冻结约束资产 SQLite（CA-V0.3.0）和带类型引用图（TRG-V1.0.0）。
- 仅执行经安全闸门批准的 SELECT/CTE SELECT 语句；拒绝写操作、多语句、系统表、未参数化SQL和越权对象。
- LLM 只理解查询目的、分解查询和生成候选查询计划；硬代码限定安全边界。
- 不构建或修改约束资产；不写数据库；不让 LLM 直接执行 SQL；不补造未命中事实。
- 独立 ODS 分类不得出现在查询接口；历史 ODS 命中必须显式拒绝并指向带类型图或操作闭包。
- 不直接提交正式库；不覆盖或改写参考源。

## 查询安全闸门（硬代码）

- 仅允许 SELECT 和 CTE SELECT 语句
- AST 解析拒绝：INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/ATTACH/PRAGMA
- 拒绝多语句（分号分隔）
- 仅允许访问已冻结白名单视图和表：
  - CA-V0.3.0 表：decision_audit, evidence, excluded_constraint_audit, field_master, multifield_constraint, multifield_constraint_field, release_meta, source_manifest
  - CA-V0.3.0 视图：approved_comparison_constraints, approved_reference_constraints, cross_table_constraints, intra_table_constraints
- SQL 必须参数化；拒绝字符串拼接
- 强制 LIMIT 子句；最大 10000 行
- 查询超时上限 30 秒
- 审计每条 SQL 文本、参数、耗时和命中数

## Skill 引用

- 无专用 Skill；LLM 增强部分依赖运行时模型能力，不绑定外部 Skill。

## 失败码

| 码 | 含义 |
|----|------|
| SCHEMA_VALIDATION_FAILED:CONSTRAINT_QUERY_REQUEST | 查询请求包 JSON Schema 校验失败 |
| SCHEMA_VALIDATION_FAILED:CONSTRAINT_ASSET_PACKAGE | 结果包 JSON Schema 校验失败 |
| INVALID_CALLER_AGENT_ID | 调用方编号不在允许列表 |
| INVALID_CALLER_STAGE | 调用阶段不在枚举范围 |
| INVALID_QUERY_PURPOSE | 查询目的不在枚举范围 |
| SAFETY_GATE_REJECTED:WRITE_OP | 检测到写操作 |
| SAFETY_GATE_REJECTED:MULTI_STATEMENT | 检测到多语句 |
| SAFETY_GATE_REJECTED:UNAUTHORIZED_OBJECT | 访问未授权表/视图 |
| SAFETY_GATE_REJECTED:UNPARAMETERIZED | SQL 未参数化 |
| SAFETY_GATE_REJECTED:NO_LIMIT | 缺少 LIMIT 子句 |
| SAFETY_GATE_REJECTED:SYSTEM_TABLE | 访问系统表 |
| SAFETY_GATE_REJECTED:INJECTION | 检测到注入模式 |
| QUERY_TIMEOUT | 查询超时 |
| ASSET_VERSION_DRIFT | 约束资产版本漂移 |
| ASSET_HASH_MISMATCH | 约束资产哈希不一致 |
| INDEPENDENT_ODS_REJECTED | 独立 ODS 查询被拒绝 |
| MAX_ROWS_EXCEEDED | 返回行数超限 |
| UNMATCHED_QUERY | 查询未命中任何资产 |
| RUNTIME_ROOT_NOT_SET | V5_RUNTIME_ROOT 未设置 |
| RUNTIME_ROOT_NOT_WRITABLE | 运行时根目录不可写 |
| RUNTIME_ROOT_OVERLAP | 运行时根与 Git/参考源重叠 |

## 版本

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-08-15 | 初始创建（EAS-20） |
