from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.agents.east_180.reviewer import GLMReviewerAgent
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256

PrecheckAgent = importlib.import_module("east_v5.agents.160.precheck").PrecheckAgent
DeepSeekReviewAgent = importlib.import_module("east_v5.agents.170.review").DeepSeekReviewAgent
QuestionSqlStageScheduler = importlib.import_module("east_v5.agents.110.scheduler").QuestionSqlStageScheduler
run_sanitized_probe = importlib.import_module("east_v5.agents.110.probe").run_sanitized_probe

TIME = "2026-08-17T00:00:00+00:00"


class ScriptedGLM:
    def __init__(self, response: dict): self.response = json.dumps(response, ensure_ascii=False)
    def review(self, _request): return self.response


def wrap(kind, identity, payload, *, producer, attempt=1, mode="question_sql", status="candidate", parents=None):
    parents = parents or []
    env = {"artifact_id": identity, "artifact_type": kind, "run_id": "run110", "qa_id": "QA110", "version": 1,
           "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
           "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents,
           "input_hashes": [item["content_hash"] for item in parents], "status": status, "mode": mode,
           "created_at": TIME, "trace_id": "trace110", "storage_locator": None}
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


def spec():
    payload = {"query_spec_id": "qspec-110", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
        "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64}, "query_goal": "脱敏筛查", "must_preserve_fact_refs": ["fact"],
        "main_object_and_grain": {"main_object": "机构", "grain": "记录"}, "query_entry": {"entry_table": "T1", "entry_conditions": []}, "related_objects_and_path": [], "filters_and_evidence": [],
        "return_fields": [{"field_id": "F1", "display_name": "字段", "source_table": "T1"}], "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["筛查"], "unanswerable": []},
        "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"}, "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [], "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}},
        "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 5}, "query_specification_package_schema_version": "query-specification-v1"}
    return wrap("query_specification_package", "qspec110", payload, producer="140")


def dual(attempt=1):
    query = spec(); builder = PendingPrecheckBuilder(ROOT); checker = PrecheckAgent(ROOT)
    candidate = {"sql_gold": "SELECT T1.F1 FROM T1", "clear_question": "筛查机构", "sql_explanation": {"select": "字段", "from_join": "T1", "where": "固定", "aggregation": "无", "sort": "固定", "business_meaning": "筛查"},
        "business_event_candidates": [{"event_name": "筛查", "objective": "筛查", "objects": ["机构"], "state_changes": []}], "specification_mapping": [{"spec_item": item, "question_fragment": "筛查机构", "sql_fragment": "SELECT T1.F1 FROM T1"} for item in MAPPED_SPEC_ITEMS]}
    pending = builder.build_pending_precheck(query, run_id="run110", qa_id="QA110", created_at=TIME, attempt_no=attempt, **candidate)
    result = checker.precheck(pending, query, checked_at=TIME)
    return checker.build_dual_review(pending, query, result, created_at=TIME)


def report_170(decision="yes", errors=()):
    return {"reviewer_id": "170", "decision": decision, "error_types": list(errors),
            "error_details": [] if decision == "yes" else [{"object": "candidate", "location": "x", "reason": "synthetic", "suggestion": "repair"} for _ in errors],
            "evidence_refs": [] if decision == "yes" else [{"source": "fixture", "ref": "x"}], "route_suggestion": "150" if not errors else {"FACT_PACKAGE_ERROR": "120", "OBSERVABLE_MAPPING_ERROR": "130", "QUERY_SPEC_ERROR": "140"}.get(errors[0], "150")}


def report_180(decision="yes", errors=()):
    error_routes = {"FACT_PACKAGE_ERROR": "120", "OBSERVABLE_MAPPING_ERROR": "130", "QUERY_SPEC_ERROR": "140", "QUESTION_SQL_ERROR": "150", "BUSINESS_EVENT_ERROR": "150", "QUESTION_FACT_OMISSION": "120"}
    route = next((route for route in ("120", "130", "140", "150") if route in {error_routes[item] for item in errors}), "150")
    return {"reviewer_id": "180", "decision": decision, "error_types": list(errors),
            "error_details": [] if decision == "yes" else [{"error_type": item, "object": "candidate", "location": "x", "reason": "synthetic", "suggestion": "repair"} for item in errors],
            "evidence_refs": [{"kind": "fixture", "ref": "x", "description": "脱敏"}], "route_suggestion": route}


