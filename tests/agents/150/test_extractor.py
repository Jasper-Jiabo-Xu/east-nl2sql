from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from east_v5.agents.east_150 import PendingPrecheckBuilder
from east_v5.agents.east_150.probe import run_sanitized_probe
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
FIXED_TIME = "2026-08-16T00:00:00+00:00"


def wrap(artifact_type: str, artifact_id: str, payload: dict, *, producer: str, attempt: int = 1, version: int = 1, parents: list[dict] | None = None, trace_id: str = "trace-150", run_id: str = "run-150", qa_id: str = "QA-150") -> dict:
    parents = parents or []
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": run_id, "qa_id": qa_id, "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [x["content_hash"] for x in parents], "status": "candidate", "mode": "question_sql", "created_at": FIXED_TIME, "trace_id": trace_id, "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def query_spec() -> dict:
    """Desensitized QUERY-SPECIFICATION-PACKAGE fixture (140 output)."""
    payload = {
        "query_spec_id": "qspec-042",
        "penalty_fact_package_ref": {"artifact_id": "penalty-150", "version": 1, "content_hash": "0" * 64},
        "observable_fact_package_ref": {"artifact_id": "observable-150", "version": 1, "content_hash": "0" * 64},
        "query_goal": "脱敏风险筛查",
        "must_preserve_fact_refs": ["fact-001"],
        "main_object_and_grain": {"main_object": "脱敏机构", "grain": "一条EAST业务记录"},
        "query_entry": {"entry_table": "EAST_D001", "entry_conditions": [{"field_id": "F001", "operator": "=", "value": "脱敏值"}]},
        "related_objects_and_path": [{"object_name": "关联表", "table_id": "EAST_D002", "relation_type": "LEFT JOIN", "join_fields": [{"from_field": "EAST_D001.F002", "to_field": "EAST_D002.F002"}]}],
        "filters_and_evidence": [{"field_id": "F001", "operator": "=", "value": "脱敏值", "evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}],
        "return_fields": [{"field_id": "F001", "display_name": "脱敏字段", "source_table": "EAST_D001"}],
        "aggregation_dedup_sort_time": {"group_by_fields": ["EAST_D001.F001"], "distinct_required": False, "order_by": [{"field_id": "F001", "direction": "ASC"}], "time_window": {"field_id": "F003", "window_type": "fixed"}},
        "observability_boundary": {"answerable": ["脱敏风险筛查"], "unanswerable": ["具体处罚金额"]},
        "expected_result_shape": {"row_grain": "一条EAST业务记录", "column_set": ["F001", "F002"], "aggregation_shape": "group_by"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001", "F002", "F003"]}, {"table_id": "EAST_D002", "allowed_fields": ["F002"]}]},
        "minimum_positive_count": 1,
        "minimum_negative_count": 1,
        "condition_coverage": [{"predicate": "F001 = 脱敏值", "positive_types": ["合规"], "negative_types": ["违规"]}],
        "code_value_coverage": [{"field_id": "F001", "target_code_values": ["A", "B"]}],
        "expected_row_group_count": {"minimum": 1, "target": 10, "tolerance_range": {"low": 1, "high": 100}},
        "join_expansion_limit": {"max_multiplier": 2.0, "max_result_rows": 1000},
        "query_specification_package_schema_version": "query-specification-v1",
    }
    return wrap("query_specification_package", "qspec-150", payload, producer="140")


def llm_candidate_sql() -> dict:
    """Desensitized LLM-extracted SQL for tests."""
    return {
        "candidate_sql": "SELECT F001, F002 FROM EAST_D001 WHERE F001 = :param1",
        "sql_parameters": [
            {"param_name": "param1", "param_type": "string", "param_value": "脱敏值"},
        ],
    }


def precheck_feedback(previous: dict, *, attempt_no: int = 1, decision: str = "fail") -> dict:
    """Simulate 160 producing PRECHECK-FAILED-FEEDBACK."""
    payload = {
        "schema_version": "v5.precheck-failed-feedback/v1",
        "pending_precheck_package_ref": artifact_ref(previous["envelope"]),
        "decision": decision,
        "failed_checks": [
            {
                "check_id": "chk-scope",
                "check_type": "scope",
                "error_code": "FIELD_NOT_IN_SCOPE",
                "error_details": "脱敏预检失败：字段不在范围内",
                "offending_segment": "EAST_D001.F999",
            }
        ],
        "attempt_no": attempt_no,
        "retry_eligible": decision == "fail" and attempt_no < 3,
    }
    return wrap("precheck_failed_feedback", f"feedback-160-{attempt_no}", payload, producer="160", attempt=attempt_no)


def _consume_160_stub(package: dict) -> str:
    """Stub: simulate 160 consuming the pending-precheck package."""
    ppre = package["payload"]
    if not ppre["candidate_sql"] or not ppre["precheck_expectations"]["expected_checks"]:
        raise ContractError("160_CONSUMPTION_REJECTED")
    return ppre["pending_precheck_id"]


class TestAgent150Contracts(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = PendingPrecheckBuilder(ROOT)
        self.spec = query_spec()
        self.sql_fields = llm_candidate_sql()

    def build_ppre(self, **overrides) -> dict:
        fields = {**self.sql_fields, **overrides}
        return self.builder.build_pending_precheck(
            self.spec,
            run_id="run-150", qa_id="QA-150",
            created_at=FIXED_TIME,
            **fields,
        )

    # ── Task 1: Build pending-precheck package ──────────────────────

    def test_task1_builds_valid_pending_precheck(self) -> None:
        ppre = self.build_ppre()
        self.builder.validate_pending_precheck(ppre)
        self.assertRegex(ppre["payload"]["pending_precheck_id"], r"^ppre-[0-9]{3}$")
        self.assertEqual(
            ppre["payload"]["question_sql_pending_precheck_package_schema_version"],
            "question-sql-pending-precheck-v1",
        )
        self.assertEqual(ppre["payload"]["query_goal"], "脱敏风险筛查")
        self.assertEqual(ppre["payload"]["entry_table"], "EAST_D001")
        self.assertIn("EAST_D001", ppre["payload"]["allowed_tables_ref"])
        self.assertIn("EAST_D002", ppre["payload"]["allowed_tables_ref"])

    def test_task1_preserves_query_spec_ref(self) -> None:
        ppre = self.build_ppre()
        self.assertEqual(
            ppre["payload"]["query_specification_package_ref"],
            artifact_ref(self.spec["envelope"]),
        )

    def test_task1_rejects_content_hash_drift(self) -> None:
        ppre = self.build_ppre()
        drifted = copy.deepcopy(ppre)
        drifted["payload"]["candidate_sql"] = "篡改SQL"
        with self.assertRaises(ContractError):
            self.builder.validate_pending_precheck(drifted)

    def test_task1_rejects_artifact_type_mismatch(self) -> None:
        bad_spec = copy.deepcopy(self.spec)
        bad_spec["envelope"]["artifact_type"] = "wrong_type"
        bad_spec["envelope"]["content_hash"] = content_hash(bad_spec["envelope"], bad_spec["payload"])
        with self.assertRaises(ContractError):
            self.builder.validate_query_spec(bad_spec)

    def test_task1_rejects_empty_candidate_sql(self) -> None:
        with self.assertRaisesRegex(ContractError, "CANDIDATE_SQL_EMPTY"):
            self.build_ppre(candidate_sql="")

    def test_task1_rejects_whitespace_only_candidate_sql(self) -> None:
        with self.assertRaisesRegex(ContractError, "CANDIDATE_SQL_EMPTY"):
            self.build_ppre(candidate_sql="   ")

    def test_task1_envelope_has_producer_150(self) -> None:
        ppre = self.build_ppre()
        self.assertEqual(ppre["envelope"]["producer_id"], "150")

    def test_task1_attempt_1_has_no_supersedes(self) -> None:
        ppre = self.build_ppre()
        self.assertIsNone(ppre["envelope"]["supersedes_ref"])
        self.assertEqual(ppre["envelope"]["attempt_no"], 1)
        self.assertEqual(ppre["envelope"]["version"], 1)

    def test_task1_pending_precheck_id_stable(self) -> None:
        """Same run_id+qa_id always produces same pending_precheck_id."""
        first = self.build_ppre()
        second = self.build_ppre()
        self.assertEqual(
            first["payload"]["pending_precheck_id"],
            second["payload"]["pending_precheck_id"],
        )

    def test_task1_precheck_expectations_derived(self) -> None:
        ppre = self.build_ppre()
        checks = ppre["payload"]["precheck_expectations"]["expected_checks"]
        check_ids = {c["check_id"] for c in checks}
        self.assertIn("chk-syntax", check_ids)
        self.assertIn("chk-scope", check_ids)
        self.assertIn("chk-param-type", check_ids)
        self.assertIn("chk-row-limit", check_ids)
        self.assertIn("chk-safety", check_ids)
        self.assertEqual(
            ppre["payload"]["precheck_expectations"]["max_result_rows_hint"],
            1000,
        )

    def test_task1_160_stub_consumes_output(self) -> None:
        ppre = self.build_ppre()
        consumed_id = _consume_160_stub(ppre)
        self.assertEqual(consumed_id, ppre["payload"]["pending_precheck_id"])

    # ── Hard-code validator tests ──────────────────────────────────

    def test_rejects_candidate_sql_empty(self) -> None:
        with self.assertRaisesRegex(ContractError, "CANDIDATE_SQL_EMPTY"):
            self.builder._validate_sql_nonempty("")

    def test_rejects_entry_table_not_in_scope(self) -> None:
        with self.assertRaisesRegex(ContractError, "ENTRY_TABLE_NOT_IN_SCOPE"):
            self.builder._validate_entry_table_in_scope(
                "NONEXISTENT_TABLE",
                {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001"]}]},
            )

    def test_rejects_allowed_tables_inconsistent(self) -> None:
        with self.assertRaisesRegex(ContractError, "ALLOWED_TABLES_INCONSISTENT"):
            self.builder._validate_allowed_tables_consistency(
                ["EAST_D999"],
                {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001"]}]},
            )

    def test_rejects_version_overwrite_attempted(self) -> None:
        with self.assertRaisesRegex(ContractError, "VERSION_OVERWRITE_ATTEMPTED"):
            self.builder._validate_version_immutability(1, {"artifact_id": "x", "version": 1, "content_hash": "0" * 64})
        with self.assertRaisesRegex(ContractError, "VERSION_OVERWRITE_ATTEMPTED"):
            self.builder._validate_version_immutability(2, None)

    def test_rejects_run_or_qa_mismatch(self) -> None:
        with self.assertRaisesRegex(ContractError, "RUN_OR_QA_MISMATCH"):
            self.builder.build_pending_precheck(
                self.spec,
                run_id="wrong-run", qa_id="QA-150",
                created_at=FIXED_TIME,
                **self.sql_fields,
            )

    def test_rejects_attempt_out_of_range(self) -> None:
        with self.assertRaisesRegex(ContractError, "ATTEMPT_OUT_OF_RANGE"):
            self.builder.build_pending_precheck(
                self.spec,
                run_id="run-150", qa_id="QA-150",
                attempt_no=4,
                created_at=FIXED_TIME,
                **self.sql_fields,
            )

    # ── Task 2: Precheck feedback retry ────────────────────────────

    def test_task2_retry_increments_version_and_attempt(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        second = self.builder.handle_precheck_feedback(
            self.spec, feedback_1, first,
            run_id="run-150", qa_id="QA-150",
            attempt_no=2,
            created_at=FIXED_TIME,
            **self.sql_fields,
        )
        self.assertEqual(second["envelope"]["version"], 2)
        self.assertEqual(second["envelope"]["attempt_no"], 2)
        self.assertEqual(second["envelope"]["supersedes_ref"], artifact_ref(first["envelope"]))
        self.assertEqual(second["envelope"]["status"], "candidate")
        self.assertEqual(
            second["payload"]["pending_precheck_id"],
            first["payload"]["pending_precheck_id"],
        )

    def test_task2_parent_refs_include_spec_feedback_previous(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        second = self.builder.handle_precheck_feedback(
            self.spec, feedback_1, first,
            run_id="run-150", qa_id="QA-150",
            attempt_no=2,
            created_at=FIXED_TIME,
            **self.sql_fields,
        )
        expected_parents = [
            artifact_ref(self.spec["envelope"]),
            artifact_ref(feedback_1["envelope"]),
            artifact_ref(first["envelope"]),
        ]
        self.assertEqual(second["envelope"]["parent_artifact_refs"], expected_parents)
        self.assertEqual(
            second["envelope"]["input_hashes"],
            [ref["content_hash"] for ref in expected_parents],
        )

    def test_task2_third_attempt_valid_candidate_is_not_blocked(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        second = self.builder.handle_precheck_feedback(
            self.spec, feedback_1, first,
            run_id="run-150", qa_id="QA-150",
            attempt_no=2,
            created_at=FIXED_TIME,
            **self.sql_fields,
        )
        feedback_2 = precheck_feedback(second, attempt_no=2)
        third = self.builder.handle_precheck_feedback(
            self.spec, feedback_2, second,
            run_id="run-150", qa_id="QA-150",
            attempt_no=3,
            created_at=FIXED_TIME,
            **self.sql_fields,
        )
        self.assertEqual(third["envelope"]["status"], "candidate")
        self.assertEqual(third["envelope"]["attempt_no"], 3)

    def test_task2_third_attempt_invalid_candidate_blocks_manual(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        second = self.builder.handle_precheck_feedback(
            self.spec, feedback_1, first,
            run_id="run-150", qa_id="QA-150",
            attempt_no=2,
            created_at=FIXED_TIME,
            **self.sql_fields,
        )
        feedback_2 = precheck_feedback(second, attempt_no=2)
        invalid_sql = {"candidate_sql": "", "sql_parameters": []}
        blocked = self.builder.handle_precheck_feedback(
            self.spec, feedback_2, second,
            run_id="run-150", qa_id="QA-150",
            attempt_no=3,
            created_at=FIXED_TIME,
            **invalid_sql,
        )
        self.assertEqual(blocked["envelope"]["status"], "blocked_manual")
        self.assertEqual(
            blocked["payload"]["pending_precheck_id"],
            first["payload"]["pending_precheck_id"],
        )

    def test_task2_rejects_feedback_ref_mismatch(self) -> None:
        first = self.build_ppre()
        bad_feedback = precheck_feedback(first, attempt_no=1)
        bad_feedback["payload"]["pending_precheck_package_ref"]["content_hash"] = "c" * 64
        bad_feedback["envelope"]["content_hash"] = content_hash(bad_feedback["envelope"], bad_feedback["payload"])
        with self.assertRaisesRegex(ContractError, "FEEDBACK_REF_MISMATCH"):
            self.builder.handle_precheck_feedback(
                self.spec, bad_feedback, first,
                run_id="run-150", qa_id="QA-150",
                attempt_no=2,
                created_at=FIXED_TIME,
                **self.sql_fields,
            )

    def test_task2_rejects_attempt_out_of_range(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        with self.assertRaisesRegex(ContractError, "ATTEMPT_OUT_OF_RANGE"):
            self.builder.handle_precheck_feedback(
                self.spec, feedback_1, first,
                run_id="run-150", qa_id="QA-150",
                attempt_no=4,
                created_at=FIXED_TIME,
                **self.sql_fields,
            )

    def test_task2_rejects_attempt_lineage_mismatch(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        with self.assertRaisesRegex(ContractError, "ATTEMPT_LINEAGE_MISMATCH"):
            self.builder.handle_precheck_feedback(
                self.spec, feedback_1, first,
                run_id="run-150", qa_id="QA-150",
                attempt_no=3,  # should be 2
                created_at=FIXED_TIME,
                **self.sql_fields,
            )

    def test_task2_rejects_run_or_qa_mismatch(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        with self.assertRaisesRegex(ContractError, "RUN_OR_QA_MISMATCH"):
            self.builder.handle_precheck_feedback(
                self.spec, feedback_1, first,
                run_id="wrong-run", qa_id="QA-150",
                attempt_no=2,
                created_at=FIXED_TIME,
                **self.sql_fields,
            )

    def test_task2_160_stub_consumes_retry_output(self) -> None:
        first = self.build_ppre()
        feedback_1 = precheck_feedback(first, attempt_no=1)
        second = self.builder.handle_precheck_feedback(
            self.spec, feedback_1, first,
            run_id="run-150", qa_id="QA-150",
            attempt_no=2,
            created_at=FIXED_TIME,
            **self.sql_fields,
        )
        consumed_id = _consume_160_stub(second)
        self.assertEqual(consumed_id, second["payload"]["pending_precheck_id"])

    # ── Probe integration test ─────────────────────────────────────

    def test_sanitized_probe_passes(self) -> None:
        result = run_sanitized_probe(ROOT)
        s = result["summary"]
        self.assertTrue(s["bad_hash_rejected"])
        self.assertTrue(s["feedback_1_executed"])
        self.assertTrue(s["feedback_2_valid_candidate"])
        self.assertTrue(s["feedback_2_invalid_blocked"])
        self.assertTrue(s["stub_160_consumed"])
        self.assertTrue(s["validator_empty_sql_rejects"])
        self.assertTrue(s["validator_entry_scope_rejects"])
        self.assertTrue(s["validator_tables_consistency_rejects"])
        self.assertTrue(s["validator_version_immutability_rejects"])


if __name__ == "__main__":
    unittest.main()
