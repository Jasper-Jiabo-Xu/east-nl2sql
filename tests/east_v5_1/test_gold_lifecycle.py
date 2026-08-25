from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256
from east_v5_1.gold_lifecycle import GoldLifecycleController

TIME = "2026-08-25T00:00:00+00:00"


def ref(name: str, char: str) -> dict[str, object]:
    return {"artifact_id": name, "version": 1, "content_hash": char * 64}


def package(artifact_type: str, producer: str, payload: dict, *, parents=None, status="validated") -> dict:
    parents = parents or []
    envelope = {"artifact_id": f"fixture-{artifact_type}", "artifact_type": artifact_type, "run_id": "gold-run", "qa_id": "QA-GOLD", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": status, "mode": "event_data", "created_at": TIME, "trace_id": "gold-trace", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def reviewed() -> dict:
    content = {"clear_question": "脱敏问题", "sql_gold": "SELECT value FROM EVENT_RECORD", "query_parameter_bindings": [], "sql_explanation": {"select": "value", "from_join": "EVENT_RECORD", "where": "固定", "aggregation": "无", "sort": "无", "business_meaning": "脱敏"}, "business_event_candidates": [{"event_name": "fixture", "objective": "fixture", "objects": ["record"], "state_changes": []}], "specification_mapping": [{"spec_item": "S1", "question_fragment": "问题", "sql_fragment": "SELECT"}]}
    body = {"schema_version": "v5.question-sql-dual-review-passed/v1", "candidate_ref": ref("candidate", "a"), "candidate_content": content, "query_specification_package": ref("query-spec", "b"), "penalty_fact_package": ref("penalty", "c"), "observable_fact_package": ref("observable", "d"), "constraint_evidence_summary": {"tables": ["EVENT_RECORD"], "fields": ["EVENT_RECORD.value"], "data_elements": [], "relationships": [], "source_refs": ["fixture"]}, "precheck_report": {"decision": "pass", "report_hash": "e" * 64}, "deepseek_review": {"decision": "pass", "issue_level": "none", "reason": "ok", "review_hash": "f" * 64}, "glm_review": {"decision": "pass", "issue_level": "none", "reason": "ok", "review_hash": "0" * 64}, "adjudication": {"decision": "pass", "report_hash": "1" * 64}, "review_round": 1, "package_hash": ""}
    body["package_hash"] = sha256({key: value for key, value in body.items() if key != "package_hash"})
    return package("question_sql_dual_review_passed", "110", body)


def next_attempt(package_value: dict) -> dict:
    result = copy.deepcopy(package_value)
    result["envelope"]["attempt_no"] = 2
    result["payload"]["adjudication"]["report_hash"] = "9" * 64
    result["payload"]["package_hash"] = sha256({key: value for key, value in result["payload"].items() if key != "package_hash"})
    result["envelope"]["content_hash"] = content_hash(result["envelope"], result["payload"])
    return result


def regression(reviewed_package: dict) -> dict:
    reviewed_ref = artifact_ref(reviewed_package["envelope"])
    data, orm, context, binding = ref("verified-data", "2"), ref("frozen-orm", "3"), ref("event-context", "4"), ref("binding", "5")
    metrics = {"positive_negative_metrics": {"positive_hits": 1, "minimum_positive_count": 1, "negative_fixture_count": 1, "minimum_negative_count": 1, "negative_hits": 0, "negative_excluded": True, "condition_coverage_passed": True, "code_value_coverage_passed": True, "passed": True}, "density_group_metrics": {"row_count": 1, "distinct_count": 1, "group_count": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}, "distinct_required": True, "passed": True}, "join_expansion_metrics": {"row_count": 1, "baseline_grain_count": 1, "actual_multiplier": 1.0, "max_multiplier": 1.0, "max_result_rows": 1, "passed": True}}
    body = {"schema_version": "v5.regression-passed-data-orm/v2", "regression_package_id": "gold-260", "mode": "event_data", "data_package_refs": [data], "orm_plan_ref": orm, "question_sql_ref": reviewed_ref, "reviewed_question_sql_ref": ref("reviewed-210", "6"), "event_query_context_ref": context, "query_parameter_binding_ref": binding, "query_parameter_binding_hash": binding["content_hash"], "query_spec_ref": reviewed_package["payload"]["query_specification_package"], "execution_instances": {"orm_params": {"fixture": "value"}, "query_binding_names": [], "operations": ["insert"]}, "sandbox_snapshot_id": "copy-1", "sandbox_execution_report": {"operations": [{"operation": "insert", "table_id": "EVENT_RECORD", "values": {"id": "e-1"}, "rowcount": 1}], "write_count": 1, "rolled_back": False}, "sql_regression_report": {"sql_gold": "SELECT value FROM EVENT_RECORD", "row_count": 1, **metrics}, "executable_package_hash": "7" * 64, "regression_status": "passed", "regressed_at": TIME}
    return package("database_copy_regression", "260", body, parents=[body["reviewed_question_sql_ref"], context, binding])


