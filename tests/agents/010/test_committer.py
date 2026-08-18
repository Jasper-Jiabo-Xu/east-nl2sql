from __future__ import annotations

import copy
import importlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref, content_hash

committer_mod = importlib.import_module("east_v5.agents.010.committer")
FormalReleaseCommitter = committer_mod.FormalReleaseCommitter
FormalReleaseError = committer_mod.FormalReleaseError

TIME = "2026-08-18T00:00:00+00:00"


def ref(artifact_id: str, fill: str) -> dict[str, object]:
    return {"artifact_id": artifact_id, "version": 1, "content_hash": fill * 64}


def package(artifact_type: str, payload: dict, *, producer: str, mode: str, status: str = "validated", parents: list[dict] | None = None) -> dict:
    parents = parents or []
    envelope = {"artifact_id": f"010-test-{artifact_type}", "artifact_type": artifact_type, "run_id": "eas37-run", "qa_id": "QA-EAS37", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": status, "mode": mode, "created_at": TIME, "trace_id": "eas37-trace", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def formal_store() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
    CREATE TABLE formal_release_state (state_id INTEGER PRIMARY KEY, database_version TEXT NOT NULL, question_dataset_version TEXT NOT NULL);
    INSERT INTO formal_release_state VALUES (1, 'fixture-db-v1', 'fixture-question-v1');
    CREATE TABLE formal_release_ledger (idempotency_key TEXT PRIMARY KEY, candidate_hash TEXT NOT NULL, receipt_json TEXT NOT NULL);
    CREATE TABLE question_dataset (question_sql_record_id TEXT PRIMARY KEY, qa_id TEXT NOT NULL, question_sql_hash TEXT NOT NULL, release_candidate_id TEXT NOT NULL);
    CREATE TABLE EVENT_RECORD (id TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT);
    """)
    return connection


def event_material(*, duplicate: bool = False) -> tuple[dict, dict, dict]:
    approved_payload = {"schema_version": "v5.question-sql-dual-review-passed/v1", "candidate_ref": ref("candidate", "a"), "candidate_content": {"clear_question": "脱敏问题", "sql_gold": "SELECT value FROM EVENT_RECORD", "sql_explanation": {"select": "value", "from_join": "EVENT_RECORD", "where": "固定", "aggregation": "无", "sort": "无", "business_meaning": "脱敏"}, "business_event_candidates": [{"event_name": "fixture", "objective": "fixture", "objects": ["record"], "state_changes": []}], "specification_mapping": [{"spec_item": "S1", "question_fragment": "问题", "sql_fragment": "SELECT"}]}, "query_specification_package": ref("query-spec", "b"), "penalty_fact_package": ref("penalty", "c"), "observable_fact_package": ref("observable", "d"), "constraint_evidence_summary": {"tables": ["EVENT_RECORD"], "fields": ["EVENT_RECORD.value"], "data_elements": [], "relationships": [], "source_refs": ["fixture"]}, "precheck_report": {"decision": "pass", "report_hash": "e" * 64}, "deepseek_review": {"decision": "pass", "issue_level": "none", "reason": "ok", "review_hash": "f" * 64}, "glm_review": {"decision": "pass", "issue_level": "none", "reason": "ok", "review_hash": "0" * 64}, "adjudication": {"decision": "pass", "report_hash": "1" * 64}, "review_round": 1, "package_hash": "2" * 64}
    approved = package("question_sql_dual_review_passed", approved_payload, producer="110", mode="event_data")
    data_ref, orm_ref = ref("verified-data", "3"), ref("frozen-orm", "4")
    writes = [{"operation": "insert", "table_id": "EVENT_RECORD", "values": {"id": "e-1", "value": "fixture"}, "rowcount": 1}]
    if duplicate:
        writes.append({"operation": "insert", "table_id": "EVENT_RECORD", "values": {"id": "e-1", "value": "duplicate"}, "rowcount": 1})
    regression_payload = {"schema_version": "v5.regression-passed-data-orm/v1", "regression_package_id": "event-regression", "mode": "event_data", "data_package_refs": [data_ref], "orm_plan_ref": orm_ref, "question_sql_ref": artifact_ref(approved["envelope"]), "query_spec_ref": approved_payload["query_specification_package"], "execution_instances": {"params": {"fixture": "e-1"}, "operations": ["insert"]}, "sandbox_snapshot_id": "snapshot-1", "sandbox_execution_report": {"operations": writes, "write_count": len(writes), "rolled_back": False}, "sql_regression_report": {"sql_gold": "SELECT value FROM EVENT_RECORD", "row_count": 1, "positive_negative_metrics": {"positive_hits": 1, "minimum_positive_count": 1, "negative_fixture_count": 1, "minimum_negative_count": 1, "negative_hits": 0, "negative_excluded": True, "condition_coverage_passed": True, "code_value_coverage_passed": True, "passed": True}, "density_group_metrics": {"row_count": 1, "distinct_count": 1, "group_count": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}, "distinct_required": True, "passed": True}, "join_expansion_metrics": {"row_count": 1, "baseline_grain_count": 1, "actual_multiplier": 1.0, "max_multiplier": 1.0, "max_result_rows": 1, "passed": True}}, "executable_package_hash": "5" * 64, "regression_status": "passed", "regressed_at": TIME}
    regression = package("database_copy_regression", regression_payload, producer="260", mode="event_data")
    candidate_payload = {"release_candidate_id": "event-release", "release_mode": "event_data", "approved_question_sql_ref": artifact_ref(approved["envelope"]), "event_regression_passed_ref": artifact_ref(regression["envelope"]), "foundation_regression_report_ref": None, "target_database_version": "fixture-db-v1", "target_question_dataset_version": "fixture-question-v1", "idempotency_key": "event-idempotency-1", "expected_write_summary": {"orm_execution": {"insert_or_update": len(writes)}}, "package_hashes": {"question_sql": approved["envelope"]["content_hash"], "data": data_ref["content_hash"], "orm": orm_ref["content_hash"], "regression": regression["envelope"]["content_hash"]}, "resume_qa_ref": None}
    candidate = package("release_candidate", candidate_payload, producer="210", mode="event_data", parents=[artifact_ref(approved["envelope"]), artifact_ref(regression["envelope"])])
    return candidate, approved, regression


class FormalReleaseCommitterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.committer = FormalReleaseCommitter(ROOT)

    def test_event_commit_is_atomic_idempotent_and_emits_consumable_receipt(self) -> None:
        candidate, approved, regression = event_material()
        connection = formal_store()
        receipt = self.committer.commit(candidate, connection, approved_question_sql=approved, event_regression=regression)
        self.assertEqual((receipt["payload"]["commit_status"], receipt["payload"]["database_version_after"]), ("committed", "fixture-db-v2"))
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM EVENT_RECORD").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM question_dataset").fetchone()[0], 1)
        self.assertEqual(self.committer.commit(candidate, connection), receipt)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM EVENT_RECORD").fetchone()[0], 1)
        self.assertEqual(receipt["payload"]["release_candidate_ref"], artifact_ref(candidate["envelope"]))

    def test_event_version_conflict_hash_drift_and_failure_each_leave_formal_store_unchanged(self) -> None:
        for kind in ("version", "hash", "write"):
            with self.subTest(kind=kind):
                candidate, approved, regression = event_material(duplicate=kind == "write")
                if kind == "version":
                    candidate["payload"]["target_database_version"] = "fixture-db-v9"
                if kind == "hash":
                    candidate["payload"]["package_hashes"]["orm"] = "0" * 64
                candidate["envelope"]["content_hash"] = content_hash(candidate["envelope"], candidate["payload"])
                connection = formal_store()
                with self.assertRaises(FormalReleaseError):
                    self.committer.commit(candidate, connection, approved_question_sql=approved, event_regression=regression)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM EVENT_RECORD").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM question_dataset").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT database_version FROM formal_release_state WHERE state_id = 1").fetchone()[0], "fixture-db-v1")

    def test_foundation_commit_consumes_real_260_frozen_batch(self) -> None:
        source = importlib.import_module("tests.agents.260.test_regression")
        case = source.FoundationRegressionTests(); case.setUp()
        try:
            copy_db, regression_formal = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
            for db in (copy_db, regression_formal):
                db.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
            report = source.regression.run_foundation_regression(ROOT, case.task, case.closure, case.verified, case.snapshot, copy_db, regression_formal, set())
            candidate = importlib.import_module("east_v5.agents.210.scheduler").DataStageCoordinator(ROOT).build_foundation_release(case.task, report)
            connection = formal_store()
            receipt = self.committer.commit(candidate, connection, foundation_task=case.task, foundation_regression=report)
            self.assertEqual(receipt["payload"]["commit_status"], "committed")
            self.assertEqual(receipt["payload"]["question_sql_record_id"], None)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 1)
            self.assertEqual(self.committer.commit(candidate, connection), receipt)
        finally:
            case.tearDown()

    def test_only_sql_execution_error_is_routed_to_110(self) -> None:
        payload = {"schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data", "input_data_refs": [ref("data", "a")], "input_orm_ref": ref("orm", "b"), "sandbox_snapshot_id": "snapshot", "failure_details": {"error_code": "SQL_EXECUTION_ERROR", "error_stage": "sql_execution", "error_location": "fixture", "expected_values": [], "actual_values": [], "sql_error_detail": {"sql_text": "SELECT", "error_code": "SQLITE_ERROR", "error_message": "fixture"}, "regression_metrics": {}}, "route_target": "010", "retry_count": 1}
        feedback = package("sql_regression_failed_feedback", payload, producer="260", mode="event_data")
        self.assertEqual(self.committer.route_sql_regression_failure(feedback)["target"], "110")
        feedback["payload"]["failure_details"]["error_code"] = "DATA_VALUE_ERROR"
        feedback["envelope"]["content_hash"] = content_hash(feedback["envelope"], feedback["payload"])
        with self.assertRaisesRegex(FormalReleaseError, "010_SQL_FEEDBACK_ROUTE_REJECTED"):
            self.committer.route_sql_regression_failure(feedback)

    def test_single_entry_points_and_status_report_are_fixed(self) -> None:
        payload = json.loads((ROOT / "fixtures/penalty/matched.json").read_text(encoding="utf-8"))
        source = self.committer.build_penalty_source_package(payload, run_id="source-run", trace_id="source-trace", created_at=TIME)
        self.assertEqual(self.committer.start_question_sql(source)["target"], "110")
        request = {"foundation_task_request": {"foundation_mode": "initial"}, "current_database_version": "fixture-db-v1", "run_id": "run", "trace_id": "trace", "created_at": TIME, "parent_artifact_refs": []}
        self.assertEqual(self.committer.start_foundation(request)["target"], "210")
        status = self.committer.status_report([{"agent": "210", "status": "blocked_manual", "attempt_no": 3, "blocked_reason": "fixture"}])
        self.assertEqual(status["manual_review"][0]["agent"], "210")


if __name__ == "__main__":
    unittest.main()
