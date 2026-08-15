from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from east_v5.agents.east_130 import ObservableFactMapper
from east_v5.agents.east_130.probe import run_sanitized_probe
from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
FIXED_TIME = "2026-08-16T00:00:00+00:00"
FIXTURES = Path(__file__).with_name("fixtures")


def wrap(artifact_type: str, artifact_id: str, payload: dict, *, producer: str, attempt: int = 1, version: int = 1, parents: list[dict] | None = None) -> dict:
    parents = parents or []
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "run-130", "qa_id": "QA-130", "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [x["content_hash"] for x in parents], "status": "candidate", "mode": "question_sql", "created_at": FIXED_TIME, "trace_id": "trace-130", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def penalty() -> dict:
    return wrap("penalty_fact_package", "penalty-130", json.loads((FIXTURES / "penalty-fact-approved.json").read_text(encoding="utf-8")), producer="120")


def assets(*, request: dict | None = None, matched: bool = True) -> dict:
    payload = json.loads((FIXTURES / "constraint-asset-approved.json").read_text(encoding="utf-8"))
    request_id = request["payload"]["request_id"] if request else "130-query-run-130-1"
    payload["request_id"] = request_id
    if not matched:
        payload.update({"matched_records": [], "constraint_summary": {"total_matched": 0, "asset_types_covered": []}, "unmatched_items": [{"target": "脱敏事实", "reason": "无命中"}]})
    return wrap("constraint_asset_package", f"asset-{request_id}", payload, producer="000", attempt=request["envelope"]["attempt_no"] if request else 1, parents=[artifact_ref(request["envelope"])] if request else [])


def candidates(asset: dict, *fact_ids: str) -> dict:
    record = asset["payload"]["matched_records"][0]
    return {"asset_package_ref": artifact_ref(asset["envelope"]), "candidates": [{"penalty_fact_id": fact_id, "asset_record_index": 0, "source_ref": record["source_refs"][0], "proxy_expression": f"以 EAST_D001.F001 筛查处罚事实 {fact_id}"} for fact_id in fact_ids]}


def review(previous: dict, kind: str = "deepseek_review_result") -> dict:
    reviewer = "170" if kind == "deepseek_review_result" else "180"
    payload = {"reviewed_package_ref": artifact_ref(previous["envelope"]), "semantic_review_report": {"reviewer_id": reviewer, "decision": "no", "error_types": ["OBSERVABLE_MAPPING_ERROR"], "error_details": [{"reason": "扩大范围"}], "evidence_refs": [], "route_suggestion": "130"}}
    return wrap(kind, f"review-{reviewer}", payload, producer=reviewer)


