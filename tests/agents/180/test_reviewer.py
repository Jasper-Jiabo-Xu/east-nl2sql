"""180 运行边界：GLM 原始输出、硬校验、重试和 110 消费。"""
from __future__ import annotations

import ast
import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.agents.east_180.probe import run_sanitized_probe
from east_v5.agents.east_180.reviewer import ERROR_ROUTE, ERROR_TYPES, GLMReviewerAgent, REVIEWER_ID, consume_110_stub
from east_v5.artifacts import content_hash
from east_v5.governance import ContractError

PrecheckAgent = importlib.import_module("east_v5.agents.160.precheck").PrecheckAgent
TIME = "2026-08-17T00:00:00+00:00"


class ScriptedGLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def review(self, request):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def report(error_types=None, *, route=None, evidence=True, extra=None):
    error_types = error_types or []
    if not error_types:
        result = {"reviewer_id": "180", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [{"kind": "query_spec", "ref": "qspec-180", "description": "冻结查询规格"}] if evidence else [], "route_suggestion": route or "150"}
    else:
        result = {
            "reviewer_id": "180", "decision": "no", "error_types": error_types,
            "error_details": [{"error_type": item, "object": "candidate", "location": "candidate_content", "reason": f"detected-{item}", "suggestion": "修复后重新预审"} for item in error_types],
            "evidence_refs": [{"kind": "frozen_package", "ref": "dual-180", "description": "冻结审核包证据"}] if evidence else [],
            "route_suggestion": route or ERROR_ROUTE.get(error_types[0], "150"),
        }
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def spec():
    payload = {
        "query_spec_id": "qspec-180", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
        "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64}, "query_goal": "脱敏风险筛查", "must_preserve_fact_refs": ["fact-1"],
        "main_object_and_grain": {"main_object": "机构", "grain": "记录"}, "query_entry": {"entry_table": "T1", "entry_conditions": []}, "related_objects_and_path": [], "filters_and_evidence": [],
        "return_fields": [{"field_id": "F1", "display_name": "字段1", "source_table": "T1"}], "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["风险"], "unanswerable": []},
        "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"}, "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [], "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}},
        "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 5}, "query_specification_package_schema_version": "query-specification-v1",
    }
    env = {"artifact_id": "qspec180", "artifact_type": "query_specification_package", "run_id": "run180", "qa_id": "QA180", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "140", "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "question_sql", "created_at": TIME, "trace_id": "trace180", "storage_locator": None}
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


def candidate():
    return {"sql_gold": "SELECT T1.F1 FROM T1", "clear_question": "筛查机构", "sql_explanation": {"select": "字段", "from_join": "T1", "where": "固定条件", "aggregation": "无", "sort": "固定", "business_meaning": "风险筛查"}, "business_event_candidates": [{"event_name": "筛查", "objective": "风险筛查", "objects": ["机构"], "state_changes": ["识别"]}], "specification_mapping": [{"spec_item": item, "question_fragment": "筛查机构", "sql_fragment": "SELECT T1.F1 FROM T1"} for item in MAPPED_SPEC_ITEMS]}


class Tests(unittest.TestCase):
    def setUp(self):
        self.precheck = PrecheckAgent(ROOT)
        self.builder = PendingPrecheckBuilder(ROOT)
        self.spec = spec()

    def dual(self, attempt=1):
        pending = self.builder.build_pending_precheck(self.spec, run_id="run180", qa_id="QA180", created_at=TIME, attempt_no=attempt, **candidate())
        checked = self.precheck.precheck(pending, self.spec, checked_at=TIME)
        return self.precheck.build_dual_review(pending, self.spec, checked, created_at=TIME)

    def test_no_default_or_caller_injected_decision(self):
        agent = GLMReviewerAgent(ROOT)
        with self.assertRaisesRegex(ContractError, "MODEL_CLIENT_REQUIRED"):
            agent.review(self.dual(), created_at=TIME)
        with self.assertRaises(TypeError):
            agent.review(self.dual(), decision="yes")

    def test_valid_glm_pass_is_hard_validated_and_consumed_by_110(self):
        client = ScriptedGLM([report()])
        result = GLMReviewerAgent(ROOT, client).review(self.dual(), created_at=TIME)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["payload"]["semantic_review_report"]["decision"], "yes")
        self.assertEqual(result["envelope"]["parent_artifact_refs"][0], result["payload"]["reviewed_package_ref"])
        self.assertEqual(consume_110_stub(ROOT, result)["decision"], "yes")

    def test_six_error_types_have_fixed_route_and_full_failure_evidence(self):
        for error_type, route in ERROR_ROUTE.items():
            with self.subTest(error_type=error_type):
                result = GLMReviewerAgent(ROOT, ScriptedGLM([report([error_type])])).review(self.dual(), created_at=TIME)
                body = result["payload"]["semantic_review_report"]
                self.assertEqual(body["route_suggestion"], route)
                self.assertEqual(body["error_details"][0]["error_type"], error_type)
                self.assertTrue(body["evidence_refs"])

    def test_multi_error_requires_single_deterministic_route(self):
        result = GLMReviewerAgent(ROOT, ScriptedGLM([report(["QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR"])])) .review(self.dual(), created_at=TIME)
        self.assertEqual(result["payload"]["semantic_review_report"]["route_suggestion"], "150")
        client = ScriptedGLM([report(["FACT_PACKAGE_ERROR", "QUERY_SPEC_ERROR"], route="120")] * 3)
        with self.assertRaisesRegex(ContractError, "MODEL_RETRY_EXHAUSTED:ERROR_ROUTE_MAPPING_INVALID"):
            GLMReviewerAgent(ROOT, client).review(self.dual(), created_at=TIME)
        self.assertEqual(client.calls, 3)

    def test_invalid_json_enum_unknown_and_missing_evidence_are_retried_not_defaulted(self):
        bad_enum = report(["NOT_AN_ERROR"])
        unknown = report(extra={"unexpected": "x"})
        no_evidence = report(["QUERY_SPEC_ERROR"], evidence=False)
        client = ScriptedGLM(["{", bad_enum, unknown])
        with self.assertRaisesRegex(ContractError, "MODEL_RETRY_EXHAUSTED"):
            GLMReviewerAgent(ROOT, client).review(self.dual(), created_at=TIME)
        self.assertEqual(client.calls, 3)
        client = ScriptedGLM([no_evidence] * 3)
        with self.assertRaisesRegex(ContractError, "MODEL_RETRY_EXHAUSTED:FAIL_REQUIRES_DETAILS_AND_EVIDENCE"):
            GLMReviewerAgent(ROOT, client).review(self.dual(), created_at=TIME)

    def test_model_transport_failure_three_retries_then_attempt_three_blocked_manual(self):
        client = ScriptedGLM([RuntimeError("network")] * 3)
        result = GLMReviewerAgent(ROOT, client).review(self.dual(attempt=3), created_at=TIME)
        self.assertEqual(client.calls, 3)
        self.assertEqual((result["envelope"]["attempt_no"], result["envelope"]["status"]), (3, "blocked_manual"))
        self.assertEqual(result["payload"]["semantic_review_report"]["error_details"][0]["code"], "MODEL_RETRY_EXHAUSTED")
        self.assertEqual(consume_110_stub(ROOT, result)["decision"], "blocked_manual")

    def test_attempt_one_exhaustion_does_not_forge_blocked_manual(self):
        with self.assertRaisesRegex(ContractError, "MODEL_RETRY_EXHAUSTED"):
            GLMReviewerAgent(ROOT, ScriptedGLM(["{"] * 3)).review(self.dual(attempt=1), created_at=TIME)

    def test_input_hash_drift_and_output_lineage_are_hard_rejected(self):
        dual = self.dual()
        drift = copy.deepcopy(dual)
        drift["payload"]["package_hash"] = "0" * 64
        drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "PACKAGE_HASH_DRIFT"):
            GLMReviewerAgent(ROOT, ScriptedGLM([report()])).review(drift, created_at=TIME)
        result = GLMReviewerAgent(ROOT, ScriptedGLM([report()])).review(dual, created_at=TIME)
        tampered = copy.deepcopy(result)
        tampered["payload"]["reviewed_package_ref"]["artifact_id"] = "substituted-input"
        tampered["envelope"]["content_hash"] = content_hash(tampered["envelope"], tampered["payload"])
        with self.assertRaisesRegex(ContractError, "LINEAGE_MISMATCH"):
            consume_110_stub(ROOT, tampered)

    def test_170_isolation_and_sanitized_probe(self):
        tree = ast.parse((ROOT / "src/east_v5/agents/east_180/reviewer.py").read_text(encoding="utf-8"))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertFalse(any("170" in value for value in imports))
        summary = run_sanitized_probe(ROOT)["summary"]
        self.assertTrue(summary["pass_consumed_by_110"])
        self.assertTrue(summary["input_hash_drift_detected"])
        self.assertTrue(summary["multi_error_decision"])
        self.assertEqual(set(ERROR_TYPES), set(ERROR_ROUTE))


if __name__ == "__main__":
    unittest.main()
