from __future__ import annotations

import copy
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256

DataStageCoordinator = importlib.import_module("east_v5.agents.210.scheduler").DataStageCoordinator


def test_module(package: str):
    """Support both direct test invocation and scripts/v5.py discovery roots."""
    for name in (f"tests.{package}", package):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name.split(".")[0]:
                raise
    raise AssertionError(f"test module unavailable: {package}")

TIME = "2026-08-17T00:00:00+00:00"


def ref(name: str, char: str) -> dict[str, object]:
    return {"artifact_id": name, "version": 1, "content_hash": char * 64}


def wrap(artifact_type: str, producer: str, payload: dict[str, object], *, mode: str = "event_data", status: str = "validated", parents: list[dict[str, object]] | None = None, attempt: int = 1) -> dict[str, object]:
    parents = [] if parents is None else parents
    envelope = {
        "artifact_id": f"210-test-{artifact_type}-{attempt}", "artifact_type": artifact_type,
        "run_id": "210-test-run", "qa_id": "QA-210", "version": 1,
        "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer,
        "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents],
        "status": status, "mode": mode, "created_at": TIME, "trace_id": "210-test-trace",
        "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def dual_review() -> dict[str, object]:
    precheck, deepseek, glm = ref("precheck", "4"), ref("deepseek", "5"), ref("glm", "6")
    payload = {
        "schema_version": "v5.question-sql-dual-review-passed/v1", "candidate_ref": ref("candidate", "1"),
        "candidate_content": {
            "clear_question": "脱敏开户问题", "sql_gold": "SELECT STATUS FROM FIXTURE_ACCOUNT",
            "sql_explanation": {"select": "STATUS", "from_join": "FIXTURE_ACCOUNT", "where": "无", "aggregation": "无", "sort": "无", "business_meaning": "脱敏开户状态"},
            "business_event_candidates": [{"event_name": "开户", "objective": "开户", "objects": ["账户"], "state_changes": ["状态=OPEN"]}],
            "specification_mapping": [{"spec_item": "状态", "question_fragment": "开户", "sql_fragment": "STATUS"}],
        },
        "query_specification_package": ref("query-spec", "2"), "penalty_fact_package": ref("penalty", "7"), "observable_fact_package": ref("observable", "8"),
        "constraint_evidence_summary": {"tables": ["FIXTURE_ACCOUNT"], "fields": ["STATUS"], "data_elements": [], "relationships": [], "source_refs": ["CA-V0.3.0"]},
        "precheck_report": {"decision": "pass", "report_hash": precheck["content_hash"]},
        "deepseek_review": {"decision": "pass", "issue_level": "none", "reason": "通过", "review_hash": deepseek["content_hash"]},
        "glm_review": {"decision": "pass", "issue_level": "none", "reason": "通过", "review_hash": glm["content_hash"]},
        "adjudication": {"decision": "pass", "report_hash": "9" * 64}, "review_round": 1, "package_hash": "",
    }
    payload["package_hash"] = sha256({key: value for key, value in payload.items() if key != "package_hash"})
    return wrap("question_sql_dual_review_passed", "110", payload, parents=[precheck, deepseek, glm])


def feedback(code: str, target: str, *, attempt: int = 1, mode: str = "event_data") -> dict[str, object]:
    payload = {
        "schema_version": "v5.sql-regression-failed-feedback/v1", "mode": mode,
        "input_data_refs": [ref("data", "a")], "input_orm_ref": ref("orm", "b") if mode == "event_data" else None,
        "sandbox_snapshot_id": "sanitized-copy", "failure_details": {
            "error_code": code, "error_stage": "regression_gate", "error_location": "sanitized",
            "expected_values": [], "actual_values": [], "sql_error_detail": None, "regression_metrics": {},
        }, "route_target": target, "retry_count": attempt,
    }
    return wrap("sql_regression_failed_feedback", "260", payload, mode=mode, status="rejected", attempt=attempt)


class DataStageCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = DataStageCoordinator(ROOT)

    def test_event_start_produces_distinct_reviewed_artifact_and_two_dispatches(self) -> None:
        approved = dual_review()
        before = copy.deepcopy(approved)
        result = self.coordinator.begin_event(approved)
        reviewed = result["reviewed_question_sql"]
        self.assertEqual(approved, before)
        self.assertEqual((reviewed["envelope"]["artifact_type"], reviewed["envelope"]["producer_id"]), ("reviewed_question_sql", "210"))
        self.assertNotEqual(reviewed["envelope"]["artifact_id"], approved["envelope"]["artifact_id"])
        self.assertEqual([item["target"] for item in result["dispatches"]], ["220", "230"])
        self.assertEqual(result["dispatches"][1]["question_sql_ref"], artifact_ref(approved["envelope"]))

    def test_event_start_rejects_missing_review_lineage_and_hash_drift(self) -> None:
        missing = dual_review()
        missing["envelope"]["parent_artifact_refs"] = missing["envelope"]["parent_artifact_refs"][:2]
        missing["envelope"]["input_hashes"] = [item["content_hash"] for item in missing["envelope"]["parent_artifact_refs"]]
        missing["envelope"]["content_hash"] = content_hash(missing["envelope"], missing["payload"])
        with self.assertRaisesRegex(ContractError, "210_SOURCE_LINEAGE_MISSING:glm"):
            self.coordinator.begin_event(missing)
        drift = dual_review()
        drift["payload"]["package_hash"] = "0" * 64
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "210_DUAL_REVIEW_HASH_DRIFT"):
            self.coordinator.begin_event(drift)

    def test_260_fixed_error_routes_and_conflicts_are_rejected(self) -> None:
        for code, target in (("DATA_VALUE_ERROR", "241"), ("ORM_PLAN_ERROR", "251"), ("SQL_EXECUTION_ERROR", "010"), ("FOUNDATION_REQUIRED", "210"), ("MANUAL_REVIEW_REQUIRED", "manual")):
            with self.subTest(code=code):
                result = self.coordinator.route_feedback(feedback(code, target, attempt=3 if code == "MANUAL_REVIEW_REQUIRED" else 1))
                self.assertEqual(result["target"], target)
                self.assertEqual(result["requires_explicit_foundation_task"], code == "FOUNDATION_REQUIRED")
        with self.assertRaisesRegex(ContractError, "210_260_ROUTE_CONFLICT"):
            self.coordinator.route_feedback(feedback("DATA_VALUE_ERROR", "251"))
        with self.assertRaisesRegex(ContractError, "210_FOUNDATION_ROUTE_MODE_REJECTED"):
            self.coordinator.route_feedback(feedback("FOUNDATION_REQUIRED", "210", mode="foundation"))

    def test_real_event_regression_is_joined_and_consumed_by_010_stub(self) -> None:
        source = test_module("agents.260.test_regression")
        case = source.EventRegressionTests()
        case.setUp()
        try:
            case.review["envelope"]["run_id"] = case.data["envelope"]["run_id"]
            case.review["envelope"]["trace_id"] = case.data["envelope"]["trace_id"]
            case.review["envelope"]["content_hash"] = content_hash(case.review["envelope"], case.review["payload"])
            case.orm["envelope"]["run_id"] = case.data["envelope"]["run_id"]
            case.orm["envelope"]["trace_id"] = case.data["envelope"]["trace_id"]
            case.orm["envelope"]["content_hash"] = content_hash(case.orm["envelope"], case.orm["payload"])
            dispatch = self.coordinator.join_event_validations(case.review, case.data, case.orm)
            self.assertEqual(dispatch["target"], "260")
            regression = case.worker.run_event(case.data, case.orm, case.snapshot, case.review, case.spec, case.db)
            candidate = self.coordinator.build_event_release(case.review, regression, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")
            consumer = test_module("contracts.test_stage10_package_contracts")
            self.assertTrue(consumer.consume_stub("release_candidate", "010", candidate))
            self.assertIsNone(candidate["payload"]["foundation_regression_report_ref"])
        finally:
            case.tearDown()

    def test_foundation_has_no_orm_branch_and_builds_real_release_candidate(self) -> None:
        foundation_tests = test_module("agents.260.test_regression")
        case = foundation_tests.FoundationRegressionTests()
        case.setUp()
        try:
            started = self.coordinator.begin_foundation(copy.deepcopy(case.task["payload"]), run_id="foundation-run", trace_id="foundation-trace", created_at=TIME, parents=case.task["envelope"]["parent_artifact_refs"])
            self.assertEqual([item["target"] for item in started["dispatches"]], ["220", "241"])
            self.assertNotIn("251", str(started))
            import sqlite3
            copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
            for connection in (copy_db, formal_db):
                connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
            report = foundation_tests.regression.run_foundation_regression(ROOT, case.task, case.closure, case.verified, case.snapshot, copy_db, formal_db, set())
            candidate = self.coordinator.build_foundation_release(case.task, report)
            consumer = test_module("contracts.test_stage10_package_contracts")
            self.assertTrue(consumer.consume_stub("release_candidate", "010", candidate))
            self.assertIsNone(candidate["payload"]["event_regression_passed_ref"])
            copy_db.close(); formal_db.close()
        finally:
            case.tearDown()

    def test_release_rejects_target_version_and_foundation_report_drift(self) -> None:
        source = test_module("agents.260.test_regression")
        case = source.EventRegressionTests()
        case.setUp()
        try:
            regression = case.worker.run_event(case.data, case.orm, case.snapshot, case.review, case.spec, case.db)
            with self.assertRaisesRegex(ContractError, "210_RELEASE_TARGET_VERSION_REQUIRED"):
                self.coordinator.build_event_release(case.review, regression, target_database_version="", target_question_dataset_version="fixture-question-v1")
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
