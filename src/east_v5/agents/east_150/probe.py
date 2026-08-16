"""A sanitized, independently replayable Agent-150 runtime probe.

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

from east_v5.agents.east_150.extractor import PendingPrecheckBuilder
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

FIXED_TIME = "2026-08-16T00:00:00+00:00"


def _wrap(artifact_type: str, artifact_id: str, payload: dict[str, Any], *, producer: str, parents: list[dict[str, Any]] | None = None, attempt: int = 1, status: str = "candidate") -> dict[str, Any]:
    parents = list(parents or [])
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "eas24-sanitized-run", "qa_id": "QA-EAS24", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": status, "mode": "question_sql", "created_at": FIXED_TIME, "trace_id": "eas24-sanitized-trace", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _query_spec() -> dict[str, Any]:
    """Desensitized QUERY-SPECIFICATION-PACKAGE fixture (140 output)."""
    payload = {
        "query_spec_id": "qspec-042",
        "penalty_fact_package_ref": {"artifact_id": "eas24-penalty", "version": 1, "content_hash": "0" * 64},
        "observable_fact_package_ref": {"artifact_id": "eas24-observable", "version": 1, "content_hash": "0" * 64},
        "query_goal": "脱敏风险筛查",
        "must_preserve_fact_refs": ["fact-001"],
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
        "query_specification_package_schema_version": "query-specification-v1",
    }
    return _wrap("query_specification_package", "eas24-qspec", payload, producer="140")


def _precheck_feedback(previous: dict[str, Any], *, attempt_no: int = 1, decision: str = "fail") -> dict[str, Any]:
    """Simulate 160 producing PRECHECK-FAILED-FEEDBACK."""
    payload = {
        "schema_version": "v5.precheck-failed-feedback/v1",
        "pending_precheck_package_ref": artifact_ref(previous["envelope"]),
        "decision": decision,
        "failed_checks": [
            {
                "check_id": "chk-scope",
                "check_type": "scope",
                "error_code": "FIELD_NOT_IN_SCOPE",
                "error_details": "脱敏预检失败：字段不在范围内",
                "offending_segment": "EAST_D001.F999",
            }
        ],
        "attempt_no": attempt_no,
        "retry_eligible": decision == "fail" and attempt_no < 3,
    }
    return _wrap("precheck_failed_feedback", f"eas24-feedback-{attempt_no}", payload, producer="160", attempt=attempt_no)


def _llm_candidate_sql() -> dict[str, Any]:
    """Desensitized LLM-extracted SQL for the probe."""
    return {
        "candidate_sql": "SELECT F001, F002 FROM EAST_D001 WHERE F001 = :param1",
        "sql_parameters": [
            {"param_name": "param1", "param_type": "string", "param_value": "脱敏值"},
        ],
    }


def _consume_160_stub(package: dict[str, Any]) -> str:
    """Simulate 160 consuming the pending-precheck package."""
    ppre = package["payload"]
    if not ppre["candidate_sql"] or not ppre["precheck_expectations"]["expected_checks"]:
        raise ContractError("160_CONSUMPTION_REJECTED")
    return ppre["pending_precheck_id"]


def run_sanitized_probe(repo_root: Path) -> dict[str, Any]:
    builder = PendingPrecheckBuilder(repo_root)

    query_spec = _query_spec()

    # Task 1: build initial pending-precheck package
    sql_fields = _llm_candidate_sql()
    first = builder.build_pending_precheck(
        query_spec,
        run_id="eas24-sanitized-run", qa_id="QA-EAS24",
        created_at=FIXED_TIME,
        **sql_fields,
    )
    builder.validate_pending_precheck(first)

    # Task 2: precheck feedback from 160 (attempt 2)
    feedback_1 = _precheck_feedback(first, attempt_no=1)
    revised_sql = _llm_candidate_sql()
    second = builder.handle_precheck_feedback(
        query_spec, feedback_1, first,
        run_id="eas24-sanitized-run", qa_id="QA-EAS24",
        attempt_no=2,
        created_at=FIXED_TIME,
        **revised_sql,
    )
    builder.validate_pending_precheck(second)

    # Task 2: valid third reconstruction stays a candidate
    feedback_2 = _precheck_feedback(second, attempt_no=2)
    revised_sql_2 = _llm_candidate_sql()
    third = builder.handle_precheck_feedback(
        query_spec, feedback_2, second,
        run_id="eas24-sanitized-run", qa_id="QA-EAS24",
        attempt_no=3,
        created_at=FIXED_TIME,
        **revised_sql_2,
    )
    builder.validate_pending_precheck(third)

    # Task 2: invalid third reconstruction becomes blocked_manual
    feedback_2_again = _precheck_feedback(second, attempt_no=2)
    invalid_sql = {"candidate_sql": "", "sql_parameters": []}
    blocked = builder.handle_precheck_feedback(
        query_spec, feedback_2_again, second,
        run_id="eas24-sanitized-run", qa_id="QA-EAS24",
        attempt_no=3,
        created_at=FIXED_TIME,
        **invalid_sql,
    )
    builder.validate_pending_precheck(blocked)

    # Content hash drift detection
    corrupted = copy.deepcopy(query_spec)
    corrupted["payload"]["query_goal"] = "篡改"
    bad_hash_rejection = False
    try:
        builder.validate_query_spec(corrupted)
    except ContractError as exc:
        bad_hash_rejection = "SCHEMA_VALIDATION_FAILED" in str(exc) or "CONTENT_HASH_DRIFT" in str(exc)

    # Downstream consumption stub
    consumer_160 = _consume_160_stub(first)

    # Verify hard-code validators reject invalid payloads
    # Test: CANDIDATE_SQL_EMPTY
    empty_sql_test = False
    try:
        builder._validate_sql_nonempty("")
    except ContractError as exc:
        empty_sql_test = str(exc) == "CANDIDATE_SQL_EMPTY"

    # Test: ENTRY_TABLE_NOT_IN_SCOPE
    entry_scope_test = False
    try:
        builder._validate_entry_table_in_scope(
            "NONEXISTENT_TABLE",
            {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001"]}]},
        )
    except ContractError as exc:
        entry_scope_test = str(exc) == "ENTRY_TABLE_NOT_IN_SCOPE"

    # Test: ALLOWED_TABLES_INCONSISTENT
    tables_consistency_test = False
    try:
        builder._validate_allowed_tables_consistency(
            ["EAST_D999"],
            {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001"]}]},
        )
    except ContractError as exc:
        tables_consistency_test = str(exc) == "ALLOWED_TABLES_INCONSISTENT"

    # Test: VERSION_OVERWRITE_ATTEMPTED
    version_test = False
    try:
        builder._validate_version_immutability(1, {"artifact_id": "x", "version": 1, "content_hash": "0" * 64})
    except ContractError as exc:
        version_test = str(exc) == "VERSION_OVERWRITE_ATTEMPTED"

    feedback_1_executed = (
        second["envelope"]["attempt_no"] == 2
        and second["envelope"]["version"] == 2
        and second["envelope"]["status"] == "candidate"
    )
    feedback_2_valid_candidate = (
        third["envelope"]["attempt_no"] == 3
        and third["envelope"]["status"] == "candidate"
        and third["payload"]["pending_precheck_id"] == first["payload"]["pending_precheck_id"]
    )
    feedback_2_invalid_blocked = blocked["envelope"]["status"] == "blocked_manual"

    return {
        "transport": third,
        "summary": {
            "artifact_ref": artifact_ref(third["envelope"]),
            "content_hash": third["envelope"]["content_hash"],
            "bad_hash_rejected": bad_hash_rejection,
            "feedback_1_executed": feedback_1_executed,
            "feedback_2_valid_candidate": feedback_2_valid_candidate,
            "feedback_2_invalid_blocked": feedback_2_invalid_blocked,
            "stub_160_consumed": consumer_160 == first["payload"]["pending_precheck_id"],
            "validator_empty_sql_rejects": empty_sql_test,
            "validator_entry_scope_rejects": entry_scope_test,
            "validator_tables_consistency_rejects": tables_consistency_test,
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
