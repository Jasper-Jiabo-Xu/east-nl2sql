from __future__ import annotations

import copy
from importlib import import_module
import unittest
from pathlib import Path

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

QuestionQuerySpecBuilder = import_module("east_v5_1.agents.140").QuestionQuerySpecBuilder
PrecheckAgent = import_module("east_v5.agents.160.precheck").PrecheckAgent


ROOT = Path(__file__).resolve().parents[3]
TIME = "2026-08-24T00:00:00+00:00"


def wrap(kind: str, identity: str, payload: dict, *, producer: str, attempt: int = 1) -> dict:
    envelope = {"artifact_id": identity, "artifact_type": kind, "run_id": "run-v51", "qa_id": "QA-v51", "version": 1,
                "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
                "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": [], "input_hashes": [], "status": "candidate",
                "mode": "question_sql", "created_at": TIME, "trace_id": "trace-v51", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def question() -> dict:
    return wrap("question_input", "question-v51", {
        "question_id": "QA-v51", "question_text": "查询脱敏表的字段一", "dataset_source": "east_model",
        "answer_contract": {"expected_tables": ["T1"], "expected_fields": ["T1.F1"]},
        "parsed_intent": {"predicate_conditions": [{"field": "F1", "operator": "=", "value": "A"}]},
        "quality_assessment": {"has_ambiguity": False, "requires_human_correction": False},
    }, producer="110")


def constraints() -> dict:
    return wrap("constraint_asset_package", "constraints-v51", {
        "request_id": "request-v51", "asset_version": "CA-V0.3.0", "executed_queries": [],
        "matched_records": [{"record_type": "single_field", "data": {"table_id": "T1", "field_id": "F1"},
                             "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": []}],
        "constraint_summary": {"total_matched": 1, "asset_types_covered": ["single_field"]}, "unmatched_items": [], "query_trace": [],
    }, producer="000")


def candidate() -> dict:
    return {
        "query_goal": "脱敏查询", "main_object_and_grain": {"main_object": "脱敏对象", "grain": "一条记录"},
        "query_entry": {"entry_table": "T1", "entry_conditions": [{"field_id": "F1", "operator": "=", "value": "A"}]},
        "related_objects_and_path": [],
        "filters_and_evidence": [{"field_id": "F1", "operator": "=", "value": "A", "evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}],
        "return_fields": [{"field_id": "F1", "display_name": "字段一", "source_table": "T1"}],
        "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["字段一"], "unanswerable": []},
        "expected_result_shape": {"row_grain": "一条记录", "column_set": ["F1"], "aggregation_shape": "none"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [],
        "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}},
        "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 10},
    }


class QuestionQuerySpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = QuestionQuerySpecBuilder(ROOT)
        self.question, self.constraints, self.fields = question(), constraints(), candidate()

    def build(self, **overrides) -> dict:
        return self.builder.build_query_spec_from_question(self.question, self.constraints, run_id="run-v51", qa_id="QA-v51", created_at=TIME, candidate={**self.fields, **overrides})

    def test_stable_canonical_output_and_real_150_consumer(self) -> None:
        first, second = self.build(), self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["payload"]["penalty_fact_package_ref"], artifact_ref(self.question["envelope"]))
        self.assertEqual(first["payload"]["observable_fact_package_ref"], artifact_ref(self.constraints["envelope"]))
        self.assertEqual(first["envelope"]["parent_artifact_refs"], [artifact_ref(self.question["envelope"]), artifact_ref(self.constraints["envelope"])])
        self.assertEqual(first["envelope"]["input_hashes"], [self.question["envelope"]["content_hash"], self.constraints["envelope"]["content_hash"]])
        consumer = PendingPrecheckBuilder(ROOT)
        consumer.validate_query_spec(first)
        sql = "SELECT T1.F1 FROM T1 WHERE T1.F1 = 'A'"
        pending = consumer.build_pending_precheck(first, run_id="run-v51", qa_id="QA-v51", sql_gold=sql, clear_question="查询脱敏表的字段一", created_at=TIME,
            sql_explanation={"select": "F1", "from_join": "T1", "where": "F1", "aggregation": "无", "sort": "无", "business_meaning": "查询"},
            business_event_candidates=[{"event_name": "查询", "objective": "查询", "objects": ["T1"], "state_changes": []}],
            specification_mapping=[{"spec_item": item, "question_fragment": "查询脱敏表的字段一", "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS], query_parameter_bindings=[])
        self.assertEqual(pending["payload"]["query_spec_ref"], artifact_ref(first["envelope"]))
        self.assertEqual(PrecheckAgent(ROOT).precheck(pending, first, checked_at=TIME)["decision"], "pass")

    def test_rejects_ambiguity_hash_lineage_and_attempt_drift(self) -> None:
        ambiguous = copy.deepcopy(self.question); ambiguous["payload"]["quality_assessment"]["has_ambiguity"] = True; ambiguous["envelope"]["content_hash"] = content_hash(ambiguous["envelope"], ambiguous["payload"])
        with self.assertRaisesRegex(ContractError, "QUESTION_AMBIGUITY_UNRESOLVED"):
            self.builder.build_query_spec_from_question(ambiguous, self.constraints, run_id="run-v51", qa_id="QA-v51", candidate=self.fields)
        drift = copy.deepcopy(self.question); drift["payload"]["question_text"] = "篡改"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.builder.build_query_spec_from_question(drift, self.constraints, run_id="run-v51", qa_id="QA-v51", candidate=self.fields)
        mixed = copy.deepcopy(self.constraints); mixed["envelope"]["trace_id"] = "other"; mixed["envelope"]["content_hash"] = content_hash(mixed["envelope"], mixed["payload"])
        with self.assertRaisesRegex(ContractError, "INPUT_LINEAGE_MISMATCH"):
            self.builder.build_query_spec_from_question(self.question, mixed, run_id="run-v51", qa_id="QA-v51", candidate=self.fields)
        with self.assertRaisesRegex(ContractError, "ATTEMPT_LINEAGE_MISMATCH"):
            self.builder.build_query_spec_from_question(self.question, self.constraints, run_id="run-v51", qa_id="QA-v51", attempt_no=2, candidate=self.fields)
        version_drift = copy.deepcopy(self.constraints); version_drift["envelope"]["version"] = 2; version_drift["envelope"]["content_hash"] = content_hash(version_drift["envelope"], version_drift["payload"])
        with self.assertRaisesRegex(ContractError, "VERSION_LINEAGE_MISMATCH"):
            self.builder.build_query_spec_from_question(self.question, version_drift, run_id="run-v51", qa_id="QA-v51", candidate=self.fields)

    def test_rejects_unmatched_scope_return_and_evidence_failures(self) -> None:
        unmatched = copy.deepcopy(self.constraints); unmatched["payload"]["unmatched_items"] = [{"target": "T1.F1", "reason": "missing"}]; unmatched["envelope"]["content_hash"] = content_hash(unmatched["envelope"], unmatched["payload"])
        with self.assertRaisesRegex(ContractError, "CONSTRAINT_ASSET_UNMATCHED"):
            self.builder.build_query_spec_from_question(self.question, unmatched, run_id="run-v51", qa_id="QA-v51", candidate=self.fields)
        outside = candidate(); outside["sql_schema_scope"] = {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2"]}]}
        with self.assertRaisesRegex(ContractError, "SQL_SCOPE_FIELD_OUT_OF_ASSET"):
            self.builder.build_query_spec_from_question(self.question, self.constraints, run_id="run-v51", qa_id="QA-v51", candidate=outside)
        no_return = candidate(); no_return["return_fields"] = []
        with self.assertRaisesRegex(ContractError, "RETURN_FIELD_NOT_COVERED"):
            self.builder.build_query_spec_from_question(self.question, self.constraints, run_id="run-v51", qa_id="QA-v51", candidate=no_return)
        no_evidence = candidate(); no_evidence["filters_and_evidence"][0]["evidence_ref"] = "forged"
        with self.assertRaisesRegex(ContractError, "FILTER_EVIDENCE_MISSING"):
            self.builder.build_query_spec_from_question(self.question, self.constraints, run_id="run-v51", qa_id="QA-v51", candidate=no_evidence)
