from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

mod = importlib.import_module("east_v5.agents.170.review")
probe_mod = importlib.import_module("east_v5.agents.170.probe")
PrecheckAgent = importlib.import_module("east_v5.agents.160.precheck").PrecheckAgent
from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder  # noqa: E402
from east_v5.artifacts import artifact_ref, content_hash  # noqa: E402
from east_v5.governance import ContractError, sha256  # noqa: E402

DeepSeekReviewAgent = mod.DeepSeekReviewAgent
ERROR_TYPES = mod.ERROR_TYPES
ERROR_TYPE_ROUTE = mod.ERROR_TYPE_ROUTE
run_sanitized_probe = probe_mod.run_sanitized_probe

TIME = "2026-08-17T00:00:00+00:00"


def wrap(kind, identity, payload, *, producer, attempt=1, mode="question_sql", parents=None):
    parents = parents or []
    env = {"artifact_id": identity, "artifact_type": kind, "run_id": "run170", "qa_id": "QA170", "version": 1,
           "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
           "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents,
           "input_hashes": [x["content_hash"] for x in parents], "status": "candidate", "mode": mode,
           "created_at": TIME, "trace_id": "trace170", "storage_locator": None}
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


def spec():
    p = {"query_spec_id": "qspec-170", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
         "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64},
         "query_goal": "脱敏风险筛查", "must_preserve_fact_refs": ["fact-1"], "main_object_and_grain": {"main_object": "机构", "grain": "记录"},
         "query_entry": {"entry_table": "T1", "entry_conditions": []}, "related_objects_and_path": [], "filters_and_evidence": [],
         "return_fields": [{"field_id": "F1", "display_name": "字段1", "source_table": "T1"}],
         "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["风险"], "unanswerable": []},
         "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"},
         "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2", "F3"]}, {"table_id": "T2", "allowed_fields": ["F2"]}]},
         "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [],
         "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 2}},
         "join_expansion_limit": {"max_multiplier": 2, "max_result_rows": 10}, "query_specification_package_schema_version": "query-specification-v1"}
    return wrap("query_specification_package", "qspec170", p, producer="140")


def candidate(sql, suffix=""):
    question = "筛查机构" + suffix
    return {"sql_gold": sql, "clear_question": question,
            "sql_explanation": {"select": "机构字段" + suffix, "from_join": "限定关联" + suffix, "where": "固定条件" + suffix, "aggregation": "无" + suffix, "sort": "固定排序" + suffix, "business_meaning": "风险筛查" + suffix},
            "business_event_candidates": [{"event_name": "筛查" + suffix, "objective": "风险筛查" + suffix, "objects": ["机构"], "state_changes": ["识别" + suffix]}],
            "specification_mapping": [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS]}


def report(decision, error_types=(), route="150"):
    if decision == "yes":
        return {"reviewer_id": "170", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [], "route_suggestion": route}
    return {"reviewer_id": "170", "decision": decision, "error_types": list(error_types),
            "error_details": [{"object": item, "location": "candidate", "reason": "synthetic", "suggestion": "repair"} for item in error_types],
            "evidence_refs": [{"source": "fixture", "ref": "fact-1"}], "route_suggestion": route}


def forge_round3(dual):
    forged = copy.deepcopy(dual)
    forged["envelope"]["attempt_no"] = 3
    forged["payload"]["review_round"] = 3
    forged["payload"]["package_hash"] = sha256({k: v for k, v in forged["payload"].items() if k != "package_hash"})
    forged["envelope"]["content_hash"] = content_hash(forged["envelope"], forged["payload"])
    return forged


