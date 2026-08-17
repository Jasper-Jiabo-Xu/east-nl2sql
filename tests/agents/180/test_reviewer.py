"""180-GLM审核员 agent tests: input validation, semantic review, output, 110 stub, probe."""
from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.agents.east_180.reviewer import (
    GLMReviewerAgent, REVIEWER_ID, ERROR_TYPES, consume_110_stub,
    _compute_package_hash,
)
from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

# 160 lives under numeric dir; importlib like the 160 tests
_mod_160 = importlib.import_module("east_v5.agents.160.precheck")
PrecheckAgent = _mod_160.PrecheckAgent

# Probe module
probe_mod = importlib.import_module("east_v5.agents.east_180.probe")
run_sanitized_probe = probe_mod.run_sanitized_probe

TIME = "2026-08-17T00:00:00+00:00"


def wrap(kind, identity, payload, *, producer, attempt=1, parents=None, mode="question_sql"):
    parents = parents or []
    env = {
        "artifact_id": identity, "artifact_type": kind, "run_id": "run180",
        "qa_id": "QA180", "version": 1, "schema_version": "COMMON-ENVELOPE/v1",
        "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt,
        "producer_id": producer, "parent_artifact_refs": parents,
        "input_hashes": [x["content_hash"] for x in parents],
        "status": "candidate", "mode": mode, "created_at": TIME,
        "trace_id": "trace180", "storage_locator": None,
    }
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


def spec():
    p = {
        "query_spec_id": "qspec-180",
        "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
        "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64},
        "query_goal": "脱敏风险筛查", "must_preserve_fact_refs": ["fact-1"],
        "main_object_and_grain": {"main_object": "机构", "grain": "记录"},
        "query_entry": {"entry_table": "T1", "entry_conditions": []},
        "related_objects_and_path": [], "filters_and_evidence": [],
        "return_fields": [{"field_id": "F1", "display_name": "字段1", "source_table": "T1"}],
        "aggregation_dedup_sort_time": {"group_by_fields": []},
        "observability_boundary": {"answerable": ["风险"], "unanswerable": []},
        "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2", "F3"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1,
        "condition_coverage": [], "code_value_coverage": [],
        "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 2}},
        "join_expansion_limit": {"max_multiplier": 2, "max_result_rows": 10},
        "query_specification_package_schema_version": "query-specification-v1",
    }
    return wrap("query_specification_package", "qspec180", p, producer="140")


def candidate(sql, suffix=""):
    question = "筛查机构" + suffix
    return {
        "sql_gold": sql, "clear_question": question,
        "sql_explanation": {
            "select": "机构字段" + suffix, "from_join": "限定关联" + suffix,
            "where": "固定条件" + suffix, "aggregation": "无" + suffix,
            "sort": "固定排序" + suffix, "business_meaning": "风险筛查" + suffix,
        },
        "business_event_candidates": [{
            "event_name": "筛查" + suffix, "objective": "风险筛查" + suffix,
            "objects": ["机构"], "state_changes": ["识别" + suffix],
        }],
        "specification_mapping": [
            {"spec_item": item, "question_fragment": question, "sql_fragment": sql}
            for item in MAPPED_SPEC_ITEMS
        ],
    }


