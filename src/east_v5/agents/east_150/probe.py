"""Desensitized EAS-54 probe with an independent 160 contract stub."""
from __future__ import annotations

import json
import copy
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.agents.east_150.extractor import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder, TrustedRouteCapability
from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash, validate_envelope
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


def _regression(*, parents: list[dict[str, Any]] | None = None, attempt: int = 1) -> dict[str, Any]:
    payload = {"schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data", "input_data_refs": [], "input_orm_ref": None, "sandbox_snapshot_id": "desensitized-copy", "failure_details": {"error_code": "SQL_EXECUTION_ERROR", "error_stage": "sql_execution", "error_location": "sql", "expected_values": [], "actual_values": [], "sql_error_detail": {"sql_text": "SELECT T1.F1 FROM T1", "error_code": "SQLITE", "error_message": "synthetic"}, "regression_metrics": {}}, "route_target": "110", "retry_count": 1}
    return _wrap("sql_regression_failed_feedback", "regression024", payload, "260", attempt=attempt, mode="event_data", parents=parents)


def consume_160_stub(root: Path, package: dict[str, Any]) -> str:
    """Independent downstream consumer of a registry-resolved 150 package."""
    try:
        validate_envelope(root, package["envelope"], package["payload"])
    except Exception as exc:
        raise ContractError("160_STUB_ENVELOPE_REJECTED") from exc
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


