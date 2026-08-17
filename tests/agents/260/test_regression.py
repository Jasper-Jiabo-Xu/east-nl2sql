from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

regression = importlib.import_module("east_v5.agents.260.regression")
TIME = "2026-08-15T00:00:00+00:00"


def wrap(artifact_type, artifact_id, payload, producer, mode, status="candidate", parents=None):
    parents = parents or []
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "foundation-fixture", "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": producer, "parent_artifact_refs": parents, "input_hashes": [p["content_hash"] for p in parents], "status": status, "mode": mode, "created_at": TIME, "trace_id": "foundation-task-fixture", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


class FoundationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads((ROOT / "fixtures/artifacts/foundation-task-package-valid.json").read_text())
        task_ref = artifact_ref(self.task["envelope"])
        closure_payload = {"schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "tables": ["FIXTURE_CUSTOMER"], "fields": ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"], "references": [], "foundation_task_ref": task_ref}
        self.closure = wrap("structure_closure", "foundation-closure", closure_payload, "220", "foundation", parents=[task_ref])
        self.snapshot = wrap("database_read_snapshot", "foundation-snapshot", {"schema_version": "v5.database-read-snapshot/v1", "snapshot_id": "foundation-snapshot", "base_database_version": "fixture-db-v1", "query_time": TIME, "query_scope": "sanitized", "executed_queries": ["SELECT 1"], "object_state_records": [], "snapshot_hash": "d" * 64}, "EAS-19", "foundation")
        data = {"schema_version": "v5.bound-data/v1", "data_package_id": "foundation-data", "structure_closure_ref": artifact_ref(self.closure["envelope"]), "operation_closure_ref": None, "database_snapshot_ref": artifact_ref(self.snapshot["envelope"]), "foundation_task_ref": task_ref, "data_groups": [{"data_group_id": "g", "records": [{"record_id": "r", "table_id": "FIXTURE_CUSTOMER", "field_values": [{"field_id": "C001", "value": "X", "standard_type": "STRING", "is_null": False}, {"field_id": "C002", "value": "Y", "standard_type": "STRING", "is_null": False}], "existing_record_refs": [], "temporary_record_refs": [], "value_provenance": [{"source_type": "foundation_task_package", "source_ref": "foundation-task-fixture"}], "case_role": "foundation", "target_condition_refs": [], "constraint_refs": []}], "record_links": [], "group_summary": {"table_record_counts": {"FIXTURE_CUSTOMER": 1}, "positive_count": 0, "hard_negative_count": 0, "background_count": 0, "foundation_count": 1, "object_count": 1}}]}
        self.verified = wrap("verified_bound_data", "foundation-verified", {"schema_version": "v5.verified-bound-data/v1", "validated_data_package": data}, "242", "foundation", "validated", [artifact_ref(self.closure["envelope"])])

    def test_accepts_complete_matching_inputs(self):
        plan = regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, self.verified, self.snapshot)
        self.assertEqual(plan["record_counts"], {"FIXTURE_CUSTOMER": 1})
        self.assertFalse(plan["writes_formal_store"])

    def test_rejects_task_ref_and_database_version_drift(self):
        bad_ref = copy.deepcopy(self.verified); bad_ref["payload"]["validated_data_package"]["foundation_task_ref"]["artifact_id"] = "other"; bad_ref["envelope"]["content_hash"] = content_hash(bad_ref["envelope"], bad_ref["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_TASK_REF_DRIFT"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, bad_ref, self.snapshot)
        bad_snapshot = copy.deepcopy(self.snapshot); bad_snapshot["payload"]["base_database_version"] = "other"; bad_snapshot["envelope"]["content_hash"] = content_hash(bad_snapshot["envelope"], bad_snapshot["payload"])
        with self.assertRaisesRegex(ContractError, "DATABASE_VERSION_DRIFT"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, self.verified, bad_snapshot)

