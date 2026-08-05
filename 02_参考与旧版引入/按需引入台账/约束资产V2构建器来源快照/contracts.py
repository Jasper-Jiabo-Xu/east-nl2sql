"""Fixed JSON contracts for the three DeepSeek extraction agents."""

from __future__ import annotations

from typing import Any


SINGLE_ITEMS = frozenset({
    "ENCODING_RULE", "CODE_DOMAIN", "DEFAULT_VALUE", "FORBIDDEN_VALUE",
    "VALUE_RANGE", "NULLABLE", "UNIQUE", "PRIMARY_KEY", "PRIVACY_TRANSFORM", "OTHER",
})
DATA_ELEMENT_ITEMS = SINGLE_ITEMS - {"UNIQUE", "PRIMARY_KEY"}
MULTI_ITEMS = frozenset({"NULLABLE", "FORBIDDEN_VALUE", "VALUE_RANGE", "REFERENCE_EXISTENCE", "COMPARISON", "OTHER"})
CONDITION_TYPES = frozenset({"ALWAYS", "WHEN", "REFERENCE", "COMPARISON", "OTHER"})
ODS_CLASSES = frozenset({"DETAIL_TO_STATE", "OBJECT_STATE_BOUNDARY", "STATE_RECONCILIATION", "NOT_ODS", "UNRESOLVED"})
ODS_ROLES = frozenset({"OBJECT", "DETAIL", "STATE"})
TRI_STATES = frozenset({"YES", "NO", "UNSPECIFIED"})
OBJECT_CONTEXT_STATUSES = frozenset({"MAPPED_MEMBER", "NOT_IN_ATOMIC_RULE", "UNRESOLVED"})
PLAN_STATUSES = frozenset({"DERIVED", "NOT_APPLICABLE", "UNRESOLVED"})
CONTROL_STEP_TYPES = frozenset({"EVALUATE_CONDITION", "READ", "BOUND", "CHECK", "RETURN_NO_CONSTRAINT", "ALLOW"})
LIFECYCLE_ACTIONS = frozenset({"FOUNDATION_READ_ONLY", "EVENT_INSERT", "EVENT_UPDATE"})
DERIVED_PLAN_REASON = "由输入原文条件、比较要求和固定生命周期定义推导"


class ContractError(ValueError):
    """Raised when an LLM output violates a fixed V2 contract."""


CODE_VALUE_KINDS = frozenset({"LITERAL", "CODE_LABEL"})
CODE_SET_EXTENSION_POLICIES = frozenset({"CLOSED", "OTHER_BANK_DEFINED", "UNRESOLVED"})
CODE_SET_SCOPE_KINDS = frozenset({"GLOBAL", "CONDITIONAL"})
CHARACTER_CLASSES = frozenset({"UPPERCASE_LETTER", "LOWERCASE_LETTER", "LETTER", "DIGIT", "ALPHANUMERIC", "CHINESE_CHARACTER", "OTHER", "UNRESOLVED"})
SEGMENT_KINDS = frozenset({"CODE", "SEQUENCE", "IDENTIFIER", "DATE", "OTHER"})
REPRESENTATION_KINDS = frozenset({"NUMERIC_CODE", "ALPHA3_CODE", "ALPHA_CODE", "CHINESE_NAME", "CODE_AND_NAME", "OTHER", "UNRESOLVED"})
STANDARD_MAPPING_STATUSES = frozenset({"BOUND", "NO_CODE_TABLE", "UNRESOLVED"})
STANDARD_VALUE_SELECTION_MODES = frozenset({"DIRECT_COLUMN", "COMPOSE_COLUMNS", "SUBSTRING_COLUMN", "UNRESOLVED"})
CODE_TABLE_USAGE_BASES = frozenset({"REVIEWED_STANDARD_BINDING", "CONSTRAINT_COMPATIBLE_ATTACHMENT4"})


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name}必须是非空字符串")
    return value.strip()


