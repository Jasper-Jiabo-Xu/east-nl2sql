"""Desensitized EAS-25 probe: real 150 → 160 → 170/180 contract closure."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.artifacts import content_hash
from east_v5.governance import ContractError

from .precheck import PrecheckAgent, consume_170_180_stub

TIME = "2026-08-16T00:00:00+00:00"


def _wrap(kind: str, identity: str, payload: dict[str, Any], producer: str, *, attempt: int = 1, mode: str = "question_sql", parents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parent_refs = list(parents or [])
    envelope = {
        "artifact_id": identity, "artifact_type": kind, "run_id": "eas25-probe", "qa_id": "QA-EAS25",
        "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer,
        "parent_artifact_refs": parent_refs, "input_hashes": [ref["content_hash"] for ref in parent_refs],
        "status": "candidate", "mode": mode, "created_at": TIME, "trace_id": "eas25-probe", "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _spec() -> dict[str, Any]:
    payload = {
        "query_spec_id": "qspec-025", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
        "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64},
        "query_goal": "脱敏风险筛查", "must_preserve_fact_refs": ["fact-1"], "main_object_and_grain": {"main_object": "机构", "grain": "记录"},
        "query_entry": {"entry_table": "T1", "entry_conditions": []}, "related_objects_and_path": [], "filters_and_evidence": [],
        "return_fields": [{"field_id": "F1", "display_name": "字段1", "source_table": "T1"}],
        "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["风险"], "unanswerable": []},
        "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [],
        "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}},
        "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 5}, "query_specification_package_schema_version": "query-specification-v1",
    }
    return _wrap("query_specification_package", "qspec025", payload, "140")


def _candidate(sql: str, suffix: str = "") -> dict[str, Any]:
    question = f"脱敏机构风险筛查{suffix}"
    return {
        "sql_gold": sql, "clear_question": question,
        "sql_explanation": {"select": f"字段{suffix}", "from_join": f"T1{suffix}", "where": f"固定{suffix}", "aggregation": f"无{suffix}", "sort": f"固定{suffix}", "business_meaning": f"筛查{suffix}"},
        "business_event_candidates": [{"event_name": f"筛查{suffix}", "objective": f"筛查{suffix}", "objects": ["机构"], "state_changes": [suffix] if suffix else []}],
        "specification_mapping": [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS],
    }


def _rehash(package: dict[str, Any]) -> dict[str, Any]:
    package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])
    return package


def _forge_invalid(package: dict[str, Any], sql: str) -> dict[str, Any]:
    """Schema-valid candidate whose SQL violates a deterministic precheck rule."""
    forged = copy.deepcopy(package)
    forged["payload"]["sql_gold"] = sql
    for item in forged["payload"]["specification_mapping"]:
        item["sql_fragment"] = sql
    return _rehash(forged)


def _is_rejected(action: Any) -> bool:
    try:
        action()
    except ContractError:
        return True
    return False


def run_sanitized_probe(root: Path) -> dict[str, Any]:
    builder_150, agent_160, query = PendingPrecheckBuilder(root), PrecheckAgent(root), _spec()

    valid = builder_150.build_pending_precheck(query, run_id="eas25-probe", qa_id="QA-EAS25", created_at=TIME, **_candidate("SELECT T1.F1 FROM T1"))
    result = agent_160.precheck(valid, query, checked_at=TIME)
    dual = agent_160.build_dual_review(valid, query, result, created_at=TIME)
    dual_again = agent_160.build_dual_review(valid, query, result, created_at=TIME)
    consumed = consume_170_180_stub(root, dual)

    fail_matrix: dict[str, Any] = {}
    for label, sql in {
        "write": "DELETE FROM T1",
        "select_star": "SELECT * FROM T1",
        "out_of_scope": "SELECT T1.F9 FROM T1",
        "unqualified": "SELECT F9 FROM T1",
        "dynamic_time": "SELECT CURRENT_DATE",
    }.items():
        forged = _forge_invalid(valid, sql)
        res = agent_160.precheck(forged, query, checked_at=TIME)
        fail_matrix[label] = {
            "decision": res["decision"],
            "rule_ids": sorted({item["failed_rule_ids"][0] for item in res["failed_items"]}),
        }
        feedback = agent_160.build_feedback(forged, res, created_at=TIME)
        agent_160.validate_feedback(feedback)
        fail_matrix[label]["feedback_attempt"] = feedback["envelope"]["attempt_no"]
        fail_matrix[label]["feedback_ref"] = feedback["payload"]["candidate_ref"] == {"artifact_id": forged["envelope"]["artifact_id"], "version": forged["envelope"]["version"], "content_hash": forged["envelope"]["content_hash"]}

    # Retry round-trip: 160 rejects attempt 1, 150 repairs to a valid attempt 2, 160 passes.
    forged_attempt1 = _forge_invalid(valid, "SELECT T1.F9 FROM T1")
    fail1 = agent_160.precheck(forged_attempt1, query, checked_at=TIME)
    feedback1 = agent_160.build_feedback(forged_attempt1, fail1, created_at=TIME)
    attempt2 = builder_150.handle_precheck_feedback(query, feedback1, forged_attempt1, run_id="eas25-probe", qa_id="QA-EAS25", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-修复"))
    result2 = agent_160.precheck(attempt2, query, checked_at=TIME)
    dual2 = agent_160.build_dual_review(attempt2, query, result2, created_at=TIME)

    return {
        "summary": {
            "valid_pass": result["decision"] == "pass",
            "dual_review_valid": agent_160.validate_dual_review(dual) is None,
            "dual_review_identical": dual["envelope"]["content_hash"] == dual_again["envelope"]["content_hash"] and dual["payload"]["package_hash"] == dual_again["payload"]["package_hash"],
            "stub_170_180_consumed": consumed["package_hash"] == dual["payload"]["package_hash"],
            "review_round": dual["payload"]["review_round"] == 1,
            "fail_matrix": fail_matrix,
            "retry_round_trip": {
                "attempt1_failed": fail1["decision"] == "fail",
                "attempt2_passed": result2["decision"] == "pass",
                "attempt2_review_round": dual2["payload"]["review_round"] == 2,
            },
        }
    }


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe(Path(__file__).resolve().parents[4])["summary"], ensure_ascii=False, sort_keys=True))
