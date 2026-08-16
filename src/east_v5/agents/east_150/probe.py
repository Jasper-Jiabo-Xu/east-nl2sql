"""Desensitized EAS-54 probe with an independent 160 contract stub."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.agents.east_150.extractor import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder, TrustedRouteContext
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, load_json

TIME = "2026-08-16T00:00:00+00:00"


def _ref(stage: str) -> dict[str, Any]:
    return {"artifact_id": f"{stage}-route-eas24", "version": 1, "content_hash": "1" * 64}


def _wrap(kind: str, identity: str, payload: dict[str, Any], producer: str, *, attempt: int = 1, parents: list[dict[str, Any]] | None = None, mode: str = "question_sql") -> dict[str, Any]:
    parent_refs = list(parents or [])
    envelope = {
        "artifact_id": identity, "artifact_type": kind, "run_id": "eas24-probe", "qa_id": "QA-EAS24",
        "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer,
        "parent_artifact_refs": parent_refs, "input_hashes": [ref["content_hash"] for ref in parent_refs],
        "status": "candidate", "mode": mode, "created_at": TIME, "trace_id": "eas24-probe", "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _spec() -> dict[str, Any]:
    payload = {
        "query_spec_id": "qspec-024", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
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
    return _wrap("query_specification_package", "qspec024", payload, "140")


def _candidate(sql: str, suffix: str = "") -> dict[str, Any]:
    question = f"脱敏机构风险筛查{suffix}"
    return {
        "sql_gold": sql, "clear_question": question,
        "sql_explanation": {"select": f"字段{suffix}", "from_join": f"T1{suffix}", "where": f"固定{suffix}", "aggregation": f"无{suffix}", "sort": f"固定{suffix}", "business_meaning": f"筛查{suffix}"},
        "business_event_candidates": [{"event_name": f"筛查{suffix}", "objective": f"筛查{suffix}", "objects": ["机构"], "state_changes": [suffix] if suffix else []}],
        "specification_mapping": [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS],
    }


def _precheck(candidate: dict[str, Any], identity: str, attempt: int) -> dict[str, Any]:
    payload = {"candidate_ref": artifact_ref(candidate["envelope"]), "precheck_decision": "fail", "failed_items": [{"failed_rule_ids": ["R"], "error_locations": ["sql_gold"], "expected_values": "ok", "actual_values": "bad", "error_details": "retry"}]}
    return _wrap("precheck_failed_feedback", identity, payload, "160", attempt=attempt)


def _review(candidate: dict[str, Any], *, parents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload = {"reviewed_package_ref": artifact_ref(candidate["envelope"]), "semantic_review_report": {"reviewer_id": "170", "decision": "no", "error_types": ["QUESTION_SQL_ERROR"], "error_details": [{}], "evidence_refs": [], "route_suggestion": "150"}}
    return _wrap("deepseek_review_result", "review024", payload, "170", attempt=candidate["envelope"]["attempt_no"], parents=parents)


def _regression(*, parents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload = {"schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data", "input_data_refs": [], "input_orm_ref": None, "sandbox_snapshot_id": "desensitized-copy", "failure_details": {"error_code": "SQL_EXECUTION_ERROR", "error_stage": "sql_execution", "error_location": "sql", "expected_values": [], "actual_values": [], "sql_error_detail": {"sql_text": "SELECT T1.F1 FROM T1", "error_code": "SQLITE", "error_message": "synthetic"}, "regression_metrics": {}}, "route_target": "110", "retry_count": 1}
    return _wrap("sql_regression_failed_feedback", "regression024", payload, "260", mode="event_data", parents=parents)


class _RouteRegistry:
    """Probe-only stand-in for the runtime registry resolver."""

    def __init__(self, root: Path, *packages: dict[str, Any]):
        self.repo_root = root
        self._packages = {tuple(artifact_ref(package["envelope"]).values()): package for package in packages}

    def resolve(self, reference: dict[str, Any]) -> dict[str, Any]:
        return self._packages[tuple(reference.values())]


def consume_160_stub(root: Path, package: dict[str, Any]) -> str:
    """Independent downstream consumer: payload schema plus frozen-field gate."""
    try:
        Draft202012Validator(load_json(root / "contracts/packages/question-sql-pending-precheck-package.schema.json")).validate(package["payload"])
    except ValidationError as exc:
        raise ContractError("160_STUB_SCHEMA_REJECTED") from exc
    payload = package["payload"]
    if len(payload["specification_mapping"]) != len(MAPPED_SPEC_ITEMS) or {entry["spec_item"] for entry in payload["specification_mapping"]} != set(MAPPED_SPEC_ITEMS):
        raise ContractError("160_STUB_MAPPING_REJECTED")
    if any(not entry["question_fragment"] or entry["sql_fragment"] not in payload["sql_gold"] for entry in payload["specification_mapping"]):
        raise ContractError("160_STUB_MAPPING_REJECTED")
    return payload["candidate_id"]


def _is_rejected(action: Any) -> bool:
    try:
        action()
    except ContractError:
        return True
    return False


def run_sanitized_probe(root: Path) -> dict[str, Any]:
    builder, query = PendingPrecheckBuilder(root), _spec()
    route_110 = _wrap("reviewed_question_sql", "opaque-approval-eas24", {}, "210")
    route_010 = _wrap("release_receipt", "opaque-release-eas24", {}, "010")
    context_110 = TrustedRouteContext.from_registry(_RouteRegistry(root, route_110), stage_110_ref=artifact_ref(route_110["envelope"]))
    context_260 = TrustedRouteContext.from_registry(_RouteRegistry(root, route_110, route_010), stage_110_ref=artifact_ref(route_110["envelope"]), stage_010_ref=artifact_ref(route_010["envelope"]))
    first = builder.build_pending_precheck(query, run_id="eas24-probe", qa_id="QA-EAS24", created_at=TIME, **_candidate("SELECT T1.F1 FROM T1"))
    consumed = consume_160_stub(root, first)
    rejection_matrix = {
        label: _is_rejected(lambda sql=sql: builder.build_pending_precheck(query, run_id="eas24-probe", qa_id="QA-EAS24", created_at=TIME, **_candidate(sql)))
        for label, sql in {"write": "DELETE FROM T1", "alias": "SELECT t.F9 FROM T1 AS t", "unqualified": "SELECT F9 FROM T1", "cte_scope": "WITH x AS (SELECT T1.F1 AS X1 FROM T1) SELECT x.F1 FROM x"}.items()
    }
    second = builder.handle_precheck_feedback(query, _precheck(first, "feedback024-2", 1), first, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-修复"))
    third = builder.handle_precheck_feedback(query, _precheck(second, "feedback024-3", 2), second, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=3, created_at=TIME, **_candidate("SELECT F9 FROM T1", "-人工"))
    reviewed = _wrap("reviewed_question_sql", "reviewed-eas24", first["payload"], "210")
    review_result = builder.handle_routed_feedback(query, _review(first, parents=[artifact_ref(route_110["envelope"])]), first, route_context=context_110, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-170"))
    glm_review = _review(first, parents=[artifact_ref(route_110["envelope"])])
    glm_review["envelope"]["artifact_type"] = "glm_review_result"; glm_review["envelope"]["producer_id"] = "180"; glm_review["payload"]["semantic_review_report"]["reviewer_id"] = "180"; glm_review["envelope"]["content_hash"] = content_hash(glm_review["envelope"], glm_review["payload"])
    glm_result = builder.handle_routed_feedback(query, glm_review, first, route_context=context_110, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-180"))
    regression_result = builder.handle_routed_feedback(query, _regression(parents=[artifact_ref(route_010["envelope"]), artifact_ref(route_110["envelope"])]), reviewed, route_context=context_260, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-260"))
    route_rejections = {"170_forged_prefix": _is_rejected(lambda: builder.handle_routed_feedback(query, _review(first, parents=[_ref("110")]), first, route_context=context_110, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "260_missing_010": _is_rejected(lambda: builder.handle_routed_feedback(query, _regression(parents=[artifact_ref(route_110["envelope"])]), reviewed, route_context=context_260, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "260_candidate_previous": _is_rejected(lambda: builder.handle_routed_feedback(query, _regression(parents=[artifact_ref(route_010["envelope"]), artifact_ref(route_110["envelope"])]), first, route_context=context_260, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1")))}
    review_170_bad = _review(first, parents=[artifact_ref(route_110["envelope"])]); review_170_bad["payload"]["semantic_review_report"]["error_types"] = []; review_170_bad["envelope"]["content_hash"] = content_hash(review_170_bad["envelope"], review_170_bad["payload"])
    review_180_bad = _review(first, parents=[artifact_ref(route_110["envelope"])]); review_180_bad["envelope"]["artifact_type"] = "glm_review_result"; review_180_bad["envelope"]["producer_id"] = "180"; review_180_bad["payload"]["semantic_review_report"]["reviewer_id"] = "180"; review_180_bad["payload"]["semantic_review_report"]["error_types"] = []; review_180_bad["envelope"]["content_hash"] = content_hash(review_180_bad["envelope"], review_180_bad["payload"])
    regression_bad = _regression(parents=[artifact_ref(route_010["envelope"]), artifact_ref(route_110["envelope"])]); regression_bad["payload"]["failure_details"]["error_code"] = "DATA_VALUE_ERROR"; regression_bad["envelope"]["content_hash"] = content_hash(regression_bad["envelope"], regression_bad["payload"])
    difference_rejections = {"170": _is_rejected(lambda: builder.handle_routed_feedback(query, review_170_bad, first, route_context=context_110, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "180": _is_rejected(lambda: builder.handle_routed_feedback(query, review_180_bad, first, route_context=context_110, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "260": _is_rejected(lambda: builder.handle_routed_feedback(query, regression_bad, reviewed, route_context=context_260, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1")))}
    return {"summary": {"candidate_valid": second["envelope"]["status"] == "candidate", "stub_160_consumed": consumed == first["payload"]["candidate_id"], "third_attempt_blocked_manual": third["envelope"]["status"] == "blocked_manual", "transport_routes": {"170": review_result["envelope"]["attempt_no"] == 2, "180": glm_result["envelope"]["attempt_no"] == 2, "260": regression_result["envelope"]["attempt_no"] == 2}, "reject_matrix": rejection_matrix, "route_reject_matrix": route_rejections, "difference_reject_matrix": difference_rejections, "candidate_hash": second["envelope"]["content_hash"]}}


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe(Path(__file__).resolve().parents[4])["summary"], ensure_ascii=False, sort_keys=True))
