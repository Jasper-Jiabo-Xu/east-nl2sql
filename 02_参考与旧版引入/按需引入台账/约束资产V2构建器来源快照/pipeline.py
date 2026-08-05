#!/usr/bin/env python3
"""Compile, map and pilot the three V2 DeepSeek agents without V1 assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from vnext.constraint_assets_v1.compile_tasks import parse_att3, parse_att4, parse_frozen_fields

from .contracts import ContractError, validate_agent1, validate_agent2, validate_agent3


ROOT = Path(__file__).resolve().parents[2]
WORKING = ROOT / "kb/working/20260727_000_goal_mode_v1"
OUTPUT = WORKING / "构建过程层/04_字段多字段与对象明细状态提取-V2"
KEY_PATH = ROOT / "deepseek-key/api-key.txt"
MODEL = "deepseek-v4-pro"
CONTRACT_VERSION = "constraint-assets-v2-20260803"
AGENT1_CONTRACT_VERSION = "constraint-assets-agent1-v3.3-20260805"
CODE_REFERENCE_LIBRARY = OUTPUT / "code_value_libraries/20260805_一表一码表_层级补全_v4/code_reference_library_v3.sqlite"

# These are deterministic project decisions, not LLM inferences.  They are
# intentionally narrow and are retained in every task input for audit.
AGENT1_EXPLICIT_BINDING_OVERRIDES: dict[str, tuple[dict[str, Any], ...]] = {
    "001011": ({
        "binding_id": "POLICY_A1_001011_GBT2260",
        "data_element_code": "001011",
        "data_element_name": "金融许可证号",
        "east_citation": "GB/T2260",
        "att3_location": "数据元说明!E17",
        "att3_quote": "按照《中华人民共和国行政区划代码》 （GB/T2260），取市（地区、自治州、盟）、直辖市行政区划代码前四位，作为地址代码。",
        "binding_status": "BOUND",
        "binding_source_ref": "SRC_POLICY:Agent1硬代码规则!001011",
        "external_standard_id": "EXT_GBT_2260",
        "standard_name": "中华人民共和国行政区划代码",
        "verification_status": "E列显式引用与受控码表唯一对应",
        "use_decision": "地址代码取CD000003的代码取值前四位。",
        "standard_source_ref": "SRC_AMC_CODE:目录!A5:C5",
        "code_table_id": "CD000003",
        "sheet_name": "县及县以上行政区划",
        "sqlite_table_name": "code_cd000003_code_set",
        "reference_standard_raw": "GB/T 2260 中华人民共和国行政区划代码",
        "directory_source_ref": "SRC_AMC_CODE:目录!A5:C5",
    },),
    "010075": ({
        "binding_id": "POLICY_A1_010075_GBT2260",
        "data_element_code": "010075",
        "data_element_name": "行政区划",
        "east_citation": "中华人民共和国行政区划代码",
        "att3_location": "数据元说明!E288",
        "att3_quote": "根据《中华人民共和国行政区划代码》，填写至二级分类。",
        "binding_status": "BOUND",
        "binding_source_ref": "SRC_POLICY:Agent1硬代码规则!010075",
        "external_standard_id": "EXT_GBT_2260",
        "standard_name": "中华人民共和国行政区划代码",
        "verification_status": "E列显式引用与受控码表唯一对应",
        "use_decision": "以CD000003表示行政区划二级分类；最终代码/名称列由E列用语和人工审查决定。",
        "standard_source_ref": "SRC_AMC_CODE:目录!A5:C5",
        "code_table_id": "CD000003",
        "sheet_name": "县及县以上行政区划",
        "sqlite_table_name": "code_cd000003_code_set",
        "reference_standard_raw": "GB/T 2260 中华人民共和国行政区划代码",
        "directory_source_ref": "SRC_AMC_CODE:目录!A5:C5",
    },),
}

AGENT1_HARD_DIRECTIVES: dict[str, tuple[dict[str, str], ...]] = {
    "001029": ({
        "directive_type": "SUPPRESS_DEPRECATED_STANDARD_ASSET",
        "standard_mention_raw": "GB13496-92",
        "required_handling": "该废止国标仅保留在E列编码段证据中；不得输出standard_references、code_table_usages或人工问题，不得尝试匹配、补全或推理其码表。",
    },),
    "002016": ({
        "directive_type": "BOUND_STANDARD_WITH_LOCAL_SUPPLEMENTS",
        "required_handling": "GB/T 4754的“如……”内容只是门类名称示例，必须只用CD000012的代码名称列作为标准主体值域；不得把两个示例写入本地码值集。仅可输出本地补充值“国际组织”“其他”，其中“其他”采用OTHER_BANK_DEFINED。",
    },),
    "001010": ({
        "directive_type": "CLASSIFY_AS_OTHER_PENDING_SEPARATE_NATIONAL_NORM_RESEARCH",
        "required_handling": "银行机构代码属于后续单独查询国家规范的证件/代码类事项。本轮保留原文为OTHER，不输出编码规则、码值或标准引用，也不得人工报错。",
    },),
    "002019": ({
        "directive_type": "CLASSIFY_AS_OTHER_PENDING_SEPARATE_NATIONAL_NORM_RESEARCH",
        "required_handling": "统一社会信用代码属于后续单独查询国家规范的证件/代码类事项。本轮保留原文为OTHER，不输出编码规则、码值或标准引用，也不得人工报错。",
    },),
    "003021": ({
        "directive_type": "APPROVED_CONDITIONAL_ACCOUNT_TYPE_DOMAIN",
        "required_handling": "按人工确认的人民币、外币、其他三级值域输出：人民币和外币分别为币种条件码值集，其他为全局回退值；不得再将外汇管理规定作为未解析标准或人工问题。",
    },),
    "010006": ({
        "directive_type": "APPROVED_DIRECT_ENUMERATION",
        "required_handling": "E列只有两个闭集码值LPR、非LPR；不得输出来源优先级、回退规则或OTHER。",
    },),
}


def exact_cell_ref(source_ref: str, column: str) -> str:
    """Narrow an inherited row-level source reference to one exact source cell."""
    matched = re.fullmatch(r"(.+![A-Z]+)(\d+):[A-Z]+\2", source_ref)
    if not matched:
        raise RuntimeError(f"无法从来源引用定位单元格: {source_ref}")
    return f"{matched.group(1)[:-1]}{column}{matched.group(2)}"


def parse_hard_format(raw_format: str, source_ref: str) -> dict[str, Any]:
    """Implement Attachment 3 表格说明 items 6--11 without LLM involvement."""
    raw = str(raw_format or "").strip().upper()
    result: dict[str, Any] = {
        "raw_format": raw,
        "format_parse_status": "PARSED",
        "data_type": None,
        "string_length_exact": None,
        "string_length_max": None,
        "integer_max_digits": None,
        "decimal_max_fraction_digits": None,
        "source_refs": [source_ref],
        "parser_policy_ref": "SRC_ATT3:表格说明!A7:A12",
    }
    if matched := re.fullmatch(r"C(\d+)", raw):
        result.update(data_type="STRING", string_length_exact=int(matched.group(1)))
    elif matched := re.fullmatch(r"C\.\.(\d+)", raw):
        result.update(data_type="STRING", string_length_max=int(matched.group(1)))
    elif raw == "I":
        result.update(data_type="INTEGER")
    elif matched := re.fullmatch(r"I\.\.(\d+)", raw):
        result.update(data_type="INTEGER", integer_max_digits=int(matched.group(1)))
    elif matched := re.fullmatch(r"D\d+\.(\d+)", raw):
        result.update(data_type="DECIMAL", decimal_max_fraction_digits=int(matched.group(1)))
    elif raw == "F":
        result.update(data_type="FLOAT")
    else:
        result.update(format_parse_status="UNSUPPORTED")
    return result


def apply_agent1_hard_format_override(code: str, hard_format: dict[str, Any]) -> dict[str, Any]:
    """Apply a user-confirmed format interpretation that Attachment 3 grammar cannot express."""
    if code != "001032":
        return hard_format
    result = dict(hard_format)
    result.update({
        "format_parse_status": "PARSED_BY_APPROVED_OVERRIDE",
        "data_type": "INTEGER",
        "string_length_exact": None,
        "string_length_max": None,
        "integer_max_digits": 20,
        "decimal_max_fraction_digits": None,
        "parser_policy_ref": "SRC_POLICY:人工确认格式规则!001032",
        "override_reason": "人工确认：数量无E列明确约束，按最大20位整数处理。",
    })
    return result


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def code_table_bindings(data_element_code: str) -> list[dict[str, Any]]:
    """Return only review-workbook-confirmed standard/code-table bindings.

    Agent1 receives these bindings as controlled reference context.  Validator
    still forbids it from selecting any table outside this exact list.
    """
    if not CODE_REFERENCE_LIBRARY.exists():
        raise RuntimeError(f"缺少已审查的规范码表库: {CODE_REFERENCE_LIBRARY}")
    with sqlite3.connect(CODE_REFERENCE_LIBRARY) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT b.binding_id, b.data_element_code, b.data_element_name,
                   b.east_citation, b.att3_location, b.att3_quote,
                   b.binding_status, b.source_ref AS binding_source_ref,
                   s.external_standard_id, s.standard_name,
                   s.verification_status, s.use_decision,
                   s.source_ref AS standard_source_ref,
                   r.code_table_id, r.sheet_name, r.sqlite_table_name,
                   r.reference_standard_raw, r.directory_source_ref
              FROM data_element_code_table_binding AS b
              JOIN external_standard AS s
                ON s.external_standard_id = b.external_standard_id
         LEFT JOIN code_table_registry AS r
                ON r.code_table_id = b.code_table_id
             WHERE b.data_element_code = ?
             ORDER BY b.binding_id
            """,
            (data_element_code,),
        ).fetchall()
    bindings = [dict(row) for row in rows]
    bindings.extend(AGENT1_EXPLICIT_BINDING_OVERRIDES.get(data_element_code, ()))
    return bindings