def reviews(pending, decision170="yes", errors170=(), decision180="yes", errors180=()):
    review170 = DeepSeekReviewAgent(ROOT).review(pending, report_170(decision170, errors170), created_at=TIME)
    review180 = GLMReviewerAgent(ROOT, ScriptedGLM(report_180(decision180, errors180))).review(pending, created_at=TIME)
    return review170, review180


class SchedulerTests(unittest.TestCase):
    def setUp(self): self.scheduler = QuestionSqlStageScheduler(ROOT)

    def test_double_yes_builds_approved_package_and_210_consumes_it(self):
        pending = dual(); first, second = reviews(pending)
        result = self.scheduler.collect_reviews(pending, [second, first], created_at=TIME)
        self.assertEqual((result["target"], result["kind"]), ("210", "question_sql_dual_review_passed"))
        approved = result["approved_package"]
        self.assertEqual((approved["envelope"]["producer_id"], approved["envelope"]["status"]), ("110", "validated"))
        self.assertEqual(approved["payload"]["review_round"], 1)
        # The executable 110 probe binds the immutable 140 package when it
        # invokes 210; bare 110 provenance may no longer enter event flow.
        self.assertTrue(run_sanitized_probe(ROOT)["summary"]["downstream_stub_consumed"])

    def test_single_no_never_starts_data_and_routes_by_error(self):
        pending = dual(); first, second = reviews(pending, decision180="no", errors180=("QUERY_SPEC_ERROR",))
        result = self.scheduler.collect_reviews(pending, [first, second])
        self.assertEqual((result["target"], result["kind"]), ("140", "repair"))
        self.assertNotIn("approved_package", result)

    def test_double_no_same_and_multi_class_route_to_topmost(self):
        pending = dual(); first, second = reviews(pending, "no", ("QUESTION_SQL_ERROR",), "no", ("BUSINESS_EVENT_ERROR",))
        self.assertEqual(self.scheduler.collect_reviews(pending, [first, second])["target"], "150")
        pending = dual(); first, second = reviews(pending, "no", ("OBSERVABLE_MAPPING_ERROR",), "no", ("QUESTION_SQL_ERROR",))
        self.assertEqual(self.scheduler.collect_reviews(pending, [second, first])["target"], "130")

    def test_missing_duplicate_and_source_hash_drift_rejected(self):
        pending = dual(); first, second = reviews(pending)
        with self.assertRaisesRegex(ContractError, "110_REVIEW_SET_INCOMPLETE"):
            self.scheduler.collect_reviews(pending, [first])
        with self.assertRaisesRegex(ContractError, "110_REVIEWER_DUPLICATE_OR_MISMATCH"):
            self.scheduler.collect_reviews(pending, [first, first])
        drift = copy.deepcopy(second); drift["payload"]["reviewed_package_ref"]["content_hash"] = "0" * 64; drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "110_REVIEW_REJECTED|110_REVIEW_SOURCE_REF_DRIFT"):
            self.scheduler.collect_reviews(pending, [first, drift])

    def test_attempt_three_or_review_blocked_manual_is_manual(self):
        pending = dual(attempt=3); first, second = reviews(pending, "no", ("QUESTION_SQL_ERROR",), "yes")
        self.assertEqual(self.scheduler.collect_reviews(pending, [first, second])["target"], "manual")
        pending = dual(); first, second = reviews(pending, "no", ("QUESTION_SQL_ERROR",), "yes")
        first["envelope"]["status"] = "blocked_manual"; first["envelope"]["content_hash"] = content_hash(first["envelope"], first["payload"])
        self.assertEqual(self.scheduler.collect_reviews(pending, [first, second])["target"], "manual")

    def test_dispatch_is_identical_for_both_reviewers_and_no_source_mutation(self):
        pending = dual(); before = copy.deepcopy(pending)
        dispatches = self.scheduler.dispatch_dual_review(pending)
        self.assertEqual(dispatches[0]["package_hash"], dispatches[1]["package_hash"])
        self.assertEqual(dispatches[0]["reviewed_package_ref"], dispatches[1]["reviewed_package_ref"])
        self.assertEqual(before, pending)

    def test_manifest_lineage_and_sanitized_runtime_probe(self):
        manifest = json.loads((ROOT / "agents/110/runtime-manifest.template.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["schema_version"], manifest["issue_key"]), ("v5.110-runtime-manifest/v1", "EAS-28"))
        self.assertFalse(manifest["contains_business_data"])
        summary = run_sanitized_probe(ROOT)["summary"]
        self.assertTrue(summary["double_yes_to_210"])
        self.assertTrue(summary["downstream_stub_consumed"])


if __name__ == "__main__": unittest.main()
