# EAST NL2SQL 约束资产使用指南

> 版本：V1.0 | 日期：2026-08-11 | 关联 Issue：EAS-11

## 一、资产全景

```
单字段约束资产                      数据元约束资产
┌──────────────────────┐           ┌──────────────────────────────┐
│ single_field_final.   │  data_   │ constraint_assets.sqlite      │
│ sqlite               │──element─▶│ (CA-V0.2.0-foundation)       │
│                      │  _code   │                              │
│ field_master         │           │ data_element                 │
│ single_field_const.  │           │ semantic_constraint          │
│ evidence             │           │ encoding_rule                │
│ multi_field_cand.    │           │ external_code_table_usage ──▶│ 国标与附件4码表.sqlite
└──────────────────────┘           │ local_code_table_usage    ──▶│ 本地码表.sqlite
                                   │ tree_reference_usage      ──▶│ 会计科目树.sqlite
                                   │ tree_reference_usage      ──▶│ 机构部门统一树.sqlite
                                   └──────────────────────────────┘
```

## 二、数据库文件清单

| 序号 | 数据库 | 路径 | 用途 |
|------|--------|------|------|
| 1 | 单字段约束资产 | `03_构建过程层/EAS-11_单字段与数据元约束比对/single_field_final.sqlite` | 1963字段的单字段约束候选 |
| 2 | 数据元约束资产 | `05_新版本交付层/约束资产/CA-V0.2.0-foundation/constraint_assets.sqlite` | 301数据元的已批准约束定义 |
| 3 | 国标与附件4码表 | `05_新版本交付层/约束资产/CA-V0.2.0-foundation/装配视图与码表引用域/国标与附件4码表.sqlite` | 国标行政区划/行业/币种等 |
| 4 | 本地码表 | `05_新版本交付层/约束资产/CA-V0.2.0-foundation/装配视图与码表引用域/本地码表.sqlite` | EAST数据元业务码值 |
| 5 | 会计科目树 | `05_新版本交付层/约束资产/CA-V0.2.0-foundation/装配视图与码表引用域/会计科目树.sqlite` | 合成会计科目层级树 |
| 6 | 机构部门统一树 | `05_新版本交付层/约束资产/CA-V0.2.0-foundation/装配视图与码表引用域/机构部门统一树.sqlite` | 机构部门层级树 |

## 三、单字段约束资产（数据库1）

### 3.1 field_master — 字段主表

| 列 | 类型 | 说明 |
|----|------|------|
| `field_id` | TEXT PK | 字段唯一ID，格式 `FLD_{表编码}_{字段编码}` |
| `table_code` | TEXT | EAST表编码，如 `JGXXB` |
| `table_name` | TEXT | 表中文名 |
| `field_code` | TEXT | 字段编码 |
| `field_name` | TEXT | 字段中文名 |
| `data_element_code` | TEXT | **关联键** → 数据元约束资产的 `data_element.data_element_code` |
| `task_id` | TEXT | EAS-10构建任务ID |
| `extraction_status` | TEXT | 提取状态 |
| `candidate_count` | INTEGER | 该字段的单字段约束条数 |

**查询示例**：查一个字段的所有约束

```sql
SELECT sfc.* FROM field_master fm
JOIN single_field_constraints sfc ON fm.field_id = sfc.field_id
WHERE fm.field_id = 'FLD_JGXXB_JRXKZH';
```

### 3.2 single_field_constraints — 单字段约束

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | INTEGER PK | 自增ID |
| `field_id` | TEXT FK | → field_master.field_id |
| `task_id` | TEXT | 构建任务ID |
| `candidate_local_id` | TEXT | 该字段内约束编号，如 C001 |
| `constraint_item_type` | TEXT | 约束类型 |
| `value_json` | TEXT | 约束值，JSON格式 |
| `evidence_refs_json` | TEXT | 证据引用，JSON数组 |
| `review_status` | TEXT | 审核状态：CANDIDATE |

**约束类型及 value_json 格式**：

