# EAST NL2SQL 单字段与多字段约束资产

版本：`full_runs/20260808_001_full_single_field_v1`
构建日期：2026-08-08（单字段）+ 2026-08-12（多字段人工批示扩展复核）
来源：EAS-10（DeepSeek 全量单字段约束构建）→ EAS-12（多字段约束审查与固化）

> 当前目录是 `03_构建过程层` 的已审查发布候选，不是 `05_新版本交付层` 的正式发布包。正式入 05 仍需独立 manifest、哈希、验收报告、PR 及人工合并。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `single_field_candidates_v1.sqlite` | 约束资产主库，含全部表 |
| `apply_human_decision_templates.py` | 确定性复现人工批示传播、端点补全及审计记录 |
| `human_decision_propagation_report.json` | 本次批示传播与验证结果 |
| `README.md` | 本文件 |

---

## SQLite 表清单（9 张表）

### 1. multi_field_candidates（472 行，14 列）★ 核心资产

多字段约束候选，经 EAS-12 全量审查、去重、消歧、字段映射验证后固化。

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER | 主键 |
| `field_id` | TEXT | 主字段 ID，格式 `FLD_{table_code}_{field_code}` |
| `task_id` | TEXT | DeepSeek 任务 ID |
| `candidate_local_id` | TEXT | 候选本地编号 |
| `field_roles_json` | TEXT | 字段角色标注（JSON 数组），含 SUBJECT/REFERENCE/TARGET/CONDITION 角色 |
| `constraint_json` | TEXT | 约束内容（JSON），含原文、结构化方向及人工决策来源 |
| `ods_classification` | TEXT | ODS 分类标注（文档用途，非 agent 决策依据） |
| `ods_reason` | TEXT | ODS 分类理由 |
| `few_shot_case_ids` | TEXT | Few-shot 案例 ID |
| `construction_flow_json` | TEXT | 候选构造流程 |
| `evidence_refs_json` | TEXT | 证据引用（关联 evidence 表的 evidence_id） |
| `review_status` | TEXT | 审核状态：APPROVE / REVIEW / DISCARDED |
| `field_group` | TEXT | **纯字段清单**（JSON 数组），格式 `TABLE_CODE.FIELD_CODE`，无角色标注 |
| `is_cross_table` | INTEGER | 0=同表多字段，1=跨表多字段 |

#### 约束类型（constraint_item_type）

| 类型 | 数量 | 说明 |
|------|------|------|
| REFERENCE_EXISTENCE | 445 | 引用存在性——"X 非空时必须在 Y 表中存在" |
| COMPARISON | 25 | 字段比较——"X ≤ Y"、"X 不应为某值" |

#### 跨表 vs 同表（is_cross_table）

| 类型 | 数量 | 说明 |
|------|------|------|
| 跨表（is_cross_table=1） | 447 | field_group 含 ≥2 个不同英文表代码，定义 JOIN 路径 |
| 同表（is_cross_table=0） | 23 | field_group 含 0-1 个英文表代码，同表字段约束 |

#### 审核状态（review_status）

| 状态 | 数量 | 说明 |
|------|------|------|
| APPROVE | 470 | 人工批示或确定性规则闭合并校验通过 |
| REVIEW | 0 | 当前无待人工判断项 |
| DISCARDED | 2 | 已废弃（循环自引用，经人工批示删除） |

#### Agent 使用示例

```sql
-- 获取所有跨表 JOIN 路径
SELECT field_id, field_group, constraint_json
FROM multi_field_candidates
WHERE is_cross_table = 1 AND review_status != 'DISCARDED';

-- 获取某表的所有引用约束
SELECT * FROM multi_field_candidates
WHERE field_group LIKE '%JGXXB%' AND review_status != 'DISCARDED';

-- 构建操作闭包：从 YGB 出发可达的所有表
WITH RECURSIVE closure AS (
  SELECT 'YGB' AS src_table, '' AS path
  UNION
  SELECT DISTINCT
    CASE WHEN fg_item LIKE 'YGB.%' THEN replace(fg_item, rtrim(fg_item, replace(fg_item, '.', '')), '')
         ELSE replace(fg_item, rtrim(fg_item, replace(fg_item, '.', '')), '') END
  FROM multi_field_candidates, json_each(field_group)
  WHERE is_cross_table = 1 AND review_status != 'DISCARDED'
)
SELECT DISTINCT src_table FROM closure WHERE src_table != '';
```

#### 与 Excel 的列对应关系

| Excel 列 | SQLite 来源 |
|----------|------------|
| candidate_id | `multi_field_candidates.id` |
| 主字段ID | `multi_field_candidates.field_id` |
| 主字段中文名 | JOIN `field_master.field_name` ON field_id |
| 所属表(英文) | JOIN `field_master.table_code` |
| 所属表(中文) | JOIN `field_master.table_name` |
| 数据元编码 | JOIN `field_master.data_element_code` |
| 约束类型 | `constraint_json->>'constraint_item_type'` |
| 条件文本 | `constraint_json->>'condition_text'` |
| 约束内容 | `constraint_json->>'requirement_text'` |
| 检核规则原文 | JOIN `evidence.content` ON evidence_refs_json |
| 数据结构备注 | JOIN `evidence.content` WHERE source_type='DATA_STRUCTURE_REMARK' |
| 字段组(英文) | `field_group` |
| 跨表/同表 | `CASE is_cross_table WHEN 1 THEN '跨表' ELSE '同表' END` |
| ODS分类 | `ods_classification` |
| 业务分析 | 由 constraint_item_type + is_cross_table + ods_classification 推导 |
| 审核状态 | `review_status` |

