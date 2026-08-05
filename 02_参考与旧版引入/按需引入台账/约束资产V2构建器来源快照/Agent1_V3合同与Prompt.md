# Agent1 V3.3：数据元说明 E 列约束提取合同

> 本文 V3.3 已取代其中遗留的 V3.2 描述。可执行 Prompt、固定 JSON 合同和硬代码以 `pipeline.py`、`contracts.py` 为准；每次真实调用均保存完整拼接 Prompt。V3.3 的新增边界为：未提及不输出、明确但未分类事实进入 `OTHER`、仅明确歧义进入 `UNRESOLVED`；直接枚举优先于外部标准；没有显式标准绑定时不得按字段语义猜测附件4码表；“其他-XX”归一为“其他”；条件码值集可表达父条件；支持码表列截取；废止 GB13496-92 使用硬代码特殊处理。

## 目的与状态

本合同重构 Agent1，不读取、不修补、不认可旧 Agent1 V2 `CODE_DOMAIN` 自由文本批次。V3.2 将已审阅的国标—数据元绑定、附件4全部码表的列目录和每表两行样例作为受控参考上下文提供给模型。所有产出均为 `review_only_not_released`，在人工审查前不得写入本地码值库或作为发布资产。

## 输入与硬代码边界

进入 DeepSeek 的唯一事实输入为一条数据元的：

- `code`、`name`、`name_en`；
- 《规范附件3》“数据元说明”sheet 的 E 列 `description`；
- 该 E 单元格的精确 `source_refs`。

以下内容不进入 Prompt：

- F 列格式及硬解析结果；
- 字段材料、检核规则、Agent2/Agent3 输出和外部知识；
- 码表中任何一行的代码、名称或说明；
- 不在当前数据元已审阅绑定中的国标/附件4映射。

额外进入 Prompt 的受控参考上下文：

- 当前数据元在“国标与数据元绑定”sheet 中的精确绑定；
- 当前数据元已审阅绑定中的全部外部标准身份；
- 附件4全部物理码表的 ID、SQLite 表名、物理表 `raw_values_json` 后的可选列名，以及每张表的两条真实样例行。

样例行只让模型理解“哪列是代码、哪列是名称、层级列如何出现”；不能作为完整值域、本任务事实或本地码值集。该上下文只让模型声明“取哪一列/如何由列拼装”，不能新增绑定。

硬代码负责：

1. 按《表格说明》解析 `Cn`、`C..n`、`I`、`I..n`、`Dw.d`、`F`；
2. 从已审阅的 `data_element_code_table_binding` 精确查找该数据元对应的国标/附件4码表，并生成仅含列名的全量国标目录；
3. 校验模型只能使用当前数据元已绑定的码表，只能从该表目录选择列；
4. 保存上述硬代码结果至调用目录；
5. 校验固定 JSON、码值枚举、精确 E 列引文与人工审核条件。

## 固定输出

```json
{
  "task_id": "V3_2_A1_DE_<数据元代码>",
  "data_element_code": "<数据元代码>",
  "extraction_status": "EXTRACTED|NO_CONSTRAINT|UNRESOLVED",
  "semantic_constraints": [],
  "code_value_sets": [],
  "encoding_rules": [],
  "standard_references": [],
  "code_table_usages": [],
  "manual_review_questions": []
}
```

- `semantic_constraints` 仅允许 `ENCODING_RULE`、`DEFAULT_VALUE`、`FORBIDDEN_VALUE`、`VALUE_RANGE`、`NULLABLE`、`PRIVACY_TRANSFORM`、`OTHER`。不允许 `CODE_DOMAIN`。
- `code_value_sets[].values` 一行一个原子码值；`是、否` 必须为两行。`CODE_LABEL` 仅可在原文有明确代码-名称配对时使用，否则为 `LITERAL`。
- `encoding_rules[].segments` 必须给出有证据的 `start_pos/end_pos`，按位置递增且不重叠。没有定位范围时不虚构分段。
- `standard_references` 摘录原文标准引用，并只能选择当前数据元已审阅绑定的 `external_standard_id`。
- `code_table_usages` 可以是 `REVIEWED_STANDARD_BINDING`，也可以在 E 列约束唯一匹配时提出 `CONSTRAINT_COMPATIBLE_ATTACHMENT4`；后者必须写人工审核问题，不能发布。两者均须从受控码表的 `value_columns` 选择最终值列；需要拼装时以 `{列名}` 组成模板，不能输出码表行。
- 每条事实、码值、分段、标准和人工问题的 `evidence_quote` 都必须是 E 列原文的非空子串，`source_refs` 只能是此 E 单元格。

## Prompt 内少样本

- “取值为是、否”演示为两个 `LITERAL` 原子码值，禁止合并为一个字符串；
- “第 1 至 3 位为地区代码、第 4 至 10 位为顺序号”演示为两个有范围的 `segments`；
- 原文明示 GB/T 2659 的三字符代码时，演示从已绑定 `CD000002` 的 `字母代码` 做 `DIRECT_COLUMN`；
- 原文明示“由两层级列按连字符拼接”时，演示 `COMPOSE_COLUMNS` 和 `{列名}` 模板。

少样本不携带本任务事实或来源。没有 E 列证据时不得照搬其结论。

## 人工审核与不发布条件

- 模型不能确定一个原文事实如何拆分、不能确定是否穷举、不能可靠确定编码段位，必须 `extraction_status=UNRESOLVED`，并在 `manual_review_questions` 写清问题和原文引文；
- JSON、枚举、引用、分段范围、任务身份任一校验失败，整条调用进入人工审核；
- F 列格式硬解析为 `UNSUPPORTED`，仍进入人工审核；
- 国标引用只有原文摘录和已审阅绑定均可追溯时才能在后续硬代码阶段连接；没有绑定绝不自动按名称匹配；
- 未经人工审查通过的调用、任何 `UNRESOLVED`、硬解析失败、未绑定的标准引用，均不得发布，也不得创建本地码值实体表。

## 金融许可证号的目标表达

若 E 列原文明确规定金融许可证号的第 1--3、4--8、9--10 位等构成，输出应是一个 `encoding_rules` 元素和多条按位次拆开的 `segments`。其中“机构类型代码”如原文明确给出可选码值，可由 segment 的 `value_set_names` 引用本任务内原子码值集；如引用附件4/国标，则只输出原文标准引用，外部绑定由硬代码补充。它绝不属于 `code_value_sets` 的整条自由文本。
