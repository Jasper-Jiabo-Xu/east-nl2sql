from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from east_v5.agents.east_140 import QuerySpecBuilder
from east_v5.agents.east_140.probe import run_sanitized_probe
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
FIXED_TIME = "2026-08-16T00:00:00+00:00"


def wrap(artifact_type: str, artifact_id: str, payload: dict, *, producer: str, attempt: int = 1, version: int = 1, parents: list[dict] | None = None, trace_id: str = "trace-140", run_id: str = "run-140", qa_id: str = "QA-140") -> dict:
    parents = parents or []
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": run_id, "qa_id": qa_id, "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [x["content_hash"] for x in parents], "status": "candidate", "mode": "question_sql", "created_at": FIXED_TIME, "trace_id": trace_id, "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def penalty() -> dict:
    payload = {
        "source_facts": [{"penalty_fact_id": "fact-001", "fact_type": "behavior", "structured_fact": {"subject": "脱敏机构", "predicate": "行为", "object": "脱敏事实", "qualifier": None, "value": None}, "original_text": "脱敏事实", "source_span_refs": [], "must_preserve_in_question": "yes"}],
        "external_evidence": {"penalty_intent": {"description": "脱敏意图", "evidence_refs": []}, "regulatory_rules": [], "business_meaning": {"description": "脱敏业务含义", "evidence_refs": []}, "penalty_background": {"description": "脱敏背景", "evidence_refs": []}},
        "evidence_conflicts": [], "uncertainties": [], "penalty_fact_package_schema_version": "penalty-fact-v1",
    }
    return wrap("penalty_fact_package", "penalty-140", payload, producer="120")