@lru_cache(maxsize=1)
def all_external_standards() -> tuple[dict[str, Any], ...]:
    """Return every reviewed external-standard identity, with no table rows."""
    if not CODE_REFERENCE_LIBRARY.exists():
        raise RuntimeError(f"缺少已审查的规范码表库: {CODE_REFERENCE_LIBRARY}")
    with sqlite3.connect(CODE_REFERENCE_LIBRARY) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT external_standard_id, east_citation, standard_name,
                   verification_status, use_decision, source_ref
              FROM external_standard
             ORDER BY external_standard_id
            """
        ).fetchall()
    return tuple(dict(row) for row in rows)


@lru_cache(maxsize=1)
def all_code_tables() -> tuple[dict[str, Any], ...]:
    """Return all Attachment-4 physical tables with columns and two real rows.

    The model sees all 27 code tables, not merely those appearing in the human
    standard-binding sheet.  `sample_rows` orient column semantics only and can
    never be copied out as an exhaustive code domain.
    """
    if not CODE_REFERENCE_LIBRARY.exists():
        raise RuntimeError(f"缺少已审查的规范码表库: {CODE_REFERENCE_LIBRARY}")
    with sqlite3.connect(CODE_REFERENCE_LIBRARY) as conn:
        conn.row_factory = sqlite3.Row
        registry_rows = conn.execute(
            """
            SELECT code_table_id, sheet_name, sqlite_table_name,
                   reference_standard_raw, directory_source_ref
              FROM code_table_registry
             ORDER BY code_table_id
            """
        ).fetchall()
        catalog: list[dict[str, Any]] = []
        for row in registry_rows:
            item = dict(row)
            table_name = item["sqlite_table_name"]
            fields = [info[1] for info in conn.execute(f"PRAGMA table_info({quote_identifier(table_name)})")]
            item["value_columns"] = fields[fields.index("raw_values_json") + 1:]
            select_columns = ", ".join(quote_identifier(column) for column in item["value_columns"])
            samples = conn.execute(f"SELECT {select_columns} FROM {quote_identifier(table_name)} ORDER BY source_row LIMIT 2").fetchall()
            item["sample_rows"] = [dict(sample) for sample in samples]
            standard_rows = conn.execute(
                """
                SELECT DISTINCT s.external_standard_id
                  FROM data_element_code_table_binding AS b
                  JOIN external_standard AS s ON s.external_standard_id = b.external_standard_id
                 WHERE b.code_table_id = ?
                 ORDER BY s.external_standard_id
                """,
                (item["code_table_id"],),
            ).fetchall()
            item["mapped_external_standard_ids"] = [standard[0] for standard in standard_rows]
            catalog.append(item)
    return tuple(catalog)


def agent1_prompt() -> str:
    return """你是 Agent1 V3.3：EAST 数据元说明约束提取 Agent。
任务边界：只处理输入的一条《规范附件3》“数据元说明”sheet 的 E 列原文，并仅使用任务内受控的 `standard_mapping_context`、`hard_agent1_directives`。不接收格式列 F、字段材料、检核规则、其他 Agent 输出、完整码表行或外部知识；不得凭常识补写、纠正或猜测。格式列 F 完全由硬代码处理，本任务不得输出格式事实。

状态判定（必须严格区分）：
1. 原文没有提到的约束，不输出任何项目；不能因此进入 OTHER 或 UNRESOLVED。
2. 原文明确事实，但不属于枚举码值、位置编码、标准引用或现有语义约束时，输出 semantic_constraints 的 OTHER，extraction_status=EXTRACTED；例如“优先使用外部结果，如无则使用机构内部结果”的来源优先级/回退规则必须为 OTHER。OTHER 供人工统一复核，不是错误、不是人工问题。
3. 原文明示了一个事实，但其自身存在无法可靠拆解、无法判断范围/含义的歧义，才用 UNRESOLVED，并提出该事实的明确人工问题。不得因为“未提到其他类别”而 UNRESOLVED。
4. `hard_agent1_directives` 优先于任何通用规则，必须逐条执行。

输出规则：
1. semantic_constraints：只能为 ENCODING_RULE、DEFAULT_VALUE、FORBIDDEN_VALUE、VALUE_RANGE、NULLABLE、PRIVACY_TRANSFORM、OTHER。已有结构化 encoding_rules 的同一编码事实不得再重复输出 semantic_constraints.ENCODING_RULE。原文未说可空/非空，不得输出 NULLABLE=UNSPECIFIED。
2. code_value_sets：只放原文明确列举的本地可选值；一个值一个 values 元素。LITERAL 的 literal_value 非空、value_code/value_label 为 null；原文明确“代码-名称”配对时用 CODE_LABEL，literal_value=null，value_code/value_label 都非空。明确穷举为 CLOSED；存在“其他/银行自定义”余项为 OTHER_BANK_DEFINED。
   - 原文的“其他-XX”“其他-银行自定义”“其他自定义”统一规范成一个 LITERAL 值“其他”，并设 OTHER_BANK_DEFINED；不把 XX、银行自定义写成另一个可选码值。原文只说“新增代码/新增类型时按需要确定”时，只设 OTHER_BANK_DEFINED，绝不凭空增加 literal “其他”。
   - 若值域取决于已明确的父条件，使用 scope_kind=CONDITIONAL 和 condition={dimension_name,dimension_value}，例如客户类型=个人客户时的证件类别。无条件集合使用 GLOBAL 和 condition=null。
