"""EAS-41: fixed-entry full release acceptance on sanitized SQLite stores."""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path


data = importlib.import_module("tests.integration.data.test_event_dual_path")
foundation = importlib.import_module("tests.integration.foundation.test_foundation_e2e")
qs = importlib.import_module("tests.integration.qs.test_question_sql_e2e")
artifacts = importlib.import_module("east_v5.artifacts")
committer_mod = importlib.import_module("east_v5.agents.010.committer")
coordinator_mod = importlib.import_module("east_v5.agents.210.scheduler")
foundation_fixture = importlib.import_module("east_v5.agents.010.sanitized_foundation_fixture")


def _formal_store(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript("""
    CREATE TABLE formal_release_state (
        state_id INTEGER PRIMARY KEY, database_version TEXT NOT NULL,
        question_dataset_version TEXT NOT NULL
    );
    INSERT INTO formal_release_state VALUES (1, 'fixture-db-v1', 'fixture-question-v1');
    CREATE TABLE formal_release_ledger (
        idempotency_key TEXT PRIMARY KEY, candidate_hash TEXT NOT NULL,
        receipt_json TEXT NOT NULL
    );
    CREATE TABLE question_dataset (
        question_sql_record_id TEXT PRIMARY KEY, qa_id TEXT NOT NULL,
        question_sql_hash TEXT NOT NULL, release_candidate_id TEXT NOT NULL
    );
    CREATE TABLE FIXTURE_T001 (F001 TEXT, F002 TEXT);
    CREATE TABLE FIXTURE_T002 (PK001 TEXT);
    CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT);
    """)
    connection.commit()
    return connection


class FullReleaseIntegrationTests(unittest.TestCase):
    def test_fixed_entry_event_and_foundation_release_are_real_and_idempotent(self) -> None:
        """010→QS→210→260(copy)→210→010 for event and Foundation fixtures."""
        # 010 is the fixed user entry for the same sanitized source used by QS.
        source_chain = qs.build_full_chain()
        committer = committer_mod.FormalReleaseCommitter(qs.ROOT)
        source = committer.build_penalty_source_package(
            source_chain["source"], run_id=qs.RUN, trace_id=qs.TRACE, created_at=qs.TIME,
        )
        self.assertEqual(committer.start_question_sql(source)["target"], "110")

        # EAS-68's real 140→150→160→170/180→110 output supplies binding v1.
        event_case = data.EventDualPathIntegrationTests("test_dual_path_chain_260_copy_regression_and_210_release_candidate")
        event_case.setUp()
        try:
            approved, reviewed, context, binding, structure, operation, _bound, verified, restricted, frozen = event_case._chain()
            self.assertEqual(binding["envelope"]["producer_id"], "110")
            self.assertEqual(context["payload"]["query_parameter_binding_ref"], artifacts.artifact_ref(binding["envelope"]))
            dispatch = event_case.coordinator.join_event_validations(
                approved, reviewed, context, structure, operation, restricted, verified, frozen, binding,
            )
            self.assertEqual(dispatch["target"], "260")

            with tempfile.TemporaryDirectory() as directory:
                formal_path = Path(directory) / "formal.sqlite"
                connection = _formal_store(formal_path)
                connection.close()
                before_copy_regression = formal_path.read_bytes()
                regression = data.regression_mod.DatabaseCopyRegression(data.ROOT).run_event(
                    verified, frozen, event_case.snapshot, reviewed, context, binding, event_case.spec, formal_path,
                )
                self.assertEqual(regression["payload"]["regression_status"], "passed")
                self.assertEqual(regression["payload"]["execution_instances"]["query_binding_names"], ["account_value"])
                self.assertEqual(formal_path.read_bytes(), before_copy_regression)

                candidate = event_case.coordinator.build_event_release(
                    approved, regression, target_database_version="fixture-db-v1",
                    target_question_dataset_version="fixture-question-v1",
                )
                connection = sqlite3.connect(formal_path)
                receipt = committer.commit(candidate, connection, approved_question_sql=approved, event_regression=regression)
                self.assertEqual(receipt["payload"]["commit_status"], "committed")
                self.assertEqual(receipt["payload"]["committed_package_hash"], candidate["envelope"]["content_hash"])
                self.assertEqual(committer.commit(candidate, connection), receipt)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM question_dataset").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT database_version FROM formal_release_state WHERE state_id = 1").fetchone()[0], "fixture-db-v2")
                connection.close()
        finally:
            for directory in event_case._tmp_dirs:
                directory.cleanup()

        # Foundation expansion consumes the real 260 FOUNDATION_REQUIRED feedback
        # and retains resume_qa_ref through 210→220→241→242→260→210→010.
        expansion_case = foundation.FoundationE2ETests("test_event_missing_object_state_routes_to_explicit_foundation_task")
        expansion_case.setUp()
        try:
            expansion_case.test_event_missing_object_state_routes_to_explicit_foundation_task()
        finally:
            expansion_case.tearDown()
            expansion_case.doCleanups()

        # The real Foundation initial 260 report is committed by 010 once and
        # replayed idempotently in a separate sanitized formal store.
        material = foundation_fixture.build_sanitized_foundation_260_material(data.ROOT)
        candidate = coordinator_mod.DataStageCoordinator(data.ROOT).build_foundation_release(material.task, material.regression_report)
        with tempfile.TemporaryDirectory() as directory:
            formal_path = Path(directory) / "foundation-formal.sqlite"
            connection = _formal_store(formal_path)
            receipt = committer.commit(candidate, connection, foundation_task=material.task, foundation_regression=material.regression_report)
            self.assertEqual(receipt["payload"]["commit_status"], "committed")
            self.assertEqual(receipt["payload"]["question_sql_record_id"], None)
            self.assertEqual(committer.commit(candidate, connection), receipt)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 1)
            connection.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
