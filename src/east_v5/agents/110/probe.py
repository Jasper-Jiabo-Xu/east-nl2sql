"""Sanitized executable 160→110→210 contract probe (no external calls)."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.agents.east_180.reviewer import GLMReviewerAgent
from east_v5.artifacts import content_hash

from .scheduler import QuestionSqlStageScheduler

PrecheckAgent = importlib.import_module("east_v5.agents.160.precheck").PrecheckAgent
DeepSeekReviewAgent = importlib.import_module("east_v5.agents.170.review").DeepSeekReviewAgent
TIME = "2026-08-17T00:00:00+00:00"


class _GLM:
    def review(self, _request: dict[str, Any]) -> str:
        return json.dumps({"reviewer_id": "180", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [{"kind": "fixture", "ref": "probe", "description": "sanitized"}], "route_suggestion": "150"})


def _wrap(payload: dict[str, Any]) -> dict[str, Any]:
    env = {"artifact_id": "qspec-110-probe", "artifact_type": "query_specification_package", "run_id": "eas28-probe", "qa_id": "QA-EAS28", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "140", "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "question_sql", "created_at": TIME, "trace_id": "eas28-probe", "storage_locator": None}
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


def run_sanitized_probe(root: Path) -> dict[str, Any]:
    spec = _wrap({"query_spec_id": "qspec-110", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64}, "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64}, "query_goal": "sanitized", "must_preserve_fact_refs": ["fact"], "main_object_and_grain": {"main_object": "org", "grain": "row"}, "query_entry": {"entry_table": "T1", "entry_conditions": []}, "related_objects_and_path": [], "filters_and_evidence": [], "return_fields": [{"field_id": "F1", "display_name": "field", "source_table": "T1"}], "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["x"], "unanswerable": []}, "expected_result_shape": {"row_grain": "row", "column_set": ["F1"], "aggregation_shape": "none"}, "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1"]}]}, "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [], "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}}, "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 1}, "query_specification_package_schema_version": "query-specification-v1"})
    candidate = {"sql_gold": "SELECT T1.F1 FROM T1", "clear_question": "sanitized", "sql_explanation": {"select": "F1", "from_join": "T1", "where": "fixed", "aggregation": "none", "sort": "fixed", "business_meaning": "probe"}, "business_event_candidates": [{"event_name": "probe", "objective": "probe", "objects": ["org"], "state_changes": []}], "specification_mapping": [{"spec_item": item, "question_fragment": "sanitized", "sql_fragment": "SELECT T1.F1 FROM T1"} for item in MAPPED_SPEC_ITEMS]}
    pending_precheck = PendingPrecheckBuilder(root).build_pending_precheck(spec, run_id="eas28-probe", qa_id="QA-EAS28", created_at=TIME, **candidate)
    checker = PrecheckAgent(root); dual = checker.build_dual_review(pending_precheck, spec, checker.precheck(pending_precheck, spec, checked_at=TIME), created_at=TIME)
    r170 = DeepSeekReviewAgent(root).review(dual, {"reviewer_id": "170", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [], "route_suggestion": "150"}, created_at=TIME)
    r180 = GLMReviewerAgent(root, _GLM()).review(dual, created_at=TIME)
    joined = QuestionSqlStageScheduler(root).collect_reviews(dual, [r180, r170], created_at=TIME)
    data = importlib.import_module("east_v5.agents.210.scheduler").DataStageCoordinator(root).begin_event(joined["approved_package"])
    return {"summary": {"double_yes_to_210": joined["target"] == "210", "downstream_stub_consumed": data["dispatches"][0]["target"] == "220", "approved_hash": joined["approved_package"]["envelope"]["content_hash"]}}


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe(Path(__file__).resolve().parents[4])["summary"], ensure_ascii=False, sort_keys=True))