3. encoding_rules：原文定义号码/标识构成、位次或连续段时输出。必须按最细、原文已明确的连续位置拆分：例如“第8至10位，其中第1位为地区、后2位为顺序号”必须拆为 8-8 与 9-10；不能只保留外层段。不得把整条编码规则或位次规则塞进 code_value_sets。
4. 标准与码表：只在最终值域确实需要引用标准/码表时输出 standard_references 与 code_table_usages。只能使用当前 bindings 中 BOUND 项，usage_basis 只能为 REVIEWED_STANDARD_BINDING；严禁根据字段名称、两个文字值、样例行或语义相似性猜测附件4码表，严禁输出 CONSTRAINT_COMPATIBLE_ATTACHMENT4。
   - E 列已经直接给出完整最终码值枚举时，该枚举优先于同一段中出现的外部标准：只输出本地 code_value_sets，不输出这些标准引用，也不找码表。
   - E 列只有“如/例如/等”样例而非完整枚举时，样例不是本地码值集；若有绑定标准，样例仅用于判断标准的层级和最终列。标准主体值域由 code_table_usages 表达，绝不创建一个 values=[] 的“标准主体”码值集。原文另行明确的标准外特殊值（例如“境外机构填报国际组织”）与“其他”可组成一个只含补充值的本地码值集，绝不能把“如”后的样例混入。
   - 对绑定码表，原文写“代码/编号/三字符代码/数字代码”时选代码列；写“中文描述/中文字典/中文名称/名称”时必须选中文名称列。样例行用于确认哪一列是对应的代码或中文名称，不得因此犹豫或人工提问。
   - 原文明确引用了标准但当前没有 BOUND 绑定、且该标准仍决定开放值域时，输出 mapping_status=UNRESOLVED、external_standard_id=null，并给出一条明确人工问题；不得伪造 NO_CODE_TABLE 或自行从全量目录绑定。
   - DIRECT_COLUMN 取一列；COMPOSE_COLUMNS 只在原文明示拼装时使用；需要“取某列前n位/第m至n位”时使用 SUBSTRING_COLUMN，填一个 final_value_columns、substring_start、substring_end。

少样本（只学习结构，不是本任务事实或来源）：
- 本地枚举：“取值为是、否。” => 两个 LITERAL：“是”“否”，不能合并为“是，否”。
- 其他模式：“A、B、其他-XX，其中XX为银行自定义。” => LITERAL “A”“B”“其他”，extension_policy=OTHER_BANK_DEFINED；不输出“其他-XX”。
- 条件码值：“个人客户取居民身份证、护照；对公客户取统一社会信用代码。” => 两个 CONDITIONAL 集合，condition 分别为 {客户类型,个人客户}、{客户类型,对公客户}。
- 嵌套分段：“第8至10位中第1位为地区编码、后2位为顺序号。” => segments 为 8-8 地区编码、9-10 顺序号。
- 代码列：“原文要求三字符代码”，已绑定表的样例含 数字代码/字母代码/代码名称 => DIRECT_COLUMN 选字母代码。
- 中文列：“原文要求填写中文描述”，已绑定表的样例含 代码取值=10、代码名称=博士研究生 => DIRECT_COLUMN 选代码名称，不得选代码取值或提出列选择问题。
- 截取列：“取行政区划代码前四位”，已绑定表含代码取值 => SUBSTRING_COLUMN，final_value_columns=["代码取值"], substring_start=1, substring_end=4。

