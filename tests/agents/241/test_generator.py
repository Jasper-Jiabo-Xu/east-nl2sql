from __future__ import annotations

import copy
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(ROOT / "src"))

mod = importlib.import_module("east_v5.agents.241.generator")
BoundDataGenerator = mod.BoundDataGenerator
closure_mod = importlib.import_module("east_v5.agents.220.closure")
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256
try:
    from tests.agents.foundation_eas114_helpers import SANITIZED_241_RUNTIME, context as foundation_context, groups_and_traces, receipt as foundation_receipt
except ModuleNotFoundError:
    from agents.foundation_eas114_helpers import SANITIZED_241_RUNTIME, context as foundation_context, groups_and_traces, receipt as foundation_receipt

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


def foundation_validation_feedback(previous: dict) -> dict:
    feedback = validation_feedback(previous)
    feedback["envelope"]["mode"] = "foundation"
    feedback["envelope"]["qa_id"] = previous["envelope"]["qa_id"]
    rehash(feedback)
    return feedback


def foundation_regression_feedback(previous: dict) -> dict:
    feedback = regression_feedback(previous)
    feedback["envelope"]["mode"] = "foundation"
    feedback["envelope"]["qa_id"] = previous["envelope"]["qa_id"]
    feedback["payload"]["mode"] = "foundation"
    rehash(feedback)
    return feedback