def release_material(reviewed_package: dict, regression_package: dict) -> tuple[dict, dict]:
    approved_ref, regression_ref = artifact_ref(reviewed_package["envelope"]), artifact_ref(regression_package["envelope"])
    body = {"schema_version": "v5.release-candidate/v2", "release_candidate_id": "gold-release", "release_mode": "event_data", "approved_question_sql_ref": approved_ref, "event_regression_passed_ref": regression_ref, "foundation_regression_report_ref": None, "target_database_version": "fixture-db-v1", "target_question_dataset_version": "fixture-question-v1", "idempotency_key": "gold-release-key", "expected_write_summary": {"orm_execution": {"insert_or_update": 1}}, "package_hashes": {"question_sql": approved_ref["content_hash"], "data": "2" * 64, "orm": "3" * 64, "query_binding": "5" * 64, "regression": regression_ref["content_hash"]}, "resume_qa_ref": None}
    candidate = package("release_candidate", "210", body, parents=[approved_ref, regression_ref], status="candidate")
    receipt_body = {"release_id": "gold-receipt", "release_candidate_ref": artifact_ref(candidate["envelope"]), "commit_status": "committed", "database_version_before": "fixture-db-v1", "database_version_after": "fixture-db-v2", "written_rows_by_table": {"EVENT_RECORD": {"insert": 1, "update": 0, "primary_key_digest": "8" * 64}}, "question_sql_record_id": "QA-GOLD", "idempotency_key": "gold-release-key", "committed_package_hash": candidate["envelope"]["content_hash"], "manifest_location": "release-manifests/gold.json", "trace_location": "release-traces/gold.json", "committed_at": TIME, "failure_detail": None}
    return candidate, package("release_receipt", "010", receipt_body, parents=[artifact_ref(candidate["envelope"])], status="approved")


def candidates() -> list[dict]:
    return [{"reviewer_id": f"baseline-{index}", "original_sql_hash": sha256(f"original-{index}"), "normalized_sql_hash": sha256(f"normalized-{index}"), "lock_evidence_hash": sha256(f"lock-{index}"), "agreement": {"exact": index == 1, "structural": True, "execution": True}} for index in range(1, 7)]


class GoldLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.controller = GoldLifecycleController(ROOT)
        self.reviewed = reviewed()

    def test_two_phase_happy_path_is_deterministic_and_uses_canonical_packages(self):
        first = self.controller.semantic_candidate(self.reviewed, list(reversed(candidates())), state_at=TIME)
        repeated = self.controller.semantic_candidate(self.reviewed, candidates(), state_at=TIME)
        self.assertEqual(first, repeated)
        self.assertEqual(first["payload"]["gold_state"], "semantic_candidate")
        self.assertIsNone(first["payload"]["final_gold_hash"])
        regression_package = regression(self.reviewed)
        confirmed = self.controller.execution_confirmed(first, regression_package, state_at=TIME)
        self.assertEqual(confirmed["payload"]["gold_state"], "execution_confirmed")
        candidate, receipt = release_material(self.reviewed, regression_package)
        final = self.controller.formal_released(confirmed, candidate, receipt, state_at=TIME)
        self.assertEqual(final["payload"]["gold_state"], "formal_released")
        self.assertIsNotNone(final["payload"]["final_gold_hash"])
        final_drift = copy.deepcopy(final)
        final_drift["payload"]["final_gold_hash"] = "0" * 64
        final_drift["artifact_hash"] = sha256({"artifact_id": final_drift["artifact_id"], "payload": final_drift["payload"]})
        with self.assertRaisesRegex(ContractError, "FINAL_GOLD_HASH_DRIFT"):
            self.controller.adjudicate_failure(final_drift, "DATA_DENSITY_DEFECT")

    def test_missing_or_bad_260_and_candidate_mutation_fail_closed(self):
        semantic = self.controller.semantic_candidate(self.reviewed, candidates(), state_at=TIME)
        candidate, receipt = release_material(self.reviewed, regression(self.reviewed))
        with self.assertRaisesRegex(ContractError, "FORMAL_RELEASE_TRANSITION_INVALID"):
            self.controller.formal_released(semantic, candidate, receipt, state_at=TIME)
        drifted = copy.deepcopy(semantic)
        drifted["payload"]["candidate_lock"]["candidates"][1]["agreement"]["exact"] = True
        drifted["artifact_hash"] = sha256({"artifact_id": drifted["artifact_id"], "payload": drifted["payload"]})
        with self.assertRaisesRegex(ContractError, "CANDIDATE_SET_HASH_DRIFT"):
            self.controller.execution_confirmed(drifted, regression(self.reviewed), state_at=TIME)
        invalid = regression(self.reviewed)
        invalid["payload"]["regression_status"] = "failed"
        invalid["envelope"]["content_hash"] = content_hash(invalid["envelope"], invalid["payload"])
        with self.assertRaisesRegex(ContractError, "REGRESSION_CANONICAL_INVALID"):
            self.controller.execution_confirmed(semantic, invalid, state_at=TIME)

    def test_adjudication_routes_are_stable_and_sql_forces_new_attempt(self):
        semantic = self.controller.semantic_candidate(self.reviewed, candidates(), state_at=TIME)
        expected = {"SQL_JOIN_DEFECT": ("150", "110", True), "DATA_DENSITY_DEFECT": ("241", None, False), "ORM_EVENT_ORDER_DEFECT": ("251", None, False), "QUESTION_POLICY_AMBIGUITY": ("blocked_manual", None, False)}
        for code, route in expected.items():
            with self.subTest(code=code):
                decision = self.controller.adjudicate_failure(semantic, code)
                self.assertEqual((decision["route_target"], decision["route_via"], decision["new_attempt_required"]), route)
        self.assertIn("260", self.controller.adjudicate_failure(semantic, "SQL_JOIN_DEFECT")["invalidates"])
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FAILURE_ROUTE"):
            self.controller.adjudicate_failure(semantic, "UNKNOWN")

    def test_sql_retry_requires_a_fresh_attempt_and_evidence(self):
        semantic = self.controller.semantic_candidate(self.reviewed, candidates(), state_at=TIME)
        successor = self.controller.restart_sql_attempt(semantic, next_attempt(self.reviewed), candidates(), state_at=TIME)
        self.assertEqual(successor["payload"]["attempt"], 2)
        self.assertNotEqual(successor["payload"]["semantic_decision_hash"], semantic["payload"]["semantic_decision_hash"])
        with self.assertRaisesRegex(ContractError, "SQL_RETRY_LINEAGE_OR_ATTEMPT_DRIFT"):
            self.controller.restart_sql_attempt(semantic, self.reviewed, candidates(), state_at=TIME)


if __name__ == "__main__":
    unittest.main()
