from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from east_v5.agents.east_130 import ObservableFactMapper
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
FIXED_TIME = "2026-08-16T00:00:00+00:00"
FIXTURES = Path(__file__).with_name("fixtures")


def wrap(mapper: ObservableFactMapper, artifact_type: str, artifact_id: str, payload: dict, *, producer: str, attempt: int = 1, version: int = 1, parents: list[dict] | None = None) -> dict:
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "run-130", "qa_id": "QA-130", "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents or [], "input_hashes": [x["content_hash"] for x in parents or []], "status": "candidate", "mode": "question_sql", "created_at": FIXED_TIME, "trace_id": "trace-130", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def penalty(mapper: ObservableFactMapper) -> dict:
    payload = json.loads((FIXTURES / "penalty-fact-approved.json").read_text(encoding="utf-8"))
    return wrap(mapper, "penalty_fact_package", "penalty-130", payload, producer="120")


def assets(mapper: ObservableFactMapper, *, matched: bool = True, request_id: str = "130-query-run-130-1") -> dict:
    payload = json.loads((FIXTURES / "constraint-asset-approved.json").read_text(encoding="utf-8"))
    payload["request_id"] = request_id
    if not matched:
        payload["matched_records"] = []; payload["constraint_summary"] = {"total_matched": 0, "asset_types_covered": []}; payload["unmatched_items"] = [{"target": "脱敏事实", "reason": "无命中"}]
    return wrap(mapper, "constraint_asset_package", f"asset-{request_id}", payload, producer="000")


def review(mapper: ObservableFactMapper, kind: str = "deepseek_review_result") -> dict:
    reviewer = "170" if kind == "deepseek_review_result" else "180"
    payload = {"reviewed_package_ref": {"artifact_id": "130-observable-run-130", "version": 1, "content_hash": "a" * 64}, "semantic_review_report": {"reviewer_id": reviewer, "decision": "no", "error_types": ["OBSERVABLE_MAPPING_ERROR"], "error_details": [{"reason": "扩大范围"}], "evidence_refs": [], "route_suggestion": "130"}}
    return wrap(mapper, kind, f"review-{reviewer}", payload, producer=reviewer)


class TestAgent130Contracts(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = ObservableFactMapper(ROOT)
        self.penalty = penalty(self.mapper)

    def test_task1_query_is_two_phase_and_enum_valid(self) -> None:
        request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        self.mapper.validate_request(request)
        self.assertEqual(request["payload"]["target_asset_types"], ["data_element", "single_field", "within_table", "cross_table"])
        self.assertEqual(request["payload"]["previous_request_refs"], [])

    def test_request_rejects_hash_drift_and_invalid_previous_ref(self) -> None:
        request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        drifted = copy.deepcopy(request); drifted["payload"]["max_rows"] = 99
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.mapper.validate_request(drifted)
        invalid = copy.deepcopy(request); invalid["payload"]["previous_request_refs"] = ["bad"]
        invalid["envelope"]["content_hash"] = content_hash(invalid["envelope"], invalid["payload"])
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED:CONSTRAINT_QUERY_REQUEST"):
            self.mapper.validate_request(invalid)

    def test_direct_and_indirect_output_is_consumable_by_140_150_stubs(self) -> None:
        result = self.mapper.build_observable_facts(self.penalty, assets(self.mapper), run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        self.mapper.validate_observable(result)
        self.assertEqual(result["payload"]["coverage_status"], "complete")
        self.assertEqual(consume_140(result), "EAST_D001.F001")
        self.assertEqual(consume_150(result), "direct")

    def test_unobservable_is_schema_valid_and_nonempty(self) -> None:
        result = self.mapper.build_observable_facts(self.penalty, assets(self.mapper, matched=False), run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        self.mapper.validate_observable(result)
        fact = result["payload"]["observable_facts"][0]
        self.assertEqual(result["payload"]["coverage_status"], "partial")
        self.assertEqual(fact["entry_table"], "NO_EAST_ASSET")
        self.assertTrue(fact["observable_proxy"])
        self.assertTrue(fact["mapping_matrix"][0]["asset_evidence_ref"])

    def test_review_170_and_180_expand_and_preserve_lineage(self) -> None:
        previous_request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        previous = self.mapper.build_observable_facts(self.penalty, assets(self.mapper), run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        for kind in ("deepseek_review_result", "glm_review_result"):
            outcome = self.mapper.handle_review_feedback(self.penalty, review(self.mapper, kind), previous, previous_request, lambda req: assets(self.mapper, request_id=req["payload"]["request_id"]), run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)
            self.assertEqual(outcome["request"]["payload"]["query_purpose"], "closure_expansion")
            self.assertEqual(outcome["request"]["payload"]["previous_request_refs"], [artifact_ref(previous_request["envelope"])])
            self.assertEqual(outcome["observable"]["envelope"]["supersedes_ref"], artifact_ref(previous["envelope"]))
            self.assertEqual(outcome["observable"]["envelope"]["version"], 2)

    def test_third_unmatched_review_is_blocked_manual(self) -> None:
        previous_request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        previous = self.mapper.build_observable_facts(self.penalty, assets(self.mapper), run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        outcome = self.mapper.handle_review_feedback(self.penalty, review(self.mapper), previous, previous_request, lambda req: assets(self.mapper, matched=False, request_id=req["payload"]["request_id"]), run_id="run-130", qa_id="QA-130", attempt_no=3, created_at=FIXED_TIME)
        self.assertEqual(outcome["observable"]["envelope"]["status"], "blocked_manual")
        self.assertEqual(outcome["observable"]["payload"]["coverage_status"], "blocked")

    def test_reject_review_not_routed_to_130(self) -> None:
        previous_request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        previous = self.mapper.build_observable_facts(self.penalty, assets(self.mapper), run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        bad = review(self.mapper); bad["payload"]["semantic_review_report"]["route_suggestion"] = "140"; bad["envelope"]["content_hash"] = content_hash(bad["envelope"], bad["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEW_NOT_ROUTED_TO_130"):
            self.mapper.handle_review_feedback(self.penalty, bad, previous, previous_request, lambda req: assets(self.mapper), run_id="run-130", qa_id="QA-130", attempt_no=1, created_at=FIXED_TIME)


def consume_140(package: dict) -> str:
    """Approved downstream contract stub for query-spec Agent 140."""
    fact = package["payload"]["observable_facts"][0]
    if not fact["entry_table"] or not fact["mapping_matrix"]:
        raise ContractError("140_CONSUMPTION_REJECTED")
    return fact["mapping_matrix"][0]["table_field_path"]


def consume_150(package: dict) -> str:
    """Approved downstream contract stub for question Agent 150."""
    boundary = package["payload"]["observable_facts"][0]["risk_screening_boundary"]
    if "不直接认定" not in boundary:
        raise ContractError("150_BOUNDARY_REJECTED")
    return package["payload"]["observable_facts"][0]["observability_type"]
