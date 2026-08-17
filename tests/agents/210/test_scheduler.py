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

    def _real_event_closures(self) -> tuple[dict[str, object], dict[str, object], dict[str, object], object]:
        """Build the actual 220→230 inputs from one 210 reviewed package."""
        approved = dual_review()
        approved["payload"]["candidate_content"]["sql_gold"] = (
            "SELECT A.STATUS FROM FIXTURE_ACCOUNT A JOIN FIXTURE_CUSTOMER C "
            "ON A.CUSTOMER_ID = C.ID"
        )
        approved["payload"]["candidate_content"]["specification_mapping"] = [
            {"spec_item": "状态", "question_fragment": "开户", "sql_fragment": "FIXTURE_ACCOUNT.STATUS"},
            {"spec_item": "账户客户", "question_fragment": "开户", "sql_fragment": "FIXTURE_ACCOUNT.CUSTOMER_ID"},
            {"spec_item": "客户标识", "question_fragment": "开户", "sql_fragment": "FIXTURE_CUSTOMER.ID"},
        ]
        approved["payload"]["candidate_content"]["business_event_candidates"][0]["state_changes"] = [
            "FIXTURE_ACCOUNT.CUSTOMER_ID->FIXTURE_CUSTOMER.ID",
        ]
        approved["payload"]["package_hash"] = sha256({key: value for key, value in approved["payload"].items() if key != "package_hash"})
        approved["envelope"]["content_hash"] = content_hash(approved["envelope"], approved["payload"])
        started = self.coordinator.begin_event(approved)
        reviewed = started["reviewed_question_sql"]
        closure_tests = test_module("agents.220.test_closure")
        _, first_asset, second_asset = closure_tests.event_results(reviewed)
        closure_mod = importlib.import_module("east_v5.agents.220.closure")
        structure = closure_mod.build_event_closure(reviewed, first_asset, second_asset)
        operation_builder = importlib.import_module("east_v5.agents.230.builder").OperationClosureBuilder(ROOT)
        operation = operation_builder.build(structure)
        return started, structure, operation, operation_builder

    def test_event_start_produces_distinct_reviewed_artifact_and_only_220_dispatch(self) -> None:
        approved = dual_review()
        before = copy.deepcopy(approved)
        result = self.coordinator.begin_event(approved)
        reviewed = result["reviewed_question_sql"]
        self.assertEqual(approved, before)
        self.assertEqual((reviewed["envelope"]["artifact_type"], reviewed["envelope"]["producer_id"]), ("reviewed_question_sql", "210"))
        self.assertNotEqual(reviewed["envelope"]["artifact_id"], approved["envelope"]["artifact_id"])
        self.assertEqual([item["target"] for item in result["dispatches"]], ["220"])
        self.assertEqual(result["dispatches"][0]["input_ref"], artifact_ref(reviewed["envelope"]))

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
        with self.assertRaisesRegex(ContractError, "210_THIRD_ATTEMPT_NOT_MANUAL"):
            self.coordinator.route_feedback(feedback("DATA_VALUE_ERROR", "241", attempt=3))
        with self.assertRaisesRegex(ContractError, "210_MANUAL_REVIEW_ATTEMPT_INVALID"):
            self.coordinator.route_feedback(feedback("MANUAL_REVIEW_REQUIRED", "manual", attempt=1))

    def test_event_is_ordered_220_then_real_230_then_241_251_252_and_260_010(self) -> None:
        started, structure, operation, operation_builder = self._real_event_closures()
        reviewed = started["reviewed_question_sql"]
        self.assertEqual([item["target"] for item in started["dispatches"]], ["220"])
        operation_dispatch = self.coordinator.dispatch_event_operation(reviewed, structure)
        self.assertEqual(operation_dispatch["target"], "230")
        self.assertEqual(operation_dispatch["structure_closure_ref"], artifact_ref(structure["envelope"]))
        closure_mod = importlib.import_module("east_v5.agents.220.closure")
        self.assertEqual(closure_mod.consume_downstream_stub("230", structure)["consumer"], "230")
        branches = self.coordinator.dispatch_event_branches(reviewed, structure, operation)
        self.assertEqual([item["target"] for item in branches], ["241", "251"])
        self.assertEqual(operation_builder.consume_downstream_stub("241", operation)["consumer"], "241")
        data_builder = importlib.import_module("east_v5.agents.241.generator").BoundDataGenerator(ROOT)
        bound = data_builder.build_bound_data(structure, operation_closure=operation, created_at=TIME)
        self.assertEqual(bound["payload"]["structure_closure_ref"], artifact_ref(structure["envelope"]))
        orm_generator = importlib.import_module("east_v5.agents.251.generator").RestrictedOrmGenerator(ROOT)
        restricted = orm_generator.build(structure, operation)
        orm_validator = importlib.import_module("east_v5.agents.252.validator").OrmValidator(ROOT)
        frozen = orm_validator.freeze_orm(restricted, structure, operation)
        self.assertEqual(frozen["envelope"]["producer_id"], "252")

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

    def test_event_rejects_unknown_unbound_and_blocked_manual_closures(self) -> None:
        started, structure, operation, _ = self._real_event_closures()
        reviewed = started["reviewed_question_sql"]
        unknown = copy.deepcopy(structure)
        unknown["payload"]["unexpected"] = True
        unknown["envelope"]["content_hash"] = content_hash(unknown["envelope"], unknown["payload"])
        with self.assertRaisesRegex(ContractError, "210_EVENT_STRUCTURE_REJECTED"):
            self.coordinator.dispatch_event_operation(reviewed, unknown)

        unbound = copy.deepcopy(operation)
        unbound["envelope"]["parent_artifact_refs"] = [ref("other-structure", "f")]
        unbound["envelope"]["input_hashes"] = ["f" * 64]
        unbound["envelope"]["content_hash"] = content_hash(unbound["envelope"], unbound["payload"])
        with self.assertRaisesRegex(ContractError, "210_EVENT_OPERATION_LINEAGE_REJECTED"):
            self.coordinator.dispatch_event_branches(reviewed, structure, unbound)

        blocked = copy.deepcopy(operation)
        blocked["envelope"]["status"] = "blocked_manual"
        blocked["envelope"]["content_hash"] = content_hash(blocked["envelope"], blocked["payload"])
        with self.assertRaisesRegex(ContractError, "210_EVENT_CLOSURE_STATE_REJECTED"):
            self.coordinator.dispatch_event_branches(reviewed, structure, blocked)

    def test_foundation_is_ordered_220_then_real_241_then_260_and_010(self) -> None:
        foundation_tests = test_module("agents.260.test_regression")
        case = foundation_tests.FoundationRegressionTests()
        case.setUp()
        try:
            started = self.coordinator.begin_foundation(copy.deepcopy(case.task["payload"]), run_id="foundation-run", trace_id="foundation-trace", created_at=TIME, parents=case.task["envelope"]["parent_artifact_refs"])
            self.assertEqual([item["target"] for item in started["dispatches"]], ["220"])
            self.assertNotIn("251", str(started))
            data_dispatch = self.coordinator.dispatch_foundation_data(case.task, case.closure, case.profile)
            self.assertEqual(data_dispatch["target"], "241")
            self.assertEqual(data_dispatch["structure_closure_ref"], artifact_ref(case.closure["envelope"]))
            self.assertEqual(foundation_tests.closure_mod.consume_downstream_stub("241", case.closure)["consumer"], "241")
            bound = foundation_tests.generator_mod.BoundDataGenerator(ROOT).build_bound_data(
                case.closure, foundation_task_package=case.task, foundation_profile=case.profile,
                snapshot=case.snapshot, created_at=TIME,
            )
            self.assertEqual(bound["payload"]["structure_closure_ref"], data_dispatch["structure_closure_ref"])
            regression_dispatch = self.coordinator.dispatch_foundation_regression(case.task, case.closure, case.verified)
            self.assertEqual(regression_dispatch["target"], "260")
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

    def test_foundation_rejects_forged_closure_binding_context_attempt_and_target_drift(self) -> None:
        foundation_tests = test_module("agents.260.test_regression")
        case = foundation_tests.FoundationRegressionTests()
        case.setUp()
        try:
            forged = copy.deepcopy(case.closure)
            forged["envelope"]["content_hash"] = "f" * 64
            with self.assertRaisesRegex(ContractError, "210_FOUNDATION_STRUCTURE_REJECTED"):
                self.coordinator.dispatch_foundation_regression(case.task, forged, case.verified)

            wrong_binding = copy.deepcopy(case.verified)
            wrong_binding["payload"]["validated_data_package"]["structure_closure_ref"] = ref("other-closure", "f")
            wrong_binding["envelope"]["content_hash"] = content_hash(wrong_binding["envelope"], wrong_binding["payload"])
            with self.assertRaisesRegex(ContractError, "210_FOUNDATION_DATA_BINDING_DRIFT"):
                self.coordinator.dispatch_foundation_regression(case.task, case.closure, wrong_binding)

            late_attempt = copy.deepcopy(case.verified)
            late_attempt["envelope"]["attempt_no"] = 2
            late_attempt["envelope"]["content_hash"] = content_hash(late_attempt["envelope"], late_attempt["payload"])
            with self.assertRaisesRegex(ContractError, "210_ATTEMPT_MISMATCH"):
                self.coordinator.dispatch_foundation_regression(case.task, case.closure, late_attempt)

            import sqlite3
            copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
            for connection in (copy_db, formal_db):
                connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
            report = foundation_tests.regression.run_foundation_regression(ROOT, case.task, case.closure, case.verified, case.snapshot, copy_db, formal_db, set())

            for field, value in (("run_id", "other-run"), ("qa_id", "QA-other"), ("trace_id", "other-trace")):
                with self.subTest(context_field=field):
                    cross_context = copy.deepcopy(report)
                    cross_context["envelope"][field] = value
                    cross_context["envelope"]["content_hash"] = content_hash(cross_context["envelope"], cross_context["payload"])
                    with self.assertRaisesRegex(ContractError, "210_CONTEXT_MISMATCH"):
                        self.coordinator.build_foundation_release(case.task, cross_context)

            late_report = copy.deepcopy(report)
            late_report["envelope"]["attempt_no"] = 2
            late_report["envelope"]["content_hash"] = content_hash(late_report["envelope"], late_report["payload"])
            with self.assertRaisesRegex(ContractError, "210_ATTEMPT_MISMATCH"):
                self.coordinator.build_foundation_release(case.task, late_report)

            wrong_database = copy.deepcopy(report)
            wrong_database["payload"]["target_database_version"] = "wrong-db-v9"
            wrong_database["payload"]["report_hash"] = sha256({key: value for key, value in wrong_database["payload"].items() if key != "report_hash"})
            wrong_database["envelope"]["content_hash"] = content_hash(wrong_database["envelope"], wrong_database["payload"])
            with self.assertRaisesRegex(ContractError, "210_FOUNDATION_TARGET_DATABASE_VERSION_DRIFT"):
                self.coordinator.build_foundation_release(case.task, wrong_database)

            duplicate_data = copy.deepcopy(report)
            duplicate_data["payload"]["validated_data_package_refs"].append(copy.deepcopy(duplicate_data["payload"]["validated_data_package_refs"][0]))
            duplicate_data["payload"]["report_hash"] = sha256({key: value for key, value in duplicate_data["payload"].items() if key != "report_hash"})
            duplicate_data["envelope"]["content_hash"] = content_hash(duplicate_data["envelope"], duplicate_data["payload"])
            with self.assertRaisesRegex(ContractError, "210_FOUNDATION_REGRESSION_LINEAGE_REJECTED"):
                self.coordinator.build_foundation_release(case.task, duplicate_data)

            forged_data = copy.deepcopy(report)
            forged_data["payload"]["validated_data_package_refs"] = [ref("forged-data", "f")]
            forged_data["payload"]["report_hash"] = sha256({key: value for key, value in forged_data["payload"].items() if key != "report_hash"})
            forged_data["envelope"]["content_hash"] = content_hash(forged_data["envelope"], forged_data["payload"])
            with self.assertRaisesRegex(ContractError, "210_FOUNDATION_REGRESSION_LINEAGE_REJECTED"):
                self.coordinator.build_foundation_release(case.task, forged_data)

            forged_evidence = copy.deepcopy(report)
            forged_evidence["payload"]["data_validation_evidence_refs"] = [ref("forged-evidence", "e")]
            forged_evidence["payload"]["report_hash"] = sha256({key: value for key, value in forged_evidence["payload"].items() if key != "report_hash"})
            forged_evidence["envelope"]["content_hash"] = content_hash(forged_evidence["envelope"], forged_evidence["payload"])
            with self.assertRaisesRegex(ContractError, "210_FOUNDATION_REGRESSION_LINEAGE_REJECTED"):
                self.coordinator.build_foundation_release(case.task, forged_evidence)
            copy_db.close(); formal_db.close()
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