| constraint_item_type | value_json 示例 | 说明 |
|---------------------|-----------------|------|
| `NULLABLE` | `{"nullable":"NO"}` | 不可为空 |
| `PRIMARY_KEY` | `{"primary_key":true}` | 主键 |
| `UNIQUE` | `{"unique":true}` | 唯一约束 |
| `STRING_LENGTH` | `{"mode":"EXACT","length":15,"length_unit":"CHARACTER"}` | 长度约束，mode=EXACT/MAX |
| `CODE_DOMAIN` | `{"allowed_values":["A","B","C"]}` | 码值域 |
| `ENCODING_RULE` | `{"format":"YYYYMMDD"}` | 编码格式 |
| `FORBIDDEN_CHARACTER_SET` | `{"characters":["~"],"match_mode":"CONTAINS_ANY","normalization":"NFKC"}` | 禁用字符集 |
| `FORBIDDEN_VALUE` | `{"forbidden_value":"99991231"}` | 禁止的特定值 |
| `VALUE_RANGE` | `{"description":"应大于等于0小于等于1"}` | 取值范围描述 |
| `OTHER` | `{"description":"..."}` | 其他业务规则描述 |

### 3.3 evidence — 证据原文

| 列 | 类型 | 说明 |
|----|------|------|
| `evidence_id` | TEXT PK | 证据唯一ID |
| `task_id` | TEXT FK | → field_master.task_id |
| `source_type` | TEXT | `VALIDATION_RULE`（检核规则）或 `DATA_STRUCTURE_REMARK`（数据结构备注） |
| `content` | TEXT | 原文 |
| `sheet_name` | TEXT | 来源Sheet |
| `cell_range` | TEXT | 来源单元格 |

### 3.4 multi_field_candidates — 多字段候选

| 列 | 类型 | 说明 |
|----|------|------|
| `field_id` | TEXT FK | 主字段 |
| `field_group_json` | TEXT | 涉及的所有字段，JSON数组 |
| `constraint_json` | TEXT | 约束内容 |
| `ods_classification` | TEXT | ODS分类 |
| `review_status` | TEXT | 审核状态 |

## 四、数据元约束资产（数据库2）

### 4.1 data_element — 数据元主表

| 列 | 类型 | 说明 |
|----|------|------|
| `data_element_code` | TEXT PK | **关联键**，如 `001011` |
| `data_element_name` | TEXT | 数据元名称 |
| `description` | TEXT | 数据元说明 |
| `data_type` | TEXT | 数据类型：STRING/DECIMAL/INTEGER |
| `string_length_exact` | INTEGER | 精确长度 |
| `string_length_max` | INTEGER | 最大长度 |

### 4.2 semantic_constraint — 语义约束

| 列 | 类型 | 说明 |
|----|------|------|
| `constraint_id` | TEXT PK | |
| `data_element_code` | TEXT FK | → data_element.data_element_code |
| `item_type` | TEXT | OTHER / VALUE_RANGE / DEFAULT_VALUE / PRIVACY_TRANSFORM |
| `value_json` | TEXT | 约束值，JSON格式 |
| `evidence_quote` | TEXT | 证据原文 |
| `source_refs_json` | TEXT | 来源引用 |

### 4.3 external_code_table_usage — 外部码表引用

**关键表：指导如何查询国标与附件4码表**

| 列 | 类型 | 说明 |
|----|------|------|
| `usage_id` | TEXT PK | |
| `data_element_code` | TEXT FK | |
| `code_table_id` | TEXT | 码表编号 |
| `sqlite_table_name` | TEXT | **目标表名**，如 `code_cd000003_code_set` |
| `selection_mode` | TEXT | 取值模式：`DIRECT_COLUMN` |
| `final_value_columns_json` | TEXT | **取哪一列**，JSON数组如 `["代码名称"]` |
| `final_value_template` | TEXT | **多列拼接模板**，如 `"{省}{市}{区}"` |
| `selection_criteria_json` | TEXT | **行筛选条件**，JSON如 `{"hierarchy_level":1}` |

**查询码表示例**：