def fixed_groups(package: dict) -> list:
    groups = copy.deepcopy(package["payload"]["data_groups"])
    groups[0]["records"][0]["field_values"][0]["value"] = "脱敏值-F001-修订"
    groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
    return groups


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.builder = BoundDataGenerator(ROOT, foundation_invocation_verifier=SANITIZED_241_RUNTIME)
        self.event_closure = fixture("structure-closure-event.json")
        self.operation = fixture("operation-closure.json")
        self.profile = fixture("foundation-profile.json")
        self.task = json.loads((ROOT / "fixtures" / "artifacts" / "foundation-task-package-valid.json").read_text(encoding="utf-8"))
        # Exercise the production 210→220 edge; do not hide missing task fields
        # in a hand-authored structure-closure fixture.
        self.foundation_closure = closure_mod.build_closure(self.task, [])
        self.foundation_closure["payload"]["references"] = [
            {"type": "hierarchy_asset", "artifact_ref": self.task["payload"]["hierarchy_asset_refs"][0]},
        ]
        rehash(self.foundation_closure)
        self.snapshot = fixture("database-read-snapshot.json")
        self.foundation_snapshot = copy.deepcopy(self.snapshot)
        self.foundation_snapshot["envelope"]["mode"] = "foundation"
        self.foundation_snapshot["payload"]["snapshot_hash"] = sha256({key: value for key, value in self.foundation_snapshot["payload"].items() if key != "snapshot_hash"})
        rehash(self.foundation_snapshot)

    def _event(self, **kwargs):
        return self.builder.build_bound_data(self.event_closure, operation_closure=self.operation, snapshot=self.snapshot, created_at=FIXED_TIME, **kwargs)

    def _foundation(self, **kwargs):
        context = foundation_context(self.task, self.foundation_closure, self.foundation_snapshot, created_at=FIXED_TIME)
        groups, traces = groups_and_traces(self.foundation_closure)
        groups = kwargs.pop("proposed_data_groups", groups)
        chosen_traces = kwargs.pop("selection_traces", traces)
        return self.builder.build_bound_data(self.foundation_closure, foundation_task_package=self.task, foundation_profile=self.profile, snapshot=self.foundation_snapshot, foundation_generation_context=context, proposed_data_groups=groups, selection_traces=chosen_traces, generation_receipt=kwargs.pop("generation_receipt", foundation_receipt(self.task, context, groups, chosen_traces)), created_at=FIXED_TIME, **kwargs)

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
        self.assertEqual(self.foundation_closure["payload"]["fields"], ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"])

    def test_foundation_missing_task_field_is_not_silently_repaired(self):
        closure = copy.deepcopy(self.foundation_closure)
        closure["payload"]["fields"] = ["FIXTURE_CUSTOMER.C001"]
        rehash(closure)
        with self.assertRaisesRegex(ContractError, "FOUNDATION_TASK_FIELD_SCOPE_MISSING"):
            self.builder.build_bound_data(
                closure, foundation_task_package=self.task, foundation_profile=self.profile,
                created_at=FIXED_TIME,
            )

    def test_foundation_task_external_field_stays_out_of_closure(self):
        groups = self._foundation()["payload"]["data_groups"]
        groups[0]["records"][0]["field_values"].append(
            {"field_id": "C999", "value": "脱敏值-C999", "standard_type": "STRING", "is_null": False}
        )
        groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
        with self.assertRaisesRegex(ContractError, "FIELD_OUT_OF_CLOSURE"):
            self._foundation(proposed_data_groups=groups)

    def test_reproducible(self):
        first = self._event()
        second = self._event()
        self.assertEqual(first["envelope"]["content_hash"], second["envelope"]["content_hash"])

    def test_foundation_reproducible_with_real_agent_receipt(self):
        self.assertEqual(self._foundation(), self._foundation())

    def test_foundation_requires_runtime_context(self):
        groups, traces = groups_and_traces(self.foundation_closure)
        with self.assertRaisesRegex(ContractError, "FOUNDATION_GENERATION_CONTEXT_REQUIRED"):
            self.builder.build_bound_data(
                self.foundation_closure, foundation_task_package=self.task, foundation_profile=self.profile,
                snapshot=self.foundation_snapshot, proposed_data_groups=groups, selection_traces=traces,
                generation_receipt={}, created_at=FIXED_TIME,
            )

    def test_foundation_rejects_incomplete_or_infeasible_selection_trace(self):
        context = foundation_context(self.task, self.foundation_closure, self.foundation_snapshot, created_at=FIXED_TIME)
        groups, traces = groups_and_traces(self.foundation_closure)
        with self.assertRaisesRegex(ContractError, "FOUNDATION_SELECTION_TRACE_COVERAGE_MISMATCH"):
            self.builder.build_bound_data(
                self.foundation_closure, foundation_task_package=self.task, foundation_profile=self.profile,
                snapshot=self.foundation_snapshot, foundation_generation_context=context, proposed_data_groups=groups,
                selection_traces=traces[:-1], generation_receipt=foundation_receipt(self.task, context, groups, traces[:-1]), created_at=FIXED_TIME,
            )
        infeasible = copy.deepcopy(traces)
        infeasible[0]["feasible_values"] = ["EAS114-not-chosen"]
        with self.assertRaisesRegex(ContractError, "FOUNDATION_SELECTION_OUTSIDE_FEASIBLE_SET"):
            self.builder.build_bound_data(
                self.foundation_closure, foundation_task_package=self.task, foundation_profile=self.profile,
                snapshot=self.foundation_snapshot, foundation_generation_context=context, proposed_data_groups=groups,
                selection_traces=infeasible, generation_receipt=foundation_receipt(self.task, context, groups, infeasible), created_at=FIXED_TIME,
            )

    def test_foundation_rejects_forged_display_name_receipt_and_missing_verifier(self):
        context = foundation_context(self.task, self.foundation_closure, self.foundation_snapshot, created_at=FIXED_TIME)
        groups, traces = groups_and_traces(self.foundation_closure)
        forged = {"agent_id": "241-初始数据生成与修改agent", "generation_kind": "business_agent", "input_context_ref": artifact_ref(context["envelope"]), "output_hash": sha256({"data_groups": groups, "selection_traces": traces})}
        with self.assertRaisesRegex(ContractError, "FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED"):
            self.builder.build_bound_data(self.foundation_closure, foundation_task_package=self.task, foundation_profile=self.profile, snapshot=self.foundation_snapshot, foundation_generation_context=context, proposed_data_groups=groups, selection_traces=traces, generation_receipt=forged, created_at=FIXED_TIME)
        with self.assertRaisesRegex(ContractError, "FOUNDATION_INVOCATION_VERIFIER_REQUIRED"):
            BoundDataGenerator(ROOT).build_bound_data(self.foundation_closure, foundation_task_package=self.task, foundation_profile=self.profile, snapshot=self.foundation_snapshot, foundation_generation_context=context, proposed_data_groups=groups, selection_traces=traces, generation_receipt=foundation_receipt(self.task, context, groups, traces), created_at=FIXED_TIME)

    def test_production_runtime_attestation_assembly_mints_241_and_verifies_at_242(self):
        from east_v5.runtime.foundation_assembly import FoundationRuntimeAssembly
        from east_v5.runtime.foundation_attestation import FoundationRuntimeAttestationService
        with tempfile.TemporaryDirectory() as raw_root:
            runtime_root = Path(raw_root); os.chmod(runtime_root, 0o700)
            issuer = FoundationRuntimeAttestationService(runtime_root, task_id="runtime-task-241", issue_id="EAS-114", target_agent_id="241", target_agent_uuid="7df640f9-973f-4c46-8302-df1256f60146", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395", run_id=self.task["envelope"]["run_id"], qa_id=self.task["envelope"]["qa_id"], trace_id=self.task["envelope"]["trace_id"], attempt_no=1, mode="foundation")
            context = foundation_context(self.task, self.foundation_closure, self.foundation_snapshot, created_at=FIXED_TIME)
            groups, traces = groups_and_traces(self.foundation_closure)
            receipt = issuer.mint_241_receipt(self.task, context, groups, traces)
            bound = FoundationRuntimeAssembly(issuer).generator(ROOT).build_bound_data(self.foundation_closure, foundation_task_package=self.task, foundation_profile=self.profile, snapshot=self.foundation_snapshot, foundation_generation_context=context, proposed_data_groups=groups, selection_traces=traces, generation_receipt=receipt, created_at=FIXED_TIME)
            verifier = FoundationRuntimeAttestationService(runtime_root, task_id="runtime-task-242", issue_id="EAS-114", target_agent_id="242", target_agent_uuid="4e801c18-7048-4227-a5c7-515f51a5e5ba", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395", run_id=self.task["envelope"]["run_id"], qa_id=self.task["envelope"]["qa_id"], trace_id=self.task["envelope"]["trace_id"], attempt_no=1, mode="foundation")
            runtime = importlib.import_module("east_v5.agents.242.sanitized_fixture").SanitizedRuntime()
            try:
                frozen = FoundationRuntimeAssembly(verifier).validator(ROOT).freeze_bound_data(bound, self.foundation_closure, runtime.resolver(), foundation_task_package=self.task, database_snapshot=self.foundation_snapshot, foundation_generation_context=context)
                self.assertEqual(frozen["payload"]["source_data_package_ref"], artifact_ref(bound["envelope"]))
            finally:
                runtime.close()
            forged = copy.deepcopy(receipt); forged["output_hash"] = "0" * 64
            with self.assertRaisesRegex(ContractError, "FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED"):
                verifier.verify(forged, {key: receipt[key] for key in receipt if key not in {"invocation_id", "runtime_attestation"}})

    def test_bootstrapped_adapter_assembly_is_the_only_foundation_attestation_path(self):
        """Exercise real bootstrap -> registry -> adapter -> 241 -> 242 wiring."""
        from east_v5.artifacts import ArtifactRegistry
        from east_v5.runtime import RuntimeAdapter, RuntimeBootstrap
        from east_v5.runtime.foundation_assembly import FoundationRuntimeAssembly
        build_foundation_profile = importlib.import_module("east_v5.agents.210.foundation").build_foundation_profile

        with tempfile.TemporaryDirectory(dir=ROOT.parent) as raw:
            root = Path(raw).resolve(); candidate, runtime_root = root / "candidate", (root / "runtime").resolve()
            shutil.copytree(ROOT, candidate, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            subprocess.run(["git", "init", "-q", str(candidate)], check=True)
            subprocess.run(["git", "-C", str(candidate), "add", "."], check=True)
            subprocess.run(["git", "-C", str(candidate), "-c", "user.email=eas114@example.invalid", "-c", "user.name=EAS-114", "commit", "-qm", "runtime-candidate"], check=True)
            head = subprocess.check_output(["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True).strip()
            roots = {"repo_root": str(candidate / "repo"), "runtime_root": str(runtime_root), "reference_root": str(root / "reference"), "reference_read_only": True}

            task = copy.deepcopy(self.task)
            task["envelope"]["parent_artifact_refs"] = []; task["envelope"]["input_hashes"] = []; rehash(task)
            closure = closure_mod.build_closure(task, [])
            closure["payload"]["references"] = [{"type": "hierarchy_asset", "artifact_ref": task["payload"]["hierarchy_asset_refs"][0]}]
            rehash(closure)
            profile = build_foundation_profile(task)
            snapshot = copy.deepcopy(self.foundation_snapshot)
            snapshot["envelope"].update({"run_id": task["envelope"]["run_id"], "qa_id": None, "trace_id": task["envelope"]["trace_id"], "attempt_no": 1})
            rehash(snapshot)
            context = foundation_context(task, closure, snapshot, created_at=FIXED_TIME)
            registry = ArtifactRegistry(candidate, roots, "EAS-114", task["envelope"]["run_id"], 1)
            for package in (task, closure, profile, snapshot, context):
                registry.register(package["envelope"], package["payload"])

            def envelope(target: str, agent_uuid: str, input_ref: dict, route: str) -> dict:
                bootstrap = {"bootstrap_version": "east-v5-runtime-bootstrap/v1", "candidate_base_sha": "a" * 40, "candidate_head_sha": head, "adapter_sha256": __import__("hashlib").sha256((candidate / "src/east_v5/runtime/adapter.py").read_bytes()).hexdigest(), "bootstrap_sha256": __import__("hashlib").sha256((candidate / "src/east_v5/runtime/bootstrap.py").read_bytes()).hexdigest(), "runner_sha256": __import__("hashlib").sha256((candidate / "scripts/runtime_bootstrap.py").read_bytes()).hexdigest(), "runtime_context": {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "eas114", "project_id": "foundation", "daemon_id": "test"}, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v1", "skill_version": "v1", "skill_manifest_sha256": "f" * 64}}
                from east_v5.runtime import root_binding_id
                return {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": "EAS-114", "run_id": task["envelope"]["run_id"], "trace_id": task["envelope"]["trace_id"], "qa_id": None, "attempt": 1, "target_agent_id": target, "target_agent_uuid": agent_uuid, "root_binding_id": root_binding_id(bootstrap["runtime_context"]), "input_ref": input_ref, "expected_output": {"artifact_type": "bound_data" if target == "241" else "verified_bound_data", "producer_id": target, "route_target": route}, "execution_bootstrap": bootstrap}

            def build_adapter(task_envelope: dict):
                return RuntimeBootstrap(candidate, task_envelope, environ={"V5_RUNTIME_ROOT": str(runtime_root)}).build_adapter(roots)

            env241 = envelope("241", "7df640f9-973f-4c46-8302-df1256f60146", artifact_ref(task["envelope"]), "242")
            adapter241 = build_adapter(env241)
            assembly241 = FoundationRuntimeAssembly.from_runtime_adapter(adapter241, task_id="runtime-task-241", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
            groups, traces = groups_and_traces(closure)
            receipt = assembly241.invocation_service.mint_241_receipt(task, context, groups, traces)
            bound = assembly241.generator(candidate).build_bound_data(closure, foundation_task_package=task, foundation_profile=profile, snapshot=snapshot, foundation_generation_context=context, proposed_data_groups=groups, selection_traces=traces, generation_receipt=receipt, created_at=FIXED_TIME)
            registered = adapter241.register_output(bound, task_id="runtime-task-241", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
            self.assertIsNone(registered["receipt"]["qa_id"])

            env242 = envelope("242", "4e801c18-7048-4227-a5c7-515f51a5e5ba", registered["output_ref"], "260")
            adapter242 = build_adapter(env242)
            assembly242 = FoundationRuntimeAssembly.from_runtime_adapter(adapter242, task_id="runtime-task-242", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
            runtime = importlib.import_module("east_v5.agents.242.sanitized_fixture").SanitizedRuntime()
            try:
                frozen = assembly242.validator(candidate).freeze_bound_data(bound, closure, runtime.resolver(), foundation_task_package=task, database_snapshot=snapshot, foundation_generation_context=context)
                self.assertEqual(frozen["payload"]["source_data_package_ref"], registered["output_ref"])
            finally:
                runtime.close()

            with self.assertRaisesRegex(ContractError, "FOUNDATION_RUNTIME_CONTEXT_INVALID"):
                FoundationRuntimeAssembly.from_runtime_adapter(adapter241, task_id="runtime-task-241", runtime_id="wrong-runtime")
            wrong_uuid = envelope("241", "00000000-0000-0000-0000-000000000000", artifact_ref(task["envelope"]), "242")
            wrong_adapter = build_adapter(wrong_uuid)
            with self.assertRaisesRegex(ContractError, "FOUNDATION_RUNTIME_CALLER_FORBIDDEN"):
                FoundationRuntimeAssembly.from_runtime_adapter(wrong_adapter, task_id="runtime-task-bad-uuid", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
            wrong_route = envelope("241", "7df640f9-973f-4c46-8302-df1256f60146", artifact_ref(task["envelope"]), "260")
            route_adapter = build_adapter(wrong_route)
            with self.assertRaisesRegex(ContractError, "FOUNDATION_RUNTIME_NODE_FORBIDDEN"):
                FoundationRuntimeAssembly.from_runtime_adapter(route_adapter, task_id="runtime-task-bad-route", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
            event_input = copy.deepcopy(self.event_closure); registry.register(event_input["envelope"], event_input["payload"])
            with self.assertRaisesRegex(ContractError, "RUNTIME_QA_ID_REQUIRED"):
                build_adapter(envelope("241", "7df640f9-973f-4c46-8302-df1256f60146", artifact_ref(event_input["envelope"]), "242"))
            forged = copy.deepcopy(bound); forged["payload"]["generation_receipt"]["output_hash"] = "0" * 64; rehash(forged)
            runtime = importlib.import_module("east_v5.agents.242.sanitized_fixture").SanitizedRuntime()
            try:
                with self.assertRaises(ContractError):
                    assembly242.validator(candidate).freeze_bound_data(forged, closure, runtime.resolver(), foundation_task_package=task, database_snapshot=snapshot, foundation_generation_context=context)
            finally:
                runtime.close()

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

    def test_foundation_validation_retry_rebinds_trace_and_receipt_for_242(self):
        initial = self._foundation()
        context = foundation_context(self.task, self.foundation_closure, self.foundation_snapshot, created_at=FIXED_TIME)
        groups, traces = groups_and_traces(self.foundation_closure, values={"FIXTURE_CUSTOMER.C001": "EAS114-CHANGED"})
        receipt = foundation_receipt(self.task, context, groups, traces, attempt_no=2)
        remapped = self.builder.apply_validation_feedback(
            initial, foundation_validation_feedback(initial), self.foundation_closure, snapshot=self.foundation_snapshot,
            proposed_data_groups=groups, foundation_task_package=self.task, foundation_generation_context=context,
            selection_traces=traces, generation_receipt=receipt, created_at=FIXED_TIME,
        )
        self.assertEqual(remapped["envelope"]["attempt_no"], 2)
        self.assertEqual(remapped["payload"]["selection_traces"], traces)
        self.assertEqual(remapped["payload"]["generation_receipt"], receipt)
        runtime = importlib.import_module("east_v5.agents.242.sanitized_fixture").SanitizedRuntime()
        try:
            validator = importlib.import_module("east_v5.agents.242.validator").DataValidator(ROOT, foundation_invocation_verifier=SANITIZED_241_RUNTIME)
            frozen = validator.freeze_bound_data(remapped, self.foundation_closure, runtime.resolver(), foundation_task_package=self.task, database_snapshot=self.foundation_snapshot, foundation_generation_context=context)
            self.assertEqual(frozen["payload"]["source_data_package_ref"], artifact_ref(remapped["envelope"]))
        finally:
            runtime.close()

    def test_foundation_regression_retry_rejects_stale_evidence_and_accepts_new_evidence(self):
        initial = self._foundation()
        context = foundation_context(self.task, self.foundation_closure, self.foundation_snapshot, created_at=FIXED_TIME)
        groups, traces = groups_and_traces(self.foundation_closure, values={"FIXTURE_CUSTOMER.C002": "EAS114-CHANGED-260"})
        with self.assertRaisesRegex(ContractError, "FOUNDATION_SELECTION_TRACE_CHOSEN_VALUE_DRIFT"):
            self.builder.apply_regression_feedback(initial, foundation_regression_feedback(initial), self.foundation_closure, snapshot=self.foundation_snapshot, proposed_data_groups=groups, foundation_task_package=self.task, foundation_generation_context=context, selection_traces=initial["payload"]["selection_traces"], generation_receipt=initial["payload"]["generation_receipt"], created_at=FIXED_TIME)
        receipt = foundation_receipt(self.task, context, groups, traces, attempt_no=2)
        remapped = self.builder.apply_regression_feedback(initial, foundation_regression_feedback(initial), self.foundation_closure, snapshot=self.foundation_snapshot, proposed_data_groups=groups, foundation_task_package=self.task, foundation_generation_context=context, selection_traces=traces, generation_receipt=receipt, created_at=FIXED_TIME)
        self.assertEqual(remapped["payload"]["generation_receipt"], receipt)
        runtime = importlib.import_module("east_v5.agents.242.sanitized_fixture").SanitizedRuntime()
        copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        try:
            validator = importlib.import_module("east_v5.agents.242.validator").DataValidator(ROOT, foundation_invocation_verifier=SANITIZED_241_RUNTIME)
            frozen = validator.freeze_bound_data(remapped, self.foundation_closure, runtime.resolver(), foundation_task_package=self.task, database_snapshot=self.foundation_snapshot, foundation_generation_context=context)
            for connection in (copy_db, formal_db):
                connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
            report = importlib.import_module("east_v5.agents.260.regression").run_foundation_regression(ROOT, self.task, self.foundation_closure, frozen, self.foundation_snapshot, copy_db, formal_db, set(), attempt_no=2)
            self.assertEqual(report["envelope"]["artifact_type"], "database_copy_regression", report["payload"])
            self.assertEqual(report["payload"]["regression_status"], "passed")
        finally:
            copy_db.close(); formal_db.close(); runtime.close()

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
