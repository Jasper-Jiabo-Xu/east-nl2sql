from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

mod = importlib.import_module("east_v5.agents.160.precheck")
probe_mod = importlib.import_module("east_v5.agents.160.probe")
from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder  # noqa: E402
from east_v5.artifacts import artifact_ref, content_hash  # noqa: E402
from east_v5.governance import ContractError  # noqa: E402

run_sanitized_probe = probe_mod.run_sanitized_probe

PrecheckAgent = mod.PrecheckAgent
consume_170_180_stub = mod.consume_170_180_stub
REPORT_RULE_ORDER = mod.REPORT_RULE_ORDER

TIME = "2026-08-16T00:00:00+00:00"


def wrap(kind, identity, payload, *, producer, attempt=1, parents=None, mode="question_sql"):
    parents = parents or []
    env = {"artifact_id": identity, "artifact_type": kind, "run_id": "run160", "qa_id": "QA160", "version": 1,
           "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
           "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents,
           "input_hashes": [x["content_hash"] for x in parents], "status": "candidate", "mode": mode,
           "created_at": TIME, "trace_id": "trace160", "storage_locator": None}
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


def spec():
    p = {"query_spec_id": "qspec-160", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
         "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64},
         "query_goal": "脱敏风险筛查", "must_preserve_fact_refs": ["fact-1"], "main_object_and_grain": {"main_object": "机构", "grain": "记录"},
         "query_entry": {"entry_table": "T1", "entry_conditions": [{"field_id": "F1", "operator": "=", "value": "x"}]}, "related_objects_and_path": [], "filters_and_evidence": [],
         "return_fields": [{"field_id": "F1", "display_name": "字段1", "source_table": "T1"}],
         "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["风险"], "unanswerable": []},
         "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"},
         "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2", "F3"]}, {"table_id": "T2", "allowed_fields": ["F2"]}]},
         "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [],
         "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 2}},
         "join_expansion_limit": {"max_multiplier": 2, "max_result_rows": 10}, "query_specification_package_schema_version": "query-specification-v1"}
    return wrap("query_specification_package", "qspec160", p, producer="140")


def candidate(sql, suffix=""):
    question = "筛查机构" + suffix
    return {"sql_gold": sql, "clear_question": question,
            "sql_explanation": {"select": "机构字段" + suffix, "from_join": "限定关联" + suffix, "where": "固定条件" + suffix, "aggregation": "无" + suffix, "sort": "固定排序" + suffix, "business_meaning": "风险筛查" + suffix},
            "query_parameter_bindings": [{"name": "v", "source_pointer": "/query_entry/entry_conditions/0"}] if ":v" in sql else [],
            "business_event_candidates": [{"event_name": "筛查" + suffix, "objective": "风险筛查" + suffix, "objects": ["机构"], "state_changes": ["识别" + suffix]}],
            "specification_mapping": [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS]}


def forge_invalid(package, sql):
    forged = copy.deepcopy(package)
    forged["payload"]["sql_gold"] = sql
    for item in forged["payload"]["specification_mapping"]:
        item["sql_fragment"] = sql
    forged["envelope"]["content_hash"] = content_hash(forged["envelope"], forged["payload"])
    return forged


