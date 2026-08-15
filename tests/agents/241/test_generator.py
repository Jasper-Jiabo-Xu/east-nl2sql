from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(ROOT / "src"))

mod = importlib.import_module("east_v5.agents.241.generator")
BoundDataGenerator = mod.BoundDataGenerator
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

FIXED_TIME = "2026-08-16T00:00:00+00:00"


def rehash(package: dict) -> None:
    package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])


def fixture(name: str) -> dict:
    package = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    rehash(package)
    return package


def wrap_feedback(artifact_type: str, artifact_id: str, payload: dict, *, producer: str, mode: str, qa_id: str | None, parent: dict) -> dict:
    ref = artifact_ref(parent)
    envelope = {
        "artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": parent["run_id"],
        "qa_id": qa_id, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": parent["attempt_no"], "producer_id": producer,
        "parent_artifact_refs": [ref], "input_hashes": [ref["content_hash"]], "status": "candidate",
        "mode": mode, "created_at": FIXED_TIME, "trace_id": parent["trace_id"], "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def validation_feedback(previous: dict) -> dict:
    group = previous["payload"]["data_groups"][0]
    record = group["records"][0]
    payload = {
        "schema_version": "v5.data-validation-failed-feedback/v1",
        "data_package_ref": artifact_ref(previous["envelope"]), "decision": "fail",
        "validator_registry_version": "v5.validator-registry/v1",
        "failed_items": [{
            "failed_module_ids": ["east_v5.validators.field"], "constraint_ids": ["C-001"],
            "record_field_locations": [{"data_group_id": group["data_group_id"], "record_id": record["record_id"], "table_id": record["table_id"], "field_id": "F001"}],
            "expected_values": ["脱敏期望"], "actual_values": ["脱敏实际"], "error_details": "脱敏校验失败",
        }],
    }
    return wrap_feedback("data_validation_failed_feedback", "eas31-vfeedback", payload, producer="242", mode="event_data", qa_id="QA-EAS31", parent=previous["envelope"])


def regression_feedback(previous: dict, *, route: str = "241", retry: int = 2) -> dict:
    payload = {
        "schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data",
        "input_data_refs": [artifact_ref(previous["envelope"])], "input_orm_ref": None,
        "sandbox_snapshot_id": "eas31-sandbox",
        "failure_details": {
            "error_code": "DATA_VALUE_ERROR", "error_stage": "sql_execution", "error_location": "FIXTURE_T001.F001",
            "expected_values": ["脱敏期望"], "actual_values": ["脱敏实际"], "sql_error_detail": None,
            "regression_metrics": {"positive_hit": 0},
        },
        "route_target": route, "retry_count": retry,
    }
    return wrap_feedback("sql_regression_failed_feedback", "eas31-rfeedback", payload, producer="260", mode="event_data", qa_id="QA-EAS31", parent=previous["envelope"])


def fixed_groups(package: dict) -> list:
    groups = copy.deepcopy(package["payload"]["data_groups"])
    groups[0]["records"][0]["field_values"][0]["value"] = "脱敏值-F001-修订"
    groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
    return groups


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.builder = BoundDataGenerator(ROOT)
        self.event_closure = fixture("structure-closure-event.json")
        self.foundation_closure = fixture("structure-closure-foundation.json")
        self.operation = fixture("operation-closure.json")
        self.profile = fixture("foundation-profile.json")
        self.snapshot = fixture("database-read-snapshot.json")

    def _event(self, **kwargs):
        return self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, created_at=FIXED_TIME, **kwargs)

    def _foundation(self, **kwargs):
        return self.builder.build_bound_data(self.foundation_closure, foundation_profile=self.profile, created_at=FIXED_TIME, **kwargs)

    # ------------------------------------------------------------ success path
    def test_event_build_valid(self):
        package = self._event()
        self.builder.validate_bound_data(package)
        payload = package["payload"]
        self.assertEqual(payload["schema_version"], "v5.bound-data/v1")
        self.assertEqual(payload["operation_closure_ref"], artifact_ref(self.operation["envelope"]))
        self.assertIsNotNone(payload["database_snapshot_ref"])
        group = payload["data_groups"][0]
        self.assertEqual(len(group["records"]), 2)
        self.assertEqual(group["records"][0]["case_role"], "positive")
        self.assertEqual(group["records"][1]["case_role"], "background")
        self.assertEqual(len(group["record_links"]), 1)
        self.assertEqual(group["group_summary"]["object_count"], 2)

    def test_foundation_build_valid(self):
        package = self._foundation()
        self.builder.validate_bound_data(package)
        self.assertIsNone(package["payload"]["operation_closure_ref"])
        self.assertEqual(package["envelope"]["mode"], "foundation")
        self.assertEqual(package["payload"]["data_groups"][0]["records"][0]["case_role"], "foundation")

    def test_reproducible(self):
        first = self._event()
        second = self._event()
        self.assertEqual(first["envelope"]["content_hash"], second["envelope"]["content_hash"])

    # ------------------------------------------------------------- mode gates
    def test_foundation_rejects_operation(self):
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.foundation_closure, foundation_profile=self.profile, operation_closure=self.operation, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")

    def test_event_requires_operation(self):
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "OPERATION_CLOSURE_REQUIRED")

    # ------------------------------------------------------------- rejection
    def test_bad_hash_rejected(self):
        corrupted = copy.deepcopy(self.event_closure)
        corrupted["payload"]["fields"] = ["FIXTURE_T001.F001"]
        with self.assertRaises(ContractError) as ctx:
            self.builder.validate_structure_closure(corrupted)
        self.assertEqual(str(ctx.exception), "CONTENT_HASH_DRIFT")

    def test_unknown_payload_field_rejected(self):
        package = self._event()
        package["payload"]["extra"] = "boom"
        with self.assertRaises(ContractError):
            self.builder.validate_bound_data(package)

    def test_record_table_out_of_closure(self):
        groups = fixed_groups(self._event())
        groups[0]["records"][0]["table_id"] = "FIXTURE_UNKNOWN"
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "RECORD_TABLE_OUT_OF_CLOSURE")

    def test_field_out_of_closure(self):
        groups = fixed_groups(self._event())
        groups[0]["records"][0]["field_values"][0]["field_id"] = "F999"
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "FIELD_OUT_OF_CLOSURE")

    def test_orphan_existing_record(self):
        groups = fixed_groups(self._event())
        groups[0]["records"][0]["existing_record_refs"] = [{"table_id": "FIXTURE_T002", "record_key": "PK-MISSING"}]
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "EXISTING_RECORD_ORPHAN")

    def test_orphan_temporary_record(self):
        groups = fixed_groups(self._event())
        groups[0]["records"][0]["temporary_record_refs"] = [{"record_id": "rec-NOPE"}]
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "TEMPORARY_RECORD_ORPHAN")

    def test_orphan_record_link(self):
        groups = fixed_groups(self._event())
        groups[0]["record_links"] = [{"source_record_id": "rec-FIXTURE_T001", "target_record_id": "rec-NOPE", "relation_type": "cross_table", "source_field_id": "F001", "target_field_id": "PK001", "constraint_refs": []}]
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "RECORD_LINK_ORPHAN")

    def test_summary_mismatch(self):
        groups = fixed_groups(self._event())
        groups[0]["group_summary"] = {**groups[0]["group_summary"], "positive_count": 99}
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "GROUP_SUMMARY_MISMATCH")

    def test_null_value_mismatch(self):
        groups = fixed_groups(self._event())
        groups[0]["records"][0]["field_values"][0]["is_null"] = True
        groups[0]["records"][0]["field_values"][0]["value"] = "non-null"
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "NULL_VALUE_MISMATCH")

    def test_value_type_mismatch(self):
        groups = fixed_groups(self._event())
        groups[0]["records"][0]["field_values"][0]["standard_type"] = "INTEGER"
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "VALUE_TYPE_MISMATCH")

    def test_provenance_empty(self):
        groups = fixed_groups(self._event())
        groups[0]["records"][0]["value_provenance"] = []
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaises(ContractError) as ctx:
            self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, proposed_data_groups=groups, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "VALUE_PROVENANCE_EMPTY")

    # ------------------------------------------------------------- feedback
    def test_validation_feedback_remap(self):
        event = self._event()
        feedback = validation_feedback(event)
        remapped = self.builder.apply_validation_feedback(event, feedback, self.event_closure, snapshot=self.snapshot, proposed_data_groups=fixed_groups(event), created_at=FIXED_TIME)
        self.assertEqual(remapped["envelope"]["version"], event["envelope"]["version"] + 1)
        self.assertEqual(remapped["envelope"]["attempt_no"], 2)
        self.assertEqual(remapped["envelope"]["supersedes_ref"], artifact_ref(event["envelope"]))
        self.assertEqual(remapped["envelope"]["status"], "candidate")

    def test_regression_attempt3_blocked(self):
        event = self._event()
        remapped = self.builder.apply_validation_feedback(event, validation_feedback(event), self.event_closure, snapshot=self.snapshot, proposed_data_groups=fixed_groups(event), created_at=FIXED_TIME)
        blocked = self.builder.apply_regression_feedback(remapped, regression_feedback(remapped, retry=2), self.event_closure, snapshot=self.snapshot, proposed_data_groups=fixed_groups(remapped), created_at=FIXED_TIME)
        self.assertEqual(blocked["envelope"]["attempt_no"], 3)
        self.assertEqual(blocked["envelope"]["status"], "blocked_manual")

    def test_feedback_ref_mismatch(self):
        event = self._event()
        feedback = validation_feedback(event)
        feedback["payload"]["data_package_ref"] = {"artifact_id": "other", "version": 1, "content_hash": "a" * 64}
        rehash(feedback)
        with self.assertRaises(ContractError) as ctx:
            self.builder.apply_validation_feedback(event, feedback, self.event_closure, proposed_data_groups=fixed_groups(event), created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "FEEDBACK_PACKAGE_REF_MISMATCH")

    def test_regression_not_routed(self):
        event = self._event()
        feedback = regression_feedback(event, route="251")
        with self.assertRaises(ContractError) as ctx:
            self.builder.apply_regression_feedback(event, feedback, self.event_closure, proposed_data_groups=fixed_groups(event), created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "REGRESSION_NOT_ROUTED_TO_241")

    def test_proposed_groups_required(self):
        event = self._event()
        with self.assertRaises(ContractError) as ctx:
            self.builder.apply_validation_feedback(event, validation_feedback(event), self.event_closure, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "PROPOSED_DATA_GROUPS_REQUIRED")

    def test_attempt_out_of_range(self):
        event = self._event()
        with self.assertRaises(ContractError) as ctx:
            self.builder.apply_validation_feedback(event, validation_feedback(event), self.event_closure, proposed_data_groups=fixed_groups(event), attempt_no=4, created_at=FIXED_TIME)
        self.assertEqual(str(ctx.exception), "ATTEMPT_OUT_OF_RANGE")

    # ------------------------------------------------------------- manifest
    def test_manifest_valid(self):
        event = self._event()
        manifest = self.builder.build_manifest(event, issue_key="EAS-31")
        self.assertEqual(manifest["issue_key"], "EAS-31")
        self.assertEqual(manifest["artifact_ref"], artifact_ref(event["envelope"]))

    def test_manifest_issue_key_mismatch(self):
        event = self._event()
        manifest = self.builder.build_manifest(event, issue_key="EAS-31")
        with self.assertRaises(ContractError):
            self.builder._validate_manifest(manifest, event, "EAS-OTHER")

    def test_manifest_boundary_violation(self):
        event = self._event()
        manifest = self.builder.build_manifest(event, issue_key="EAS-31")
        manifest["runtime_locator"] = "/etc/passwd"
        with self.assertRaises(ContractError):
            self.builder._validate_manifest(manifest, event, "EAS-31")

    # ---------------------------------------------------------- downstream
    def test_downstream_242_stub(self):
        event = self._event()
        self.builder.validate_bound_data(event)
        group = event["payload"]["data_groups"][0]
        self.assertGreater(len(group["records"]), 0)
        self.assertEqual(group["group_summary"], BoundDataGenerator._summarize(group["records"]))


if __name__ == "__main__":
    unittest.main()