class TestAgent130Contracts(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = ObservableFactMapper(ROOT)
        self.penalty = penalty()

    def observable(self, asset: dict | None = None) -> dict:
        asset = asset or assets()
        return self.mapper.build_observable_facts(self.penalty, asset, run_id="run-130", qa_id="QA-130", mapping_candidates=candidates(asset, "fact-001") if asset["payload"]["matched_records"] else None, created_at=FIXED_TIME)

    def test_task1_schema_and_hash_rejection(self) -> None:
        request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        self.assertEqual(request["payload"]["target_asset_types"], ["data_element", "single_field", "within_table", "cross_table"])
        drifted = copy.deepcopy(request); drifted["payload"]["max_rows"] = 99
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.mapper.validate_request(drifted)

    def test_real_000_shaped_input_candidate_validation_then_140_150_consumption(self) -> None:
        asset = assets()
        self.assertNotIn("penalty_fact_ids", asset["payload"]["matched_records"][0]["data"])
        self.assertNotIn("constraint_evidence_ref", asset["payload"]["matched_records"][0]["data"])
        result = self.observable(asset)
        self.mapper.validate_observable(result)
        self.assertEqual(result["payload"]["coverage_status"], "complete")
        self.assertEqual(consume_140(result), "EAST_D001.F001")
        self.assertEqual(consume_150(result), "direct")
        catalog = json.loads((ROOT / "config/v5-package-catalog.json").read_text(encoding="utf-8"))
        self.assertIn("east_observable_mapping_candidate", [item["id"] for item in catalog["packages"]])

    def test_no_candidate_downgrades_to_nonempty_partial_unobservable(self) -> None:
        result = self.mapper.build_observable_facts(self.penalty, assets(), run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        fact = result["payload"]["observable_facts"][0]
        self.assertEqual(result["payload"]["coverage_status"], "partial")
        self.assertEqual(fact["observability_type"], "unobservable")
        self.assertEqual(fact["entry_table"], "NO_EAST_ASSET")
        self.assertTrue(fact["observable_proxy"])

    def test_rejects_candidate_cross_package_unknown_record_wrong_evidence_and_empty_location(self) -> None:
        asset = assets(); good = candidates(asset, "fact-001")
        other = assets(); other["payload"]["request_id"] = "other-request"; other["envelope"]["content_hash"] = content_hash(other["envelope"], other["payload"]); wrong_package = copy.deepcopy(good); wrong_package["asset_package_ref"] = artifact_ref(other["envelope"])
        with self.assertRaisesRegex(ContractError, "CANDIDATE_ASSET_PACKAGE_MISMATCH"):
            self.mapper.build_observable_facts(self.penalty, asset, run_id="run-130", qa_id="QA-130", mapping_candidates=wrong_package, created_at=FIXED_TIME)
        missing = copy.deepcopy(good); missing["candidates"][0]["asset_record_index"] = 9
        with self.assertRaisesRegex(ContractError, "CANDIDATE_RECORD_NOT_FOUND"):
            self.mapper.build_observable_facts(self.penalty, asset, run_id="run-130", qa_id="QA-130", mapping_candidates=missing, created_at=FIXED_TIME)
        evidence = copy.deepcopy(good); evidence["candidates"][0]["source_ref"]["source_id"] = "other"
        with self.assertRaisesRegex(ContractError, "CANDIDATE_EVIDENCE_NOT_IN_RECORD"):
            self.mapper.build_observable_facts(self.penalty, asset, run_id="run-130", qa_id="QA-130", mapping_candidates=evidence, created_at=FIXED_TIME)
        empty = assets(); empty["payload"]["matched_records"][0]["data"]["field_id"] = ""; empty["envelope"]["content_hash"] = content_hash(empty["envelope"], empty["payload"])
        with self.assertRaisesRegex(ContractError, "CANDIDATE_ASSET_LOCATION_EMPTY"):
            self.mapper.build_observable_facts(self.penalty, empty, run_id="run-130", qa_id="QA-130", mapping_candidates=candidates(empty, "fact-001"), created_at=FIXED_TIME)

    def test_rejects_duplicate_or_conflicting_candidates(self) -> None:
        asset = assets(); duplicate = candidates(asset, "fact-001")
        duplicate["candidates"].append(copy.deepcopy(duplicate["candidates"][0]))
        with self.assertRaisesRegex(ContractError, "CANDIDATE_DUPLICATE_FACT"):
            self.mapper.build_observable_facts(self.penalty, asset, run_id="run-130", qa_id="QA-130", mapping_candidates=duplicate, created_at=FIXED_TIME)
        two = copy.deepcopy(self.penalty); two["payload"]["source_facts"].append(copy.deepcopy(two["payload"]["source_facts"][0])); two["payload"]["source_facts"][1]["penalty_fact_id"] = "fact-002"; two["envelope"]["content_hash"] = content_hash(two["envelope"], two["payload"])
        with self.assertRaisesRegex(ContractError, "CANDIDATE_RECORD_CONFLICT"):
            self.mapper.build_observable_facts(two, asset, run_id="run-130", qa_id="QA-130", mapping_candidates=candidates(asset, "fact-001", "fact-002"), created_at=FIXED_TIME)

    def test_review_170_180_bind_current_request_and_candidate_result(self) -> None:
        request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        previous = self.observable()
        for kind in ("deepseek_review_result", "glm_review_result"):
            outcome = self.mapper.handle_review_feedback(self.penalty, review(previous, kind), previous, request, lambda req: assets(request=req), run_id="run-130", qa_id="QA-130", attempt_no=2, candidate_builder=lambda result: candidates(result, "fact-001"), created_at=FIXED_TIME)
            self.assertEqual(outcome["request"]["payload"]["query_purpose"], "closure_expansion")
            self.assertEqual(outcome["assets"]["payload"]["request_id"], outcome["request"]["payload"]["request_id"])
            self.assertEqual(outcome["observable"]["envelope"]["supersedes_ref"], artifact_ref(previous["envelope"]))

    def test_review_rejections_and_third_attempt_block(self) -> None:
        request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        previous = self.observable(); bad = review(previous); bad["payload"]["reviewed_package_ref"]["content_hash"] = "c" * 64; bad["envelope"]["content_hash"] = content_hash(bad["envelope"], bad["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEWED_PACKAGE_REF_MISMATCH"):
            self.mapper.handle_review_feedback(self.penalty, bad, previous, request, lambda req: assets(request=req), run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)
        request2 = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)
        prior2 = self.observable(); prior2["envelope"]["attempt_no"] = 2; prior2["envelope"]["version"] = 2; prior2["envelope"]["supersedes_ref"] = {"artifact_id": "old", "version": 1, "content_hash": "b" * 64}; prior2["envelope"]["content_hash"] = content_hash(prior2["envelope"], prior2["payload"])
        outcome = self.mapper.handle_review_feedback(self.penalty, review(prior2), prior2, request2, lambda req: assets(request=req, matched=False), run_id="run-130", qa_id="QA-130", attempt_no=3, created_at=FIXED_TIME)
        self.assertEqual(outcome["observable"]["envelope"]["status"], "blocked_manual")

    def test_third_nonempty_assets_without_valid_candidate_is_blocked_manual(self) -> None:
        request2 = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)
        prior2 = self.observable()
        prior2["envelope"]["attempt_no"] = 2; prior2["envelope"]["version"] = 2
        prior2["envelope"]["supersedes_ref"] = {"artifact_id": "old", "version": 1, "content_hash": "b" * 64}
        prior2["envelope"]["content_hash"] = content_hash(prior2["envelope"], prior2["payload"])
        outcome = self.mapper.handle_review_feedback(
            self.penalty, review(prior2), prior2, request2, lambda req: assets(request=req),
            run_id="run-130", qa_id="QA-130", attempt_no=3,
            candidate_builder=lambda asset: {"asset_package_ref": artifact_ref(asset["envelope"]), "candidates": []},
            created_at=FIXED_TIME,
        )
        self.assertTrue(outcome["assets"]["payload"]["matched_records"])
        self.assertEqual(outcome["observable"]["envelope"]["status"], "blocked_manual")
        self.assertEqual(outcome["observable"]["payload"]["coverage_status"], "blocked")

    def test_rejects_review_route_request_result_parent_and_attempt_drift(self) -> None:
        request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        previous = self.observable()
        bad_route = review(previous); bad_route["payload"]["semantic_review_report"]["route_suggestion"] = "140"; bad_route["envelope"]["content_hash"] = content_hash(bad_route["envelope"], bad_route["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEW_NOT_ROUTED_TO_130"):
            self.mapper.handle_review_feedback(self.penalty, bad_route, previous, request, lambda req: assets(request=req), run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)
        def wrong_id(req: dict) -> dict:
            result = assets(request=req); result["payload"]["request_id"] = "other"; result["envelope"]["content_hash"] = content_hash(result["envelope"], result["payload"]); return result
        with self.assertRaisesRegex(ContractError, "ASSET_REQUEST_ID_MISMATCH"):
            self.mapper.handle_review_feedback(self.penalty, review(previous), previous, request, wrong_id, run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)
        def no_parent(req: dict) -> dict:
            result = assets(request=req); result["envelope"]["parent_artifact_refs"] = []; result["envelope"]["input_hashes"] = []; result["envelope"]["content_hash"] = content_hash(result["envelope"], result["payload"]); return result
        with self.assertRaisesRegex(ContractError, "ASSET_REQUEST_PARENT_MISSING"):
            self.mapper.handle_review_feedback(self.penalty, review(previous), previous, request, no_parent, run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)
        with self.assertRaisesRegex(ContractError, "ATTEMPT_LINEAGE_MISMATCH"):
            self.mapper.handle_review_feedback(self.penalty, review(previous), previous, request, lambda req: assets(request=req), run_id="run-130", qa_id="QA-130", attempt_no=1, created_at=FIXED_TIME)

    def test_rejects_run_qa_trace_drift_in_predecessor(self) -> None:
        request = self.mapper.plan_constraint_query(self.penalty, run_id="run-130", qa_id="QA-130", created_at=FIXED_TIME)
        previous = self.observable()
        for key, value in (("run_id", "other-run"), ("qa_id", "other-qa"), ("trace_id", "other-trace")):
            drifted = copy.deepcopy(request); drifted["envelope"][key] = value; drifted["envelope"]["content_hash"] = content_hash(drifted["envelope"], drifted["payload"])
            with self.assertRaisesRegex(ContractError, "PREVIOUS_REQUEST_LINEAGE_MISMATCH"):
                self.mapper.handle_review_feedback(self.penalty, review(previous), previous, drifted, lambda req: assets(request=req), run_id="run-130", qa_id="QA-130", attempt_no=2, created_at=FIXED_TIME)

    def test_manifest_issue_run_attempt_boundary_and_registry_consistency(self) -> None:
        observable = self.observable(); manifest = self.mapper.build_manifest(observable, issue_key="EAS-22")
        for wrong in ("vnext/03_构建过程层/issues/EAS-23/run-130/1/manifest.json", "vnext/03_构建过程层/issues/EAS-22/other-run/1/manifest.json", "vnext/03_构建过程层/issues/EAS-22/run-130/2/manifest.json"):
            invalid = copy.deepcopy(manifest); invalid["runtime_locator"] = wrong
            with self.assertRaisesRegex(ContractError, "MANIFEST_RUNTIME_BOUNDARY_VIOLATION"):
                self.mapper._validate_manifest(invalid, observable, "EAS-22")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); registry = ArtifactRegistry(ROOT, {"repo_root": str(root / "repo"), "runtime_root": str(root / "runtime"), "reference_root": str(root / "reference"), "reference_read_only": True}, "EAS-22", "run-130", 1)
            asset = assets(); registry.register(self.penalty["envelope"], self.penalty["payload"]); registry.register(asset["envelope"], asset["payload"])
            registry.register(observable["envelope"], observable["payload"])
            self.assertEqual(manifest["artifact_ref"], artifact_ref(registry.resolve(artifact_ref(observable["envelope"]))["envelope"]))

    def test_sanitized_runtime_probe(self) -> None:
        result = run_sanitized_probe(ROOT)
        self.mapper.validate_observable(result["transport"])
        self.assertEqual(result["transport"]["envelope"]["status"], "blocked_manual")
        self.assertTrue(all(result["summary"][key] for key in ("bad_hash_rejected", "review_170_executed", "review_180_executed", "attempt3_nonempty_assets_blocked", "stub_140_consumed", "stub_150_consumed")))


def consume_140(package: dict) -> str:
    fact = package["payload"]["observable_facts"][0]
    if not fact["entry_table"] or not fact["mapping_matrix"]:
        raise ContractError("140_CONSUMPTION_REJECTED")
    return fact["mapping_matrix"][0]["table_field_path"]


def consume_150(package: dict) -> str:
    boundary = package["payload"]["observable_facts"][0]["risk_screening_boundary"]
    if "不直接认定" not in boundary:
        raise ContractError("150_BOUNDARY_REJECTED")
    return package["payload"]["observable_facts"][0]["observability_type"]