class Tests(unittest.TestCase):
    def setUp(self):
        self.agent = PrecheckAgent(ROOT)
        self.builder = PendingPrecheckBuilder(ROOT)
        self.spec = spec()
        self.sql = "SELECT T1.F1, T1.F2 FROM T1 WHERE T1.F1=:v"
        self.valid = self.builder.build_pending_precheck(self.spec, run_id="run160", qa_id="QA160", created_at=TIME, **candidate(self.sql))

    def precheck(self, package, query=None):
        return self.agent.precheck(package, query or self.spec, checked_at=TIME)

    def failed_rule_ids(self, result):
        return sorted({item["failed_rule_ids"][0] for item in result["failed_items"]})

    def test_catalog_registers_160_edges(self):
        catalog = json.loads((ROOT / "config/v5-package-catalog.json").read_text(encoding="utf-8"))
        packages = {p["id"]: p for p in catalog["packages"]}
        self.assertEqual(packages["question_sql_pending_precheck"]["producer"], "150")
        self.assertIn("160", packages["question_sql_pending_precheck"]["consumers"])
        self.assertEqual(packages["precheck_failed_feedback"]["producer"], "160")
        self.assertEqual(packages["precheck_failed_feedback"]["consumers"], ["150"])
        self.assertEqual(packages["question_sql_pending_dual_review"]["producer"], "160")
        self.assertEqual(packages["question_sql_pending_dual_review"]["consumers"], ["170", "180"])

    def test_valid_candidate_passes_and_dual_review_is_deterministic(self):
        result = self.precheck(self.valid)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual([r["rule_id"] for r in result["report"]["rules"]], list(REPORT_RULE_ORDER))
        self.assertTrue(all(r["status"] == "pass" for r in result["report"]["rules"]))
        dual = self.agent.build_dual_review(self.valid, self.spec, result, created_at=TIME)
        again = self.agent.build_dual_review(self.valid, self.spec, result, created_at=TIME)
        self.assertEqual(dual["envelope"]["content_hash"], again["envelope"]["content_hash"])
        self.assertEqual(dual["payload"]["package_hash"], again["payload"]["package_hash"])
        self.assertEqual(dual["payload"]["review_round"], 1)
        self.assertEqual(dual["envelope"]["producer_id"], "160")
        self.assertEqual(dual["envelope"]["artifact_type"], "question_sql_pending_dual_review")
        self.assertEqual(dual["payload"]["candidate_ref"], artifact_ref(self.valid["envelope"]))
        self.assertEqual(dual["payload"]["query_specification_package"], self.valid["payload"]["query_spec_ref"])
        consumed = consume_170_180_stub(ROOT, dual)
        self.assertEqual(consumed["package_hash"], dual["payload"]["package_hash"])

    def test_sql_fail_matrix(self):
        cases = {
            "write": ("DELETE FROM T1", "PC-SQL-002"),
            "select_star": ("SELECT * FROM T1", "PC-SQL-003"),
            "out_of_scope": ("SELECT T1.F9 FROM T1", "PC-SQL-005"),
            "unqualified": ("SELECT F9 FROM T1", "PC-SQL-006"),
            "dynamic_time": ("SELECT CURRENT_DATE", "PC-SQL-004"),
        }
        for label, (sql, rule) in cases.items():
            with self.subTest(label=label):
                result = self.precheck(forge_invalid(self.valid, sql))
                self.assertEqual(result["decision"], "fail")
                self.assertIn(rule, self.failed_rule_ids(result))

    def test_failed_feedback_package_is_precise_and_consumable_by_150(self):
        forged = forge_invalid(self.valid, "SELECT T1.F9 FROM T1")
        result = self.precheck(forged)
        feedback = self.agent.build_feedback(forged, result, created_at=TIME)
        self.assertEqual(feedback["envelope"]["producer_id"], "160")
        self.assertEqual(feedback["envelope"]["artifact_type"], "precheck_failed_feedback")
        self.assertEqual(feedback["envelope"]["attempt_no"], self.valid["envelope"]["attempt_no"])
        self.assertEqual(feedback["payload"]["precheck_decision"], "fail")
        self.assertEqual(feedback["payload"]["candidate_ref"], artifact_ref(forged["envelope"]))
        self.assertTrue(feedback["payload"]["failed_items"])
        item = feedback["payload"]["failed_items"][0]
        self.assertEqual(set(item), {"failed_rule_ids", "error_locations", "expected_values", "actual_values", "error_details"})
        # 150 consumes the feedback and repairs to a valid attempt 2.
        repaired = self.builder.handle_precheck_feedback(self.spec, feedback, forged, run_id="run160", qa_id="QA160", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1", "-修复"))
        self.assertEqual(repaired["envelope"]["attempt_no"], 2)
        self.assertEqual(self.precheck(repaired)["decision"], "pass")

    def test_mapping_incomplete_and_fragment_drift_fail(self):
        bad_map = copy.deepcopy(self.valid)
        bad_map["payload"]["specification_mapping"] = bad_map["payload"]["specification_mapping"][:-1]
        bad_map["envelope"]["content_hash"] = content_hash(bad_map["envelope"], bad_map["payload"])
        self.assertIn("PC-MAP-001", self.failed_rule_ids(self.precheck(bad_map)))
        bad_frag = copy.deepcopy(self.valid)
        bad_frag["payload"]["specification_mapping"][0]["sql_fragment"] = "NOT_IN_SQL"
        bad_frag["envelope"]["content_hash"] = content_hash(bad_frag["envelope"], bad_frag["payload"])
        self.assertIn("PC-MAP-002", self.failed_rule_ids(self.precheck(bad_frag)))

    def test_lineage_rejection(self):
        drift = copy.deepcopy(self.valid)
        drift["payload"]["query_spec_ref"] = {"artifact_id": "other-spec", "version": 1, "content_hash": "c" * 64}
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        self.assertIn("PC-LIN-001", self.failed_rule_ids(self.precheck(drift)))

    def test_input_rejection_matrix(self):
        drift = copy.deepcopy(self.valid)
        drift["payload"]["clear_question"] = "drift"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.agent.validate_pending_precheck(drift)
        wrong_producer = copy.deepcopy(self.valid)
        wrong_producer["envelope"]["producer_id"] = "170"
        wrong_producer["envelope"]["content_hash"] = content_hash(wrong_producer["envelope"], wrong_producer["payload"])
        with self.assertRaisesRegex(ContractError, "PRECHECK_PRODUCER_REJECTED"):
            self.agent.validate_pending_precheck(wrong_producer)
        wrong_type = copy.deepcopy(self.valid)
        wrong_type["envelope"]["artifact_type"] = "reviewed_question_sql"
        wrong_type["envelope"]["content_hash"] = content_hash(wrong_type["envelope"], wrong_type["payload"])
        with self.assertRaisesRegex(ContractError, "ARTIFACT_TYPE_MISMATCH"):
            self.agent.validate_pending_precheck(wrong_type)

    def test_dual_review_package_hash_drift_rejected(self):
        result = self.precheck(self.valid)
        dual = self.agent.build_dual_review(self.valid, self.spec, result, created_at=TIME)
        forged = copy.deepcopy(dual)
        forged["payload"]["package_hash"] = "0" * 64
        forged["envelope"]["content_hash"] = content_hash(forged["envelope"], forged["payload"])
        with self.assertRaisesRegex(ContractError, "PACKAGE_HASH_DRIFT"):
            self.agent.validate_dual_review(forged)

    def test_attempt_round_trip_and_boundary(self):
        first = forge_invalid(self.valid, "SELECT T1.F9 FROM T1")
        f1 = self.agent.build_feedback(first, self.precheck(first), created_at=TIME)
        self.assertEqual(f1["envelope"]["attempt_no"], 1)
        second = self.builder.handle_precheck_feedback(self.spec, f1, first, run_id="run160", qa_id="QA160", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1", "-修复"))
        self.assertEqual(second["envelope"]["attempt_no"], 2)
        self.assertEqual(self.precheck(second)["decision"], "pass")
        dual2 = self.agent.build_dual_review(second, self.spec, self.precheck(second), created_at=TIME)
        self.assertEqual(dual2["payload"]["review_round"], 2)
        # Attempt 3 candidate is still precheckable; attempt 4 is out of range.
        third_forged = forge_invalid(second, "SELECT T1.F9 FROM T1")
        self.assertEqual(self.precheck(third_forged)["decision"], "fail")
        with self.assertRaisesRegex(ContractError, "ATTEMPT_OUT_OF_RANGE"):
            self.builder.handle_precheck_feedback(self.spec, f1, first, run_id="run160", qa_id="QA160", attempt_no=4, created_at=TIME, **candidate("SELECT T1.F2 FROM T1", "-越界"))

    def test_sanitized_probe_summary(self):
        summary = run_sanitized_probe(ROOT)["summary"]
        self.assertTrue(summary["valid_pass"])
        self.assertTrue(summary["dual_review_identical"])
        self.assertTrue(summary["stub_170_180_consumed"])
        self.assertTrue(all(item["decision"] == "fail" for item in summary["fail_matrix"].values()))
        self.assertTrue(summary["retry_round_trip"]["attempt2_passed"])


if __name__ == "__main__":
    unittest.main()
