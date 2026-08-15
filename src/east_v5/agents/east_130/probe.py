"""A sanitized, independently replayable Agent-130 runtime probe.

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

from east_v5.agents.east_130.extractor import ObservableFactMapper
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

FIXED_TIME = "2026-08-16T00:00:00+00:00"


def _wrap(artifact_type: str, artifact_id: str, payload: dict[str, Any], *, producer: str, parents: list[dict[str, Any]] | None = None, attempt: int = 1) -> dict[str, Any]:
    parents = list(parents or [])
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "eas49-sanitized-run", "qa_id": "QA-EAS49", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": "candidate", "mode": "question_sql", "created_at": FIXED_TIME, "trace_id": "eas49-sanitized-trace", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _penalty() -> dict[str, Any]:
    payload = {"source_facts": [{"penalty_fact_id": "fact-001", "fact_type": "behavior", "structured_fact": {"subject": "脱敏机构", "predicate": "行为", "object": "脱敏事实", "qualifier": None, "value": None}, "original_text": "脱敏事实", "source_span_refs": [], "must_preserve_in_question": "yes"}], "external_evidence": {"penalty_intent": {"description": "脱敏意图", "evidence_refs": []}, "regulatory_rules": [], "business_meaning": {"description": "脱敏业务含义", "evidence_refs": []}, "penalty_background": {"description": "脱敏背景", "evidence_refs": []}}, "evidence_conflicts": [], "uncertainties": [], "penalty_fact_package_schema_version": "penalty-fact-v1"}
    return _wrap("penalty_fact_package", "eas49-penalty", payload, producer="120")


def _assets(request: dict[str, Any]) -> dict[str, Any]:
    payload = {"request_id": request["payload"]["request_id"], "asset_version": "CA-V0.3.0", "executed_queries": [], "matched_records": [{"record_type": "single_field", "data": {"table_id": "EAST_D001", "field_id": "F001", "field_name": "脱敏字段", "data_type": "VARCHAR"}, "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": []}], "constraint_summary": {"total_matched": 1, "asset_types_covered": ["single_field"]}, "unmatched_items": [], "query_trace": []}
    return _wrap("constraint_asset_package", f"eas49-assets-{request['payload']['request_id']}", payload, producer="000", parents=[artifact_ref(request["envelope"])], attempt=request["envelope"]["attempt_no"])


def _candidates(asset: dict[str, Any]) -> dict[str, Any]:
    record = asset["payload"]["matched_records"][0]
    return {"asset_package_ref": artifact_ref(asset["envelope"]), "candidates": [{"penalty_fact_id": "fact-001", "asset_record_index": 0, "source_ref": record["source_refs"][0], "proxy_expression": "以 EAST_D001.F001 筛查处罚事实 fact-001"}]}


def _review(previous: dict[str, Any], kind: str) -> dict[str, Any]:
    reviewer = "170" if kind == "deepseek_review_result" else "180"
    payload = {"reviewed_package_ref": artifact_ref(previous["envelope"]), "semantic_review_report": {"reviewer_id": reviewer, "decision": "no", "error_types": ["OBSERVABLE_MAPPING_ERROR"], "error_details": [{"reason": "脱敏回退验证"}], "evidence_refs": [], "route_suggestion": "130"}}
    return _wrap(kind, f"eas49-review-{reviewer}", payload, producer=reviewer)


def _no_candidates(asset: dict[str, Any]) -> dict[str, Any]:
    return {"asset_package_ref": artifact_ref(asset["envelope"]), "candidates": []}


def _consume_140_stub(package: dict[str, Any]) -> str:
    fact = package["payload"]["observable_facts"][0]
    if not fact["entry_table"] or not fact["mapping_matrix"]:
        raise ContractError("140_CONSUMPTION_REJECTED")
    return fact["mapping_matrix"][0]["table_field_path"]


def _consume_150_stub(package: dict[str, Any]) -> str:
    fact = package["payload"]["observable_facts"][0]
    if "不直接认定" not in fact["risk_screening_boundary"]:
        raise ContractError("150_BOUNDARY_REJECTED")
    return fact["observability_type"]


def run_sanitized_probe(repo_root: Path) -> dict[str, Any]:
    mapper = ObservableFactMapper(repo_root)
    penalty = _penalty()
    request = mapper.plan_constraint_query(penalty, run_id="eas49-sanitized-run", qa_id="QA-EAS49", created_at=FIXED_TIME)
    first_assets = _assets(request)
    first = mapper.build_observable_facts(penalty, first_assets, run_id="eas49-sanitized-run", qa_id="QA-EAS49", mapping_candidates=_candidates(first_assets), created_at=FIXED_TIME)
    mapper.validate_observable(first)
    review_170 = mapper.handle_review_feedback(
        penalty, _review(first, "deepseek_review_result"), first, request, _assets,
        run_id="eas49-sanitized-run", qa_id="QA-EAS49", attempt_no=2,
        candidate_builder=_candidates, created_at=FIXED_TIME,
    )
    remapped = review_170["observable"]
    mapper.validate_observable(remapped)
    review_180 = mapper.handle_review_feedback(
        penalty, _review(remapped, "glm_review_result"), remapped, review_170["request"], _assets,
        run_id="eas49-sanitized-run", qa_id="QA-EAS49", attempt_no=3,
        candidate_builder=_no_candidates, created_at=FIXED_TIME,
    )
    blocked = review_180["observable"]
    mapper.validate_observable(blocked)
    corrupted = copy.deepcopy(penalty); corrupted["payload"]["source_facts"][0]["original_text"] = "篡改"
    try:
        mapper.validate_penalty(corrupted)
    except ContractError as exc:
        bad_hash_rejection = str(exc) == "CONTENT_HASH_DRIFT"
    else:
        bad_hash_rejection = False
    if not bad_hash_rejection:
        raise ContractError("SANITIZED_PROBE_BAD_HASH_NOT_REJECTED")
    consumer_140 = _consume_140_stub(remapped)
    consumer_150 = _consume_150_stub(remapped)
    review_170_executed = (
        review_170["observable"]["envelope"]["attempt_no"] == 2
        and review_170["observable"]["envelope"]["status"] == "candidate"
        and review_170["assets"]["payload"]["request_id"] == review_170["request"]["payload"]["request_id"]
    )
    review_180_executed = (
        review_180["observable"]["envelope"]["attempt_no"] == 3
        and review_180["assets"]["payload"]["request_id"] == review_180["request"]["payload"]["request_id"]
    )
    blocked_nonempty_assets = bool(review_180["assets"]["payload"]["matched_records"]) and blocked["envelope"]["status"] == "blocked_manual"
    return {"transport": blocked, "summary": {"artifact_ref": artifact_ref(blocked["envelope"]), "content_hash": blocked["envelope"]["content_hash"], "bad_hash_rejected": bad_hash_rejection, "review_170_executed": review_170_executed, "review_180_executed": review_180_executed, "attempt3_nonempty_assets_blocked": blocked_nonempty_assets, "stub_140_consumed": consumer_140 == "EAST_D001.F001", "stub_150_consumed": consumer_150 == "direct"}}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--emit-transport", action="store_true"); args = parser.parse_args()
    result = run_sanitized_probe(Path(__file__).resolve().parents[4])
    print(json.dumps(result["transport"] if args.emit_transport else result["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