class Tests(unittest.TestCase):
    def setUp(self):
        self.agent = DeepSeekReviewAgent(ROOT)
        self.builder = PendingPrecheckBuilder(ROOT)
        self.precheck = PrecheckAgent(ROOT)
        self.spec = spec()
        candidate_pkg = self.builder.build_pending_precheck(
            self.spec, run_id="run170", qa_id="QA170", created_at=TIME,
            **candidate("SELECT T1.F1, T1.F2 FROM T1"),
        )
        result = self.precheck.precheck(candidate_pkg, self.spec, checked_at=TIME)
        self.dual = self.precheck.build_dual_review(candidate_pkg, self.spec, result, created_at=TIME)

    def review(self, package, rep):
        return self.agent.review(package, rep, created_at=TIME)

    def test_catalog_registers_170_edges(self):
        catalog = json.loads((ROOT / "config/v5-package-catalog.json").read_text(encoding="utf-8"))
        packages = {p["id"]: p for p in catalog["packages"]}
        self.assertEqual(packages["deepseek_review_result"]["producer"], "170")
        self.assertEqual(packages["deepseek_review_result"]["consumers"], ["110", "130", "140", "150"])
        self.assertEqual(packages["question_sql_pending_dual_review"]["producer"], "160")
        self.assertEqual(packages["question_sql_pending_dual_review"]["consumers"], ["170", "180"])

    def test_pass_review(self):
        result = self.review(self.dual, report("yes"))
        payload = result["payload"]
        self.assertEqual(payload["reviewed_package_ref"], artifact_ref(self.dual["envelope"]))
        self.assertEqual(payload["semantic_review_report"]["reviewer_id"], "170")
        self.assertEqual(payload["semantic_review_report"]["decision"], "yes")
        self.assertEqual(payload["semantic_review_report"]["error_types"], [])
        self.assertEqual(result["envelope"]["producer_id"], "170")
        self.assertEqual(result["envelope"]["artifact_type"], "deepseek_review_result")
        self.assertEqual(result["envelope"]["status"], "candidate")
        self.assertEqual(result["envelope"]["parent_artifact_refs"], [artifact_ref(self.dual["envelope"])])
        self.assertEqual(result["envelope"]["input_hashes"], [self.dual["envelope"]["content_hash"]])
        self.agent.validate_result(result)

    def test_six_error_types_each_route_consistently(self):
        for error_type in ERROR_TYPES:
            with self.subTest(error_type=error_type):
                result = self.review(self.dual, report("no", (error_type,), ERROR_TYPE_ROUTE[error_type]))
                payload = result["payload"]["semantic_review_report"]
                self.assertEqual(payload["decision"], "no")
                self.assertEqual(payload["error_types"], [error_type])
                self.assertEqual(payload["route_suggestion"], ERROR_TYPE_ROUTE[error_type])
                self.assertEqual(result["envelope"]["status"], "candidate")

    def test_multiple_non_conflicting_errors(self):
        result = self.review(self.dual, report("no", ("QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR"), "150"))
        payload = result["payload"]["semantic_review_report"]
        self.assertEqual(payload["error_types"], ["QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR"])
        self.assertEqual(payload["route_suggestion"], "150")

    def test_insufficient_evidence_rejected(self):
        rep = report("no", ("QUESTION_SQL_ERROR",), "150")
        rep["evidence_refs"] = []
        with self.assertRaisesRegex(ContractError, "EVIDENCE_INSUFFICIENT"):
            self.review(self.dual, rep)

    def test_reject_without_details_rejected(self):
        rep = report("no", ("QUESTION_SQL_ERROR",), "150")
        rep["error_details"] = []
        with self.assertRaisesRegex(ContractError, "REJECT_WITHOUT_DETAILS"):
            self.review(self.dual, rep)

    def test_conflicting_route_rejected(self):
        # FACT_PACKAGE_ERROR 路由 120，但报告建议 150 → 冲突硬拒绝。
        with self.assertRaisesRegex(ContractError, "ROUTE_SUGGESTION_INCONSISTENT"):
            self.review(self.dual, report("no", ("FACT_PACKAGE_ERROR",), "150"))
        # 跨生产者混合（120 + 150）无法路由到唯一对象。
        with self.assertRaisesRegex(ContractError, "ROUTE_SUGGESTION_INCONSISTENT"):
            self.review(self.dual, report("no", ("FACT_PACKAGE_ERROR", "QUESTION_SQL_ERROR"), "150"))

    def test_180_isolation(self):
        # 170 不消费 180 的 glm_review_result 包类型。
        glm_report = {"reviewer_id": "180", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [], "route_suggestion": "150"}
        glm = wrap("glm_review_result", "glm170", {"reviewed_package_ref": artifact_ref(self.dual["envelope"]), "semantic_review_report": glm_report}, producer="180")
        with self.assertRaisesRegex(ContractError, "ARTIFACT_TYPE_MISMATCH"):
            self.agent.validate_dual_review(glm)
        # 180 的 reviewer_id 报告被 170 硬拒绝。
        rep = report("yes")
        rep["reviewer_id"] = "180"
        with self.assertRaisesRegex(ContractError, "REVIEWER_ID_REJECTED"):
            self.review(self.dual, rep)

    def test_semantic_report_enum_and_field_validation(self):
        with self.assertRaisesRegex(ContractError, "DECISION_INVALID"):
            self.review(self.dual, report("maybe"))
        rep = report("no", ("QUESTION_SQL_ERROR",), "150")
        rep["error_types"] = ["NOT_A_TYPE"]
        with self.assertRaisesRegex(ContractError, "ERROR_TYPE_UNKNOWN"):
            self.review(self.dual, rep)
        rep = report("no", ("QUESTION_SQL_ERROR",), "999")
        with self.assertRaisesRegex(ContractError, "ROUTE_SUGGESTION_INVALID"):
            self.review(self.dual, rep)
        rep = report("no", ("QUESTION_SQL_ERROR", "QUESTION_SQL_ERROR"), "150")
        with self.assertRaisesRegex(ContractError, "ERROR_TYPES_DUPLICATE"):
            self.review(self.dual, rep)
        rep = report("yes")
        rep["error_types"] = ["QUESTION_SQL_ERROR"]
        with self.assertRaisesRegex(ContractError, "PASS_WITH_ERRORS"):
            self.review(self.dual, rep)
        rep = report("no", (), "150")
        with self.assertRaisesRegex(ContractError, "REJECT_WITHOUT_ERRORS"):
            self.review(self.dual, rep)

    def test_unknown_and_missing_report_fields(self):
        rep = report("yes")
        rep["extra"] = True
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD:semantic_review_report"):
            self.review(self.dual, rep)
        rep = report("yes")
        del rep["route_suggestion"]
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD:semantic_review_report"):
            self.review(self.dual, rep)

    def test_input_hash_drift_rejected(self):
        drift = copy.deepcopy(self.dual)
        drift["payload"]["package_hash"] = "0" * 64
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "PACKAGE_HASH_DRIFT"):
            self.agent.validate_dual_review(drift)

    def test_envelope_content_hash_drift_rejected(self):
        drift = copy.deepcopy(self.dual)
        drift["payload"]["candidate_content"]["clear_question"] = "drift"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.agent.validate_dual_review(drift)

    def test_review_round_mismatch_rejected(self):
        drift = copy.deepcopy(self.dual)
        drift["payload"]["review_round"] = 2
        drift["payload"]["package_hash"] = sha256({k: v for k, v in drift["payload"].items() if k != "package_hash"})
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEW_ROUND_MISMATCH"):
            self.agent.validate_dual_review(drift)

    def test_stale_version_and_schema_version_rejected(self):
        drift = copy.deepcopy(self.dual)
        drift["envelope"]["schema_version"] = "COMMON-ENVELOPE/v0"
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "SCHEMA_VERSION_UNSUPPORTED"):
            self.agent.validate_dual_review(drift)
        drift = copy.deepcopy(self.dual)
        drift["envelope"]["version"] = 0
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "VERSION_INVALID"):
            self.agent.validate_dual_review(drift)

    def test_wrong_producer_rejected(self):
        drift = copy.deepcopy(self.dual)
        drift["envelope"]["producer_id"] = "180"
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "DUAL_REVIEW_PRODUCER_REJECTED"):
            self.agent.validate_dual_review(drift)

    def test_round3_no_blocks_manual(self):
        round3 = forge_round3(self.dual)
        self.agent.validate_dual_review(round3)
        result = self.review(round3, report("no", ("QUESTION_SQL_ERROR",), "150"))
        self.assertEqual(result["envelope"]["status"], "blocked_manual")
        self.agent.validate_result(result)

    def test_model_failure_blocks_manual_after_three(self):
        def failing(_payload):
            raise RuntimeError("model unavailable")

        blocked = self.agent.run(self.dual, failing, created_at=TIME)
        self.assertEqual(blocked["decision"], "blocked_manual")
        self.assertEqual(blocked["attempts"], 3)
        self.assertEqual(blocked["reason"], "LLM_REVIEW_FAILED")

    def test_model_recovers_on_retry(self):
        calls = {"n": 0}

        def flaky(_payload):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return report("yes")

        result = self.agent.run(self.dual, flaky, created_at=TIME)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["payload"]["semantic_review_report"]["decision"], "yes")

    def test_invalid_llm_output_retries_then_blocks(self):
        blocked = self.agent.run(self.dual, lambda _p: report("maybe"), created_at=TIME)
        self.assertEqual(blocked["decision"], "blocked_manual")
        self.assertEqual(blocked["attempts"], 3)

    def test_downstream_stub_consumes_result(self):
        result = self.review(self.dual, report("no", ("QUESTION_SQL_ERROR",), "150"))
        consumed = probe_mod.consume_110_stub(ROOT, result)
        self.assertEqual(consumed["consumer"], "110")
        self.assertEqual(consumed["decision"], "no")

    def test_result_unknown_field_rejected(self):
        result = self.review(self.dual, report("yes"))
        result["payload"]["extra"] = True
        result["envelope"]["content_hash"] = content_hash(result["envelope"], result["payload"])
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED:deepseek_review_result"):
            self.agent.validate_result(result)

    def test_sanitized_probe_summary(self):
        summary = run_sanitized_probe(ROOT)["summary"]
        self.assertTrue(summary["dual_review_identical"])
        self.assertTrue(summary["pass_reviewed"])
        self.assertTrue(summary["six_error_types_covered"])
        self.assertTrue(summary["every_error_routes_consistently"])
        self.assertTrue(summary["every_error_status_candidate"])
        self.assertTrue(summary["multi_error_non_conflicting"])
        self.assertTrue(summary["round3_blocked_manual"])
        self.assertTrue(summary["model_failure_blocked_manual"])


if __name__ == "__main__":
    unittest.main()
