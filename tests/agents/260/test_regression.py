from __future__ import annotations

import copy
import importlib
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256

producer = importlib.import_module("east_v5.agents.210.foundation")
closure_mod = importlib.import_module("east_v5.agents.220.closure")
generator_mod = importlib.import_module("east_v5.agents.241.generator")
validator_mod = importlib.import_module("east_v5.agents.242.validator")
fixture_mod = importlib.import_module("east_v5.agents.242.sanitized_fixture")
regression = importlib.import_module("east_v5.agents.260.regression")
TIME = "2026-08-15T00:00:00+00:00"
HIERARCHY_REF = {"artifact_id": "TRG-V1.0.0", "version": 1, "content_hash": "b" * 64}
CA_REF = {"artifact_id": "CA-V0.3.0", "version": 1, "content_hash": "a" * 64}


def wrap(artifact_type, artifact_id, payload, producer_id, mode, parents=None, status="candidate"):
    parents = parents or []
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "eas60-test", "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": producer_id, "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": status, "mode": mode, "created_at": TIME, "trace_id": "eas60-test", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


class FoundationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = fixture_mod.SanitizedRuntime()
        self.resolver = self.runtime.resolver()
        self.task = producer.build_foundation_task_package({"schema_version": "v5.foundation-task-package/v1", "foundation_task_id": "eas60-initial", "foundation_mode": "initial_seed", "trigger_reason": "sanitized initial seed", "target_database_version": "fixture-db-v1", "target_object_types": ["FIXTURE_CUSTOMER"], "target_table_field_scope": {"FIXTURE_CUSTOMER": ["C001", "C002"]}, "target_counts": {"FIXTURE_CUSTOMER": 1}, "distribution_targets": {"FIXTURE_CUSTOMER": {"default": 1}}, "hierarchy_asset_refs": [HIERARCHY_REF], "prohibited_record_types": ["EVENT_OWNED"], "resume_qa_ref": None, "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0"}, run_id="eas60-test", trace_id="eas60-test", created_at=TIME, parents=[CA_REF, HIERARCHY_REF])
        self.profile = producer.build_foundation_profile(self.task)
        self.closure = closure_mod.build_closure(self.task, [])
        self.closure["payload"]["fields"] = ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"]
        self.closure["payload"]["references"] = [{"type": "hierarchy_asset", "artifact_ref": HIERARCHY_REF}]
        self.closure["envelope"]["content_hash"] = content_hash(self.closure["envelope"], self.closure["payload"])
        snapshot_payload = {"schema_version": "v5.database-read-snapshot/v1", "snapshot_id": "eas60-snapshot", "base_database_version": "fixture-db-v1", "query_time": TIME, "query_scope": "sanitized", "executed_queries": ["SELECT 1"], "object_state_records": [], "snapshot_hash": ""}
        snapshot_payload["snapshot_hash"] = sha256({key: value for key, value in snapshot_payload.items() if key != "snapshot_hash"})
        self.snapshot = wrap("database_read_snapshot", "eas60-snapshot", snapshot_payload, "EAS-19", "foundation")
        self.bound = generator_mod.BoundDataGenerator(ROOT).build_bound_data(self.closure, foundation_task_package=self.task, foundation_profile=self.profile, snapshot=self.snapshot, created_at=TIME)
        self.verified = validator_mod.DataValidator(ROOT).freeze_bound_data(self.bound, self.closure, self.resolver)

    def tearDown(self):
        self.runtime.close()

    def plan(self):
        return regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, self.verified, self.snapshot)

    def test_initial_seed_complete_210_to_260_consumption_and_copy_delta(self):
        plan = self.plan()
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT, C002 TEXT)")
        result = regression.run_database_copy_regression(plan, self.verified, copy_db, formal_db, set())
        self.assertEqual(result["database_delta"], {"FIXTURE_CUSTOMER": 1})
        self.assertEqual(formal_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
        self.assertTrue(result["rollback_verified"])

    def test_expansion_mode_uses_same_machine_path(self):
        payload = copy.deepcopy(self.task["payload"])
        payload.update({"foundation_task_id": "eas60-expansion", "foundation_mode": "expansion", "trigger_reason": "sanitized expansion"})
        task = producer.build_foundation_task_package(payload, run_id="eas60-test", trace_id="eas60-test", created_at=TIME, parents=[CA_REF, HIERARCHY_REF])
        profile = producer.build_foundation_profile(task)
        closure = closure_mod.build_closure(task, [])
        closure["payload"].update({"fields": ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"], "references": [{"type": "hierarchy_asset", "artifact_ref": HIERARCHY_REF}]})
        closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"])
        bound = generator_mod.BoundDataGenerator(ROOT).build_bound_data(closure, foundation_task_package=task, foundation_profile=profile, snapshot=self.snapshot, created_at=TIME)
        verified = validator_mod.DataValidator(ROOT).freeze_bound_data(bound, closure, self.resolver)
        self.assertEqual(regression.validate_foundation_regression_inputs(ROOT, task, closure, verified, self.snapshot)["record_counts"], {"FIXTURE_CUSTOMER": 1})

    def test_schema_profile_hash_version_and_task_ref_rejections(self):
        missing = copy.deepcopy(self.verified); missing["payload"].pop("validated_at"); missing["envelope"]["content_hash"] = content_hash(missing["envelope"], missing["payload"])
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED:VERIFIED_BOUND_DATA"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, missing, self.snapshot)
        with self.assertRaisesRegex(ContractError, "FOUNDATION_TASK_PACKAGE_SCHEMA_INVALID"):
            regression.validate_foundation_regression_inputs(ROOT, self.profile, self.closure, self.verified, self.snapshot)
        drift = copy.deepcopy(self.verified); drift["payload"]["validated_data_package"]["foundation_task_ref"]["artifact_id"] = "other"; drift["payload"]["validated_hash"] = sha256(drift["payload"]["validated_data_package"]); drift["envelope"]["content_hash"] = content_hash(drift["envelope"], drift["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_TASK_REF_DRIFT"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, drift, self.snapshot)
        bad_snapshot = copy.deepcopy(self.snapshot); bad_snapshot["payload"]["base_database_version"] = "other"; bad_snapshot["payload"]["snapshot_hash"] = sha256({key: value for key, value in bad_snapshot["payload"].items() if key != "snapshot_hash"}); bad_snapshot["envelope"]["content_hash"] = content_hash(bad_snapshot["envelope"], bad_snapshot["payload"])
        with self.assertRaisesRegex(ContractError, "DATABASE_VERSION_DRIFT"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, self.verified, bad_snapshot)

    def test_distribution_hierarchy_scope_prohibited_and_reference_rejections(self):
        data = self.verified["payload"]["validated_data_package"]
        for name, mutate, expected in (
            ("distribution", lambda value: value["data_groups"][0]["records"][0]["target_condition_refs"].append("distribution:other"), "FOUNDATION_DISTRIBUTION_MISMATCH"),
            ("scope", lambda value: value["data_groups"][0]["records"][0]["field_values"].append({"field_id": "OUT", "value": "x", "standard_type": "STRING", "is_null": False}), "FOUNDATION_SCOPE_OUT_OF_BOUNDS"),
            ("prohibited", lambda value: value["data_groups"][0]["records"][0].update({"record_type": "EVENT_OWNED"}), "FOUNDATION_PROHIBITED_TYPE_HIT"),
            ("reference", lambda value: value["data_groups"][0]["records"][0].update({"temporary_record_refs": [{"record_id": "missing"}]}), "FOUNDATION_REFERENTIAL_INTEGRITY_FAILED"),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.verified); mutate(candidate["payload"]["validated_data_package"]); candidate["payload"]["validated_hash"] = sha256(candidate["payload"]["validated_data_package"]); candidate["envelope"]["content_hash"] = content_hash(candidate["envelope"], candidate["payload"])
                with self.assertRaisesRegex(ContractError, expected):
                    regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, candidate, self.snapshot)

    def test_copy_failure_rolls_back_and_downstream_unconsumable_rejected(self):
        plan = self.plan()
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        values = self.verified["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["field_values"]
        copy_db.execute("INSERT INTO FIXTURE_CUSTOMER VALUES (?, 'taken')", (values[0]["value"],))
        copy_db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            regression.run_database_copy_regression(plan, self.verified, copy_db, formal_db, set())
        self.assertEqual(copy_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 1)
        self.assertEqual(formal_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
