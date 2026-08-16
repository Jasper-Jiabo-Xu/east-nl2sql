"""A sanitized, independently replayable Agent-140 runtime probe.

It deliberately uses only committed desensitized constants.  The default CLI
prints a non-sensitive summary; ``--emit-transport`` prints the validated
transport package locally so an Agent task can prove a real package was made
without copying the payload into an Issue comment.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from east_v5.agents.east_140.extractor import QuerySpecBuilder
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

FIXED_TIME = "2026-08-16T00:00:00+00:00"


def _wrap(artifact_type: str, artifact_id: str, payload: dict[str, Any], *, producer: str, parents: list[dict[str, Any]] | None = None, attempt: int = 1) -> dict[str, Any]:
    parents = list(parents or [])
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "eas23-sanitized-run", "qa_id": "QA-EAS23", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": "candidate", "mode": "question_sql", "created_at": FIXED_TIME, "trace_id": "eas23-sanitized-trace", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _penalty() -> dict[str, Any]:
    payload = {
        "source_facts": [
            {
                "penalty_fact_id": "fact-001",
                "fact_type": "behavior",
                "structured_fact": {"subject": "脱敏机构", "predicate": "行为", "object": "脱敏事实", "qualifier": None, "value": None},
                "original_text": "脱敏事实",
                "source_span_refs": [],
                "must_preserve_in_question": "yes",
            }
        ],
        "external_evidence": {
            "penalty_intent": {"description": "脱敏意图", "evidence_refs": []},
            "regulatory_rules": [],
            "business_meaning": {"description": "脱敏业务含义", "evidence_refs": []},
            "penalty_background": {"description": "脱敏背景", "evidence_refs": []},
        },
        "evidence_conflicts": [],
        "uncertainties": [],
        "penalty_fact_package_schema_version": "penalty-fact-v1",
    }
    return _wrap("penalty_fact_package", "eas23-penalty", payload, producer="120")


def _observable() -> dict[str, Any]:
    payload = {
        "observable_facts": [
            {
                "observable_fact_id": "observable-fact-001",
                "penalty_fact_refs": ["fact-001"],
                "topic": "监管处罚风险筛查",
                "main_object": "脱敏机构",
                "query_grain": "一条EAST业务记录或聚合事件",
                "entry_table": "EAST_D001",
                "related_tables_fields": [{"table_id": "EAST_D002", "field_id": "F002", "purpose": "关联字段"}],
                "within_table_relations": [],
                "cross_table_relations": [],
                "time_amount_conditions": ["仅使用冻结资产中可表达的时间或金额条件"],
                "observable_proxy": "以 EAST_D001.F001 筛查处罚事实 fact-001",
                "observability_type": "direct",
                "unobservable_parts": [],
                "risk_screening_boundary": "仅用于风险筛查，不直接认定监管违法或替代人工结论。",
                "mapping_matrix": [
                    {
                        "penalty_fact_id": "fact-001",
                        "proxy_expression": "以 EAST_D001.F001 筛查处罚事实 fact-001",
                        "table_field_path": "EAST_D001.F001",
                        "asset_evidence_ref": "constraint_asset:CA-V0.3.0#record-0",
                    }
                ],
                "constraint_asset_refs": ["constraint_asset:CA-V0.3.0#record-0"],
            }
        ],
        "coverage_status": "complete",
        "asset_version": "CA-V0.3.0",
        "unresolved_items": [],
    }
    return _wrap("east_observable_fact_package", "eas23-observable", payload, producer="130")


def _review(previous: dict[str, Any], kind: str) -> dict[str, Any]:
    reviewer = "170" if kind == "deepseek_review_result" else "180"
    payload = {
        "reviewed_package_ref": artifact_ref(previous["envelope"]),
        "semantic_review_report": {
            "reviewer_id": reviewer,
            "decision": "no",
            "error_types": ["QUERY_SPEC_ERROR"],
            "error_details": [{"reason": "脱敏回退验证"}],
            "evidence_refs": [],
            "route_suggestion": "140",
        },
    }
    return _wrap(kind, f"eas23-review-{reviewer}", payload, producer=reviewer)


def _llm_extracted_fields() -> dict[str, Any]:
    """Desensitized LLM-extracted fields for the probe."""
    return {
        "query_goal": "脱敏风险筛查",
        "main_object_and_grain": {"main_object": "脱敏机构", "grain": "一条EAST业务记录"},
        "query_entry": {"entry_table": "EAST_D001", "entry_conditions": [{"field_id": "F001", "operator": "=", "value": "脱敏值"}]},
        "related_objects_and_path": [{"object_name": "关联表", "table_id": "EAST_D002", "relation_type": "LEFT JOIN", "join_fields": [{"from_field": "EAST_D001.F002", "to_field": "EAST_D002.F002"}]}],
        "filters_and_evidence": [{"field_id": "F001", "operator": "=", "value": "脱敏值", "evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}],
        "return_fields": [{"field_id": "F001", "display_name": "脱敏字段", "source_table": "EAST_D001"}],
        "aggregation_dedup_sort_time": {"group_by_fields": ["EAST_D001.F001"], "distinct_required": False, "order_by": [{"field_id": "F001", "direction": "ASC"}], "time_window": {"field_id": "F003", "window_type": "fixed"}},
        "observability_boundary": {"answerable": ["脱敏风险筛查"], "unanswerable": ["具体处罚金额"]},
        "expected_result_shape": {"row_grain": "一条EAST业务记录", "column_set": ["F001", "F002"], "aggregation_shape": "group_by"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001", "F002", "F003"]}, {"table_id": "EAST_D002", "allowed_fields": ["F002"]}]},
        "minimum_positive_count": 1,
        "minimum_negative_count": 1,
        "condition_coverage": [{"predicate": "F001 = 脱敏值", "positive_types": ["合规"], "negative_types": ["违规"]}],
        "code_value_coverage": [{"field_id": "F001", "target_code_values": ["A", "B"]}],
        "expected_row_group_count": {"minimum": 1, "target": 10, "tolerance_range": {"low": 1, "high": 100}},
        "join_expansion_limit": {"max_multiplier": 2.0, "max_result_rows": 1000},
    }


def _consume_150_stub(package: dict[str, Any]) -> str:
    """Simulate 150 consuming the query specification."""
    spec = package["payload"]
    if not spec["query_goal"] or not spec["must_preserve_fact_refs"]:
        raise ContractError("150_CONSUMPTION_REJECTED")
    return spec["query_spec_id"]


def _consume_170_stub(package: dict[str, Any]) -> str:
    """Simulate 170 consuming the query specification."""
    spec = package["payload"]
    if not spec["sql_schema_scope"]["allowed_tables"]:
        raise ContractError("170_CONSUMPTION_REJECTED")
    return spec["query_specification_package_schema_version"]


def run_sanitized_probe(repo_root: Path) -> dict[str, Any]:
    builder = QuerySpecBuilder(repo_root)

    penalty = _penalty()
    observable = _observable()

    # Task 1: build initial query spec
    fields = _llm_extracted_fields()
    first = builder.build_query_spec(
        penalty, observable,
        run_id="eas23-sanitized-run", qa_id="QA-EAS23",
        created_at=FIXED_TIME,
        **fields,
    )
    builder.validate_query_spec(first)

    # Task 2: review feedback from 170 (attempt 2)
    review_170 = _review(first, "deepseek_review_result")
    second = builder.handle_review_feedback(
        penalty, observable, review_170, first,
        run_id="eas23-sanitized-run", qa_id="QA-EAS23",
        attempt_no=2,
        created_at=FIXED_TIME,
        **fields,
    )
    builder.validate_query_spec(second)

    # Task 2: review feedback from 180 (attempt 3 → blocked_manual)
    review_180 = _review(second, "glm_review_result")
    blocked = builder.handle_review_feedback(
        penalty, observable, review_180, second,
        run_id="eas23-sanitized-run", qa_id="QA-EAS23",
        attempt_no=3,
        created_at=FIXED_TIME,
        **fields,
    )
    builder.validate_query_spec(blocked)

    # Content hash drift detection
    corrupted = copy.deepcopy(penalty)
    corrupted["payload"]["source_facts"][0]["original_text"] = "篡改"
    bad_hash_rejection = False
    try:
        builder.validate_penalty(corrupted)
    except ContractError as exc:
        bad_hash_rejection = str(exc) == "CONTENT_HASH_DRIFT"

    if not bad_hash_rejection:
        raise ContractError("SANITIZED_PROBE_BAD_HASH_NOT_REJECTED")

    # Downstream consumption stubs
    consumer_150 = _consume_150_stub(first)
    consumer_170 = _consume_170_stub(first)

    # Verify hard-code validators reject invalid payloads
    # Test: MUST_PRESERVE_FACTS_NOT_COVERED
    missing_facts_test = False
    try:
        builder._validate_must_preserve_facts(
            {"source_facts": [{"penalty_fact_id": "fact-002", "must_preserve_in_question": "yes"}]},
            ["fact-001"],  # missing fact-002
        )
    except ContractError as exc:
        missing_facts_test = str(exc) == "MUST_PRESERVE_FACTS_NOT_COVERED"

    # Test: INVALID_COUNT
    invalid_count_test = False
    try:
        builder._validate_positive_counts(0, 1)
    except ContractError as exc:
        invalid_count_test = str(exc) == "INVALID_COUNT"

    # Test: JOIN_EXPANSION_EXCEEDED
    join_limit_test = False
    try:
        builder._validate_join_limit({"max_multiplier": -1, "max_result_rows": 100})
    except ContractError as exc:
        join_limit_test = str(exc) == "JOIN_EXPANSION_EXCEEDED"

    # Test: ROW_GROUP_RANGE_INVALID
    row_group_test = False
    try:
        builder._validate_row_group_count({"minimum": 0, "target": 5, "tolerance_range": {"low": 10, "high": 5}})
    except ContractError as exc:
        row_group_test = str(exc) == "ROW_GROUP_RANGE_INVALID"

    # Test: SQL_SCOPE_TABLE_NOT_FOUND
    sql_scope_test = False
    try:
        builder._validate_sql_scope(
            {"allowed_tables": [{"table_id": "NONEXISTENT_TABLE", "allowed_fields": ["F1"]}]},
            {"observable_facts": [{"entry_table": "EAST_D001", "related_tables_fields": []}]},
        )
    except ContractError as exc:
        sql_scope_test = str(exc) == "SQL_SCOPE_TABLE_NOT_FOUND"

    # Test: VERSION_OVERWRITE_ATTEMPTED
    version_test = False
    try:
        builder._validate_version_immutability(1, {"artifact_id": "x", "version": 1, "content_hash": "0" * 64})
    except ContractError as exc:
        version_test = str(exc) == "VERSION_OVERWRITE_ATTEMPTED"

    review_170_executed = (
        second["envelope"]["attempt_no"] == 2
        and second["envelope"]["version"] == 2
        and second["envelope"]["status"] == "candidate"
    )
    review_180_blocked = (
        blocked["envelope"]["attempt_no"] == 3
        and blocked["envelope"]["status"] == "blocked_manual"
    )

    return {
        "transport": blocked,
        "summary": {
            "artifact_ref": artifact_ref(blocked["envelope"]),
            "content_hash": blocked["envelope"]["content_hash"],
            "bad_hash_rejected": bad_hash_rejection,
            "review_170_executed": review_170_executed,
            "review_180_blocked": review_180_blocked,
            "stub_150_consumed": consumer_150 == "qspec-001",
            "stub_170_consumed": consumer_170 == "query-specification-v1",
            "validator_must_preserve_rejects": missing_facts_test,
            "validator_invalid_count_rejects": invalid_count_test,
            "validator_join_limit_rejects": join_limit_test,
            "validator_row_group_rejects": row_group_test,
            "validator_sql_scope_rejects": sql_scope_test,
            "validator_version_immutability_rejects": version_test,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-transport", action="store_true")
    args = parser.parse_args()
    result = run_sanitized_probe(Path(__file__).resolve().parents[4])
    print(json.dumps(result["transport"] if args.emit_transport else result["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
