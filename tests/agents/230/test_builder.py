from __future__ import annotations

import copy
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import content_hash
from east_v5.governance import ContractError


FIXED_TIME = "2026-08-16T00:00:00+00:00"


def structure_closure(*, mode: str = "event_data", references: list[dict] | None = None) -> dict:
    payload = {
        "schema_version": "v5.structure-closure/v1",
        "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0",
        "tables": ["FIXTURE_ACCOUNT", "FIXTURE_CUSTOMER"],
        "fields": ["FIXTURE_ACCOUNT.CUSTOMER_ID", "FIXTURE_ACCOUNT.STATUS", "FIXTURE_CUSTOMER.ID"],
        "references": references if references is not None else [
            {"type": "cross_table", "data": {"from": "FIXTURE_ACCOUNT.CUSTOMER_ID", "to": "FIXTURE_CUSTOMER.ID"}},
            {"type": "object_detail_state", "data": {"object": "FIXTURE_ACCOUNT", "state_field": "FIXTURE_ACCOUNT.STATUS"}},
        ],
    }
    envelope = {
        "artifact_id": "fixture-structure-closure", "artifact_type": "structure_closure",
        "run_id": "eas30-sanitized-run", "qa_id": "QA-EAS30", "version": 1,
        "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": 1, "producer_id": "220",
        "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": mode,
        "created_at": FIXED_TIME, "trace_id": "eas30-sanitized-trace", "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


class OperationClosureBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = importlib.import_module("east_v5.agents.230.builder").OperationClosureBuilder(ROOT)
        self.input = structure_closure()

    def test_builds_recomputable_event_only_plan_for_both_consumers(self) -> None:
        package = self.builder.build(self.input)
        payload = package["payload"]

        self.assertEqual(package["envelope"]["artifact_type"], "operation_closure")
        self.assertEqual(package["envelope"]["producer_id"], "230")
        self.assertEqual(package["envelope"]["mode"], "event_data")
        self.assertEqual(payload["schema_version"], "v5.operation-closure/v1")
        self.assertEqual(payload["consumers"], ["241", "251"])
        self.assertEqual([item["sequence_no"] for item in payload["operations"]], list(range(1, len(payload["operations"]) + 1)))
        self.assertTrue({"READ", "CHECK", "INSERT", "UPDATE"}.issubset({item["operation_type"] for item in payload["operations"]}))
        self.assertEqual(self.builder.build(self.input)["envelope"]["content_hash"], package["envelope"]["content_hash"])

    def test_both_downstream_stubs_consume_exact_same_package(self) -> None:
        package = self.builder.build(self.input)
        self.assertEqual(self.builder.consume_downstream_stub("241", package)["consumer"], "241")
        self.assertEqual(self.builder.consume_downstream_stub("251", package)["consumer"], "251")

    def test_foundation_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "FOUNDATION_OPERATION_CLOSURE_FORBIDDEN"):
            self.builder.build(structure_closure(mode="foundation"))

    def test_unknown_input_field_and_hash_drift_are_rejected(self) -> None:
        unknown = copy.deepcopy(self.input)
        unknown["payload"]["unexpected"] = True
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD:STRUCTURE_CLOSURE"):
            self.builder.build(unknown)

        drifted = copy.deepcopy(self.input)
        drifted["payload"]["tables"] = ["FIXTURE_ACCOUNT"]
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.builder.build(drifted)

    def test_rejects_invalid_relationship_and_cycle(self) -> None:
        invalid = structure_closure(references=[{"type": "cross_table", "data": {"from": "FIXTURE_ACCOUNT.UNKNOWN", "to": "FIXTURE_CUSTOMER.ID"}}])
        with self.assertRaisesRegex(ContractError, "REFERENCE_FIELD_OUT_OF_CLOSURE"):
            self.builder.build(invalid)

        package = self.builder.build(self.input)
        cycle = copy.deepcopy(package)
        cycle["payload"]["operations"][0]["dependencies"] = [cycle["payload"]["operations"][-1]["operation_step_id"]]
        cycle["envelope"]["content_hash"] = content_hash(cycle["envelope"], cycle["payload"])
        with self.assertRaisesRegex(ContractError, "DEPENDENCY_CYCLE"):
            self.builder.validate_operation_closure_package(cycle)

    def test_rejects_wrong_consumer_placeholder_and_attempt(self) -> None:
        package = self.builder.build(self.input)
        wrong_consumer = copy.deepcopy(package)
        wrong_consumer["payload"]["consumers"] = ["241"]
        wrong_consumer["envelope"]["content_hash"] = content_hash(wrong_consumer["envelope"], wrong_consumer["payload"])
        with self.assertRaisesRegex(ContractError, "CONSUMER_ROUTE_INVALID"):
            self.builder.validate_operation_closure_package(wrong_consumer)

        dangling = copy.deepcopy(package)
        dangling["payload"]["operations"][0]["data_placeholders"] = ["placeholder:FIXTURE_ACCOUNT.UNKNOWN"]
        dangling["envelope"]["content_hash"] = content_hash(dangling["envelope"], dangling["payload"])
        with self.assertRaisesRegex(ContractError, "PLACEHOLDER_DANGLING"):
            self.builder.validate_operation_closure_package(dangling)

        with self.assertRaisesRegex(ContractError, "ATTEMPT_OUT_OF_RANGE"):
            self.builder.retry_status(4)


if __name__ == "__main__":
    unittest.main()
