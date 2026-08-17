"""Desensitized EAS-27 probe: 150 → 160 → 180 → 110 contract closure."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.agents.east_180.reviewer import ERROR_ROUTE, GLMReviewerAgent, REVIEWER_ID, consume_110_stub
from east_v5.artifacts import content_hash
from east_v5.governance import ContractError

# 160 source lives under the numeric directory; use importlib like the 160 tests do.
_mod_160 = importlib.import_module("east_v5.agents.160.precheck")
PrecheckAgent = _mod_160.PrecheckAgent

TIME = "2026-08-17T00:00:00+00:00"


def _wrap(kind: str, identity: str, payload: dict[str, Any], producer: str, *,
          attempt: int = 1, mode: str = "question_sql",
          parents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parent_refs = list(parents or [])
    envelope = {
        "artifact_id": identity, "artifact_type": kind, "run_id": "eas27-probe",
        "qa_id": "QA-EAS27", "version": 1, "schema_version": "COMMON-ENVELOPE/v1",
        "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt,
        "producer_id": producer, "parent_artifact_refs": parent_refs,
        "input_hashes": [ref["content_hash"] for ref in parent_refs],
        "status": "candidate", "mode": mode, "created_at": TIME,
        "trace_id": "eas27-probe", "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _spec() -> dict[str, Any]:
    payload = {
        "query_spec_id": "qspec-027",
        "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
        "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64},
        "query_goal": "脱敏风险筛查", "must_preserve_fact_refs": ["fact-1"],
        "main_object_and_grain": {"main_object": "机构", "grain": "记录"},
        "query_entry": {"entry_table": "T1", "entry_conditions": []},
        "related_objects_and_path": [], "filters_and_evidence": [],
        "return_fields": [{"field_id": "F1", "display_name": "字段1", "source_table": "T1"}],
        "aggregation_dedup_sort_time": {"group_by_fields": []},
        "observability_boundary": {"answerable": ["风险"], "unanswerable": []},
        "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1,
        "condition_coverage": [], "code_value_coverage": [],
        "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}},
        "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 5},
        "query_specification_package_schema_version": "query-specification-v1",
    }
    return _wrap("query_specification_package", "qspec027", payload, "140")


def _candidate(sql: str, suffix: str = "") -> dict[str, Any]:
    question = f"脱敏机构风险筛查{suffix}"
    return {
        "sql_gold": sql, "clear_question": question,
        "sql_explanation": {
            "select": f"字段{suffix}", "from_join": f"T1{suffix}",
            "where": f"固定{suffix}", "aggregation": f"无{suffix}",
            "sort": f"固定{suffix}", "business_meaning": f"筛查{suffix}",
        },
        "business_event_candidates": [{
            "event_name": f"筛查{suffix}", "objective": f"筛查{suffix}",
            "objects": ["机构"], "state_changes": [suffix] if suffix else [],
        }],
        "specification_mapping": [
            {"spec_item": item, "question_fragment": question, "sql_fragment": sql}
            for item in MAPPED_SPEC_ITEMS
        ],
    }


class _StaticGLM:
    """脱敏探针使用的 GLM 传输替身，只返回完整原始 JSON。"""

    def __init__(self, report: dict[str, Any]):
        self.raw = json.dumps(report, ensure_ascii=False)

    def review(self, request: dict[str, Any]) -> str:
        assert request["reviewer_id"] == REVIEWER_ID
        return self.raw


def _glm_report(error_types: list[str] | None = None) -> dict[str, Any]:
    error_types = error_types or []
    if not error_types:
        return {"reviewer_id": REVIEWER_ID, "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [{"kind": "query_spec", "ref": "qspec-027", "description": "冻结查询规格已覆盖"}], "route_suggestion": "150"}
    route = ERROR_ROUTE[error_types[0]]
    return {
        "reviewer_id": REVIEWER_ID, "decision": "no", "error_types": error_types,
        "error_details": [{"error_type": error_type, "object": "candidate", "location": "payload", "reason": f"probe-{error_type}", "suggestion": "修复后重新预审"} for error_type in error_types],
        "evidence_refs": [{"kind": "frozen_package", "ref": "package_hash", "description": "脱敏冻结输入"}],
        "route_suggestion": route,
    }


def run_sanitized_probe(root: Path) -> dict[str, Any]:
    """Run the full desensitized probe: 150 → 160 → 180 → 110."""
    builder_150 = PendingPrecheckBuilder(root)
    agent_160 = PrecheckAgent(root)
    query = _spec()

    # Build a valid candidate and run through 160
    valid = builder_150.build_pending_precheck(
        query, run_id="eas27-probe", qa_id="QA-EAS27",
        created_at=TIME, **_candidate("SELECT T1.F1 FROM T1"),
    )
    result_160 = agent_160.precheck(valid, query, checked_at=TIME)
    dual = agent_160.build_dual_review(valid, query, result_160, created_at=TIME)

    # 180 pass path
    pass_result = GLMReviewerAgent(root, _StaticGLM(_glm_report())).review(dual, created_at=TIME)
    consumed_pass = consume_110_stub(root, pass_result)

    # 180 fail paths: each of the six error types
    fail_matrix: dict[str, Any] = {}
    error_type_cases = {
        "fact_package_error": ("FACT_PACKAGE_ERROR", "120"),
        "observable_mapping_error": ("OBSERVABLE_MAPPING_ERROR", "130"),
        "query_spec_error": ("QUERY_SPEC_ERROR", "140"),
        "question_sql_error": ("QUESTION_SQL_ERROR", "150"),
        "business_event_error": ("BUSINESS_EVENT_ERROR", "150"),
        "question_fact_omission": ("QUESTION_FACT_OMISSION", "120"),
    }
    for label, (error_type, route) in error_type_cases.items():
        fail_result = GLMReviewerAgent(root, _StaticGLM(_glm_report([error_type]))).review(dual, created_at=TIME)
        consumed_fail = consume_110_stub(root, fail_result)
        fail_matrix[label] = {
            "decision": consumed_fail["decision"],
            "error_types": consumed_fail["error_types"],
            "route_suggestion": consumed_fail["route_suggestion"],
        }

    # Multiple non-conflicting errors
    multi_result = GLMReviewerAgent(root, _StaticGLM(_glm_report(["QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR"]))).review(dual, created_at=TIME)
    consumed_multi = consume_110_stub(root, multi_result)

    # Deterministic: same input → same output
    pass_again = GLMReviewerAgent(root, _StaticGLM(_glm_report())).review(dual, created_at=TIME)
    deterministic = pass_result["envelope"]["content_hash"] == pass_again["envelope"]["content_hash"]

    # Input hash drift detection
    drift = copy.deepcopy(dual)
    drift["payload"]["package_hash"] = "0" * 64
    drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
    drift_detected = False
    try:
        GLMReviewerAgent(root).validate_input(drift)
    except ContractError as exc:
        drift_detected = "PACKAGE_HASH_DRIFT" in str(exc)

    return {
        "summary": {
            "pass_review_valid": pass_result["envelope"]["artifact_type"] == "glm_review_result",
            "pass_reviewer_id": pass_result["payload"]["semantic_review_report"]["reviewer_id"] == REVIEWER_ID,
            "pass_decision": consumed_pass["decision"] == "yes",
            "pass_consumed_by_110": consumed_pass["consumer"] == "110",
            "fail_matrix": fail_matrix,
            "multi_error_decision": consumed_multi["decision"] == "no",
            "multi_error_types_count": len(json.loads(consumed_multi["error_types"])) == 2,
            "deterministic": deterministic,
            "input_hash_drift_detected": drift_detected,
        },
    }


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe(Path(__file__).resolve().parents[4])["summary"], ensure_ascii=False, sort_keys=True))
