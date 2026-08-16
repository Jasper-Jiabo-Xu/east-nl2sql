from __future__ import annotations

import copy
import importlib
import unittest
from pathlib import Path

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
_closure_builder = importlib.import_module("east_v5.agents.230.builder")
_closure_probe = importlib.import_module("east_v5.agents.230.probe")
_generator = importlib.import_module("east_v5.agents.251.generator")
try:
    _stub = importlib.import_module("agents.251.approved_252_stub")
except ModuleNotFoundError:
    _stub = importlib.import_module("tests.agents.251.approved_252_stub")


def wrap(artifact_type, artifact_id, payload, producer, previous, *, status="rejected"):
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": previous["envelope"]["run_id"], "qa_id": previous["envelope"]["qa_id"], "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": previous["envelope"]["attempt_no"], "producer_id": producer, "parent_artifact_refs": [artifact_ref(previous["envelope"])], "input_hashes": [previous["envelope"]["content_hash"]], "status": status, "mode": "event_data", "created_at": previous["envelope"]["created_at"], "trace_id": previous["envelope"]["trace_id"], "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def feedback_252(previous):
    payload = {"schema_version": "v5.orm-validation-failed-feedback/v1", "orm_plan_ref": artifact_ref(previous["envelope"]), "decision": "fail", "validation_types": ["static_ast"], "failed_items": [{"failed_rule_ids": ["AST-001"], "operation_locations": ["orm-op-001"], "expected_values": ["allowed API"], "actual_values": ["fixture failure"], "error_details": "脱敏失败"}]}
    return wrap("orm_validation_failed_feedback", "eas33-252-feedback", payload, "252", previous)


def feedback_260(previous):
    payload = {"schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data", "input_data_refs": [], "input_orm_ref": artifact_ref(previous["envelope"]), "sandbox_snapshot_id": "eas33-sanitized-snapshot", "failure_details": {"error_code": "ORM_PLAN_ERROR", "error_stage": "orm_execution", "error_location": "orm-op-001", "expected_values": ["受限调用计划"], "actual_values": ["脱敏失败"], "sql_error_detail": None, "regression_metrics": {"positive_hit": 0}}, "route_target": "251", "retry_count": previous["envelope"]["attempt_no"]}
    return wrap("sql_regression_failed_feedback", "eas33-260-feedback", payload, "260", previous)


class RestrictedOrmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = _closure_probe._structure()
        self.operation = _closure_builder.OperationClosureBuilder(ROOT).build(self.structure)
        self.builder = _generator.RestrictedOrmGenerator(ROOT)
        self.package = self.builder.build(self.structure, self.operation)

    def test_frozen_contract_hash_and_independent_252_stub(self) -> None:
        repeated = self.builder.build(self.structure, self.operation)
        self.assertEqual(self.package, repeated)
        payload = self.package["payload"]
        self.assertEqual(payload["execution_contract"]["transaction_id"], "txn-001")
        self.assertIn("data_placeholder_ref", payload["operations"][0])
        self.assertEqual(_stub.consume(self.package, ROOT)["empty_write_count"], 0)

    def test_rejects_foundation_unknown_raw_sql_and_contract_hash_drift(self) -> None:
        foundation = copy.deepcopy(self.structure); foundation["envelope"]["mode"] = "foundation"; foundation["envelope"]["content_hash"] = content_hash(foundation["envelope"], foundation["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_ORM_FORBIDDEN"):
            self.builder.build(foundation, self.operation)
        source = copy.deepcopy(self.package); source["payload"]["orm_source_code"] += "transaction.execute('INSERT')\n"; source["payload"]["code_hash"] = self.builder._code_hash(source["payload"]["orm_source_code"], source["payload"]["execution_contract"], source["payload"]["operations"]); source["envelope"]["content_hash"] = content_hash(source["envelope"], source["payload"])
        with self.assertRaisesRegex(ContractError, "252_STUB_API_REJECTED"):
            _stub.consume(source, ROOT)
        drifted = copy.deepcopy(self.package); drifted["payload"]["operations"][0]["where_ref"] = "drift"; drifted["envelope"]["content_hash"] = content_hash(drifted["envelope"], drifted["payload"])
        with self.assertRaisesRegex(ContractError, "OPERATION_OR_SLOT_MISMATCH"):
            self.builder.validate_restricted_orm(drifted, self.structure, self.operation)
        unknown = copy.deepcopy(self.package); unknown["payload"]["unknown"] = True
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD:RESTRICTED_ORM"):
            self.builder.validate_restricted_orm(unknown, self.structure, self.operation)

    def test_full_252_feedback_contract_rejects_hash_and_attempt_drift(self) -> None:
        feedback = feedback_252(self.package)
        second = self.builder.apply_252_feedback(self.package, feedback, self.structure, self.operation)
        self.assertEqual((second["envelope"]["version"], second["envelope"]["attempt_no"]), (2, 2))
        self.assertEqual(second["envelope"]["supersedes_ref"], artifact_ref(self.package["envelope"]))
        hash_drift = copy.deepcopy(feedback); hash_drift["payload"]["failed_items"][0]["error_details"] = "漂移"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.builder.apply_252_feedback(self.package, hash_drift, self.structure, self.operation)
        attempt_drift = copy.deepcopy(feedback); attempt_drift["envelope"]["attempt_no"] = 2; attempt_drift["envelope"]["content_hash"] = content_hash(attempt_drift["envelope"], attempt_drift["payload"])
        with self.assertRaisesRegex(ContractError, "ATTEMPT_LINEAGE_MISMATCH"):
            self.builder.apply_252_feedback(self.package, attempt_drift, self.structure, self.operation)

    def test_full_260_feedback_contract_rejects_route_hash_and_attempt_drift(self) -> None:
        second = self.builder.apply_252_feedback(self.package, feedback_252(self.package), self.structure, self.operation)
        feedback = feedback_260(second)
        third = self.builder.apply_260_feedback(second, feedback, self.structure, self.operation)
        self.assertEqual((third["envelope"]["version"], third["envelope"]["attempt_no"], third["envelope"]["status"]), (3, 3, "blocked_manual"))
        wrong_route = copy.deepcopy(feedback); wrong_route["payload"]["route_target"] = "241"; wrong_route["envelope"]["content_hash"] = content_hash(wrong_route["envelope"], wrong_route["payload"])
        with self.assertRaisesRegex(ContractError, "REGRESSION_NOT_ROUTED_TO_251"):
            self.builder.apply_260_feedback(second, wrong_route, self.structure, self.operation)
        wrong_attempt = copy.deepcopy(feedback); wrong_attempt["payload"]["retry_count"] = 1; wrong_attempt["envelope"]["content_hash"] = content_hash(wrong_attempt["envelope"], wrong_attempt["payload"])
        with self.assertRaisesRegex(ContractError, "REGRESSION_NOT_ROUTED_TO_251"):
            self.builder.apply_260_feedback(second, wrong_attempt, self.structure, self.operation)