def _reviewed_fixture(first: dict[str, Any], query: dict[str, Any], route_170: dict[str, Any], route_180: dict[str, Any]) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[4]
    fixture = json.loads((root / "tests/agents/220/fixtures/event-data-dual-review.json").read_text(encoding="utf-8"))
    payload = copy.deepcopy(fixture["payload"])
    payload.update({"qa_id": "QA-EAS24", "candidate_ref": artifact_ref(first["envelope"]), "query_spec_ref": artifact_ref(query["envelope"]),
                    "precheck_report_ref": {"artifact_id": "precheck-eas24", "version": 1, "content_hash": "c" * 64},
                    "deepseek_review_ref": artifact_ref(route_170["envelope"]), "glm_review_ref": artifact_ref(route_180["envelope"])})
    payload["package_hash"] = hashlib.sha256(json.dumps({key: value for key, value in payload.items() if key != "package_hash"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return _wrap(
        "reviewed_question_sql", "reviewed-eas24", payload, "210", mode="event_data",
        parents=[artifact_ref(first["envelope"]), artifact_ref(route_170["envelope"]), artifact_ref(route_180["envelope"])],
    )


def run_sanitized_probe(root: Path) -> dict[str, Any]:
    builder, query = PendingPrecheckBuilder(root), _spec()
    first = builder.build_pending_precheck(query, run_id="eas24-probe", qa_id="QA-EAS24", created_at=TIME, **_candidate("SELECT T1.F1 FROM T1"))
    route_170 = _review(first); route_170["envelope"]["artifact_id"] = "route170-eas24"; route_170["envelope"]["content_hash"] = content_hash(route_170["envelope"], route_170["payload"])
    route_180 = _review(first); route_180["envelope"]["artifact_id"] = "route180-eas24"; route_180["envelope"]["artifact_type"] = "glm_review_result"; route_180["envelope"]["producer_id"] = "180"; route_180["payload"]["semantic_review_report"]["reviewer_id"] = "180"; route_180["envelope"]["content_hash"] = content_hash(route_180["envelope"], route_180["payload"])
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        registry = ArtifactRegistry(root, {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}, "EAS-24", "probe-route", 1)
        registry.register(query["envelope"], query["payload"]); registry.register(first["envelope"], first["payload"]); registry.register(route_170["envelope"], route_170["payload"]); registry.register(route_180["envelope"], route_180["payload"])
        approved170 = _review(first); approved170["envelope"]["artifact_id"] = "approved170-eas24"; approved170["payload"]["semantic_review_report"]["decision"] = "yes"; approved170["payload"]["semantic_review_report"]["error_types"] = []; approved170["envelope"]["content_hash"] = content_hash(approved170["envelope"], approved170["payload"])
        approved180 = _review(first); approved180["envelope"]["artifact_id"] = "approved180-eas24"; approved180["envelope"]["artifact_type"] = "glm_review_result"; approved180["envelope"]["producer_id"] = "180"; approved180["payload"]["semantic_review_report"].update({"reviewer_id": "180", "decision": "yes", "error_types": []}); approved180["envelope"]["content_hash"] = content_hash(approved180["envelope"], approved180["payload"])
        registry.register(approved170["envelope"], approved170["payload"]); registry.register(approved180["envelope"], approved180["payload"])
        rejection_matrix = {
            label: _is_rejected(lambda sql=sql: builder.build_pending_precheck(query, run_id="eas24-probe", qa_id="QA-EAS24", created_at=TIME, **_candidate(sql)))
            for label, sql in {"write": "DELETE FROM T1", "alias": "SELECT t.F9 FROM T1 AS t", "unqualified": "SELECT F9 FROM T1", "cte_scope": "WITH x AS (SELECT T1.F1 AS X1 FROM T1) SELECT x.F1 FROM x"}.items()
        }
        second = builder.handle_precheck_feedback(query, _precheck(first, "feedback024-2", 1), first, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-修复"))
        third = builder.handle_precheck_feedback(query, _precheck(second, "feedback024-3", 2), second, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=3, created_at=TIME, **_candidate("SELECT F9 FROM T1", "-人工"))
        reviewed = _reviewed_fixture(first, query, approved170, approved180)
        registry.register(reviewed["envelope"], reviewed["payload"])
        review_170 = _review(first, parents=[artifact_ref(route_170["envelope"])])
        registry.register(review_170["envelope"], review_170["payload"])
        capability_170 = TrustedRouteCapability.from_registry(registry, review_refs=[artifact_ref(review_170["envelope"])])
        review_result = builder.handle_routed_feedback(query, review_170, first, route_capability=capability_170, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-170"))
        glm_review = _review(first, parents=[artifact_ref(route_180["envelope"])])
        glm_review["envelope"]["artifact_id"] = "review180-eas24"; glm_review["envelope"]["artifact_type"] = "glm_review_result"; glm_review["envelope"]["producer_id"] = "180"; glm_review["payload"]["semantic_review_report"]["reviewer_id"] = "180"; glm_review["envelope"]["content_hash"] = content_hash(glm_review["envelope"], glm_review["payload"])
        registry.register(glm_review["envelope"], glm_review["payload"])
        capability_180 = TrustedRouteCapability.from_registry(registry, review_refs=[artifact_ref(glm_review["envelope"])])
        glm_result = builder.handle_routed_feedback(query, glm_review, first, route_capability=capability_180, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-180"))
        regression = _regression(attempt=reviewed["envelope"]["attempt_no"])
        builder._validate_regression_package(regression)
        registry.register(regression["envelope"], regression["payload"])
        route_record = _wrap("sql_regression_route_record", "route110-eas24", {"schema_version": "v5.sql-regression-route-record/v1", "source_feedback_ref": artifact_ref(regression["envelope"]), "source_feedback_type": "sql_regression_failed_feedback", "route_path": ["260", "210", "010", "110"], "route_target": "150", "route_reason": "SQL_EXECUTION_ERROR"}, "110", attempt=regression["envelope"]["attempt_no"], mode="event_data", parents=[artifact_ref(regression["envelope"])])
        registry.register(route_record["envelope"], route_record["payload"])
        capability_regression = TrustedRouteCapability.from_registry(registry, route_record_ref=artifact_ref(route_record["envelope"]))
        regression_result = builder.handle_routed_feedback(query, regression, reviewed, route_capability=capability_regression, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1", "-260"))
        registered_regression_result = registry.register(regression_result["envelope"], regression_result["payload"])
        resolved_regression_result = registry.resolve(artifact_ref(regression_result["envelope"]))
        consumed = consume_160_stub(root, resolved_regression_result)
        forged_regression = copy.deepcopy(regression); forged_regression["payload"]["sandbox_snapshot_id"] = "forged-copy"; forged_regression["envelope"]["content_hash"] = content_hash(forged_regression["envelope"], forged_regression["payload"])
        substituted_170 = copy.deepcopy(review_170); substituted_170["envelope"]["artifact_id"] = "substituted-170"; substituted_170["envelope"]["content_hash"] = content_hash(substituted_170["envelope"], substituted_170["payload"])
        substituted_180 = copy.deepcopy(glm_review); substituted_180["envelope"]["artifact_id"] = "substituted-180"; substituted_180["envelope"]["content_hash"] = content_hash(substituted_180["envelope"], substituted_180["payload"])
        swapped_query = copy.deepcopy(query); swapped_query["envelope"]["artifact_id"] = "qspec-swapped"; swapped_query["envelope"]["content_hash"] = content_hash(swapped_query["envelope"], swapped_query["payload"])
        parentless_record = _wrap("sql_regression_route_record", "route110-parentless", route_record["payload"], "110", mode="event_data")
        registry.register(parentless_record["envelope"], parentless_record["payload"])
        route_rejections = {"review_substitution": _is_rejected(lambda: builder.handle_routed_feedback(query, substituted_170, first, route_capability=capability_170, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))) and _is_rejected(lambda: builder.handle_routed_feedback(query, substituted_180, first, route_capability=capability_180, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "260_forged_feedback": _is_rejected(lambda: builder.handle_routed_feedback(query, forged_regression, reviewed, route_capability=capability_regression, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "260_retry_base": _is_rejected(lambda: builder.handle_routed_feedback(query, regression, reviewed, route_capability=capability_regression, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F1 FROM T1"))), "query_spec_substitution": _is_rejected(lambda: builder.handle_routed_feedback(swapped_query, regression, reviewed, route_capability=capability_regression, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "route_record_parent": _is_rejected(lambda: TrustedRouteCapability.from_registry(registry, route_record_ref=artifact_ref(parentless_record["envelope"])))}
        required_change_rejections = {"160_sql_unchanged": _is_rejected(lambda: builder.handle_precheck_feedback(query, _precheck(first, "unchanged", 1), first, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F1 FROM T1"))), "170_sql_unchanged": _is_rejected(lambda: builder.handle_routed_feedback(query, review_170, first, route_capability=capability_170, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F1 FROM T1")))}
        review_170_bad = _review(first, parents=[artifact_ref(route_170["envelope"])]); review_170_bad["payload"]["semantic_review_report"]["error_types"] = []; review_170_bad["envelope"]["content_hash"] = content_hash(review_170_bad["envelope"], review_170_bad["payload"])
        review_180_bad = _review(first, parents=[artifact_ref(route_180["envelope"])]); review_180_bad["envelope"]["artifact_type"] = "glm_review_result"; review_180_bad["envelope"]["producer_id"] = "180"; review_180_bad["payload"]["semantic_review_report"]["reviewer_id"] = "180"; review_180_bad["payload"]["semantic_review_report"]["error_types"] = []; review_180_bad["envelope"]["content_hash"] = content_hash(review_180_bad["envelope"], review_180_bad["payload"])
        regression_bad = copy.deepcopy(regression); regression_bad["payload"]["failure_details"]["error_code"] = "DATA_VALUE_ERROR"; regression_bad["envelope"]["content_hash"] = content_hash(regression_bad["envelope"], regression_bad["payload"])
        difference_rejections = {"170": _is_rejected(lambda: builder.handle_routed_feedback(query, review_170_bad, first, route_capability=capability_170, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "180": _is_rejected(lambda: builder.handle_routed_feedback(query, review_180_bad, first, route_capability=capability_180, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1"))), "260": _is_rejected(lambda: builder.handle_routed_feedback(query, regression_bad, reviewed, route_capability=capability_regression, run_id="eas24-probe", qa_id="QA-EAS24", attempt_no=2, created_at=TIME, **_candidate("SELECT T1.F2 FROM T1")))}
    return {"summary": {"candidate_valid": second["envelope"]["status"] == "candidate", "stub_160_consumed": consumed == regression_result["payload"]["candidate_id"], "third_attempt_blocked_manual": third["envelope"]["status"] == "blocked_manual", "transport_routes": {"170": review_result["envelope"]["attempt_no"] == 2, "180": glm_result["envelope"]["attempt_no"] == 2, "260": regression_result["envelope"]["attempt_no"] == 2}, "runtime_registry_chain": {"source_260_schema": True, "source_260_mode_attempt": regression["envelope"]["mode"] == "event_data" and regression["envelope"]["attempt_no"] == reviewed["envelope"]["attempt_no"], "route_record_exact": route_record["envelope"]["parent_artifact_refs"] == [artifact_ref(regression["envelope"])] and route_record["envelope"]["mode"] == "event_data" and route_record["envelope"]["attempt_no"] == regression["envelope"]["attempt_no"], "repaired_registered": registered_regression_result == regression_result["envelope"], "repaired_resolved": resolved_regression_result == regression_result}, "route_record_direct_parent": artifact_ref(route_record["envelope"]) in regression_result["envelope"]["parent_artifact_refs"], "reject_matrix": rejection_matrix, "route_reject_matrix": route_rejections, "required_change_reject_matrix": required_change_rejections, "difference_reject_matrix": difference_rejections, "candidate_hash": second["envelope"]["content_hash"]}}


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe(Path(__file__).resolve().parents[4])["summary"], ensure_ascii=False, sort_keys=True))