```sql
-- 查门类级别的行业分类
SELECT DISTINCT 代码名称
FROM code_cd000012_code_set
WHERE 行业级别 = '行业门类'
  AND 代码名称 IS NOT NULL;
```

### 4.4 local_code_table_usage — 本地码表引用

| 列 | 类型 | 说明 |
|----|------|------|
| `usage_id` | TEXT PK | |
| `data_element_code` | TEXT FK | |
| `local_code_set_id` | TEXT | **码表ID**，如 `LCS_DE_003006_c29ce71e0cae` |
| `selection_field` | TEXT | **取哪一列**：`literal_value` / `value_code` / `value_label` / `literal_value_or_code_label` |
| `selection_criteria_json` | TEXT | 条件筛选，JSON格式 |
| `extension_policy` | TEXT | 扩展策略：CLOSED |

**查询码表示例**：

```sql
-- 第1步：从local_code_set_id找到实际表名
SELECT sqlite_table_name FROM local_code_set_registry
WHERE local_code_set_id = 'LCS_DE_003006_c29ce71e0cae';
-- 返回: local_lcs_de_003006_c29ce71e0cae

-- 第2步：查询码值
SELECT DISTINCT value_code, value_label
FROM local_lcs_de_003006_c29ce71e0cae
WHERE value_code IS NOT NULL;
```

### 4.5 encoding_rule / text_pattern_constraint / standard_reference

| 表 | 用途 |
|----|------|
| `encoding_rule` | 编码规则，如日期格式YYYYMMDD |
| `text_pattern_constraint` | 文本模式约束，如正则表达式 |
| `standard_reference` | 引用的外部标准 |

## 五、完整查询链路

Agent 查询一个字段的最终约束值：

```
Step 1: 查字段
  SELECT data_element_code FROM field_master WHERE field_id = 'FLD_XXX_YYY';
  → 返回 data_element_code = '001011'

Step 2: 查数据元格式
  SELECT data_type, string_length_exact, string_length_max
  FROM data_element WHERE data_element_code = '001011';
  → STRING, exact=15

Step 3: 查单字段约束
  SELECT constraint_item_type, value_json
  FROM single_field_constraints WHERE field_id = 'FLD_XXX_YYY';

Step 4: 查数据元语义约束
  SELECT item_type, value_json FROM semantic_constraint
  WHERE data_element_code = '001011';

Step 5: 查码表引用
  -- 外部码表
  SELECT sqlite_table_name, final_value_columns_json, selection_criteria_json, final_value_template
  FROM external_code_table_usage WHERE data_element_code = '001011';
  
  -- 本地码表
  SELECT local_code_set_id, selection_field, selection_criteria_json
  FROM local_code_table_usage WHERE data_element_code = '001011';

Step 6: 解析码表实际值
  -- 对于 local: local_code_set_registry → 实际表名 → 按selection_field取列
  -- 对于 external: 直接查 sqlite_table_name 按 selection_criteria 筛选
```

## 六、特殊规则

1. **码值字段不做长度限制**：有 CODE_DOMAIN 的字段，STRING_LENGTH 仅用 `MAX` 模式，值为 `max(len(v) for v in allowed_values)`
2. **多列拼接**：通过 `final_value_template` 标注（external）或 `selection_field` 隐含规则（local）
3. **条件码表**：`selection_criteria_json` 中的 `condition` 表示需按维度筛选，如「机构类型代码=A时取此表」
4. **会计科目树**：通过 `tree_reference_usage` 引用，表中有 `e00306_code` 列标注 003006 子类映射

## 七、构建状态

| 指标 | 数值 |
|------|------|
| 字段总数 | 1,963 |
| 单字段约束 | 3,508 条 |
| 多字段候选 | 488 条 |
| 证据原文 | 5,353 条 |
| IN_SET 残留 | 0 |
| 人工标注 TRUE_CONFLICT→取CA | 6 |
| 人工标注 UNCERTAIN→取CA | 15 |
| 人工标注 UNCERTAIN→取EAS | 10 |
