from __future__ import annotations

import copy
import hashlib
import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, canonical_bytes, sha256
try:
    from tests.agents.foundation_eas114_helpers import SANITIZED_241_RUNTIME, context as foundation_context, groups_and_traces, receipt as foundation_receipt
except ModuleNotFoundError:
    from agents.foundation_eas114_helpers import SANITIZED_241_RUNTIME, context as foundation_context, groups_and_traces, receipt as foundation_receipt

producer = importlib.import_module("east_v5.agents.210.foundation")
closure_mod = importlib.import_module("east_v5.agents.220.closure")
generator_mod = importlib.import_module("east_v5.agents.241.generator")
validator_mod = importlib.import_module("east_v5.agents.242.validator")
fixture_mod = importlib.import_module("east_v5.agents.242.sanitized_fixture")
regression = importlib.import_module("east_v5.agents.260.regression")
event_regression = regression.DatabaseCopyRegression
try:
    stub_210 = importlib.import_module("tests.agents.260.approved_210_stub")
except ModuleNotFoundError:
    stub_210 = importlib.import_module("agents.260.approved_210_stub")
TIME = "2026-08-15T00:00:00+00:00"
HIERARCHY_REF = {"artifact_id": "TRG-V1.0.0", "version": 1, "content_hash": "b" * 64}
CA_REF = {"artifact_id": "CA-V0.3.0", "version": 1, "content_hash": "a" * 64}