---

### 2. field_master（1,963 行，12 列）

全量字段主表，EAST 全部 74 张表的字段索引。

| 列名 | 类型 | 说明 |
|------|------|------|
| `field_id` | TEXT | 主键，格式 `FLD_{table_code}_{field_code}` |
| `table_code` | TEXT | 英文表代码（如 JGXXB、YGB） |
| `table_name` | TEXT | 中文表名（如 机构信息表、员工表） |
| `field_code` | TEXT | 英文字段代码（如 YHJGDM、GH） |
| `field_name` | TEXT | 中文字段名（如 银行机构代码、工号） |
| `data_element_code` | TEXT | 数据元编码 |
| `extraction_status` | TEXT | 提取状态 |
| `candidate_count` | INTEGER | 候选约束数量 |
| `source_row` | INTEGER | 来源行号 |

---

### 3. single_field_constraints（3,520 行，8 列）

单字段约束资产，每个字段的独立约束（非空、长度、编码规则等）。

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER | 主键 |
| `field_id` | TEXT | 关联 field_master.field_id |
| `constraint_item_type` | TEXT | 约束类型 |
| `value_json` | TEXT | 约束值 |
| `evidence_refs_json` | TEXT | 证据引用 |
| `review_status` | TEXT | 审核状态 |

---

### 4. evidence（5,353 行，7 列）

原始检核规则文本，从 EAST 材料 Excel 提取。

| 列名 | 类型 | 说明 |
|------|------|------|
| `evidence_id` | TEXT | 主键，如 `EVID-SFV1-TASK-JGXXB-YHJGDM-VR-ROW2_R01` |
| `task_id` | TEXT | 关联任务 |
| `source_type` | TEXT | VALIDATION_RULE（检核规则）/ DATA_STRUCTURE_REMARK（数据结构备注） |
| `content` | TEXT | 原始文本 |
| `sheet_name` | TEXT | 来源 Sheet |
| `cell_range` | TEXT | 来源单元格 |

---

### 5. source_manifest（1 行，4 列）

来源文件清单，记录输入文件的 SHA-256。

| 列名 | 说明 |
|------|------|
| `source_file_id` | 来源文件标识 |
| `source_file_sha256` | SHA-256 哈希 |
| `source_file_path` | 来源文件路径 |
| `sheet_name` | 来源 Sheet |

---

### 6-9. human_decision_overlay / human_review_queue / multi_field_decision_templates / multi_field_decision_audit

人工决策覆盖记录、模板及逐候选前后哈希审计。`multi_field_decision_templates` 保存同文案模板哈希和边界保护；`multi_field_decision_audit` 保存 470 条有效候选的来源、决策依据及前后内容哈希。

---

### 8. comparison_results（空）

单字段与数据元约束比对结果表（预留给 EAS-11 后续使用）。

---

## 数据关系图

```
source_manifest ──→ evidence ←── evidence_refs_json
                         ↑
field_master ←── field_id ──→ multi_field_candidates
    ↓                              ↓
single_field_constraints      field_group (JSON数组)
                              constraint_json (JSON)
                              field_roles_json (JSON)
```

---

## 完整性校验

- SQLite integrity_check: **ok**
- 470 条有效候选、2 条经人工批准废弃；待人工判断 0 条
- 1,011 个有效 field_group 项全部为 `TABLE.FIELD`，且均存在于 field_master
- 445 条引用存在性约束均保存 `PROVIDER_TO_CONSUMER` 方向；多目标引用保存 `ANY` 语义
- 同文案传播同时校验主字段和提供者关系，避免把同文案自引用候选误批准
- 跨表约束的 JOIN 路径均可追溯到 field_master

---

## 构建过程

1. **EAS-10**：DeepSeek 对 1,963 字段逐字段语义解析，输出单字段约束 + 多字段候选
2. **EAS-12**：对 472 条多字段候选进行全量审查、去重、消歧、字段映射验证。
3. **人工批示扩展复核**：读取附件 `019ff606-ccdb-7185-aa66-713fb7d43b60`（SHA-256 `a3ffe34f6c44ddb1a5aa86a0676a66fb592078bfbeb3fa094dd1d85924fc32e3`）中“人工判断审查”Sheet 的 M 列：
   - 33 条显式批示逐条落地；
   - 对相同检核表述及同类确定性映射传播，另闭合 51 条原 `REVIEW`；
   - 361 条既有 `APPROVE` 裸表端点补为 `TABLE.FIELD`；
   - 对 M 列“个人/对公活期存款分户账”代码重复的单元格，以同格中文表述和冻结 field_master 校正为 `GRHQCKFHZ.HQCKZH` 与 `DGHQCKFHZ.HQCKZH`；
   - 结果为 `APPROVE 470 / REVIEW 0 / DISCARDED 2`。

---

## 版本

- 构建日期：2026-08-12
- 审查状态：APPROVE 470 / REVIEW 0 / DISCARDED 2
- 冻结来源：`规范附件3数据元-规范附件3数据结构-规范附件4检核规则-字段粒度合并-已填充检核规则.xlsx`
- 当前 SQLite SHA-256：`611c99f31069c493154bb10d47e6db9b73bc8335d18b86199f610a3c18873478`；正式发布时必须写入 05 层 manifest，不能复用历史哈希。