def _no_few_shot(value: str, name: str) -> None:
    if any(marker in value for marker in ("少样本", "few-shot", "few shot", "示例推导")):
        raise ContractError(f"{name}不得把few-shot作为结论来源")


def _refs(value: Any, allowed: set[str], name: str = "source_refs") -> None:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ContractError(f"{name}必须是非空字符串数组")
    extra = set(value) - allowed
    if extra:
        raise ContractError(f"{name}包含任务外引用: {sorted(extra)}")


def _quote_in_description(value: Any, description: str, name: str) -> str:
    quote = _text(value, name)
    if quote not in description:
        raise ContractError(f"{name}必须是数据元说明E列原文子串")
    return quote


def _value(item_type: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ContractError("value必须是对象")
    if item_type == "NULLABLE" and value.get("nullable") not in TRI_STATES:
        raise ContractError("NULLABLE.value.nullable必须为YES/NO/UNSPECIFIED")
    if item_type in {"UNIQUE", "PRIMARY_KEY"} and value.get("value") not in TRI_STATES:
        raise ContractError("UNIQUE/PRIMARY_KEY.value必须为YES/NO/UNSPECIFIED")
    required = {
        "ENCODING_RULE": "encoding_rule_text", "CODE_DOMAIN": "code_domain_mention",
        "DEFAULT_VALUE": "default_value", "FORBIDDEN_VALUE": "forbidden_value", "VALUE_RANGE": "range_description",
        "PRIVACY_TRANSFORM": "privacy_transform_text", "OTHER": "other_text",
    }
    if item_type in required:
        _text(value.get(required[item_type]), f"{item_type}.value.{required[item_type]}")


def _constraint(candidate: dict[str, Any], allowed_items: frozenset[str], refs: set[str], *, multi: bool = False) -> None:
    item_type = candidate.get("constraint_item_type")
    if item_type not in allowed_items:
        raise ContractError(f"不允许的约束项: {item_type}")
    _value(item_type, candidate.get("value"))
    _text(candidate.get("evidence_quote"), "evidence_quote")
    _refs(candidate.get("source_refs"), refs)
    if multi:
        if candidate.get("condition_type") not in CONDITION_TYPES:
            raise ContractError("多字段condition_type非法")
        if candidate["condition_type"] != "ALWAYS":
            _text(candidate.get("condition_text"), "condition_text")
        _text(candidate.get("requirement_text"), "requirement_text")
        mentions = candidate.get("participant_mentions")
        if not isinstance(mentions, list) or len(mentions) < 2:
            raise ContractError("多字段候选至少需要两个participant_mentions")
        for mention in mentions:
            if not isinstance(mention, dict):
                raise ContractError("participant_mention必须是对象")
            _text(mention.get("field_mention"), "participant_mentions.field_mention")
            if mention.get("participant_role") not in {"SUBJECT", "CONDITION", "REFERENCE", "TARGET", "OTHER"}:
                raise ContractError("participant_role非法")


def validate_agent1(payload: dict[str, Any], task: dict[str, Any]) -> None:
    if payload.get("task_id") != task["task_id"] or payload.get("data_element_code") != task["data_element"]["code"]:
        raise ContractError("Agent1任务身份不匹配")
    if payload.get("extraction_status") not in {"EXTRACTED", "NO_CONSTRAINT", "UNRESOLVED"}:
        raise ContractError("Agent1 extraction_status非法")
    # V3 purposefully separates semantic facts, atomic local values, encoding
    # segments and external-standard mentions.  `CODE_DOMAIN` free text is not
    # a legal Agent1 output any more.
    constraints = payload.get("semantic_constraints")
    if not isinstance(constraints, list):
        raise ContractError("Agent1 semantic_constraints必须为数组")
    refs = set(task["data_element"]["source_refs"])
    description = task["data_element"]["description"]
    for candidate in constraints:
        if candidate.get("constraint_item_type") == "NULLABLE" and candidate.get("value", {}).get("nullable") == "UNSPECIFIED":
            raise ContractError("Agent1不得因原文未提及而输出NULLABLE=UNSPECIFIED")
        _constraint(candidate, DATA_ELEMENT_ITEMS - {"CODE_DOMAIN"}, refs)
        _quote_in_description(candidate.get("evidence_quote"), description, "semantic_constraints.evidence_quote")
    code_sets = payload.get("code_value_sets")
    if not isinstance(code_sets, list):
        raise ContractError("Agent1 code_value_sets必须为数组")
    set_names: set[str] = set()
    set_keys: set[tuple[str, str, str, str]] = set()
    for code_set in code_sets:
        if not isinstance(code_set, dict):
            raise ContractError("code_value_set必须为对象")
        set_name = _text(code_set.get("set_name"), "code_value_set.set_name")
        if code_set.get("extension_policy") not in CODE_SET_EXTENSION_POLICIES:
            raise ContractError("code_value_set.extension_policy非法")
        scope_kind = code_set.get("scope_kind", "GLOBAL")
        if scope_kind not in CODE_SET_SCOPE_KINDS:
            raise ContractError("code_value_set.scope_kind非法")
        condition = code_set.get("condition")
        if scope_kind == "CONDITIONAL":
            if not isinstance(condition, dict):
                raise ContractError("CONDITIONAL码值集必须提供condition")
            _text(condition.get("dimension_name"), "code_value_set.condition.dimension_name")
            _text(condition.get("dimension_value"), "code_value_set.condition.dimension_value")
        elif condition is not None:
            raise ContractError("GLOBAL码值集的condition必须为null")
        condition_name = str((condition or {}).get("dimension_name") or "")
        condition_value = str((condition or {}).get("dimension_value") or "")
        set_key = (set_name, scope_kind, condition_name, condition_value)
        if set_key in set_keys:
            raise ContractError("同一任务不得重复输出相同条件的code_value_set")
        set_keys.add(set_key)
        set_names.add(set_name)
        _quote_in_description(code_set.get("evidence_quote"), description, "code_value_set.evidence_quote")
        _refs(code_set.get("source_refs"), refs)
        values = code_set.get("values")
        if not isinstance(values, list) or not values:
            raise ContractError("code_value_set.values必须为非空数组")
        seen_values: set[tuple[str, str, str]] = set()
        for value in values:
            if not isinstance(value, dict) or value.get("value_kind") not in CODE_VALUE_KINDS:
                raise ContractError("code_value.value_kind非法")
            literal_raw = value.get("literal_value")
            code = str(value.get("value_code") or "").strip()
            label = str(value.get("value_label") or "").strip()
            if value["value_kind"] == "LITERAL":
                literal = _text(literal_raw, "code_value.literal_value")
                if code or label:
                    raise ContractError("LITERAL不得填写value_code或value_label")
            else:
                if literal_raw is not None:
                    raise ContractError("CODE_LABEL的literal_value必须为null")
                if not code or not label:
                    raise ContractError("CODE_LABEL必须同时有value_code和value_label")
                literal = ""
            key = (literal, code, label)
            if key in seen_values:
                raise ContractError("同一码值集不得重复原子码值")
            seen_values.add(key)
            _quote_in_description(value.get("evidence_quote"), description, "code_value.evidence_quote")
            _refs(value.get("source_refs"), refs)
    rules = payload.get("encoding_rules")
    if not isinstance(rules, list):
        raise ContractError("Agent1 encoding_rules必须为数组")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ContractError("encoding_rule必须为对象")
        _text(rule.get("rule_name"), "encoding_rule.rule_name")
        exact_length = rule.get("exact_length")
        if exact_length is not None and (not isinstance(exact_length, int) or exact_length < 1):
            raise ContractError("encoding_rule.exact_length必须为正整数或null")
        classes = rule.get("character_classes")
        if not isinstance(classes, list) or not classes or set(classes) - CHARACTER_CLASSES:
            raise ContractError("encoding_rule.character_classes非法")
        _quote_in_description(rule.get("evidence_quote"), description, "encoding_rule.evidence_quote")
        _refs(rule.get("source_refs"), refs)
        segments = rule.get("segments")
        if not isinstance(segments, list) or not segments:
            raise ContractError("encoding_rule.segments必须为非空数组")
        positions: list[tuple[int, int]] = []
        for segment in segments:
            if not isinstance(segment, dict) or segment.get("segment_kind") not in SEGMENT_KINDS:
                raise ContractError("encoding_rule.segment_kind非法")
            start, end = segment.get("start_pos"), segment.get("end_pos")
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                raise ContractError("编码分段起止位置非法")
            if exact_length is not None and end > exact_length:
                raise ContractError("编码分段超出exact_length")
            positions.append((start, end))
            _text(segment.get("segment_name"), "encoding_rule.segment_name")
            segment_classes = segment.get("character_classes")
            if not isinstance(segment_classes, list) or not segment_classes or set(segment_classes) - CHARACTER_CLASSES:
                raise ContractError("编码分段character_classes非法")
            references = segment.get("value_set_names")
            if not isinstance(references, list) or not all(isinstance(name, str) and name.strip() for name in references):
                raise ContractError("编码分段value_set_names必须为字符串数组")
            if set(references) - set_names:
                raise ContractError("编码分段只能引用本任务已输出的本地码值集")
            _quote_in_description(segment.get("evidence_quote"), description, "encoding_segment.evidence_quote")
            _refs(segment.get("source_refs"), refs)
        if positions != sorted(positions) or any(right[0] <= left[1] for left, right in zip(positions, positions[1:])):
            raise ContractError("编码分段必须按位置有序且不可重叠")
    standards = payload.get("standard_references")
    if not isinstance(standards, list):
        raise ContractError("Agent1 standard_references必须为数组")
    mapping_context = task.get("standard_mapping_context")
    if not isinstance(mapping_context, dict) or not isinstance(mapping_context.get("bindings"), list) or not isinstance(mapping_context.get("all_external_standards"), (list, tuple)) or not isinstance(mapping_context.get("all_code_tables"), (list, tuple)):
        raise ContractError("Agent1缺少已审阅的standard_mapping_context")
    bindings = mapping_context["bindings"]
    bound_by_standard = {item["external_standard_id"]: item for item in bindings}
    catalog_by_table = {item["code_table_id"]: item for item in mapping_context["all_code_tables"]}
    standard_status_by_id: dict[str, str] = {}
    suppressed_mentions = {
        str(item.get("standard_mention_raw", "")).strip()
        for item in task.get("hard_agent1_directives", ())
        if item.get("directive_type") == "SUPPRESS_DEPRECATED_STANDARD_ASSET"
    }
    for standard in standards:
        if not isinstance(standard, dict) or standard.get("representation_kind") not in REPRESENTATION_KINDS or standard.get("mapping_status") not in STANDARD_MAPPING_STATUSES:
            raise ContractError("standard_reference.representation_kind非法")
        _quote_in_description(standard.get("standard_mention_raw"), description, "standard_reference.standard_mention_raw")
        if standard.get("standard_mention_raw") in suppressed_mentions:
            raise ContractError("废止标准已由硬代码特殊处理，不得作为Agent1标准资产输出")
        _quote_in_description(standard.get("evidence_quote"), description, "standard_reference.evidence_quote")
        _refs(standard.get("source_refs"), refs)
        external_id = standard.get("external_standard_id")
        if standard["mapping_status"] == "UNRESOLVED":
            if external_id is not None:
                raise ContractError("UNRESOLVED标准引用不得填external_standard_id")
        else:
            if external_id not in bound_by_standard:
                raise ContractError("标准引用只能使用当前数据元已审阅绑定的external_standard_id")
            expected = "BOUND" if bound_by_standard[external_id].get("binding_status") == "BOUND" else "NO_CODE_TABLE"
            if standard["mapping_status"] != expected:
                raise ContractError("标准引用mapping_status与已审阅绑定不一致")
            standard_status_by_id[external_id] = standard["mapping_status"]
    usages = payload.get("code_table_usages")
    if not isinstance(usages, list):
        raise ContractError("Agent1 code_table_usages必须为数组")
    seen_usages: set[str] = set()
    reviewed_usage_standards: set[str] = set()
    has_constraint_compatible_usage = False
    for usage in usages:
        if not isinstance(usage, dict) or usage.get("selection_mode") not in STANDARD_VALUE_SELECTION_MODES or usage.get("usage_basis") not in CODE_TABLE_USAGE_BASES:
            raise ContractError("code_table_usage固定码值非法")
        external_id = usage.get("external_standard_id")
        code_table_id = usage.get("code_table_id")
        catalog = catalog_by_table.get(code_table_id)
        if not catalog:
            raise ContractError("码表取值声明必须引用受控全量码表目录中的表")
        if usage.get("sqlite_table_name") != catalog.get("sqlite_table_name"):
            raise ContractError("码表取值声明sqlite_table_name与目录不一致")
        if code_table_id in seen_usages:
            raise ContractError("同一码表不得重复输出取值声明")
        seen_usages.add(code_table_id)
        if usage["usage_basis"] == "REVIEWED_STANDARD_BINDING":
            binding = bound_by_standard.get(external_id)
            if not binding or binding.get("binding_status") != "BOUND" or code_table_id != binding.get("code_table_id"):
                raise ContractError("REVIEWED_STANDARD_BINDING必须使用当前数据元已审阅的BOUND绑定")
            reviewed_usage_standards.add(external_id)
        else:
            if external_id is not None:
                raise ContractError("CONSTRAINT_COMPATIBLE_ATTACHMENT4不得填external_standard_id")
            has_constraint_compatible_usage = True
        columns = usage.get("final_value_columns")
        if not isinstance(columns, list) or not all(isinstance(column, str) and column.strip() for column in columns):
            raise ContractError("码表取值声明final_value_columns必须为字符串数组")
        if set(columns) - set(catalog["value_columns"]):
            raise ContractError("码表取值声明包含目录外列名")
        mode = usage["selection_mode"]
        template = usage.get("final_value_template")
        if mode == "DIRECT_COLUMN":
            if len(columns) != 1 or template is not None:
                raise ContractError("DIRECT_COLUMN必须只选一个列且template为null")
        elif mode == "COMPOSE_COLUMNS":
            if len(columns) < 2 or not isinstance(template, str) or not template.strip() or any(f"{{{column}}}" not in template for column in columns):
                raise ContractError("COMPOSE_COLUMNS必须至少两个列且template引用每一列")
        elif mode == "SUBSTRING_COLUMN":
            start, end = usage.get("substring_start"), usage.get("substring_end")
            if len(columns) != 1 or template is not None or not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                raise ContractError("SUBSTRING_COLUMN必须选择一列、template为null且提供合法substring_start/end")
        elif columns or template is not None:
            raise ContractError("UNRESOLVED码表取值声明不得选择列或模板")
        if mode != "SUBSTRING_COLUMN" and (usage.get("substring_start") is not None or usage.get("substring_end") is not None):
            raise ContractError("非SUBSTRING_COLUMN不得填写substring_start/end")
        _quote_in_description(usage.get("evidence_quote"), description, "code_table_usage.evidence_quote")
        _refs(usage.get("source_refs"), refs)
        _text(usage.get("rationale"), "code_table_usage.rationale")
    bound_standard_ids = {external_id for external_id, status in standard_status_by_id.items() if status == "BOUND"}
    if bound_standard_ids - reviewed_usage_standards:
        raise ContractError("每条已绑定且被引用的标准都必须有对应的REVIEWED_STANDARD_BINDING码表声明")
    standard_review_required = any(item["mapping_status"] != "BOUND" for item in standards) or any(item["selection_mode"] == "UNRESOLVED" for item in usages) or has_constraint_compatible_usage
    questions = payload.get("manual_review_questions")
    if not isinstance(questions, list):
        raise ContractError("Agent1 manual_review_questions必须为数组")
    for question in questions:
        if not isinstance(question, dict):
            raise ContractError("manual_review_question必须为对象")
        _text(question.get("question"), "manual_review_question.question")
        _quote_in_description(question.get("evidence_quote"), description, "manual_review_question.evidence_quote")
        _refs(question.get("source_refs"), refs)
    if standard_review_required and not questions:
        raise ContractError("标准映射或码表列选择未确定时必须输出人工审核问题")
    if payload["extraction_status"] == "NO_CONSTRAINT" and (any((constraints, code_sets, rules, standards, usages)) or questions):
        raise ContractError("NO_CONSTRAINT时五个输出数组和人工问题必须为空")
    if payload["extraction_status"] == "EXTRACTED" and not any((constraints, code_sets, rules, standards, usages)):
        raise ContractError("EXTRACTED时至少应输出一项已确定事实")
    if payload["extraction_status"] == "UNRESOLVED" and not questions:
        raise ContractError("UNRESOLVED时必须输出明确的人工审核问题")


def validate_agent2(payload: dict[str, Any], task: dict[str, Any]) -> None:
    if payload.get("task_id") != task["task_id"] or payload.get("field_id") != task["field"]["field_id"]:
        raise ContractError("Agent2任务身份不匹配")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ContractError("Agent2 candidates必须为数组")
    refs = {task["field"]["source_ref"]} | {item["source_ref"] for item in task["validation_rules"]}
    for candidate in candidates:
        classification = candidate.get("classification")
        if classification == "SINGLE_FIELD":
            _constraint(candidate, SINGLE_ITEMS, refs)
        elif classification == "MULTI_FIELD":
            _constraint(candidate, MULTI_ITEMS, refs, multi=True)
        elif classification in {"OTHER", "UNRESOLVED"}:
            _text(candidate.get("reason"), "OTHER/UNRESOLVED.reason")
            _text(candidate.get("evidence_quote"), "OTHER/UNRESOLVED.evidence_quote")
            _refs(candidate.get("source_refs"), refs)
        else:
            raise ContractError("Agent2 classification非法")


def validate_agent3(payload: dict[str, Any], task: dict[str, Any]) -> None:
    if payload.get("task_id") != task["task_id"] or payload.get("atomic_multifield_rule_id") != task["atomic_multifield_rule_id"]:
        raise ContractError("Agent3任务身份不匹配")
    classification = payload.get("ods_classification")
    if classification not in ODS_CLASSES:
        raise ContractError("ods_classification非法")
    refs = set(task["multifield_rule"]["source_refs"])
    for item in task["mapped_field_group"]:
        refs.add(item["field_name_source_ref"])
        if item.get("remarks_source_ref"):
            refs.add(item["remarks_source_ref"])
    _refs(payload.get("source_refs"), refs)
    _no_few_shot(_text(payload.get("evidence_quote"), "Agent3.evidence_quote"), "Agent3.evidence_quote")
    _no_few_shot(_text(payload.get("rationale"), "Agent3.rationale"), "Agent3.rationale")
    members = payload.get("ods_members")
    object_context = payload.get("object_context")
    if not isinstance(object_context, dict) or object_context.get("status") not in OBJECT_CONTEXT_STATUSES:
        raise ContractError("object_context.status非法")
    if not isinstance(object_context.get("field_ids"), list):
        raise ContractError("object_context.field_ids必须为数组")
    allowed_field_ids = {item["field_id"] for item in task["mapped_field_group"]}
    if set(object_context["field_ids"]) - allowed_field_ids:
        raise ContractError("object_context.field_ids必须引用已映射字段")
    _no_few_shot(_text(object_context.get("rationale"), "object_context.rationale"), "object_context.rationale")
    plan = payload.get("constraint_control_plan")
    if not isinstance(plan, dict) or plan.get("plan_status") not in PLAN_STATUSES:
        raise ContractError("constraint_control_plan.plan_status非法")
    if plan["plan_status"] == "DERIVED":
        if plan.get("reason") != DERIVED_PLAN_REASON:
            raise ContractError("DERIVED控制计划reason必须使用固定文本")
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ContractError("DERIVED控制计划必须含steps")
        for expected, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("step_no") != expected or step.get("step_type") not in CONTROL_STEP_TYPES:
                raise ContractError("控制步骤序号或类型非法")
            target_ids = step.get("target_field_ids")
            if not isinstance(target_ids, list) or set(target_ids) - allowed_field_ids:
                raise ContractError("控制步骤字段必须引用已映射字段")
            gate = step.get("gate")
            if not isinstance(gate, dict) or gate.get("mode") not in {"ALWAYS", "WHEN_TRUE", "WHEN_FALSE"}:
                raise ContractError("控制步骤gate.mode非法")
            if gate["mode"] == "ALWAYS":
                if gate.get("condition_step_no") is not None:
                    raise ContractError("ALWAYS控制步骤不得引用条件步骤")
            elif not isinstance(gate.get("condition_step_no"), int) or not 1 <= gate["condition_step_no"] < expected:
                raise ContractError("条件分支必须引用此前的条件步骤")
            _no_few_shot(_text(step.get("instruction"), "控制步骤instruction"), "控制步骤instruction")
    else:
        if plan.get("steps") not in ([], None):
            raise ContractError("非DERIVED控制计划不得输出steps")
        _text(plan.get("reason"), "非DERIVED控制计划reason")
    if classification in {"NOT_ODS", "UNRESOLVED"}:
        if members not in ([], None):
            raise ContractError("NOT_ODS/UNRESOLVED不得输出ODS成员")
        _text(payload.get("unresolved_reason"), "NOT_ODS/UNRESOLVED.unresolved_reason")
        return
    if not isinstance(members, list) or not members:
        raise ContractError("正向ODS分类必须输出成员")
    for member in members:
        if not isinstance(member, dict) or member.get("field_id") not in allowed_field_ids or member.get("ods_role") not in ODS_ROLES:
            raise ContractError("ODS成员必须引用输入字段且使用固定角色")
        if member.get("lifecycle_action") not in LIFECYCLE_ACTIONS:
            raise ContractError("ODS成员lifecycle_action非法")
        expected_action = {"OBJECT": "FOUNDATION_READ_ONLY", "DETAIL": "EVENT_INSERT", "STATE": "EVENT_UPDATE"}[member["ods_role"]]
        if member["lifecycle_action"] != expected_action:
            raise ContractError("ODS角色与生命周期动作不一致")
        _no_few_shot(_text(member.get("role_evidence"), "ods_members.role_evidence"), "ods_members.role_evidence")
    object_member_ids = {member["field_id"] for member in members if member["ods_role"] == "OBJECT"}
    if object_context["status"] == "MAPPED_MEMBER" and (not object_context["field_ids"] or set(object_context["field_ids"]) != object_member_ids):
        raise ContractError("MAPPED_MEMBER对象上下文必须等于ODS对象成员")
    if object_context["status"] == "NOT_IN_ATOMIC_RULE" and object_context["field_ids"]:
        raise ContractError("NOT_IN_ATOMIC_RULE不得带对象字段")