class Tests(unittest.TestCase):
    def setUp(self):
        self.agent_180 = GLMReviewerAgent(ROOT)
        self.agent_160 = PrecheckAgent(ROOT)
        self.builder = PendingPrecheckBuilder(ROOT)
        self.spec = spec()
        self.sql = "SELECT T1.F1, T1.F2 FROM T1 WHERE T1.F1=:v"
        self.valid = self.builder.build_pending_precheck(
            self.spec, run_id="run180", qa_id="QA180", created_at=TIME,
            **candidate(self.sql),
        )
        self.result_160 = self.agent_160.precheck(self.valid, self.spec, checked_at=TIME)
        self.dual = self.agent_160.build_dual_review(
            self.valid, self.spec, self.result_160, created_at=TIME,
        )

    def test_catalog_registers_180_edges(self):
        catalog = json.loads((ROOT / "config/v5-package-catalog.json").read_text(encoding="utf-8"))
        packages = {p["id"]: p for p in catalog["packages"]}
        self.assertEqual(packages["question_sql_pending_dual_review"]["producer"], "160")
        self.assertIn("180", packages["question_sql_pending_dual_review"]["consumers"])
        self.assertEqual(packages["glm_review_result"]["producer"], "180")
        self.assertIn("110", packages["glm_review_result"]["consumers"])

    def test_reviewer_id_is_180(self):
        self.assertEqual(REVIEWER_ID, "180")

    def test_error_types_six_members(self):
        self.assertEqual(len(ERROR_TYPES), 6)
        expected = {
            "FACT_PACKAGE_ERROR", "OBSERVABLE_MAPPING_ERROR",
            "QUERY_SPEC_ERROR", "QUESTION_SQL_ERROR",
            "BUSINESS_EVENT_ERROR", "QUESTION_FACT_OMISSION",
        }
        self.assertEqual(ERROR_TYPES, expected)

    # ── Pass path ──────────────────────────────────────────────

    def test_pass_review_produces_valid_glm_review_result(self):
        result = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        self.assertEqual(result["envelope"]["artifact_type"], "glm_review_result")
        self.assertEqual(result["envelope"]["producer_id"], "180")
        report = result["payload"]["semantic_review_report"]
        self.assertEqual(report["reviewer_id"], "180")
        self.assertEqual(report["decision"], "yes")
        self.assertEqual(report["error_types"], [])
        self.assertEqual(report["route_suggestion"], "150")

    def test_pass_review_is_deterministic(self):
        first = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        again = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        self.assertEqual(first["envelope"]["content_hash"], again["envelope"]["content_hash"])
        self.assertEqual(
            first["payload"]["semantic_review_report"],
            again["payload"]["semantic_review_report"],
        )

    def test_pass_review_lineage_tracks_input(self):
        result = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        self.assertIn(
            artifact_ref(self.dual["envelope"]),
            result["envelope"]["parent_artifact_refs"],
        )
        self.assertIn(
            self.dual["envelope"]["content_hash"],
            result["envelope"]["input_hashes"],
        )

    def test_pass_review_attempt_no_inherited(self):
        result = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        self.assertEqual(result["envelope"]["attempt_no"], self.dual["envelope"]["attempt_no"])

    # ── Fail path: each error type ─────────────────────────────

    def test_fail_matrix_all_six_error_types(self):
        cases = {
            "FACT_PACKAGE_ERROR": "120",
            "OBSERVABLE_MAPPING_ERROR": "130",
            "QUERY_SPEC_ERROR": "140",
            "QUESTION_SQL_ERROR": "150",
            "BUSINESS_EVENT_ERROR": "150",
            "QUESTION_FACT_OMISSION": "120",
        }
        for error_type, route in cases.items():
            with self.subTest(error_type=error_type):
                result = self.agent_180.review(
                    self.dual, decision="no",
                    error_types=[error_type],
                    error_details=[{"error_type": error_type, "description": f"测试{error_type}"}],
                    route_suggestion=route,
                    created_at=TIME,
                )
                report = result["payload"]["semantic_review_report"]
                self.assertEqual(report["decision"], "no")
                self.assertIn(error_type, report["error_types"])
                self.assertEqual(report["route_suggestion"], route)
                self.assertEqual(report["reviewer_id"], "180")

    def test_fail_multiple_error_types(self):
        result = self.agent_180.review(
            self.dual, decision="no",
            error_types=["QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR"],
            error_details=[
                {"error_type": "QUESTION_SQL_ERROR", "description": "SQL语义偏差"},
                {"error_type": "BUSINESS_EVENT_ERROR", "description": "事件遗漏"},
            ],
            route_suggestion="150",
            created_at=TIME,
        )
        report = result["payload"]["semantic_review_report"]
        self.assertEqual(len(report["error_types"]), 2)
        self.assertIn("QUESTION_SQL_ERROR", report["error_types"])
        self.assertIn("BUSINESS_EVENT_ERROR", report["error_types"])

    # ── Input validation rejection ─────────────────────────────

    def test_input_rejection_wrong_producer(self):
        wrong = copy.deepcopy(self.dual)
        wrong["envelope"]["producer_id"] = "999"
        wrong["envelope"]["content_hash"] = content_hash(wrong["envelope"], wrong["payload"])
        with self.assertRaisesRegex(ContractError, "DUAL_REVIEW_PRODUCER_REJECTED"):
            self.agent_180.validate_input(wrong)

    def test_input_rejection_wrong_artifact_type(self):
        wrong = copy.deepcopy(self.dual)
        wrong["envelope"]["artifact_type"] = "something_else"
        wrong["envelope"]["content_hash"] = content_hash(wrong["envelope"], wrong["payload"])
        # validate_envelope catches this first as ARTIFACT_TYPE_INVALID
        with self.assertRaisesRegex(ContractError, "ARTIFACT_TYPE"):
            self.agent_180.validate_input(wrong)

    def test_input_rejection_package_hash_drift(self):
        drift = copy.deepcopy(self.dual)
        drift["payload"]["package_hash"] = "0" * 64
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "PACKAGE_HASH_DRIFT"):
            self.agent_180.validate_input(drift)

    def test_input_rejection_precheck_not_pass(self):
        bad = copy.deepcopy(self.dual)
        bad["payload"]["precheck_report"]["decision"] = "fail"
        bad["payload"]["precheck_report"]["rules"] = []
        # Need to fix the envelope hash after payload change, but the schema
        # will reject "decision: fail" since it's const "pass" in the schema.
        # So we expect SCHEMA_VALIDATION_FAILED instead.
        bad["envelope"]["content_hash"] = content_hash(bad["envelope"], bad["payload"])
        with self.assertRaises(ContractError):
            self.agent_180.validate_input(bad)

    # ── Output validation ──────────────────────────────────────

    def test_output_rejection_wrong_reviewer_id(self):
        result = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        # Tamper reviewer_id
        tampered = copy.deepcopy(result)
        tampered["payload"]["semantic_review_report"]["reviewer_id"] = "170"
        tampered["envelope"]["content_hash"] = content_hash(tampered["envelope"], tampered["payload"])
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            self.agent_180.validate_output(tampered)

    def test_output_rejection_pass_with_errors_inconsistent(self):
        # decision=yes but non-empty error_types → INCONSISTENT_PASS_WITH_ERRORS
        # We can't produce this via review() since it enforces consistency,
        # so we construct a package manually to validate_output.
        result = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        tampered = copy.deepcopy(result)
        tampered["payload"]["semantic_review_report"]["error_types"] = ["QUESTION_SQL_ERROR"]
        tampered["envelope"]["content_hash"] = content_hash(tampered["envelope"], tampered["payload"])
        with self.assertRaisesRegex(ContractError, "INCONSISTENT_PASS_WITH_ERRORS"):
            self.agent_180.validate_output(tampered)

    def test_output_rejection_fail_without_errors_inconsistent(self):
        result = self.agent_180.review(
            self.dual, decision="no",
            error_types=["QUESTION_SQL_ERROR"],
            error_details=[{"error_type": "QUESTION_SQL_ERROR", "description": "test"}],
            route_suggestion="150",
            created_at=TIME,
        )
        tampered = copy.deepcopy(result)
        tampered["payload"]["semantic_review_report"]["error_types"] = []
        tampered["envelope"]["content_hash"] = content_hash(tampered["envelope"], tampered["payload"])
        with self.assertRaisesRegex(ContractError, "INCONSISTENT_FAIL_WITHOUT_ERRORS"):
            self.agent_180.validate_output(tampered)

    # ── Decision enforcement ───────────────────────────────────

    def test_invalid_decision_rejected(self):
        with self.assertRaisesRegex(ContractError, "DECISION_INVALID"):
            self.agent_180.review(self.dual, decision="maybe", route_suggestion="150", created_at=TIME)

    def test_fail_without_error_types_rejected(self):
        with self.assertRaisesRegex(ContractError, "FAIL_REQUIRES_ERROR_TYPES"):
            self.agent_180.review(self.dual, decision="no", error_types=[], route_suggestion="150", created_at=TIME)

    def test_invalid_error_type_rejected(self):
        with self.assertRaisesRegex(ContractError, "ERROR_TYPE_INVALID"):
            self.agent_180.review(
                self.dual, decision="no",
                error_types=["INVALID_ERROR_TYPE"],
                route_suggestion="150",
                created_at=TIME,
            )

    def test_invalid_route_suggestion_rejected(self):
        with self.assertRaisesRegex(ContractError, "ROUTE_SUGGESTION_INVALID"):
            self.agent_180.review(self.dual, decision="yes", route_suggestion="999", created_at=TIME)

    # ── consume_110_stub ───────────────────────────────────────

    def test_consume_110_stub_pass(self):
        result = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        consumed = consume_110_stub(ROOT, result)
        self.assertEqual(consumed["consumer"], "110")
        self.assertEqual(consumed["reviewer_id"], "180")
        self.assertEqual(consumed["decision"], "yes")
        self.assertEqual(consumed["route_suggestion"], "150")

    def test_consume_110_stub_fail(self):
        result = self.agent_180.review(
            self.dual, decision="no",
            error_types=["FACT_PACKAGE_ERROR"],
            error_details=[{"error_type": "FACT_PACKAGE_ERROR", "description": "test"}],
            route_suggestion="120",
            created_at=TIME,
        )
        consumed = consume_110_stub(ROOT, result)
        self.assertEqual(consumed["decision"], "no")
        self.assertEqual(consumed["route_suggestion"], "120")
        self.assertEqual(json.loads(consumed["error_types"]), ["FACT_PACKAGE_ERROR"])

    # ── 170/180 isolation ─────────────────────────────────────

    def test_180_never_reads_170_conclusions(self):
        """180 has no import of any 170 module; this is structural isolation."""
        import ast
        source = (ROOT / "src/east_v5/agents/east_180/reviewer.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "170" in node.module:
                    self.fail(f"180 reviewer imports from 170: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "170" in alias.name:
                        self.fail(f"180 reviewer imports 170: {alias.name}")

    # ── Attempt round-trip and boundary ────────────────────────

    def test_attempt_round_trip_and_boundary(self):
        """Attempt 1-3 are valid; attempt 4 would be ATTEMPT_OUT_OF_RANGE at 150 level.

        For 180, attempt_no is inherited from the dual review package.
        The 180 agent validates that review_round == attempt_no.
        """
        # Attempt 1 (default)
        result1 = self.agent_180.review(self.dual, decision="yes", route_suggestion="150", created_at=TIME)
        self.assertEqual(result1["envelope"]["attempt_no"], 1)

        # Build a dual review for attempt 2
        second = self.builder.build_pending_precheck(
            self.spec, run_id="run180", qa_id="QA180", created_at=TIME,
            attempt_no=2, **candidate(self.sql, "-修复"),
        )
        result2_160 = self.agent_160.precheck(second, self.spec, checked_at=TIME)
        dual2 = self.agent_160.build_dual_review(second, self.spec, result2_160, created_at=TIME)
        result2 = self.agent_180.review(dual2, decision="yes", route_suggestion="150", created_at=TIME)
        self.assertEqual(result2["envelope"]["attempt_no"], 2)

    # ── Sanitized probe summary ────────────────────────────────

    def test_sanitized_probe_summary(self):
        summary = run_sanitized_probe(ROOT)["summary"]
        self.assertTrue(summary["pass_review_valid"])
        self.assertTrue(summary["pass_reviewer_id"])
        self.assertTrue(summary["pass_decision"])
        self.assertTrue(summary["pass_consumed_by_110"])
        self.assertTrue(summary["deterministic"])
        self.assertTrue(summary["input_hash_drift_detected"])
        self.assertTrue(summary["multi_error_decision"])
        self.assertTrue(summary["multi_error_types_count"])
        for label, item in summary["fail_matrix"].items():
            with self.subTest(fail_label=label):
                self.assertEqual(item["decision"], "no")


if __name__ == "__main__":
    unittest.main()
