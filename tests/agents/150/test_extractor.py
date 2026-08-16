from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder, TrustedRouteCapability
from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT, TIME = Path(__file__).resolve().parents[3], "2026-08-16T00:00:00+00:00"


def wrap(kind, identity, payload, *, producer, attempt=1, parents=None, mode="question_sql"):
    parents = parents or []
    env = {"artifact_id": identity, "artifact_type": kind, "run_id": "run150", "qa_id": "QA150", "version": 1,
           "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
           "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents,
           "input_hashes": [x["content_hash"] for x in parents], "status": "candidate", "mode": mode,
           "created_at": TIME, "trace_id": "trace150", "storage_locator": None}
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


def spec():
    p = {"query_spec_id": "qspec-150", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
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
    return wrap("query_specification_package", "qspec150", p, producer="140")


def candidate(sql, suffix="", *, evidence_refs=None):
    question = "筛查机构" + suffix
    result = {"sql_gold": sql, "clear_question": question,
              "sql_explanation": {"select": "机构字段" + suffix, "from_join": "限定关联" + suffix, "where": "固定条件" + suffix, "aggregation": "无" + suffix, "sort": "固定排序" + suffix, "business_meaning": "风险筛查" + suffix},
              "business_event_candidates": [{"event_name": "筛查" + suffix, "objective": "风险筛查" + suffix, "objects": ["机构"], "state_changes": ["识别" + suffix]}],
              "specification_mapping": [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS]}
    if evidence_refs is not None:
        result["evidence_refs"] = evidence_refs
    return result


def precheck(old, locations=("sql_gold",), producer="160"):
    payload = {"candidate_ref": artifact_ref(old["envelope"]), "precheck_decision": "fail", "failed_items": [{"failed_rule_ids": ["RULE"], "error_locations": list(locations), "expected_values": "合法", "actual_values": "错误", "error_details": "修复"}]}
    return wrap("precheck_failed_feedback", "feedback150", payload, producer=producer, attempt=old["envelope"]["attempt_no"])


class Tests(unittest.TestCase):
    def setUp(self):
        self.b, self.s, self.sql = PendingPrecheckBuilder(ROOT), spec(), "SELECT T1.F1, T1.F2 FROM T1 WHERE T1.F1=:v"
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        roots = {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}
        self.registry = ArtifactRegistry(ROOT, roots, "EAS-24", "route150", 1)
        self.registry.register(self.s["envelope"], self.s["payload"])
        self.route170 = self.review_package("deepseek_review_result", "170", "route170")
        self.route180 = self.review_package("glm_review_result", "180", "route180")
        self.registry.register(self.route170["envelope"], self.route170["payload"])
        self.registry.register(self.route180["envelope"], self.route180["payload"])
        self.cap170 = TrustedRouteCapability.from_registry(self.registry, review_refs=[artifact_ref(self.route170["envelope"])])
        self.cap180 = TrustedRouteCapability.from_registry(self.registry, review_refs=[artifact_ref(self.route180["envelope"])])
        self.cap_dual = TrustedRouteCapability.from_registry(self.registry, review_refs=[artifact_ref(self.route170["envelope"]), artifact_ref(self.route180["envelope"])])

    def build(self, **kw):
        return self.b.build_pending_precheck(self.s, run_id="run150", qa_id="QA150", created_at=TIME, **{**candidate(self.sql), **kw})

    def repair(self, old, feedback, **kw):
        return self.b.handle_precheck_feedback(self.s, feedback, old, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **{**candidate(self.sql), **kw})

    def review_package(self, kind, who, identity, *, reviewed_ref=None, error_types=("QUESTION_SQL_ERROR",), parents=None):
        payload = {"reviewed_package_ref": reviewed_ref or {"artifact_id": "review-input", "version": 1, "content_hash": "d" * 64},
                   "semantic_review_report": {"reviewer_id": who, "decision": "no", "error_types": list(error_types), "error_details": [{}], "evidence_refs": [], "route_suggestion": "150"}}
        return wrap(kind, identity, payload, producer=who, parents=parents)

    def review(self, old, kind="deepseek_review_result", error_types=("QUESTION_SQL_ERROR",), parents=None):
        who, route = ("170", self.route170) if kind == "deepseek_review_result" else ("180", self.route180)
        return self.review_package(kind, who, "feedback" + who, reviewed_ref=artifact_ref(old["envelope"]), error_types=error_types, parents=[artifact_ref(route["envelope"])] if parents is None else parents)

    def reviewed(self, old):
        self.registry.register(old["envelope"], old["payload"])
        approved170 = self.review_package("deepseek_review_result", "170", "approved170", reviewed_ref=artifact_ref(old["envelope"]), error_types=())
        approved170["payload"]["semantic_review_report"]["decision"] = "yes"; approved170["envelope"]["content_hash"] = content_hash(approved170["envelope"], approved170["payload"])
        approved180 = self.review_package("glm_review_result", "180", "approved180", reviewed_ref=artifact_ref(old["envelope"]), error_types=())
        approved180["payload"]["semantic_review_report"]["decision"] = "yes"; approved180["envelope"]["content_hash"] = content_hash(approved180["envelope"], approved180["payload"])
        self.registry.register(approved170["envelope"], approved170["payload"]); self.registry.register(approved180["envelope"], approved180["payload"])
        fixture = json.loads((ROOT / "tests/agents/220/fixtures/event-data-dual-review.json").read_text(encoding="utf-8"))
        payload = copy.deepcopy(fixture["payload"])
        payload.update({"qa_id": "QA150", "candidate_ref": artifact_ref(old["envelope"]), "query_spec_ref": artifact_ref(self.s["envelope"]),
                        "precheck_report_ref": {"artifact_id": "precheck150", "version": 1, "content_hash": "c" * 64},
                        "deepseek_review_ref": artifact_ref(approved170["envelope"]), "glm_review_ref": artifact_ref(approved180["envelope"])})
        payload["package_hash"] = hashlib.sha256(json.dumps({key: value for key, value in payload.items() if key != "package_hash"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return wrap("reviewed_question_sql", "reviewed150", payload, producer="210", attempt=old["envelope"]["attempt_no"], mode="event_data")

    def regression(self, parents=None, location="sql"):
        payload = {"schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data", "input_data_refs": [], "input_orm_ref": None, "sandbox_snapshot_id": "copy",
                   "failure_details": {"error_code": "SQL_EXECUTION_ERROR", "error_stage": "sql_execution", "error_location": location, "expected_values": [], "actual_values": [], "sql_error_detail": {"sql_text": self.sql, "error_code": "SQLITE", "error_message": "bad"}, "regression_metrics": {}}, "route_target": "110", "retry_count": 1}
        return wrap("sql_regression_failed_feedback", "reg150", payload, producer="260", mode="event_data", parents=parents)

    def regression_capability(self, feedback):
        self.registry.register(feedback["envelope"], feedback["payload"])
        payload = {"schema_version": "v5.sql-regression-route-record/v1", "source_feedback_ref": artifact_ref(feedback["envelope"]), "source_feedback_type": "sql_regression_failed_feedback", "route_path": ["260", "210", "010", "110"], "route_target": "150", "route_reason": "SQL_EXECUTION_ERROR"}
        record = wrap("sql_regression_route_record", "route110", payload, producer="110", mode="event_data")
        self.registry.register(record["envelope"], record["payload"])
        return TrustedRouteCapability.from_registry(self.registry, route_record_ref=artifact_ref(record["envelope"]))

    def test_sql_parser_and_quoted_aliases(self):
        cases = [("SELECT t.F9 FROM T1 AS t", "SQL_FIELD_OUT_OF_SCOPE"), ("SELECT F9 FROM T1", "SQL_UNQUALIFIED_FIELD_OUT_OF_SCOPE"), ("SELECT F2 FROM T1 JOIN T2 ON T1.F2=T2.F2", "SQL_UNQUALIFIED_FIELD_AMBIGUOUS"), ("WITH x AS (SELECT T1.F1 AS X1 FROM T1) SELECT x.F1 FROM x", "SQL_FIELD_OUT_OF_SCOPE"), ("SELECT \"F9\" FROM T1", "SQL_QUOTED_IDENTIFIER_OUT_OF_SCOPE")]
        for sql, code in cases:
            with self.subTest(sql=sql), self.assertRaisesRegex(ContractError, code):
                self.build(**candidate(sql))
        self.build(**candidate('SELECT y."X1" FROM (SELECT T1.F1 AS "X1" FROM T1) AS y'))

    def test_requires_all_generated_fields(self):
        with self.assertRaisesRegex(ContractError, "GENERATED_FIELDS_REQUIRED"):
            self.b.build_pending_precheck(self.s, run_id="run150", qa_id="QA150", sql_gold=self.sql, created_at=TIME)

    def test_mapping_missing_rejected(self):
        bad = candidate(self.sql); bad["specification_mapping"] = bad["specification_mapping"][:-1]
        with self.assertRaisesRegex(ContractError, "SPECIFICATION_MAPPING_INCOMPLETE"):
            self.build(**bad)

    def test_mapping_duplicate_rejected(self):
        bad = candidate(self.sql); bad["specification_mapping"].append(copy.deepcopy(bad["specification_mapping"][0]))
        with self.assertRaisesRegex(ContractError, "SPECIFICATION_MAPPING_INCOMPLETE"):
            self.build(**bad)

    def test_mapping_unrelated_fragment_rejected(self):
        bad = candidate(self.sql); bad["specification_mapping"][0]["sql_fragment"] = "NOT_IN_SQL"
        with self.assertRaisesRegex(ContractError, "SPECIFICATION_MAPPING_FRAGMENT_INVALID"):
            self.build(**bad)

    def test_sql_positive_join_aggregate_subquery_cte_topn_matrix(self):
        for sql in ("SELECT t.F1 FROM T1 AS t JOIN T2 AS u ON t.F2=u.F2", "SELECT F1, COUNT(F2) FROM T1 GROUP BY F1", "SELECT F1 FROM T1 WHERE F1 IN (SELECT F1 FROM T1)", "WITH x AS (SELECT T1.F1 AS X1 FROM T1) SELECT x.X1 FROM x", "SELECT F1 FROM T1 ORDER BY F1 LIMIT 5"):
            with self.subTest(sql=sql):
                self.build(**candidate(sql))

    def test_160_producer_and_content_hash_rejected(self):
        old = self.build()
        with self.assertRaisesRegex(ContractError, "PRECHECK_PRODUCER_REJECTED"):
            self.repair(old, precheck(old, producer="170"), **candidate("SELECT T1.F2 FROM T1"))
        drift = copy.deepcopy(old); drift["payload"]["clear_question"] = "drift"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.b.validate_pending_precheck(drift)

    def test_160_table_field_location_allows_sql_correction(self):
        old = self.build()
        repaired = self.repair(old, precheck(old, ("T1.F9",)), **candidate("SELECT T1.F2 FROM T1"))
        self.assertEqual(repaired["payload"]["sql_gold"], "SELECT T1.F2 FROM T1")

    def test_260_error_matrix_rejects_non_sql_execution(self):
        old, reviewed = self.build(), None
        reviewed = self.reviewed(old)
        feedback = self.regression(); feedback["payload"]["failure_details"]["error_code"] = "DATA_VALUE_ERROR"; feedback["envelope"]["content_hash"] = content_hash(feedback["envelope"], feedback["payload"])
        capability = self.regression_capability(feedback)
        with self.assertRaisesRegex(ContractError, "REGRESSION_ROUTE_REJECTED"):
            self.b.handle_routed_feedback(self.s, feedback, reviewed, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))

    def test_precheck_required_change_map_is_targeted(self):
        old = self.build()
        sql_only = self.repair(old, precheck(old, ("payload.sql_gold",)), **candidate("SELECT T1.F2 FROM T1"))
        self.assertEqual(sql_only["payload"]["clear_question"], old["payload"]["clear_question"])
        self.assertEqual(sql_only["payload"]["business_event_candidates"], old["payload"]["business_event_candidates"])
        with self.assertRaisesRegex(ContractError, "REQUIRED_CHANGE_MISSING"):
            self.repair(old, precheck(old, ("sql_gold",)), **candidate(self.sql))
        events = candidate(self.sql); events["business_event_candidates"] = [{"event_name": "重算", "objective": "风险筛查", "objects": ["机构"], "state_changes": ["重算"]}]
        event_only = self.repair(old, precheck(old, ("business_event_candidates[0]",)), **events)
        self.assertEqual(event_only["payload"]["sql_gold"], old["payload"]["sql_gold"])
        facts = candidate(self.sql, "-事实", evidence_refs=[*old["payload"]["evidence_refs"], "fact-correction-1"])
        fact_only = self.repair(old, precheck(old, ("fact",)), **facts)
        self.assertNotEqual(fact_only["payload"]["clear_question"], old["payload"]["clear_question"])
        self.assertNotEqual(fact_only["payload"]["evidence_refs"], old["payload"]["evidence_refs"])
        with self.assertRaisesRegex(ContractError, "REQUIRED_CHANGE_LOCATION_UNKNOWN"):
            self.repair(old, precheck(old, ("unknown_field",)), **candidate(self.sql))

    def test_review_required_change_map(self):
        old = self.build()
        for kind, capability in (("deepseek_review_result", self.cap170), ("glm_review_result", self.cap180)):
            sql = self.b.handle_routed_feedback(self.s, self.review(old, kind, ("QUESTION_SQL_ERROR",)), old, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))
            self.assertNotEqual(sql["payload"]["sql_gold"], old["payload"]["sql_gold"])
            events = candidate(self.sql); events["business_event_candidates"] = [{"event_name": "新事件", "objective": "风险", "objects": ["机构"], "state_changes": []}]
            result = self.b.handle_routed_feedback(self.s, self.review(old, kind, ("BUSINESS_EVENT_ERROR",)), old, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **events)
            self.assertEqual(result["payload"]["sql_gold"], old["payload"]["sql_gold"])
        facts = candidate(self.sql, "-事实", evidence_refs=[*old["payload"]["evidence_refs"], "fact-correction-1"])
        result = self.b.handle_routed_feedback(self.s, self.review(old, error_types=("QUESTION_FACT_OMISSION",)), old, route_capability=self.cap170, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **facts)
        self.assertNotEqual(result["payload"]["evidence_refs"], old["payload"]["evidence_refs"])

    def test_route_capability_rejects_arbitrary_resolver_and_wrong_parent(self):
        old = self.build()
        class Resolver: pass
        with self.assertRaisesRegex(ContractError, "TRUSTED_ROUTE_CAPABILITY_INVALID"):
            TrustedRouteCapability.from_registry(Resolver(), review_refs=[artifact_ref(self.route170["envelope"])])
        with self.assertRaisesRegex(ContractError, "TRUSTED_ROUTE_CAPABILITY_REJECTED"):
            self.b.handle_routed_feedback(self.s, self.review(old, parents=[]), old, route_capability=self.cap170, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))

    def test_260_requires_frozen_220_dual_review_lineage(self):
        old, reviewed = self.build(), None
        reviewed = self.reviewed(old)
        feedback = self.regression(); capability = self.regression_capability(feedback)
        result = self.b.handle_routed_feedback(self.s, feedback, reviewed, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))
        self.assertEqual(result["envelope"]["attempt_no"], 2)
        self.assertEqual(result["envelope"]["artifact_id"], old["envelope"]["artifact_id"])
        self.assertEqual(result["envelope"]["supersedes_ref"], artifact_ref(old["envelope"]))
        forged = copy.deepcopy(reviewed); forged["payload"].pop("glm_review_ref"); forged["envelope"]["content_hash"] = content_hash(forged["envelope"], forged["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEWED_PREVIOUS_LINEAGE_REJECTED"):
            self.b.handle_routed_feedback(self.s, feedback, forged, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))
        no_approval = copy.deepcopy(reviewed); no_approval["payload"]["deepseek_review_ref"] = artifact_ref(self.route170["envelope"]); no_approval["payload"]["package_hash"] = hashlib.sha256(json.dumps({key: value for key, value in no_approval["payload"].items() if key != "package_hash"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(); no_approval["envelope"]["content_hash"] = content_hash(no_approval["envelope"], no_approval["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEWED_PREVIOUS_LINEAGE_REJECTED"):
            self.b.handle_routed_feedback(self.s, feedback, no_approval, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))
        wrong_query_spec = copy.deepcopy(reviewed); wrong_query_spec["payload"]["query_spec_ref"] = {"artifact_id": "other-spec", "version": 1, "content_hash": "e" * 64}; wrong_query_spec["payload"]["package_hash"] = hashlib.sha256(json.dumps({key: value for key, value in wrong_query_spec["payload"].items() if key != "package_hash"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(); wrong_query_spec["envelope"]["content_hash"] = content_hash(wrong_query_spec["envelope"], wrong_query_spec["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEWED_PREVIOUS_LINEAGE_REJECTED"):
            self.b.handle_routed_feedback(self.s, feedback, wrong_query_spec, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))
        forged_feedback = copy.deepcopy(feedback); forged_feedback["payload"]["sandbox_snapshot_id"] = "forged"; forged_feedback["envelope"]["content_hash"] = content_hash(forged_feedback["envelope"], forged_feedback["payload"])
        with self.assertRaisesRegex(ContractError, "TRUSTED_ROUTE_CAPABILITY_REJECTED"):
            self.b.handle_routed_feedback(self.s, forged_feedback, reviewed, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))
        with self.assertRaisesRegex(ContractError, "TRUSTED_ROUTE_CAPABILITY_REJECTED"):
            self.b.handle_routed_feedback(self.s, self.regression(parents=[artifact_ref(self.route170["envelope"])]), reviewed, route_capability=capability, run_id="run150", qa_id="QA150", attempt_no=2, created_at=TIME, **candidate("SELECT T1.F2 FROM T1"))


if __name__ == "__main__":
    unittest.main()