def observable() -> dict:
    payload = {
        "observable_facts": [{"observable_fact_id": "observable-fact-001", "penalty_fact_refs": ["fact-001"], "topic": "监管处罚风险筛查", "main_object": "脱敏机构", "query_grain": "一条EAST业务记录或聚合事件", "entry_table": "EAST_D001", "related_tables_fields": [{"table_id": "EAST_D002", "field_id": "F002", "purpose": "关联字段"}], "within_table_relations": [], "cross_table_relations": [], "time_amount_conditions": ["仅使用冻结资产中可表达的时间或金额条件"], "observable_proxy": "以 EAST_D001.F001 筛查处罚事实 fact-001", "observability_type": "direct", "unobservable_parts": [], "risk_screening_boundary": "仅用于风险筛查，不直接认定监管违法或替代人工结论。", "mapping_matrix": [{"penalty_fact_id": "fact-001", "proxy_expression": "以 EAST_D001.F001 筛查处罚事实 fact-001", "table_field_path": "EAST_D001.F001", "asset_evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}, {"penalty_fact_id": "fact-001", "proxy_expression": "以 EAST_D001.F002 支持关联", "table_field_path": "EAST_D001.F002", "asset_evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}, {"penalty_fact_id": "fact-001", "proxy_expression": "以 EAST_D001.F003 支持时间窗口", "table_field_path": "EAST_D001.F003", "asset_evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}], "constraint_asset_refs": ["constraint_asset:CA-V0.3.0#record-0"]}],
        "coverage_status": "complete", "asset_version": "CA-V0.3.0", "unresolved_items": [],
    }
    return wrap("east_observable_fact_package", "observable-140", payload, producer="130")


def llm_fields() -> dict:
    return {
        "query_goal": "脱敏风险筛查",
        "main_object_and_grain": {"main_object": "脱敏机构", "grain": "一条EAST业务记录"},
        "query_entry": {"entry_table": "EAST_D001", "entry_conditions": [{"field_id": "F001", "operator": "=", "value": "脱敏值"}]},
        "related_objects_and_path": [{"object_name": "关联表", "table_id": "EAST_D002", "relation_type": "LEFT JOIN", "join_fields": [{"from_field": "EAST_D001.F002", "to_field": "EAST_D002.F002"}]}],
        "filters_and_evidence": [{"field_id": "F001", "operator": "=", "value": "脱敏值", "evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}],
        "return_fields": [{"field_id": "F001", "display_name": "脱敏字段", "source_table": "EAST_D001"}],
        "aggregation_dedup_sort_time": {"group_by_fields": ["EAST_D001.F001"], "distinct_required": False, "order_by": [{"field_id": "F001", "direction": "ASC"}], "time_window": {"field_id": "F003", "window_type": "fixed"}},
        "observability_boundary": {"answerable": ["脱敏风险筛查"], "unanswerable": ["具体处罚金额"]},
        "expected_result_shape": {"row_grain": "一条EAST业务记录", "column_set": ["F001", "F002"], "aggregation_shape": "group_by"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001", "F002", "F003"]}, {"table_id": "EAST_D002", "allowed_fields": ["F002"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1,
        "condition_coverage": [{"predicate": "F001 = 脱敏值", "positive_types": ["合规"], "negative_types": ["违规"]}],
        "code_value_coverage": [{"field_id": "F001", "target_code_values": ["A", "B"]}],
        "expected_row_group_count": {"minimum": 1, "target": 10, "tolerance_range": {"low": 1, "high": 100}},
        "join_expansion_limit": {"max_multiplier": 2.0, "max_result_rows": 1000},
    }


def review(previous: dict, kind: str = "deepseek_review_result") -> dict:
    reviewer = "170" if kind == "deepseek_review_result" else "180"
    payload = {"reviewed_package_ref": artifact_ref(previous["envelope"]), "semantic_review_report": {"reviewer_id": reviewer, "decision": "no", "error_types": ["QUERY_SPEC_ERROR"], "error_details": [{"reason": "脱敏回退验证"}], "evidence_refs": [], "route_suggestion": "140"}}
    return wrap(kind, f"review-{reviewer}", payload, producer=reviewer)


class TestAgent140Contracts(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = QuerySpecBuilder(ROOT)
        self.penalty = penalty()
        self.observable = observable()
        self.fields = llm_fields()

    def build_spec(self, **overrides) -> dict:
        fields = {**self.fields, **overrides}
        return self.builder.build_query_spec(
            self.penalty, self.observable,
            run_id="run-140", qa_id="QA-140",
            created_at=FIXED_TIME,
            **fields,
        )

    # ── Task 1: Build query specification ──────────────────────────

    def test_task1_builds_valid_query_spec(self) -> None:
        spec = self.build_spec()
        self.builder.validate_query_spec(spec)
        self.assertRegex(spec["payload"]["query_spec_id"], r"^qspec-[0-9]{3}$")
        self.assertEqual(spec["payload"]["query_specification_package_schema_version"], "query-specification-v1")
        self.assertIn("fact-001", spec["payload"]["must_preserve_fact_refs"])

    def test_task1_rejects_content_hash_drift(self) -> None:
        spec = self.build_spec()
        drifted = copy.deepcopy(spec)
        drifted["payload"]["query_goal"] = "篡改"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.builder.validate_query_spec(drifted)

    def test_task1_rejects_artifact_type_mismatch(self) -> None:
        bad_penalty = copy.deepcopy(self.penalty)
        bad_penalty["envelope"]["artifact_type"] = "wrong_type"
        bad_penalty["envelope"]["content_hash"] = content_hash(bad_penalty["envelope"], bad_penalty["payload"])
        with self.assertRaises(ContractError):
            self.builder.validate_penalty(bad_penalty)

    # ── Hard-code validator tests ──────────────────────────────────

    def test_rejects_must_preserve_facts_not_covered(self) -> None:
        with self.assertRaisesRegex(ContractError, "MUST_PRESERVE_FACTS_NOT_COVERED"):
            self.builder._validate_must_preserve_facts(
                {"source_facts": [{"penalty_fact_id": "fact-002", "must_preserve_in_question": "yes"}]},
                ["fact-001"],
            )

    def test_rejects_sql_scope_table_not_found(self) -> None:
        with self.assertRaisesRegex(ContractError, "SQL_SCOPE_TABLE_NOT_FOUND"):
            self.builder._validate_sql_scope(
                {"allowed_tables": [{"table_id": "NONEXISTENT", "allowed_fields": ["F1"]}]},
                {"observable_facts": [{"entry_table": "EAST_D001", "related_tables_fields": []}]},
            )

    def test_rejects_sql_scope_unknown_field(self) -> None:
        with self.assertRaisesRegex(ContractError, "SQL_SCOPE_FIELD_NOT_FOUND"):
            self.builder._validate_sql_scope(
                {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["FIELD-NOT-OBSERVABLE"]}]},
                self.observable["payload"],
            )

    def test_rejects_mixed_input_lineage(self) -> None:
        mixed = copy.deepcopy(self.observable)
        mixed["envelope"]["run_id"] = "other-run"
        mixed["envelope"]["content_hash"] = content_hash(mixed["envelope"], mixed["payload"])
        with self.assertRaisesRegex(ContractError, "INPUT_LINEAGE_MISMATCH"):
            self.builder.build_query_spec(self.penalty, mixed, run_id="run-140", qa_id="QA-140", created_at=FIXED_TIME, **self.fields)

    def test_rejects_ref_integrity_violation(self) -> None:
        wrong_ref = {"artifact_id": "wrong", "version": 1, "content_hash": "0" * 64}
        with self.assertRaisesRegex(ContractError, "REF_INTEGRITY_VIOLATION"):
            self.builder._validate_ref_integrity(
                wrong_ref, artifact_ref(self.observable["envelope"]),
                self.penalty["envelope"], self.observable["envelope"],
            )

    def test_rejects_invalid_count(self) -> None:
        with self.assertRaisesRegex(ContractError, "INVALID_COUNT"):
            self.builder._validate_positive_counts(0, 1)
        with self.assertRaisesRegex(ContractError, "INVALID_COUNT"):
            self.builder._validate_positive_counts(1, 0)

    def test_rejects_join_expansion_exceeded(self) -> None:
        with self.assertRaisesRegex(ContractError, "JOIN_EXPANSION_EXCEEDED"):
            self.builder._validate_join_limit({"max_multiplier": -1, "max_result_rows": 100})
        with self.assertRaisesRegex(ContractError, "JOIN_EXPANSION_EXCEEDED"):
            self.builder._validate_join_limit({"max_multiplier": 2.0, "max_result_rows": 0})

    def test_rejects_row_group_range_invalid(self) -> None:
        with self.assertRaisesRegex(ContractError, "ROW_GROUP_RANGE_INVALID"):
            self.builder._validate_row_group_count({"minimum": 0, "target": 5, "tolerance_range": {"low": 10, "high": 5}})

    def test_rejects_inconsistent_row_group_targets(self) -> None:
        with self.assertRaisesRegex(ContractError, "ROW_GROUP_TARGET_INCONSISTENT"):
            self.builder._validate_row_group_count({"minimum": 100, "target": 1, "tolerance_range": {"low": 0, "high": 2}})

    def test_rejects_version_overwrite_attempted(self) -> None:
        with self.assertRaisesRegex(ContractError, "VERSION_OVERWRITE_ATTEMPTED"):
            self.builder._validate_version_immutability(1, {"artifact_id": "x", "version": 1, "content_hash": "0" * 64})
        with self.assertRaisesRegex(ContractError, "VERSION_OVERWRITE_ATTEMPTED"):
            self.builder._validate_version_immutability(2, None)

    # ── Task 2: Review feedback ────────────────────────────────────

    def test_review_170_180_supersede_and_version_increment(self) -> None:
        first = self.build_spec()
        for kind in ("deepseek_review_result", "glm_review_result"):
            second = self.builder.handle_review_feedback(
                self.penalty, self.observable, review(first, kind), first,
                run_id="run-140", qa_id="QA-140", attempt_no=2,
                created_at=FIXED_TIME, **self.fields,
            )
            self.assertEqual(second["envelope"]["version"], 2)
            self.assertEqual(second["envelope"]["attempt_no"], 2)
            self.assertEqual(second["envelope"]["supersedes_ref"], artifact_ref(first["envelope"]))
            self.assertEqual(second["envelope"]["status"], "candidate")
            self.assertEqual(second["payload"]["query_spec_id"], first["payload"]["query_spec_id"])
            self.assertEqual(second["envelope"]["parent_artifact_refs"], [artifact_ref(self.penalty["envelope"]), artifact_ref(self.observable["envelope"]), artifact_ref(review(first, kind)["envelope"]), artifact_ref(first["envelope"])])

    def test_third_attempt_valid_candidate_is_not_blocked(self) -> None:
        first = self.build_spec()
        second = self.builder.handle_review_feedback(
            self.penalty, self.observable, review(first, "deepseek_review_result"), first,
            run_id="run-140", qa_id="QA-140", attempt_no=2,
            created_at=FIXED_TIME, **self.fields,
        )
        candidate = self.builder.handle_review_feedback(
            self.penalty, self.observable, review(second, "glm_review_result"), second,
            run_id="run-140", qa_id="QA-140", attempt_no=3,
            created_at=FIXED_TIME, **self.fields,
        )
        self.assertEqual(candidate["envelope"]["status"], "candidate")
        self.assertEqual(candidate["envelope"]["attempt_no"], 3)

    def test_third_attempt_invalid_candidate_blocks_manual(self) -> None:
        first = self.build_spec()
        second = self.builder.handle_review_feedback(self.penalty, self.observable, review(first), first, run_id="run-140", qa_id="QA-140", attempt_no=2, created_at=FIXED_TIME, **self.fields)
        invalid_fields = {**self.fields, "expected_row_group_count": {"minimum": 100, "target": 1, "tolerance_range": {"low": 0, "high": 2}}}
        blocked = self.builder.handle_review_feedback(self.penalty, self.observable, review(second, "glm_review_result"), second, run_id="run-140", qa_id="QA-140", attempt_no=3, created_at=FIXED_TIME, **invalid_fields)
        self.assertEqual(blocked["envelope"]["status"], "blocked_manual")
        self.assertEqual(blocked["payload"]["query_spec_id"], first["payload"]["query_spec_id"])

    def test_rejects_review_not_routed_to_140(self) -> None:
        first = self.build_spec()
        bad = review(first)
        bad["payload"]["semantic_review_report"]["route_suggestion"] = "130"
        bad["envelope"]["content_hash"] = content_hash(bad["envelope"], bad["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEW_NOT_ROUTED_TO_140"):
            self.builder.handle_review_feedback(
                self.penalty, self.observable, bad, first,
                run_id="run-140", qa_id="QA-140", attempt_no=2,
                created_at=FIXED_TIME, **self.fields,
            )

    def test_rejects_reviewed_package_ref_mismatch(self) -> None:
        first = self.build_spec()
        bad = review(first)
        bad["payload"]["reviewed_package_ref"]["content_hash"] = "c" * 64
        bad["envelope"]["content_hash"] = content_hash(bad["envelope"], bad["payload"])
        with self.assertRaisesRegex(ContractError, "REVIEWED_PACKAGE_REF_MISMATCH"):
            self.builder.handle_review_feedback(
                self.penalty, self.observable, bad, first,
                run_id="run-140", qa_id="QA-140", attempt_no=2,
                created_at=FIXED_TIME, **self.fields,
            )

    def test_rejects_attempt_out_of_range(self) -> None:
        first = self.build_spec()
        with self.assertRaisesRegex(ContractError, "ATTEMPT_OUT_OF_RANGE"):
            self.builder.handle_review_feedback(
                self.penalty, self.observable, review(first), first,
                run_id="run-140", qa_id="QA-140", attempt_no=4,
                created_at=FIXED_TIME, **self.fields,
            )

    # ── Probe integration test ─────────────────────────────────────

    def test_sanitized_probe_passes(self) -> None:
        result = run_sanitized_probe(ROOT)
        s = result["summary"]
        self.assertTrue(s["bad_hash_rejected"])
        self.assertTrue(s["review_170_executed"])
        self.assertTrue(s["review_180_valid_candidate"])
        self.assertTrue(s["review_180_invalid_blocked"])
        self.assertTrue(s["stub_150_consumed"])
        self.assertTrue(s["stub_170_consumed"])
        self.assertTrue(s["stub_180_consumed"])
        self.assertTrue(s["stub_220_consumed"])
        self.assertTrue(s["stub_260_consumed"])
        self.assertTrue(s["validator_must_preserve_rejects"])
        self.assertTrue(s["validator_invalid_count_rejects"])
        self.assertTrue(s["validator_join_limit_rejects"])
        self.assertTrue(s["validator_row_group_rejects"])
        self.assertTrue(s["validator_sql_scope_rejects"])
        self.assertTrue(s["validator_sql_scope_field_rejects"])
        self.assertTrue(s["validator_row_group_consistency_rejects"])
        self.assertTrue(s["validator_version_immutability_rejects"])


if __name__ == "__main__":
    unittest.main()