证据纪律：每一条事实、每一个原子值、每一个分段和每一个码表取值声明的 evidence_quote 必须逐字是 E 列原文的一个连续、非空子串；不得概括、改写、拼接多句或省略中间文字。source_refs 只能使用输入给出的 E 列来源。少样本、硬代码目录、项目政策和码表样例都不是事实来源。只输出固定 JSON。"""


def agent2_prompt() -> str:
    return """你是 Agent2：EAST 字段材料与检核规则约束拆分 Agent。\n任务边界：只处理输入的一条字段结构材料、备注和该字段已挂载的检核规则。字段任务明确不含数据元说明、数据元 Agent 输出、格式列、全库 Schema、答疑或外部知识。\n格式边界：字段格式（类型、字符串精确/最大长度、整数最大位数、小数最大位数）完全由硬代码依据《规范附件3》“表格说明”解析；本任务不接收格式列事实，不得输出这些格式约束。\n输出同时包括：\n1. SINGLE_FIELD：只涉及当前字段的固定单字段约束项 ENCODING_RULE、CODE_DOMAIN、DEFAULT_VALUE、FORBIDDEN_VALUE、VALUE_RANGE、NULLABLE、UNIQUE、PRIMARY_KEY、PRIVACY_TRANSFORM、OTHER。\n2. MULTI_FIELD：原文明示当前字段与至少一个其他字段的事实；约束项只能是 NULLABLE、FORBIDDEN_VALUE、VALUE_RANGE、REFERENCE_EXISTENCE、COMPARISON、OTHER，并保留 participant_mentions、condition_type、condition_text、requirement_text。\n每个 MULTI_FIELD 必须在 participant_mentions 中列出当前字段和其他原文支持字段；其他字段只能原样写中文名、表名+字段名或英文码，禁止补猜。table_mention 只能写检核规则或备注中显式出现的表名/表代码；若原文未显式出现表名，返回空字符串，由硬代码按当前字段所在表补齐并判定范围。\n若无从原文支持的第二字段，不得伪造 MULTI_FIELD；改为 SINGLE_FIELD、OTHER 或 UNRESOLVED。JSON 键纪律：NULLABLE 必须为 {\"nullable\":\"YES|NO|UNSPECIFIED\"}，不能写布尔值；COMPARISON/REFERENCE_EXISTENCE 的 value 必须为 {\"range_description\":\"原文要求\"}，比较符和字段关系仍写在 requirement_text，不得发明 comparison_operator、compared_field 等键。只输出 JSON。"""


def agent3_prompt() -> str:
    return """你是 Agent3：对象-明细-状态（ODS）关系与约束控制计划提炼 Agent。\n任务边界：仅依据输入的一条、已由硬代码唯一映射的多字段约束及其必要表字段上下文，判断该约束是否包含 ODS 关系。覆盖表内和跨表关系。\n生命周期定义：OBJECT=由 Foundation/补充 Foundation 铺底生成、生成过程仅引用的字段（FOUNDATION_READ_ONLY）；DETAIL=业务事件生成过程 INSERT 的字段（EVENT_INSERT）；STATE=业务事件生成过程 UPDATE 的字段（EVENT_UPDATE）。只能据输入判断候选角色；不能证明时用 UNRESOLVED，不得发明对象键或字段。\nods_classification 只能为 DETAIL_TO_STATE、OBJECT_STATE_BOUNDARY、STATE_RECONCILIATION、NOT_ODS、UNRESOLVED。仅有数值等式或比较、缺少上述角色证据时，必须为 NOT_ODS 或 UNRESOLVED。正向分类只能将输入 mapped_field_group 内字段写入 ods_members，角色只能为 OBJECT、DETAIL、STATE；不得新增字段。\n你还必须输出 constraint_control_plan：这是“约束控制计划候选”，仅表达检查前后次序，绝不是 ORM、SQL、生产取值公式或可执行写库计划。计划步骤只能用 EVALUATE_CONDITION、READ、BOUND、CHECK、RETURN_NO_CONSTRAINT、ALLOW；不可输出 INSERT 或 UPDATE 步骤。每个步骤必须有 gate：无条件为 {mode:\"ALWAYS\",condition_step_no:null}；条件为真/假分支为 {mode:\"WHEN_TRUE|WHEN_FALSE\",condition_step_no:此前 EVALUATE_CONDITION 的步骤号}。正向 ODS 且信息足够时给出从 1 连续编号的步骤；不适用/无法判断时写 NOT_APPLICABLE 或 UNRESOLVED 并说明原因。plan_status=DERIVED 时，reason 必须逐字填写“由输入原文条件、比较要求和固定生命周期定义推导”。\n少样本（只学习 ODS 判断，不把示例外推为事实）：\n- “成立日期非空且非99991231时，应晚于19490101且小于等于采集日期” => NOT_ODS。\n- “持股比例应大于等于0，小于等于1” => NOT_ODS。\n- “已用额度应小于等于授信额度” => 可为正向 ODS，前提是输入角色证据支持。\n- “原五级分类不应等于新五级分类” => NOT_ODS。\n- “当前授信余额应等于当前授信额度-透支金额” => 可为正向 ODS，前提是输入角色证据支持。\n- “交易借贷标志”为贷，“账户余额”应当大于或等于“交易金额” => 可为 DETAIL_TO_STATE；控制计划应为：1 EVALUATE_CONDITION（ALWAYS）交易借贷标志；2 RETURN_NO_CONSTRAINT（WHEN_FALSE, condition_step_no=1）；3 READ（WHEN_TRUE, condition_step_no=1）账户余额；4 BOUND 或 CHECK（WHEN_TRUE, condition_step_no=1）交易金额不大于账户余额；5 ALLOW（WHEN_TRUE, condition_step_no=1）。\n少样本不是来源，不得在任何输出中把少样本、示例或其结论作为依据；结论、角色证据和 rationale 只能引用输入的检核规则、字段名/备注或固定生命周期定义。每条结论必须用输入 source_refs 和 evidence_quote 追溯。只输出 JSON。"""


def response_contract(kind: str) -> str:
    common = "所有 source_refs 必须逐字来自输入；value 的键不得改名。"
    if kind == "AGENT1":
        return common + """
返回 JSON：{task_id,data_element_code,extraction_status:"EXTRACTED|NO_CONSTRAINT|UNRESOLVED",semantic_constraints,code_value_sets,encoding_rules,standard_references,code_table_usages,manual_review_questions}。
semantic_constraints:[{constraint_item_type:"ENCODING_RULE|DEFAULT_VALUE|FORBIDDEN_VALUE|VALUE_RANGE|NULLABLE|PRIVACY_TRANSFORM|OTHER",value,evidence_quote,source_refs}]；value 键固定为 ENCODING_RULE={encoding_rule_text}、DEFAULT_VALUE={default_value}、FORBIDDEN_VALUE={forbidden_value}、VALUE_RANGE={range_description}、NULLABLE={nullable:"YES|NO"}、PRIVACY_TRANSFORM={privacy_transform_text}、OTHER={other_text}。
code_value_sets:[{set_name,scope_kind:"GLOBAL|CONDITIONAL",condition:GLOBAL时null或CONDITIONAL时{dimension_name,dimension_value},extension_policy:"CLOSED|OTHER_BANK_DEFINED|UNRESOLVED",evidence_quote,source_refs,values:[{value_kind:"LITERAL|CODE_LABEL",literal_value,value_code,value_label,evidence_quote,source_refs}]}]；LITERAL 的 literal_value 非空且 value_code/value_label 为null；CODE_LABEL 的 literal_value 为null且 value_code/value_label 必须两个都非空。
encoding_rules:[{rule_name,exact_length:正整数或null,character_classes:["UPPERCASE_LETTER|LOWERCASE_LETTER|LETTER|DIGIT|ALPHANUMERIC|CHINESE_CHARACTER|OTHER|UNRESOLVED"],evidence_quote,source_refs,segments:[{start_pos:正整数,end_pos:正整数,segment_name,segment_kind:"CODE|SEQUENCE|IDENTIFIER|DATE|OTHER",character_classes:[同前],value_set_names:[仅本任务 set_name],evidence_quote,source_refs}]}]。segments 必须按起止位置递增且不可重叠。
standard_references:[{standard_mention_raw,external_standard_id:字符串或null,mapping_status:"BOUND|NO_CODE_TABLE|UNRESOLVED",representation_kind:"NUMERIC_CODE|ALPHA3_CODE|ALPHA_CODE|CHINESE_NAME|CODE_AND_NAME|OTHER|UNRESOLVED",evidence_quote,source_refs}]。
code_table_usages:[{usage_basis:"REVIEWED_STANDARD_BINDING|CONSTRAINT_COMPATIBLE_ATTACHMENT4",external_standard_id:字符串或null,code_table_id,sqlite_table_name,selection_mode:"DIRECT_COLUMN|COMPOSE_COLUMNS|SUBSTRING_COLUMN|UNRESOLVED",final_value_columns:[所选码表 value_columns],final_value_template:字符串或null,substring_start:正整数或null,substring_end:正整数或null,evidence_quote,source_refs,rationale}]。DIRECT_COLUMN/COMPOSE_COLUMNS/UNRESOLVED时substring_start/end均为null；SUBSTRING_COLUMN时只选一列、template为null，填写连续的1基起止位置。
manual_review_questions:[{question,evidence_quote,source_refs}]。NO_CONSTRAINT 时五个事实数组和人工问题均为空；UNRESOLVED 时保留已可确定的事实，且 manual_review_questions 必须至少一条并清楚说明待人工确定的问题。"""
    if kind == "AGENT2":
        return common + "\n返回 JSON：{task_id,field_id,candidates:[...] }。每个候选含 classification:\"SINGLE_FIELD|MULTI_FIELD|OTHER|UNRESOLVED\"、evidence_quote、source_refs。SINGLE_FIELD 含 constraint_item_type,value，constraint_item_type 只能为 ENCODING_RULE、CODE_DOMAIN、DEFAULT_VALUE、FORBIDDEN_VALUE、VALUE_RANGE、NULLABLE、UNIQUE、PRIMARY_KEY、PRIVACY_TRANSFORM、OTHER。MULTI_FIELD 另含 constraint_item_type,value,participant_mentions:[{table_mention,field_mention,participant_role:\"SUBJECT|CONDITION|REFERENCE|TARGET|OTHER\"}],condition_type:\"ALWAYS|WHEN|REFERENCE|COMPARISON|OTHER\",condition_text(非ALWAYS必填),requirement_text。OTHER/UNRESOLVED 含 reason。固定 value 例：NULLABLE={\"nullable\":\"NO\"}；COMPARISON/REFERENCE_EXISTENCE={\"range_description\":\"原文要求\"}；OTHER={\"other_text\":\"原文\"}。"
    return common + "\n返回 JSON：{task_id,atomic_multifield_rule_id,ods_classification,ods_members,object_context,constraint_control_plan,source_refs,evidence_quote,rationale,unresolved_reason}。ods_members 的每项：{field_id,ods_role:\"OBJECT|DETAIL|STATE\",lifecycle_action:\"FOUNDATION_READ_ONLY|EVENT_INSERT|EVENT_UPDATE\",role_evidence}。object_context：{status:\"MAPPED_MEMBER|NOT_IN_ATOMIC_RULE|UNRESOLVED\",field_ids:[仅已映射字段],rationale}。constraint_control_plan：{plan_status:\"DERIVED|NOT_APPLICABLE|UNRESOLVED\",steps:[{step_no:从1连续编号,step_type:\"EVALUATE_CONDITION|READ|BOUND|CHECK|RETURN_NO_CONSTRAINT|ALLOW\",target_field_ids:[仅已映射字段],gate:{mode:\"ALWAYS|WHEN_TRUE|WHEN_FALSE\",condition_step_no:条件步骤号或null},instruction}],reason}；gate=WHEN_TRUE/WHEN_FALSE 时 condition_step_no 必须指向此前的 EVALUATE_CONDITION，gate=ALWAYS 时为 null。DERIVED 时 steps 必填且 reason 必须逐字为“由输入原文条件、比较要求和固定生命周期定义推导”，其他状态时 steps=[] 且 reason 必填。NOT_ODS/UNRESOLVED 时 ods_members 必须为 []，且 unresolved_reason 必填。"


def task_agent1(code: str, elements: dict[str, dict[str, Any]]) -> dict[str, Any]:
    element = elements[code]
    description_ref = exact_cell_ref(element["source_refs"][0], "E")
    selected = {key: element[key] for key in ("code", "name", "name_en", "description")}
    selected["source_refs"] = [description_ref]
    bindings = code_table_bindings(code)
    hard_format = parse_hard_format(element.get("format_raw", ""), exact_cell_ref(element["source_refs"][0], "F"))
    return {"task_id": f"V3_3_A1_DE_{code}", "task_kind": "AGENT1", "contract_version": AGENT1_CONTRACT_VERSION, "data_element": selected, "hard_agent1_directives": AGENT1_HARD_DIRECTIVES.get(code, ()), "standard_mapping_context": {"binding_policy": "仅能使用当前bindings中的BOUND码表；E列直接完整枚举优先，禁止按字段语义猜测附件4码表。", "bindings": bindings, "all_external_standards": all_external_standards(), "all_code_tables": all_code_tables()}, "hard_format_constraints": apply_agent1_hard_format_override(code, hard_format), "hard_code_table_bindings": bindings}


def task_agent2(field: dict[str, Any]) -> dict[str, Any]:
    selected = {key: field[key] for key in ("field_id", "table_id", "table_code", "table_name", "field_code", "field_name", "field_no", "remarks", "change_type", "change_column", "change_description", "source_ref")}
    return {"task_id": f"V2_A2_{field['field_id']}", "task_kind": "AGENT2", "contract_version": CONTRACT_VERSION, "field": selected, "validation_rules": field["validation_rules"], "hard_format_constraints": parse_hard_format(field.get("format_raw", ""), exact_cell_ref(field["source_ref"], "L")), "excluded_inputs": ["data_element_description", "agent1_output", "full_schema", "faq", "external_knowledge"]}


def build_prompt(task: dict[str, Any]) -> str:
    model_task = model_input(task)
    prompt = {"AGENT1": agent1_prompt, "AGENT2": agent2_prompt, "AGENT3": agent3_prompt}[task["task_kind"]]()
    return prompt + "\n\n固定 JSON 合同：\n" + response_contract(task["task_kind"]) + "\n\n输入任务与受控参考上下文（唯一可用依据）：\n" + json.dumps(model_task, ensure_ascii=False, indent=2)


def model_input(task: dict[str, Any]) -> dict[str, Any]:
    """Return precisely the JSON fact package appended to the real LLM prompt."""
    return {key: value for key, value in task.items() if key not in {"hard_format_constraints", "hard_code_table_bindings"}}


def exact_mapping(candidate: dict[str, Any], subject: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    by_table_alias: dict[str, set[str]] = {}
    by_field_alias: dict[tuple[str, str], set[str]] = {}
    lookup = {field["field_id"]: field for field in fields}
    for field in fields:
        for alias in {field["table_code"], field["table_name"]}:
            by_table_alias.setdefault(alias, set()).add(field["table_code"])
        for alias in {field["field_code"], field["field_name"]}:
            by_field_alias.setdefault((field["table_code"], alias), set()).add(field["field_id"])
    mapped, reviews = [], []
    for ordinal, mention in enumerate(candidate["participant_mentions"], start=1):
        table_text = str(mention.get("table_mention") or "").strip()
        field_text = str(mention["field_mention"]).strip()
        table_codes = by_table_alias.get(table_text, set()) if table_text else {subject["table_code"]}
        field_ids: set[str] = set()
        for table_code in table_codes:
            field_ids |= by_field_alias.get((table_code, field_text), set())
        if len(field_ids) != 1:
            reviews.append({"ordinal": ordinal, "mention": mention, "candidate_table_codes": sorted(table_codes), "candidate_field_ids": sorted(field_ids), "mapping_status": "MAPPING_NOT_FOUND" if not field_ids else "MAPPING_AMBIGUOUS"})
            continue
        field_id = next(iter(field_ids))
        field = lookup[field_id]
        mapped.append({"ordinal": ordinal, "field_id": field_id, "table_code": field["table_code"], "field_code": field["field_code"], "field_name": field["field_name"], "field_name_source_ref": exact_cell_ref(field["source_ref"], "F"), "remarks_source_ref": exact_cell_ref(field["source_ref"], "M") if field.get("remarks") else None, "participant_role": mention["participant_role"], "mapping_basis": {"table_mention": table_text or subject["table_code"], "field_mention": field_text, "table_resolution": "EXPLICIT_MENTION" if table_text else "DEFAULT_CURRENT_SUBJECT_TABLE", "match_type": "EXACT_CODE_OR_NAME"}})
    if reviews:
        return {"mapping_status": "PENDING_REVIEW", "mapping_reviews": reviews}
    unique = {item["field_id"] for item in mapped}
    if len(unique) < 2:
        return {"mapping_status": "PENDING_REVIEW", "mapping_reviews": [{"mapping_status": "MAPPING_EVIDENCE_INSUFFICIENT", "reason": "映射后不足两个不同字段"}]}
    rule_anchor = sorted(candidate["source_refs"])
    atomic_id = "AMR_" + canonical_hash({"fields": sorted(unique), "item": candidate["constraint_item_type"], "condition": candidate.get("condition_text", ""), "requirement": candidate.get("requirement_text", ""), "source_refs": rule_anchor})[:24]
    return {"mapping_status": "MAPPED_UNIQUE", "atomic_multifield_rule_id": atomic_id, "scope": "WITHIN_TABLE" if len({item["table_code"] for item in mapped}) == 1 else "CROSS_TABLE", "mapped_field_group": mapped, "mapping_reviews": []}


def task_agent3(agent2_task: dict[str, Any], candidate: dict[str, Any], mapping: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    table_codes = {item["table_code"] for item in mapping["mapped_field_group"]}
    context = [{**{key: item[key] for key in ("field_id", "table_code", "table_name", "field_code", "field_name", "remarks")}, "field_name_source_ref": exact_cell_ref(item["source_ref"], "F"), "remarks_source_ref": exact_cell_ref(item["source_ref"], "M") if item.get("remarks") else None} for item in fields if item["table_code"] in table_codes]
    rule = {key: candidate[key] for key in ("constraint_item_type", "value", "condition_type", "condition_text", "requirement_text", "evidence_quote", "source_refs") if key in candidate}
    return {"task_id": f"V2_A3_{mapping['atomic_multifield_rule_id']}", "task_kind": "AGENT3", "contract_version": CONTRACT_VERSION, "atomic_multifield_rule_id": mapping["atomic_multifield_rule_id"], "scope": mapping["scope"], "mapped_field_group": mapping["mapped_field_group"], "multifield_rule": rule, "table_context": context, "lifecycle_policy": {"OBJECT": "FOUNDATION_READ_ONLY", "DETAIL": "EVENT_INSERT", "STATE": "EVENT_UPDATE"}, "control_plan_boundary": "仅约束控制步骤候选；不是ORM、SQL、生产取值公式或可执行写库计划", "parent_agent2_task_id": agent2_task["task_id"]}


def call(task: dict[str, Any], full_prompt: str | None = None) -> tuple[str, dict[str, Any]]:
    key = KEY_PATH.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("DeepSeek API key为空")
    full_prompt = full_prompt or build_prompt(task)
    max_tokens = 24000 if task.get("task_kind") == "AGENT1" and task.get("data_element", {}).get("code") == "001011" else 8000
    body = {"model": MODEL, "temperature": 0, "max_tokens": max_tokens, "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": "你是严谨的 EAST 监管材料结构化提取器。不得补充、纠正或猜测输入之外的事实。只输出 JSON。"}, {"role": "user", "content": full_prompt}]}
    request = urllib.request.Request("https://api.deepseek.com/chat/completions", data=json.dumps(body, ensure_ascii=False).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.loads(response.read().decode())
    content = ((result.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not isinstance(content, str):
        raise RuntimeError("DeepSeek没有返回choices[0].message.content")
    return full_prompt, {"content": content, "metadata": {"model": result.get("model", MODEL), "usage": result.get("usage", {}), "finish_reason": (result.get("choices") or [{}])[0].get("finish_reason", "")}}


def parse(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    result = json.loads(text[text.find("{"):text.rfind("}") + 1])
    if not isinstance(result, dict):
        raise ContractError("响应根节点必须是JSON对象")
    return result


def normalize_agent1_payload(payload: dict[str, Any], description: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply only deterministic output normalizations and preserve their audit trail.

    The model may redundantly restate a structured encoding rule as a semantic
    ENCODING_RULE, or create an empty conditional set to describe an open domain.
    Neither carries additional publishable facts.  Removing them prevents a
    formatting artifact from turning an otherwise valid extraction into failure.
    """
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    changes: list[dict[str, Any]] = []
    if normalized.get("encoding_rules") and isinstance(normalized.get("semantic_constraints"), list):
        before = len(normalized["semantic_constraints"])
        normalized["semantic_constraints"] = [
            item for item in normalized["semantic_constraints"]
            if item.get("constraint_item_type") != "ENCODING_RULE"
        ]
        if len(normalized["semantic_constraints"]) != before:
            changes.append({"change_type": "DROP_DUPLICATE_STRUCTURED_ENCODING_RULE", "count": before - len(normalized["semantic_constraints"])})
    empty_rules = [rule for rule in normalized.get("encoding_rules", []) if isinstance(rule, dict) and not rule.get("segments")]
    if empty_rules:
        normalized["encoding_rules"] = [rule for rule in normalized["encoding_rules"] if rule not in empty_rules]
        for rule in empty_rules:
            normalized.setdefault("semantic_constraints", []).append({
                "constraint_item_type": "ENCODING_RULE",
                "value": {"encoding_rule_text": rule.get("evidence_quote") or rule.get("rule_name")},
                "evidence_quote": rule.get("evidence_quote"),
                "source_refs": rule.get("source_refs"),
            })
        changes.append({"change_type": "CONVERT_UNSEGMENTED_ENCODING_RULE_TO_SEMANTIC_CONSTRAINT", "count": len(empty_rules)})
    if description:
        for constraint in normalized.get("semantic_constraints", []):
            quote = constraint.get("evidence_quote")
            if not isinstance(quote, str) or quote not in description:
                constraint["evidence_quote"] = description
                changes.append({"change_type": "REPLACE_NONVERBATIM_CONSTRAINT_QUOTE_WITH_FULL_DESCRIPTION", "constraint_item_type": constraint.get("constraint_item_type")})
        for rule in normalized.get("encoding_rules", []):
            quote = rule.get("evidence_quote")
            if isinstance(quote, str) and quote in description:
                continue
            segment_quotes = [
                segment.get("evidence_quote")
                for segment in rule.get("segments", [])
                if isinstance(segment.get("evidence_quote"), str) and segment["evidence_quote"] in description
            ]
            if segment_quotes:
                rule["evidence_quote"] = segment_quotes[0]
                changes.append({"change_type": "REPLACE_NONCONTIGUOUS_RULE_QUOTE_WITH_VERBATIM_SEGMENT_QUOTE", "rule_name": rule.get("rule_name")})
        for code_set in normalized.get("code_value_sets", []):
            quote = code_set.get("evidence_quote")
            if isinstance(quote, str) and quote in description:
                continue
            value_quotes = [
                value.get("evidence_quote")
                for value in code_set.get("values", [])
                if isinstance(value.get("evidence_quote"), str) and value["evidence_quote"] in description
            ]
            if value_quotes:
                code_set["evidence_quote"] = value_quotes[0]
                changes.append({"change_type": "REPLACE_NONCONTIGUOUS_CODE_SET_QUOTE_WITH_VERBATIM_VALUE_QUOTE", "set_name": code_set.get("set_name")})
    questions = normalized.setdefault("manual_review_questions", [])
    for standard in normalized.get("standard_references", []):
        if standard.get("mapping_status") == "NO_CODE_TABLE" and not standard.get("external_standard_id"):
            standard["mapping_status"] = "UNRESOLVED"
            standard["external_standard_id"] = None
            question = f"原文引用的标准“{standard.get('standard_mention_raw', '')}”当前没有已审阅绑定；请确认是否应接入受控码表或保留为开放外部值域。"
            if not any(item.get("question") == question for item in questions if isinstance(item, dict)):
                questions.append({"question": question, "evidence_quote": standard.get("evidence_quote"), "source_refs": standard.get("source_refs")})
            changes.append({"change_type": "NORMALIZE_UNBOUND_STANDARD_TO_UNRESOLVED", "standard_mention_raw": standard.get("standard_mention_raw")})
    if isinstance(normalized.get("code_value_sets"), list):
        empty_names = {
            str(item.get("set_name"))
            for item in normalized["code_value_sets"]
            if isinstance(item, dict) and isinstance(item.get("values"), list) and not item["values"]
        }
        if empty_names:
            normalized["code_value_sets"] = [
                item for item in normalized["code_value_sets"]
                if str(item.get("set_name")) not in empty_names or item.get("values")
            ]
            for rule in normalized.get("encoding_rules", []):
                for segment in rule.get("segments", []):
                    if isinstance(segment.get("value_set_names"), list):
                        segment["value_set_names"] = [name for name in segment["value_set_names"] if name not in empty_names]
            changes.append({"change_type": "DROP_EMPTY_CODE_VALUE_SET", "set_names": sorted(empty_names)})
    return normalized, changes


def apply_agent1_hard_overrides(payload: dict[str, Any], task: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply explicitly approved, source-traceable per-element hard decisions."""
    code = task["data_element"]["code"]
    refs = task["data_element"]["source_refs"]
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    description = str(task["data_element"].get("description") or "")
    def literal(value: str) -> dict[str, Any]:
        return {"value_kind": "LITERAL", "literal_value": value, "value_code": None, "value_label": None, "evidence_quote": value, "source_refs": refs}
    def other_only() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not description:
            raise RuntimeError(f"{code}硬代码裁决缺少数据元说明原文")
        payload.update({
            "extraction_status": "EXTRACTED",
            "semantic_constraints": [{"constraint_item_type": "OTHER", "value": {"other_text": description}, "evidence_quote": description, "source_refs": refs}],
            "code_value_sets": [], "encoding_rules": [], "standard_references": [], "code_table_usages": [], "manual_review_questions": [],
        })
        return payload, [{"change_type": "CLASSIFY_APPROVED_NATIONAL_IDENTIFIER_AS_OTHER", "data_element_code": code, "source_ref": refs[0]}]
    if code in {"001010", "002019"}:
        return other_only()
    if code == "010006":
        payload.update({
            "extraction_status": "EXTRACTED", "semantic_constraints": [], "encoding_rules": [], "standard_references": [], "code_table_usages": [], "manual_review_questions": [],
            "code_value_sets": [{"set_name": "利率类型", "scope_kind": "GLOBAL", "condition": None, "extension_policy": "CLOSED", "evidence_quote": description, "source_refs": refs, "values": [literal("LPR"), literal("非LPR")]}],
        })
        return payload, [{"change_type": "APPLY_APPROVED_DIRECT_ENUMERATION", "data_element_code": code, "source_ref": refs[0]}]
    if code == "003021":
        rmb_values = ["基本户", "一般户", "专用户", "临时户", "临时户（验资注册）", "临时户（验资增资）", "待核准账户", "人民币保证金账户", "同业存款账户", "委贷基金账户"]
        foreign_values = ["经常项目-外汇结算账户", "资本项目-外汇资本金账户"]
        payload.update({
            "extraction_status": "EXTRACTED", "semantic_constraints": [], "encoding_rules": [], "standard_references": [], "code_table_usages": [], "manual_review_questions": [],
            "code_value_sets": [
                {"set_name": "人民币账户类型", "scope_kind": "CONDITIONAL", "condition": {"dimension_name": "币种", "dimension_value": "人民币"}, "extension_policy": "CLOSED", "evidence_quote": "人民币：基本户，一般户，专用户，临时户，临时户（验资注册），临时户（验资增资），待核准账户，人民币保证金账户，同业存款账户，委贷基金账户。", "source_refs": refs, "values": [literal(value) for value in rmb_values]},
                {"set_name": "外币账户类型", "scope_kind": "CONDITIONAL", "condition": {"dimension_name": "币种", "dimension_value": "外币"}, "extension_policy": "CLOSED", "evidence_quote": "如“经常项目-外汇结算账户”，“资本项目-外汇资本金账户”等。", "source_refs": refs, "values": [literal(value) for value in foreign_values]},
                {"set_name": "其他账户类型", "scope_kind": "GLOBAL", "condition": None, "extension_policy": "OTHER_BANK_DEFINED", "evidence_quote": "以“其他-XX”填报，其中“XX”为银行自定义类型。", "source_refs": refs, "values": [literal("其他")]},
            ],
        })
        return payload, [{"change_type": "APPLY_APPROVED_CONDITIONAL_CODE_DOMAIN", "data_element_code": code, "source_ref": refs[0]}]
    if code != "001029":
        return payload, []
    payload["standard_references"] = [
        item for item in payload.get("standard_references", [])
        if "GB13496-92" not in str(item.get("standard_mention_raw", ""))
    ]
    payload["code_table_usages"] = []
    payload["manual_review_questions"] = []
    payload["encoding_rules"] = [{
        "rule_name": "金融机构代码硬编码分段规则",
        "exact_length": 11,
        "character_classes": ["ALPHANUMERIC"],
        "evidence_quote": "第一级：清算中心编码，占4个字节。规定一个中心城市范围内只能用中心城市人民银行的清算中心代码，不能使用人民银行县级支行清算中心代码。",
        "source_refs": refs,
        "segments": [
            {"start_pos": 1, "end_pos": 4, "segment_name": "清算中心编码", "segment_kind": "CODE", "character_classes": ["DIGIT"], "value_set_names": [], "evidence_quote": "第一级：清算中心编码，占4个字节。规定一个中心城市范围内只能用中心城市人民银行的清算中心代码，不能使用人民银行县级支行清算中心代码。", "source_refs": refs},
            {"start_pos": 5, "end_pos": 5, "segment_name": "机构类别代码", "segment_kind": "CODE", "character_classes": ["ALPHANUMERIC"], "value_set_names": [], "evidence_quote": "最左边1位可以作为机构类别代码", "source_refs": refs},
            {"start_pos": 6, "end_pos": 6, "segment_name": "原银行行别和保险公司标志代码", "segment_kind": "CODE", "character_classes": ["ALPHANUMERIC"], "value_set_names": [], "evidence_quote": "在原1位银行行别和保险公司标志代码（GB13496-92）的基础上，左右各扩充1位。", "source_refs": refs},
            {"start_pos": 7, "end_pos": 7, "segment_name": "机构代码右扩充位", "segment_kind": "CODE", "character_classes": ["ALPHANUMERIC"], "value_set_names": [], "evidence_quote": "在原1位银行行别和保险公司标志代码（GB13496-92）的基础上，左右各扩充1位。", "source_refs": refs},
            {"start_pos": 8, "end_pos": 8, "segment_name": "中心城市下属地区编码", "segment_kind": "CODE", "character_classes": ["ALPHANUMERIC"], "value_set_names": [], "evidence_quote": "第1位为中心城市下属地区（主要是下属县或县级市、区）编码，若数字资源不足时，可以向英文字母A-Z扩充。", "source_refs": refs},
            {"start_pos": 9, "end_pos": 10, "segment_name": "金融机构顺序号", "segment_kind": "SEQUENCE", "character_classes": ["ALPHANUMERIC"], "value_set_names": [], "evidence_quote": "后2位为金融机构顺序号。", "source_refs": refs},
            {"start_pos": 11, "end_pos": 11, "segment_name": "校验码", "segment_kind": "OTHER", "character_classes": ["ALPHANUMERIC"], "value_set_names": [], "evidence_quote": "第四级：校验码，占1个字节，计算方法沿用“全国清算中心代码”中的规定。", "source_refs": refs},
        ],
    }]
    return payload, [{"change_type": "APPLY_APPROVED_HARD_ENCODING_OVERRIDE", "data_element_code": "001029", "source_ref": refs[0]}]


def run_agent(task: dict[str, Any], output: Path, namespace: str = "pilot") -> dict[str, Any]:
    base = output / namespace / task["task_kind"].lower() / task["task_id"]
    base.mkdir(parents=True, exist_ok=True)
    validated_path = base / "validated_output.json"
    if validated_path.exists():
        payload = json.loads(validated_path.read_text(encoding="utf-8"))
        {"AGENT1": validate_agent1, "AGENT2": validate_agent2, "AGENT3": validate_agent3}[task["task_kind"]](payload, task)
        return payload
    prompt = build_prompt(task)
    (base / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    write_json(base / "input.json", model_input(task))
    if "hard_format_constraints" in task:
        write_json(base / "hard_format_constraints.json", task["hard_format_constraints"])
    if "hard_code_table_bindings" in task:
        write_json(base / "hard_code_table_bindings.json", task["hard_code_table_bindings"])
    _, response = call(task, prompt)
    write_json(base / "raw_response.json", response)
    payload = parse(response["content"])
    if task["task_kind"] == "AGENT1":
        payload, normalization_changes = normalize_agent1_payload(payload, task["data_element"]["description"])
        payload, hard_override_changes = apply_agent1_hard_overrides(payload, task)
        normalization_changes.extend(hard_override_changes)
        write_json(base / "normalized_output.json", {"normalization_changes": normalization_changes, "payload": payload})
    try:
        {"AGENT1": validate_agent1, "AGENT2": validate_agent2, "AGENT3": validate_agent3}[task["task_kind"]](payload, task)
    except ContractError as exc:
        (base / "validation_error.txt").write_text(str(exc) + "\n", encoding="utf-8")
        raise
    write_json(base / "validated_output.json", payload)
    return payload


def pilot(output: Path) -> dict[str, Any]:
    output = output.resolve()
    elements, raw_fields, _ = parse_att3()
    rules, _ = parse_att4()
    fields, _ = parse_frozen_fields(raw_fields, rules)
    field = next(item for item in fields if item["field_id"] == "FLD_GRHQCKFHZMX_ZHYE")
    task1 = task_agent1(field["data_element_code"], elements)
    task2 = task_agent2(field)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "V2任务合同与硬代码设计.json", {"contract_version": CONTRACT_VERSION, "agents": ["Agent1:数据元说明E列", "Agent2:字段材料+检核规则", "Agent3:ODS+约束控制计划候选"], "hard_code": ["格式列F/L按表格说明解析，不进入Agent1/Agent2 prompt", "冻结输入与来源哈希校验", "JSON/枚举/来源引用校验", "自然语言表字段唯一精确映射", "空table_mention按当前字段所在表补齐", "表内/跨表范围判定", "stable atomic_multifield_rule_id", "映射失败人工审核"], "prohibited": ["旧V1候选SQLite", "GLM审核结果", "模糊匹配自动入库", "把Agent3控制计划当作ORM、SQL或正式写库计划"]})
    output1 = run_agent(task1, output)
    output2 = run_agent(task2, output)
    multi = [item for item in output2["candidates"] if item.get("classification") == "MULTI_FIELD"]
    if not multi:
        raise RuntimeError("Agent2试跑未产生多字段候选，无法继续Agent3；请人工查看Agent2输出。")
    mappings = [exact_mapping(item, task2["field"], fields) for item in multi]
    write_json(output / "pilot" / "AGENT2_mapping.json", {"agent2_task_id": task2["task_id"], "mappings": mappings})
    mapping = next((item for item in mappings if item["mapping_status"] == "MAPPED_UNIQUE"), None)
    if mapping is None:
        raise RuntimeError("Agent2多字段候选均未唯一映射；已输出人工审核映射清单，不能调用Agent3。")
    chosen = multi[mappings.index(mapping)]
    task3 = task_agent3(task2, chosen, mapping, fields)
    output3 = run_agent(task3, output)
    report = {"created_at": now(), "contract_version": CONTRACT_VERSION, "pilot_subject": {"data_element": task1["data_element"]["code"], "field": task2["field"]["field_id"], "validation_rules": [item["rule_no"] for item in task2["validation_rules"]]}, "agent1_status": output1["extraction_status"], "agent2_candidate_count": len(output2["candidates"]), "agent2_multifield_count": len(multi), "agent3_classification": output3["ods_classification"], "review_locations": {"agent1": str((output / "pilot" / "agent1" / task1["task_id"]).relative_to(ROOT)), "agent2": str((output / "pilot" / "agent2" / task2["task_id"]).relative_to(ROOT)), "mapping": str((output / "pilot" / "AGENT2_mapping.json").relative_to(ROOT)), "agent3": str((output / "pilot" / "agent3" / task3["task_id"]).relative_to(ROOT))}}
    write_json(output / "pilot" / "pilot_report.json", report)
    return report


def agent1_batch(output: Path, workers: int, selected_codes: set[str] | None = None) -> dict[str, Any]:
    """Run only Agent1, preserving a per-data-element human review package."""
    output = output.resolve()
    elements, _, _ = parse_att3()
    if selected_codes is not None:
        unknown_codes = selected_codes - set(elements)
        if unknown_codes:
            raise RuntimeError(f"Agent1请求了不存在的数据元编码: {sorted(unknown_codes)}")
    codes = sorted(selected_codes if selected_codes is not None else elements)
    tasks = [task_agent1(code, elements) for code in codes]
    output.mkdir(parents=True, exist_ok=True)
    batch_root = output / "agent1_batch"
    write_json(batch_root / "batch_contract.json", {
        "contract_version": AGENT1_CONTRACT_VERSION,
        "batch_scope": "Agent1 only; 数据元说明E列逐条独立调用",
        "task_count": len(tasks),
        "selected_data_element_codes": codes,
        "workers": workers,
        "llm_excluded": ["数据元说明F列格式", "字段材料", "Agent2/Agent3输出", "码表行", "外部知识"],
        "llm_authorized_reference_context": ["当前数据元已审阅的国标/附件4码表绑定", "全部附件4码表的列目录与每表两条样例行"],
        "hard_code_only": ["数据元格式Cn/C..n/I/I..n/Dw.d/F解析", "JSON/枚举/来源校验", "调用目录保存完整硬代码绑定"],
        "publication_status": "review_only_not_released",
    })

    def execute(task: dict[str, Any]) -> dict[str, Any]:
        code = task["data_element"]["code"]
        base = batch_root / "agent1" / task["task_id"]
        try:
            payload = run_agent(task, output, namespace="agent1_batch")
            review_reasons = []
            if payload["extraction_status"] == "UNRESOLVED":
                review_reasons.append("AGENT1_UNRESOLVED")
            if task["hard_format_constraints"]["format_parse_status"] != "PARSED":
                review_reasons.append("HARD_FORMAT_UNSUPPORTED")
            if payload["standard_references"] and not task["hard_code_table_bindings"]:
                review_reasons.append("STANDARD_REFERENCE_WITHOUT_REVIEWED_CODE_TABLE_BINDING")
            if any(item["mapping_status"] == "UNRESOLVED" for item in payload["standard_references"]):
                review_reasons.append("STANDARD_REFERENCE_MAPPING_UNRESOLVED")
            if any(item["selection_mode"] == "UNRESOLVED" for item in payload["code_table_usages"]):
                review_reasons.append("STANDARD_VALUE_COLUMN_SELECTION_UNRESOLVED")
            if any(item["usage_basis"] == "CONSTRAINT_COMPATIBLE_ATTACHMENT4" for item in payload["code_table_usages"]):
                review_reasons.append("CONSTRAINT_COMPATIBLE_ATTACHMENT4_MAPPING_REQUIRES_REVIEW")
            if any(item["extension_policy"] == "UNRESOLVED" for item in payload["code_value_sets"]):
                review_reasons.append("CODE_SET_EXTENSION_UNRESOLVED")
            return {
                "data_element_code": code,
                "data_element_name": task["data_element"]["name"],
                "description": task["data_element"]["description"],
                "description_source_ref": task["data_element"]["source_refs"][0],
                "hard_format_constraints": task["hard_format_constraints"],
                "hard_code_table_bindings": task["hard_code_table_bindings"],
                "run_status": "VALIDATED",
                "extraction_status": payload["extraction_status"],
                "semantic_constraints": payload["semantic_constraints"],
                "code_value_sets": payload["code_value_sets"],
                "encoding_rules": payload["encoding_rules"],
                "standard_references": payload["standard_references"],
                "code_table_usages": payload["code_table_usages"],
                "manual_review_questions": payload["manual_review_questions"],
                "review_reasons": review_reasons,
                "review_dir": str(base.relative_to(ROOT)),
            }
        except Exception as exc:  # Keep batch running; each failure is a manual-review record.
            error = {"error_type": type(exc).__name__, "message": str(exc), "recorded_at": now()}
            write_json(base / "batch_error.json", error)
            return {
                "data_element_code": code,
                "data_element_name": task["data_element"]["name"],
                "description": task["data_element"]["description"],
                "description_source_ref": task["data_element"]["source_refs"][0],
                "hard_format_constraints": task["hard_format_constraints"],
                "hard_code_table_bindings": task["hard_code_table_bindings"],
                "run_status": "MANUAL_REVIEW_REQUIRED",
                "error": error,
                "review_reasons": ["AGENT1_CALL_OR_CONTRACT_FAILURE"],
                "review_dir": str(base.relative_to(ROOT)),
            }

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(execute, task) for task in tasks]
        for completed, future in enumerate(as_completed(futures), start=1):
            records.append(future.result())
            if completed % 25 == 0 or completed == len(tasks):
                print(f"Agent1 progress: {completed}/{len(tasks)}", flush=True)
    records.sort(key=lambda item: item["data_element_code"])

    run_counts = Counter(item["run_status"] for item in records)
    extraction_counts = Counter(item.get("extraction_status", "NO_OUTPUT") for item in records)
    item_counts = Counter(
        constraint["constraint_item_type"]
        for item in records if item["run_status"] == "VALIDATED"
        for constraint in item["semantic_constraints"]
    )
    code_value_count = sum(
        len(code_set["values"])
        for item in records if item["run_status"] == "VALIDATED"
        for code_set in item["code_value_sets"]
    )
    encoding_segment_count = sum(
        len(rule["segments"])
        for item in records if item["run_status"] == "VALIDATED"
        for rule in item["encoding_rules"]
    )
    other_review_records = [
        {
            "data_element_code": item["data_element_code"],
            "data_element_name": item["data_element_name"],
            "other_constraints": [constraint for constraint in item["semantic_constraints"] if constraint["constraint_item_type"] == "OTHER"],
            "review_dir": item["review_dir"],
        }
        for item in records
        if item["run_status"] == "VALIDATED" and any(constraint["constraint_item_type"] == "OTHER" for constraint in item["semantic_constraints"])
    ]
    review = {
        "created_at": now(),
        "contract_version": AGENT1_CONTRACT_VERSION,
        "publication_status": "review_only_not_released",
        "task_count": len(tasks),
        "run_status_counts": dict(sorted(run_counts.items())),
        "extraction_status_counts": dict(sorted(extraction_counts.items())),
        "constraint_item_counts": dict(sorted(item_counts.items())),
        "atomic_local_code_value_count": code_value_count,
        "encoding_segment_count": encoding_segment_count,
        "other_review_codes": [item["data_element_code"] for item in other_review_records],
        "other_review_records": other_review_records,
        "manual_review_codes": [item["data_element_code"] for item in records if item["review_reasons"]],
        "manual_review_records": [{"data_element_code": item["data_element_code"], "data_element_name": item["data_element_name"], "review_reasons": item["review_reasons"], "review_dir": item["review_dir"]} for item in records if item["review_reasons"]],
        "records": records,
    }
    write_json(batch_root / "agent1_review.json", review)
    return {key: value for key, value in review.items() if key != "records"} | {"review_file": str((batch_root / "agent1_review.json").relative_to(ROOT))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--agent1-all", action="store_true")
    parser.add_argument("--agent1-codes", help="逗号分隔的数据元编码；用于可审计的Agent1定向回归")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 6:
        parser.error("--workers必须在1至6之间")
    if args.pilot and (args.agent1_all or args.agent1_codes):
        parser.error("--pilot不能与Agent1批量参数同时使用")
    if not args.pilot and not args.agent1_all and not args.agent1_codes:
        parser.error("必须选择 --pilot、--agent1-all 或 --agent1-codes")
    if args.agent1_all and args.agent1_codes:
        parser.error("--agent1-all与--agent1-codes不能同时使用")
    selected_codes = {code.strip() for code in args.agent1_codes.split(",") if code.strip()} if args.agent1_codes else None
    result = pilot(args.output) if args.pilot else agent1_batch(args.output, args.workers, selected_codes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
