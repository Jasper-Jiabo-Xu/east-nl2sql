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
    payload = {"request_id": request["payload"]["request_id"], "asset_version": "CA-V0.3.0", "executed_queries": [], "matched_records": [{"record_type": "single_field", "data": {"table_id": "EAST_D001", "field_id": "F001", "penalty_fact_ids": ["fact-001"], "constraint_evidence_ref": "constraint-eas49"}, "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": []}], "constraint_summary": {"total_matched": 1, "asset_types_covered": ["single_field"]}, "unmatched_items": [], "query_trace": []}
    return _wrap("constraint_asset_package", f"eas49-assets-{request['payload']['request_id']}", payload, producer="000", parents=[artifact_ref(request["envelope"])], attempt=request["envelope"]["attempt_no"])


def _review(previous: dict[str, Any]) -> dict[str, Any]:
    payload = {"reviewed_package_ref": artifact_ref(previous["envelope"]), "semantic_review_report": {"reviewer_id": "170", "decision": "no", "error_types": ["OBSERVABLE_MAPPING_ERROR"], "error_details": [{"reason": "脱敏回退验证"}], "evidence_refs": [], "route_suggestion": "130"}}
    return _wrap("deepseek_review_result", "eas49-review-170", payload, producer="170")


def run_sanitized_probe(repo_root: Path) -> dict[str, Any]:
    mapper = ObservableFactMapper(repo_root)
    penalty = _penalty()
    request = mapper.plan_constraint_query(penalty, run_id="eas49-sanitized-run", qa_id="QA-EAS49", created_at=FIXED_TIME)
    first = mapper.build_observable_facts(penalty, _assets(request), run_id="eas49-sanitized-run", qa_id="QA-EAS49", created_at=FIXED_TIME)
    mapper.validate_observable(first)
    remapped = mapper.handle_review_feedback(penalty, _review(first), first, request, _assets, run_id="eas49-sanitized-run", qa_id="QA-EAS49", attempt_no=2, created_at=FIXED_TIME)["observable"]
    mapper.validate_observable(remapped)
    corrupted = copy.deepcopy(penalty); corrupted["payload"]["source_facts"][0]["original_text"] = "篡改"
    try:
        mapper.validate_penalty(corrupted)
    except ContractError as exc:
        bad_hash_rejection = str(exc) == "CONTENT_HASH_DRIFT"
    else:
        bad_hash_rejection = False
    if not bad_hash_rejection:
        raise ContractError("SANITIZED_PROBE_BAD_HASH_NOT_REJECTED")
    facts = remapped["payload"]["observable_facts"]
    if not facts[0]["entry_table"] or "不直接认定" not in facts[0]["risk_screening_boundary"]:
        raise ContractError("SANITIZED_PROBE_DOWNSTREAM_REJECTED")
    return {"transport": remapped, "summary": {"artifact_ref": artifact_ref(remapped["envelope"]), "content_hash": remapped["envelope"]["content_hash"], "bad_hash_rejected": True, "review_170_remap": True, "stub_140_consumed": bool(facts[0]["entry_table"]), "stub_150_consumed": True}}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--emit-transport", action="store_true"); args = parser.parse_args()
    result = run_sanitized_probe(Path(__file__).resolve().parents[4])
    print(json.dumps(result["transport"] if args.emit_transport else result["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