def build_foundation_bound(task, closure, profile, snapshot):
    context = foundation_context(task, closure, snapshot, created_at=TIME)
    groups, traces = groups_and_traces(closure)
    return generator_mod.BoundDataGenerator(ROOT, foundation_invocation_verifier=SANITIZED_241_RUNTIME).build_bound_data(
        closure, foundation_task_package=task, foundation_profile=profile, snapshot=snapshot,
        foundation_generation_context=context, proposed_data_groups=groups, selection_traces=traces,
        generation_receipt=foundation_receipt(task, context, groups, traces), created_at=TIME,
    )


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
        self.bound = build_foundation_bound(self.task, self.closure, self.profile, self.snapshot)
        self.verified = validator_mod.DataValidator(ROOT, foundation_invocation_verifier=SANITIZED_241_RUNTIME).freeze_bound_data(self.bound, self.closure, self.resolver, foundation_task_package=self.task, database_snapshot=self.snapshot, foundation_generation_context=foundation_context(self.task, self.closure, self.snapshot, created_at=TIME))

    def tearDown(self):
        self.runtime.close()

    def plan(self):
        return regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, self.verified, self.snapshot)

    def test_initial_seed_complete_210_to_260_consumption_and_copy_delta(self):
        plan = self.plan()
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        result = regression.run_database_copy_regression(ROOT, plan, self.verified, copy_db, formal_db, set())
        self.assertEqual(result["database_delta"], {"FIXTURE_CUSTOMER": 1})
        self.assertEqual(formal_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
        self.assertFalse(result["rollback_verified"])

    def test_foundation_formal_report_and_feedback_are_consumed_by_210(self):
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        package = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, copy_db, formal_db, set())
        self.assertEqual(package["payload"]["regression_status"], "passed")
        batch = package["payload"]["foundation_write_batch"]
        self.assertTrue(all("?" not in statement for statement in batch["rendered_sql_for_audit"]))
        self.assertEqual(package["payload"]["foundation_write_batch_hash"], sha256({key: batch[key] for key in ("transaction_groups", "sql_statements", "parameter_sets", "execution_order", "expected_write_counts")}))
        self.assertEqual(package["payload"]["target_count_validation"]["FIXTURE_CUSTOMER"]["actual"], package["payload"]["database_state_delta"]["FIXTURE_CUSTOMER"]["after"])
        self.assertEqual(package["payload"]["table_write_summary"]["FIXTURE_CUSTOMER"]["key_range"]["key_fields"], ["C001"])
        self.assertNotEqual(package["payload"]["sandbox_execution_report"]["transactions"][0]["started_at"], self.verified["payload"]["validated_at"])
        self.assertNotEqual(package["payload"]["regressed_at"], self.verified["payload"]["validated_at"])
        regression._validate_schema(ROOT, package, "contracts/packages/foundation-regression-report.schema.json", "FOUNDATION_REPORT")
        self.assertEqual(stub_210.consume(package, ROOT)["kind"], "success")
        invalid_time = copy.deepcopy(package)
        invalid_time["payload"]["regressed_at"] = "not-a-date-time"
        invalid_time["payload"]["report_hash"] = sha256({key: value for key, value in invalid_time["payload"].items() if key != "report_hash"})
        invalid_time["envelope"]["content_hash"] = content_hash(invalid_time["envelope"], invalid_time["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(invalid_time, ROOT)
        hash_drift = copy.deepcopy(package)
        hash_drift["payload"]["report_hash"] = "f" * 64
        hash_drift["envelope"]["content_hash"] = content_hash(hash_drift["envelope"], hash_drift["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_HASH_REJECTED"):
            stub_210.consume(hash_drift, ROOT)
        contradictory = copy.deepcopy(package)
        contradictory["payload"]["database_state_delta"]["FIXTURE_CUSTOMER"]["after"] = 2
        contradictory["payload"]["report_hash"] = sha256({key: value for key, value in contradictory["payload"].items() if key != "report_hash"})
        contradictory["envelope"]["content_hash"] = content_hash(contradictory["envelope"], contradictory["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_EXECUTION_FACT_REJECTED"):
            stub_210.consume(contradictory, ROOT)
        missing = copy.deepcopy(package)
        missing["payload"].pop("target_count_validation")
        missing["envelope"]["content_hash"] = content_hash(missing["envelope"], missing["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(missing, ROOT)
        package["payload"]["foundation_write_batch"]["unexpected"] = True
        package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(package, ROOT)
        # Use a fresh successful package so the rejection is semantic rather
        # than a transport/schema artefact.
        detached_copy, detached_formal = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (detached_copy, detached_formal): connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        detached_execution = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, detached_copy, detached_formal, set())
        detached_execution["payload"]["sandbox_execution_report"]["statements"][0]["statement_id"] = "not-in-foundation-write-batch"
        detached_execution["payload"]["report_hash"] = sha256({key: value for key, value in detached_execution["payload"].items() if key != "report_hash"})
        detached_execution["envelope"]["content_hash"] = content_hash(detached_execution["envelope"], detached_execution["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_EXECUTION_FACT_REJECTED"):
            stub_210.consume(detached_execution, ROOT)
        audit_copy, audit_formal = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (audit_copy, audit_formal): connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        detached_audit = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, audit_copy, audit_formal, set())
        detached_audit["payload"]["foundation_write_batch"]["rendered_sql_for_audit"][0] = "INSERT INTO WRONG_TABLE (WRONG_FIELD) VALUES ('wrong-value')"
        detached_audit["payload"]["report_hash"] = sha256({key: value for key, value in detached_audit["payload"].items() if key != "report_hash"})
        detached_audit["envelope"]["content_hash"] = content_hash(detached_audit["envelope"], detached_audit["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_EXECUTION_FACT_REJECTED"):
            stub_210.consume(detached_audit, ROOT)
        inflated_copy, inflated_formal = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (inflated_copy, inflated_formal): connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        inflated = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, inflated_copy, inflated_formal, set())
        inflated["payload"]["distribution_validation"]["expected"]["FIXTURE_CUSTOMER"]["default"] = 100
        inflated["payload"]["distribution_validation"]["allowed_tolerance"]["FIXTURE_CUSTOMER"]["default"] = 99
        inflated["payload"]["report_hash"] = sha256({key: value for key, value in inflated["payload"].items() if key != "report_hash"})
        inflated["envelope"]["content_hash"] = content_hash(inflated["envelope"], inflated["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_EXECUTION_FACT_REJECTED"):
            stub_210.consume(inflated, ROOT)
        ghost_copy, ghost_formal = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (ghost_copy, ghost_formal): connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        ghost = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, ghost_copy, ghost_formal, set())
        for surface in ("baseline", "delta", "after", "allowed_tolerance"):
            ghost["payload"]["distribution_validation"][surface]["FIXTURE_CUSTOMER"]["ghost"] = 0
        ghost["payload"]["report_hash"] = sha256({key: value for key, value in ghost["payload"].items() if key != "report_hash"})
        ghost["envelope"]["content_hash"] = content_hash(ghost["envelope"], ghost["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_EXECUTION_FACT_REJECTED"):
            stub_210.consume(ghost, ROOT)
        bad_copy, bad_formal = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (bad_copy, bad_formal):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        value = self.verified["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["field_values"][0]["value"]
        bad_copy.execute("INSERT INTO FIXTURE_CUSTOMER VALUES (?, 'taken')", (value,)); bad_copy.commit()
        retry_feedback = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, bad_copy, bad_formal, set())
        self.assertEqual(retry_feedback["payload"]["route_target"], "241")
        self.assertEqual(stub_210.consume(retry_feedback, ROOT)["kind"], "feedback")
        feedback = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, bad_copy, bad_formal, set(), attempt_no=3)
        self.assertEqual(feedback["payload"]["mode"], "foundation")
        self.assertEqual(feedback["payload"]["route_target"], "manual")
        self.assertEqual(stub_210.consume(feedback, ROOT)["kind"], "feedback")

    def test_compiler_order_preserves_record_to_group_binding(self):
        batch = {"operations": [
            {"index": 1, "record_id": "r2", "table": "FIXTURE_CUSTOMER", "sql": "INSERT INTO \"FIXTURE_CUSTOMER\" (\"C001\") VALUES (?)", "parameters": ["second"]},
            {"index": 2, "record_id": "r1", "table": "FIXTURE_CUSTOMER", "sql": "INSERT INTO \"FIXTURE_CUSTOMER\" (\"C001\") VALUES (?)", "parameters": ["first"]},
        ]}
        frozen = regression._foundation_write_batch(batch, {"r1": "source-group-one", "r2": "source-group-two"})
        self.assertEqual([item["data_group_id"] for item in frozen["parameter_sets"]], ["source-group-two", "source-group-one"])
        self.assertEqual([item["source_record_id"] for item in frozen["sql_statements"]], ["r2", "r1"])

    def test_foundation_transport_hash_drift_is_hard_rejected_before_feedback(self):
        task = copy.deepcopy(self.task)
        task["envelope"]["content_hash"] = "0" * 64
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db): connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            regression.run_foundation_regression(ROOT, task, self.closure, self.verified, self.snapshot, copy_db, formal_db, set())

    def test_nonempty_copy_expansion_is_rejected_from_post_write_actual_count(self):
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        copy_db.execute("INSERT INTO FIXTURE_CUSTOMER VALUES ('preexisting', 'legal')"); copy_db.commit()
        feedback = regression.run_foundation_regression(ROOT, self.task, self.closure, self.verified, self.snapshot, copy_db, formal_db, set())
        self.assertEqual(feedback["payload"]["route_target"], "241")
        self.assertEqual(stub_210.consume(feedback, ROOT)["kind"], "feedback")

    def test_expansion_mode_uses_same_machine_path(self):
        payload = copy.deepcopy(self.task["payload"])
        payload.update({"foundation_task_id": "eas60-expansion", "foundation_mode": "expansion", "trigger_reason": "sanitized expansion"})
        task = producer.build_foundation_task_package(payload, run_id="eas60-test", trace_id="eas60-test", created_at=TIME, parents=[CA_REF, HIERARCHY_REF])
        profile = producer.build_foundation_profile(task)
        closure = closure_mod.build_closure(task, [])
        closure["payload"].update({"fields": ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"], "references": [{"type": "hierarchy_asset", "artifact_ref": HIERARCHY_REF}]})
        closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"])
        bound = build_foundation_bound(task, closure, profile, self.snapshot)
        verified = validator_mod.DataValidator(ROOT, foundation_invocation_verifier=SANITIZED_241_RUNTIME).freeze_bound_data(bound, closure, self.resolver, foundation_task_package=task, database_snapshot=self.snapshot, foundation_generation_context=foundation_context(task, closure, self.snapshot, created_at=TIME))
        self.assertEqual(regression.validate_foundation_regression_inputs(ROOT, task, closure, verified, self.snapshot)["record_counts"], {"FIXTURE_CUSTOMER": 1})

    def test_authenticated_nonempty_expansion_reaches_target_and_210(self):
        payload = copy.deepcopy(self.task["payload"])
        payload.update({"foundation_task_id": "eas60-expansion-target-two", "foundation_mode": "expansion", "trigger_reason": "sanitized authenticated expansion", "target_counts": {"FIXTURE_CUSTOMER": 2}, "distribution_targets": {"FIXTURE_CUSTOMER": {"default": 2}}})
        task = producer.build_foundation_task_package(payload, run_id="eas60-test", trace_id="eas60-test", created_at=TIME, parents=[CA_REF, HIERARCHY_REF])
        closure = closure_mod.build_closure(task, [])
        closure["payload"].update({"fields": ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"], "references": [{"type": "hierarchy_asset", "artifact_ref": HIERARCHY_REF}]})
        closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"])
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["payload"]["object_state_records"] = [{"record_keys": {"table_id": "FIXTURE_CUSTOMER", "primary_key": "preexisting"}, "data": {"C001": "preexisting", "C002": "legal"}}]
        snapshot["payload"]["snapshot_hash"] = sha256({key: value for key, value in snapshot["payload"].items() if key != "snapshot_hash"})
        snapshot["envelope"]["content_hash"] = content_hash(snapshot["envelope"], snapshot["payload"])
        profile = producer.build_foundation_profile(task)
        bound = build_foundation_bound(task, closure, profile, snapshot)
        verified = validator_mod.DataValidator(ROOT, foundation_invocation_verifier=SANITIZED_241_RUNTIME).freeze_bound_data(bound, closure, self.resolver, foundation_task_package=task, database_snapshot=snapshot, foundation_generation_context=foundation_context(task, closure, snapshot, created_at=TIME))
        plan = regression.validate_foundation_regression_inputs(ROOT, task, closure, verified, snapshot)
        self.assertEqual(plan["baseline_counts"], {"FIXTURE_CUSTOMER": 1})
        self.assertEqual(plan["record_counts"], {"FIXTURE_CUSTOMER": 1})
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db): connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        copy_db.execute("INSERT INTO FIXTURE_CUSTOMER VALUES ('preexisting', 'legal')"); copy_db.commit()
        package = regression.run_foundation_regression(ROOT, task, closure, verified, snapshot, copy_db, formal_db, set())
        self.assertEqual(package["payload"]["database_state_delta"]["FIXTURE_CUSTOMER"], {"before": 1, "after": 2, "delta": 1, "passed": True})
        self.assertEqual(package["payload"]["distribution_validation"], {"requirements_present": True, "expected": {"FIXTURE_CUSTOMER": {"default": 2}}, "baseline": {"FIXTURE_CUSTOMER": {"default": 1}}, "delta": {"FIXTURE_CUSTOMER": {"default": 1}}, "after": {"FIXTURE_CUSTOMER": {"default": 2}}, "allowed_tolerance": {"FIXTURE_CUSTOMER": {"default": 0}}, "passed": True})
        self.assertEqual(stub_210.consume(package, ROOT)["kind"], "success")

    def test_distribution_target_and_snapshot_classification_rejections(self):
        with self.assertRaisesRegex(ContractError, "FOUNDATION_DISTRIBUTION_MISMATCH"):
            regression._normalise_distribution({"FIXTURE_CUSTOMER": {"region-east": 1}}, self.task["payload"]["distribution_targets"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_SNAPSHOT_DISTRIBUTION_MAPPING_UNAVAILABLE"):
            regression._snapshot_distribution(self.snapshot["payload"], {"FIXTURE_CUSTOMER": {"region-east": 1}})

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

    def test_verified_and_snapshot_content_hash_drifts_are_rejected(self):
        verified_drift = copy.deepcopy(self.verified)
        verified_drift["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["field_values"][0]["value"] = "substituted"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, verified_drift, self.snapshot)
        snapshot_hash_drift = copy.deepcopy(self.snapshot)
        snapshot_hash_drift["payload"]["executed_queries"].append("SELECT changed")
        snapshot_hash_drift["envelope"]["content_hash"] = content_hash(snapshot_hash_drift["envelope"], snapshot_hash_drift["payload"])
        verified_for_snapshot = copy.deepcopy(self.verified)
        verified_for_snapshot["payload"]["validated_data_package"]["database_snapshot_ref"] = artifact_ref(snapshot_hash_drift["envelope"])
        verified_for_snapshot["payload"]["validated_hash"] = sha256(verified_for_snapshot["payload"]["validated_data_package"])
        verified_for_snapshot["envelope"]["content_hash"] = content_hash(verified_for_snapshot["envelope"], verified_for_snapshot["payload"])
        with self.assertRaisesRegex(ContractError, "DATABASE_SNAPSHOT_HASH_DRIFT"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, verified_for_snapshot, snapshot_hash_drift)
        snapshot_envelope_drift = copy.deepcopy(self.snapshot)
        snapshot_envelope_drift["payload"]["executed_queries"].append("SELECT changed")
        snapshot_envelope_drift["payload"]["snapshot_hash"] = sha256({key: value for key, value in snapshot_envelope_drift["payload"].items() if key != "snapshot_hash"})
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, self.closure, self.verified, snapshot_envelope_drift)

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
        missing_hierarchy = copy.deepcopy(self.closure)
        missing_hierarchy["payload"]["references"] = []
        missing_hierarchy["envelope"]["content_hash"] = content_hash(missing_hierarchy["envelope"], missing_hierarchy["payload"])
        verified_for_closure = copy.deepcopy(self.verified)
        verified_for_closure["payload"]["validated_data_package"]["structure_closure_ref"] = artifact_ref(missing_hierarchy["envelope"])
        verified_for_closure["payload"]["validated_hash"] = sha256(verified_for_closure["payload"]["validated_data_package"])
        verified_for_closure["envelope"]["content_hash"] = content_hash(verified_for_closure["envelope"], verified_for_closure["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_HIERARCHY_REFERENCE_INVALID"):
            regression.validate_foundation_regression_inputs(ROOT, self.task, missing_hierarchy, verified_for_closure, self.snapshot)

    def test_execution_binds_plan_to_reauthenticated_verified_package(self):
        plan = self.plan()
        swapped = copy.deepcopy(self.verified)
        swapped["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["field_values"][0]["value"] = "replayed-substitution"
        swapped["payload"]["validated_hash"] = sha256(swapped["payload"]["validated_data_package"])
        swapped["envelope"]["content_hash"] = content_hash(swapped["envelope"], swapped["payload"])
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        with self.assertRaisesRegex(ContractError, "EXECUTION_VERIFIED_REF_DRIFT"):
            regression.run_database_copy_regression(ROOT, plan, swapped, copy_db, formal_db, set())
        self.assertEqual(copy_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
        drifted_plan = copy.deepcopy(plan)
        drifted_plan["snapshot_hash"] = "f" * 64
        with self.assertRaisesRegex(ContractError, "REGRESSION_PLAN_HASH_DRIFT"):
            regression.run_database_copy_regression(ROOT, drifted_plan, self.verified, copy_db, formal_db, set())

    def test_rejects_nonisolated_copy_before_any_write(self):
        plan = self.plan()
        same = sqlite3.connect(":memory:")
        same.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        with self.assertRaisesRegex(ContractError, "DATABASE_COPY_FORMAL_NOT_ISOLATED"):
            regression.run_database_copy_regression(ROOT, plan, self.verified, same, same, set())
        self.assertEqual(same.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
        same.close()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "shared.sqlite"
            seed = sqlite3.connect(database)
            seed.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
            seed.commit()
            seed.close()
            copy_db, formal_db = sqlite3.connect(database), sqlite3.connect(database)
            with self.assertRaisesRegex(ContractError, "DATABASE_COPY_FORMAL_NOT_ISOLATED"):
                regression.run_database_copy_regression(ROOT, plan, self.verified, copy_db, formal_db, set())
            self.assertEqual(copy_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
            copy_db.close()
            formal_db.close()

    def test_delta_mismatch_rolls_back_before_commit(self):
        plan = self.plan()
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        copy_db.execute("CREATE TRIGGER duplicate_foundation_row AFTER INSERT ON FIXTURE_CUSTOMER BEGIN INSERT INTO FIXTURE_CUSTOMER VALUES ('trigger-row', 'unexpected'); END")
        copy_db.commit()
        with self.assertRaisesRegex(ContractError, "DATABASE_DELTA_MISMATCH"):
            regression.run_database_copy_regression(ROOT, plan, self.verified, copy_db, formal_db, set())
        self.assertEqual(copy_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
        self.assertEqual(formal_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)

    def test_copy_failure_rolls_back_and_downstream_unconsumable_rejected(self):
        plan = self.plan()
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        for connection in (copy_db, formal_db):
            connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        values = self.verified["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["field_values"]
        copy_db.execute("INSERT INTO FIXTURE_CUSTOMER VALUES (?, 'taken')", (values[0]["value"],))
        copy_db.commit()
        with self.assertRaisesRegex(ContractError, "DATABASE_COPY_SNAPSHOT_BASELINE_MISMATCH"):
            regression.run_database_copy_regression(ROOT, plan, self.verified, copy_db, formal_db, set())
        self.assertEqual(copy_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 1)
        self.assertEqual(formal_db.execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)


class EventRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "formal.sqlite"
        connection = sqlite3.connect(self.db)
        connection.executescript("CREATE TABLE FIXTURE_CUSTOMER (ID TEXT PRIMARY KEY); CREATE TABLE FIXTURE_ACCOUNT (CUSTOMER_ID TEXT, STATUS TEXT, UNIQUE(CUSTOMER_ID, STATUS));")
        connection.commit(); connection.close()
        self.worker = event_regression(ROOT)
        self.data = copy.deepcopy(importlib.import_module("east_v5.agents.242.probe").run_sanitized_probe(ROOT)["transport"])
        self.data["envelope"].update({"mode": "event_data", "qa_id": "QA-260"})
        group = self.data["payload"]["validated_data_package"]["data_groups"][0]
        group["records"] = [
            {"record_id":"customer-positive","table_id":"FIXTURE_CUSTOMER","field_values":[{"field_id":"ID","value":"C1","standard_type":"STRING","is_null":False}],"existing_record_refs":[],"temporary_record_refs":[],"value_provenance":[{"source_type":"structure_closure_constraint","source_ref":"CA-V0.3.0"}],"case_role":"positive","target_condition_refs":["condition:open:positive"],"constraint_refs":[]},
            {"record_id":"account-positive","table_id":"FIXTURE_ACCOUNT","field_values":[{"field_id":"CUSTOMER_ID","value":"C1","standard_type":"STRING","is_null":False},{"field_id":"STATUS","value":"OPEN","standard_type":"STRING","is_null":False}],"existing_record_refs":[],"temporary_record_refs":[{"record_id":"customer-positive"}],"value_provenance":[{"source_type":"structure_closure_constraint","source_ref":"CA-V0.3.0"}],"case_role":"positive","target_condition_refs":["condition:open:positive"],"constraint_refs":[]},
            {"record_id":"account-negative","table_id":"FIXTURE_ACCOUNT","field_values":[{"field_id":"CUSTOMER_ID","value":"C2","standard_type":"STRING","is_null":False},{"field_id":"STATUS","value":"CLOSED","standard_type":"STRING","is_null":False}],"existing_record_refs":[],"temporary_record_refs":[],"value_provenance":[{"source_type":"structure_closure_constraint","source_ref":"CA-V0.3.0"}],"case_role":"hard_negative","target_condition_refs":["condition:open:negative"],"constraint_refs":[]},
        ]
        self.data["payload"]["validated_hash"] = sha256(self.data["payload"]["validated_data_package"]); self.data["envelope"]["content_hash"] = content_hash(self.data["envelope"], self.data["payload"])
        self.orm = copy.deepcopy(importlib.import_module("east_v5.agents.252.probe").run_sanitized_probe()["transport"])
        self.orm["envelope"].update({"mode": "event_data", "qa_id": "QA-260"}); self.orm["envelope"]["content_hash"] = content_hash(self.orm["envelope"], self.orm["payload"])
        payload = {"schema_version":"v5.database-read-snapshot/v1","snapshot_id":"event-snapshot","base_database_version":"db-v1","query_time":TIME,"query_scope":"sanitized","executed_queries":["SELECT 1"],"object_state_records":[],"snapshot_hash":""}
        payload["snapshot_hash"] = sha256({key:value for key,value in payload.items() if key != "snapshot_hash"})
        self.snapshot = wrap("database_read_snapshot", "event-snapshot", payload, "EAS-19", "event_data", status="validated"); self.snapshot["envelope"]["qa_id"] = "QA-260"; self.snapshot["envelope"]["content_hash"] = content_hash(self.snapshot["envelope"], self.snapshot["payload"])
        query_payload = {"query_spec_id":"qspec-260","penalty_fact_package_ref":{"artifact_id":"penalty","version":1,"content_hash":"1"*64},"observable_fact_package_ref":{"artifact_id":"observable","version":1,"content_hash":"2"*64},"query_goal":"open account","must_preserve_fact_refs":["fact"],"main_object_and_grain":{"main_object":"account","grain":"FIXTURE_ACCOUNT.CUSTOMER_ID"},"query_entry":{"entry_table":"FIXTURE_ACCOUNT","entry_conditions":[]},"related_objects_and_path":[],"filters_and_evidence":[],"return_fields":[{"field_id":"CUSTOMER_ID","display_name":"customer","source_table":"FIXTURE_ACCOUNT"},{"field_id":"STATUS","display_name":"status","source_table":"FIXTURE_ACCOUNT"}],"aggregation_dedup_sort_time":{"group_by_fields":["CUSTOMER_ID"],"distinct_required":True,"order_by":[],"time_window":{"field_id":"STATUS","window_type":"point"}},"observability_boundary":{"answerable":["open"],"unanswerable":[]},"expected_result_shape":{"row_grain":"account","column_set":["CUSTOMER_ID","STATUS"],"aggregation_shape":"none"},"sql_schema_scope":{"allowed_tables":[{"table_id":"FIXTURE_ACCOUNT","allowed_fields":["CUSTOMER_ID","STATUS"]}]},"minimum_positive_count":2,"minimum_negative_count":1,"condition_coverage":[{"predicate":"open","positive_types":["positive"],"negative_types":["hard_negative"]}],"code_value_coverage":[{"field_id":"STATUS","target_code_values":["OPEN"]}],"expected_row_group_count":{"minimum":1,"target":1,"tolerance_range":{"low":0,"high":0}},"join_expansion_limit":{"max_multiplier":1,"max_result_rows":1},"query_specification_package_schema_version":"query-specification-v1"}
        self.spec = wrap("query_specification_package", "qspec-260", query_payload, "140", "question_sql", status="validated"); self.spec["envelope"]["qa_id"] = "QA-260"; self.spec["envelope"]["content_hash"] = content_hash(self.spec["envelope"], self.spec["payload"])
        review_payload = {"schema_version":"v5.question-sql-dual-review-passed/v1","candidate_ref":{"artifact_id":"candidate","version":1,"content_hash":"3"*64},"candidate_content":{"clear_question":"open accounts","sql_gold":"SELECT CUSTOMER_ID, STATUS FROM FIXTURE_ACCOUNT WHERE STATUS = 'OPEN'","sql_explanation":{"select":"s","from_join":"f","where":"w","aggregation":"","sort":"","business_meaning":"b"},"business_event_candidates":[{"event_name":"open","objective":"o","objects":["account"],"state_changes":[]}],"specification_mapping":[{"spec_item":"open","question_fragment":"open","sql_fragment":"STATUS"}]},"query_specification_package":artifact_ref(self.spec["envelope"]),"penalty_fact_package":{"artifact_id":"penalty","version":1,"content_hash":"1"*64},"observable_fact_package":{"artifact_id":"observable","version":1,"content_hash":"2"*64},"constraint_evidence_summary":{"tables":["FIXTURE_ACCOUNT"],"fields":["STATUS"],"data_elements":[],"relationships":[],"source_refs":["CA-V0.3.0"]},"precheck_report":{"decision":"pass","report_hash":"4"*64},"deepseek_review":{"decision":"pass","issue_level":"none","reason":"ok","review_hash":"5"*64},"glm_review":{"decision":"pass","issue_level":"none","reason":"ok","review_hash":"6"*64},"adjudication":{"decision":"pass","report_hash":"7"*64},"review_round":1,"package_hash":""}
        review_payload["candidate_content"]["query_parameter_bindings"] = []
        review_payload["package_hash"] = sha256({key:value for key,value in review_payload.items() if key != "package_hash"})
        self.review = wrap("question_sql_dual_review_passed", "review-260", review_payload, "110", "event_data", parents=[artifact_ref(self.spec["envelope"])], status="validated"); self.review["envelope"]["qa_id"] = "QA-260"; self.review["envelope"]["content_hash"] = content_hash(self.review["envelope"], self.review["payload"])
        self.refresh_query_refs()

    def tearDown(self): self.temp.cleanup()

    def refresh_query_refs(self):
        context_keys = ("run_id", "qa_id", "trace_id", "attempt_no", "created_at")
        for package in (self.orm, self.snapshot, self.spec, self.review):
            package["envelope"].update({key: self.data["envelope"][key] for key in context_keys})
        self.orm["envelope"]["content_hash"] = content_hash(self.orm["envelope"], self.orm["payload"])
        self.snapshot["envelope"]["content_hash"] = content_hash(self.snapshot["envelope"], self.snapshot["payload"])
        self.spec["envelope"]["content_hash"] = content_hash(self.spec["envelope"], self.spec["payload"])
        self.review["payload"]["query_specification_package"] = artifact_ref(self.spec["envelope"])
        self.review["payload"]["package_hash"] = sha256({key: value for key, value in self.review["payload"].items() if key != "package_hash"})
        reports = [
            {"artifact_id": "precheck", "version": 1, "content_hash": self.review["payload"]["precheck_report"]["report_hash"]},
            {"artifact_id": "deepseek", "version": 1, "content_hash": self.review["payload"]["deepseek_review"]["review_hash"]},
            {"artifact_id": "glm", "version": 1, "content_hash": self.review["payload"]["glm_review"]["review_hash"]},
        ]
        self.review["envelope"]["parent_artifact_refs"] = [*reports, artifact_ref(self.spec["envelope"])]
        self.review["envelope"]["input_hashes"] = [self.spec["envelope"]["content_hash"]]
        self.review["envelope"]["input_hashes"] = [item["content_hash"] for item in self.review["envelope"]["parent_artifact_refs"]]
        self.review["envelope"]["content_hash"] = content_hash(self.review["envelope"], self.review["payload"])
        scheduler = importlib.import_module("east_v5.agents.110.scheduler").QuestionSqlStageScheduler(ROOT)
        self.binding = scheduler.build_query_parameter_binding(self.review, self.spec, created_at=TIME)
        started = importlib.import_module("east_v5.agents.210.scheduler").DataStageCoordinator(ROOT).begin_event(self.review, self.spec, self.binding)
        self.reviewed, self.context = started["reviewed_question_sql"], started["event_query_context"]

    def test_full_110_event_success_copy_only_and_independent_210_consumption(self):
        before = self.db.read_bytes(); package = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        self.assertEqual(package["payload"]["regression_status"], "passed"); self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(self.spec["payload"]["minimum_positive_count"], 2)
        self.assertEqual(package["payload"]["sql_regression_report"]["positive_negative_metrics"]["positive_hits"], 2)
        self.worker._validate(package, "regression-passed-data-orm.schema.json", "event")
        self.assertEqual(stub_210.consume(package, ROOT)["decision"], "accepted")

    def test_contextless_and_context_or_140_drift_are_hard_rejected(self):
        with self.assertRaises(TypeError):
            self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.spec, self.db)  # type: ignore[call-arg]
        fake_spec = copy.deepcopy(self.spec)
        fake_spec["envelope"]["mode"] = "event_data"
        fake_spec["envelope"]["content_hash"] = content_hash(fake_spec["envelope"], fake_spec["payload"])
        with self.assertRaisesRegex(ContractError, "INPUT_ENVELOPE_INVALID"):
            self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, fake_spec, self.db)
        for key in ("run_id", "qa_id", "trace_id", "attempt_no"):
            with self.subTest(key=key):
                context = copy.deepcopy(self.context)
                context["envelope"][key] = "other" if key != "attempt_no" else 2
                context["envelope"]["content_hash"] = content_hash(context["envelope"], context["payload"])
                with self.assertRaisesRegex(ContractError, "EVENT_CONTEXT_LINEAGE_MISMATCH"):
                    self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, context, self.binding, self.spec, self.db)

    def test_self_consistent_source_question_ref_tamper_rejects_before_copy(self):
        before = self.db.read_bytes()
        context = copy.deepcopy(self.context)
        context["payload"]["source_question_sql_ref"] = {"artifact_id": "other-approved-question-sql", "version": 1, "content_hash": "f" * 64}
        context["payload"]["projection_hash"] = sha256({key: value for key, value in context["payload"].items() if key != "projection_hash"})
        context["envelope"]["parent_artifact_refs"] = [context["payload"][key] for key in ("source_query_spec_ref", "source_question_sql_ref", "reviewed_question_sql_ref")]
        context["envelope"]["input_hashes"] = [ref["content_hash"] for ref in context["envelope"]["parent_artifact_refs"]]
        context["envelope"]["content_hash"] = content_hash(context["envelope"], context["payload"])
        with self.assertRaisesRegex(ContractError, "EVENT_CONTEXT_SOURCE_QUESTION_LINEAGE_REJECTED"):
            self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, context, self.binding, self.spec, self.db)
        self.assertEqual(before, self.db.read_bytes())

    def test_negative_fixture_count_shortfall_is_rejected(self):
        self.spec["payload"]["minimum_negative_count"] = 99; self.refresh_query_refs()
        feedback = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        self.assertEqual(feedback["payload"]["route_target"], "241")

    def test_negative_identity_leak_is_rejected(self):
        connection = sqlite3.connect(self.db); connection.execute("INSERT INTO FIXTURE_ACCOUNT VALUES ('C2', 'CLOSED')"); connection.commit(); connection.close()
        self.review["payload"]["candidate_content"]["sql_gold"] = "SELECT CUSTOMER_ID, STATUS FROM FIXTURE_ACCOUNT"
        self.refresh_query_refs()
        feedback = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        self.assertEqual(feedback["payload"]["route_target"], "241")

    def test_shared_grain_different_condition_is_excluded_by_full_projection_key(self):
        negative = self.data["payload"]["validated_data_package"]["data_groups"][0]["records"][2]
        negative["field_values"][0]["value"] = "C1"
        self.data["payload"]["validated_hash"] = sha256(self.data["payload"]["validated_data_package"])
        self.data["envelope"]["content_hash"] = content_hash(self.data["envelope"], self.data["payload"])
        package = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        self.assertEqual(package["payload"]["regression_status"], "passed")

    def test_row_count_and_join_are_independent_gates(self):
        self.spec["payload"]["expected_row_group_count"].update({"target": 2, "tolerance_range": {"low": 0, "high": 0}}); self.refresh_query_refs()
        self.assertEqual(self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)["payload"]["route_target"], "241")
        self.tearDown(); self.setUp()
        self.spec["payload"]["join_expansion_limit"].update({"max_multiplier": 0.5}); self.refresh_query_refs()
        self.assertEqual(self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)["payload"]["route_target"], "241")

    def test_attempt_three_sql_error_routes_manual_and_210_rejects_unknown_operation_field(self):
        self.review["payload"]["candidate_content"]["sql_gold"]="SELECT missing FROM FIXTURE_ACCOUNT"
        with self.assertRaisesRegex(ContractError, "210_FIELD_PROJECTION_SCOPE_VIOLATION"):
            self.refresh_query_refs()

        self.tearDown(); self.setUp(); package = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        package["payload"]["sandbox_execution_report"]["operations"][0]["unexpected_nested_field"] = True
        package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(package, ROOT)

    def test_orm_execution_and_gold_sql_errors_have_distinct_feedback(self):
        plan = self.orm["payload"]["validated_orm_plan"]
        plan["orm_source_code"] = "def apply(context, params):\n    with context.transaction() as tx:\n        tx.insert('FIXTURE_ACCOUNT', {'CUSTOMER_ID': 'C1', 'STATUS': 'OPEN'})\n        tx.insert('FIXTURE_ACCOUNT', {'CUSTOMER_ID': 'C1', 'STATUS': 'OPEN'})\n    return {'executed_operation_ids': ['op-1'], 'write_count': 2}\n"
        frozen = hashlib.sha256(canonical_bytes({"orm_source_code": plan["orm_source_code"], "execution_contract": plan["execution_contract"], "operations": plan["operations"]})).hexdigest()
        plan["code_hash"] = frozen; self.orm["payload"]["validated_hash"] = frozen; self.orm["envelope"]["content_hash"] = content_hash(self.orm["envelope"], self.orm["payload"])
        orm_feedback = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        self.assertEqual((orm_feedback["payload"]["failure_details"]["error_code"], orm_feedback["payload"]["failure_details"]["error_stage"], orm_feedback["payload"]["route_target"]), ("ORM_PLAN_ERROR", "orm_execution", "251"))
        self.tearDown(); self.setUp()
        self.review["payload"]["candidate_content"]["sql_gold"] = "SELECT missing FROM FIXTURE_ACCOUNT"
        with self.assertRaisesRegex(ContractError, "210_FIELD_PROJECTION_SCOPE_VIOLATION"):
            self.refresh_query_refs()

    def test_stub_rejects_hash_drift_and_foundation_required_is_reachable(self):
        package = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        package["payload"]["executable_package_hash"] = "f" * 64
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(package, ROOT)
        record = self.data["payload"]["validated_data_package"]["data_groups"][0]["records"][0]
        record["existing_record_refs"] = [{"table_id": "FIXTURE_CUSTOMER", "record_key": "not-in-snapshot"}]
        self.data["payload"]["validated_hash"] = sha256(self.data["payload"]["validated_data_package"])
        self.data["envelope"]["content_hash"] = content_hash(self.data["envelope"], self.data["payload"])
        feedback = self.worker.run_event(self.data, self.orm, self.snapshot, self.reviewed, self.context, self.binding, self.spec, self.db)
        self.assertEqual(feedback["payload"]["failure_details"]["error_code"], "FOUNDATION_REQUIRED")
        self.assertEqual(feedback["payload"]["route_target"], "210")

    def test_110_unknown_field_and_schema_drift_are_rejected(self):
        self.review["payload"]["unexpected"] = True; self.review["envelope"]["content_hash"] = content_hash(self.review["envelope"], self.review["payload"])
        with self.assertRaisesRegex(ContractError, "210_DUAL_REVIEW_REJECTED"):
            importlib.import_module("east_v5.agents.210.scheduler").DataStageCoordinator(ROOT).begin_event(self.review, self.spec, self.binding)
