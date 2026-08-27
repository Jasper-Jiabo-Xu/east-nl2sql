"""Public production seams for the Foundation local data-plane entrypoint."""
from __future__ import annotations

import hashlib
import hmac
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import east_v5.runtime.foundation_data_plane_entrypoint as entrypoint_module

from east_v5.runtime.foundation_data_plane_entrypoint import (
    FoundationDataPlaneEntrypoint,
    FoundationDataPlaneError,
)


class FoundationDataPlaneEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.reference = self.base / "eastQuestionSet"; self.reference.mkdir()
        self.home = self.base / "home"; self.home.mkdir()
        self.context = {
            "resolver_version": "daemon_local_platform_data_resolver_v1",
            "workspace_id": "workspace", "project_id": "project", "daemon_id": "daemon",
        }
        self.entrypoint = FoundationDataPlaneEntrypoint(self.reference, home=self.home, platform="darwin")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_provision_creates_a_bound_private_root_idempotently(self) -> None:
        first = self.entrypoint.provision(self.context)
        second = self.entrypoint.provision(self.context)
        self.assertEqual(first, second)
        self.assertEqual(stat.S_IMODE(first.runtime_root.stat().st_mode), 0o700)
        marker = first.runtime_root / "daemon-root-binding-v12.json"
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(json.loads(marker.read_text()), {
            "schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": first.root_binding_id,
        })
        self.assertTrue((first.runtime_root / "east-v5-full-runtime-v12-state.json").is_file())

    def test_provision_rejects_existing_marker_drift(self) -> None:
        provisioned = self.entrypoint.provision(self.context)
        marker = provisioned.runtime_root / "daemon-root-binding-v12.json"
        marker.write_text(json.dumps({"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": "0" * 64}))
        marker.chmod(0o600)
        with self.assertRaisesRegex(FoundationDataPlaneError, "FOUNDATION_DATA_PLANE_ROOT_MARKER_DRIFT"):
            self.entrypoint.provision(self.context)

    def test_eas19_seals_only_refs_and_hashes_for_the_repo_launcher(self) -> None:
        runtime = self.entrypoint.provision(self.context)
        key = runtime.runtime_root / ".foundation-parent-chain-materializer-v1.key"
        key.write_bytes(b"k" * 32); key.chmod(0o600)
        envelope = lambda name: {"artifact_id": name, "version": 1, "content_hash": hashlib.sha256(name.encode()).hexdigest()}
        task_envelope = {**envelope("task"), "run_id": "accepted-run", "attempt_no": 1}
        recovered = entrypoint_module.RecoveredParentChain(
            {"envelope": task_envelope}, {"envelope": envelope("closure")}, "e" * 64, {},
        )
        selection = SimpleNamespace(
            database_snapshot={"envelope": envelope("snapshot")},
            generation_context={"envelope": envelope("context")},
            resolver_universe_ref=envelope("universe"),
        )
        receipt = self.entrypoint._seal_launch_inputs(runtime, recovered, selection)
        self.assertEqual(receipt["schema_version"], "foundation-launch-inputs/v1")
        self.assertEqual(receipt["root_binding_id"], runtime.root_binding_id)
        self.assertEqual(receipt, self.entrypoint._seal_launch_inputs(runtime, recovered, selection))

    def test_resolve_formal_baseline_normalizes_once_and_checks_full_hash(self) -> None:
        source = self.reference / "formal.db"; source.write_bytes(b"approved-formal-baseline")
        lock = self.base / "formal_input_lock.json"
        lock.write_text(json.dumps({"lock": {
            "formal_db_baseline_path": "eastQuestionSet/formal.db",
            "formal_db_baseline_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "formal_db_baseline_size_bytes": source.stat().st_size,
        }}))
        baseline = self.entrypoint.resolve_formal_baseline(lock)
        self.assertEqual(baseline.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(baseline.size_bytes, source.stat().st_size)

    def test_resolve_formal_baseline_rejects_escape(self) -> None:
        lock = self.base / "formal_input_lock.json"
        lock.write_text(json.dumps({"lock": {
            "formal_db_baseline_path": "../formal.db", "formal_db_baseline_sha256": "0" * 64,
            "formal_db_baseline_size_bytes": 1,
        }}))
        with self.assertRaisesRegex(FoundationDataPlaneError, "FOUNDATION_DATA_PLANE_LOCATOR_INVALID"):
            self.entrypoint.resolve_formal_baseline(lock)

    def _valid_target(self, **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "id": "target-task", "status": "completed", "runtime_id": "authorized-runtime",
            "agent_id": "assistant-agent", "issue_id": "source-issue", "kind": "comment",
            "trigger_comment_id": "trigger-comment", "delivered_comment_ids": ["trigger-comment"],
            "work_dir": "/controlled/run", "display_only": "must-not-survive-projection",
        }
        record.update(overrides)
        return record

    def _direct_root(self) -> dict[str, object]:
        return {
            "id": "root-task", "status": "completed", "runtime_id": "other-runtime",
            "agent_id": "other-agent", "issue_id": "source-issue", "kind": "direct",
            "trigger_comment_id": None, "delivered_comment_ids": [],
            "work_dir": "/controlled/root",
        }

    def _six_run_shape(self) -> list[dict[str, object]]:
        comments = [
            {
                "id": f"comment-{index}", "status": "completed",
                "runtime_id": "authorized-runtime" if index % 2 == 0 else "other-runtime",
                "agent_id": "assistant-agent" if index % 2 == 0 else "other-agent",
                "issue_id": "source-issue", "kind": "comment",
                "trigger_comment_id": f"trigger-{index}", "delivered_comment_ids": [f"trigger-{index}"],
                "work_dir": f"/controlled/run-{index}",
            }
            for index in range(4)
        ]
        return [self._valid_target(), *comments, self._direct_root()]

    def _patched_run_record(self, runs: object) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.object(entrypoint_module, "_RECOVERY_SOURCE_ISSUE", "source-issue"))
        stack.enter_context(patch.object(entrypoint_module, "_RECOVERY_SOURCE_TASK", "target-task"))
        stack.enter_context(patch.object(entrypoint_module, "_RECOVERY_SOURCE_AGENT", "assistant-agent"))
        stack.enter_context(patch.object(entrypoint_module, "_RECOVERY_SOURCE_RUNTIME", "authorized-runtime"))
        stack.enter_context(patch.object(entrypoint_module, "_RECOVERY_SOURCE_TRIGGER_COMMENT", "trigger-comment"))
        stack.enter_context(patch.object(
            entrypoint_module.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(runs)),
        ))
        return stack

    def test_platform_run_record_projects_the_unique_target_run(self) -> None:
        """The six-run EAS-113 shape succeeds; only the target survives projection."""
        runs = self._six_run_shape()
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append((argv, kwargs))
            return SimpleNamespace(stdout=json.dumps(runs))

        with patch.object(entrypoint_module, "_RECOVERY_SOURCE_ISSUE", "source-issue"), patch.object(
            entrypoint_module, "_RECOVERY_SOURCE_TASK", "target-task"
        ), patch.object(entrypoint_module, "_RECOVERY_SOURCE_AGENT", "assistant-agent"), patch.object(
            entrypoint_module, "_RECOVERY_SOURCE_RUNTIME", "authorized-runtime"
        ), patch.object(entrypoint_module, "_RECOVERY_SOURCE_TRIGGER_COMMENT", "trigger-comment"), patch.object(
            entrypoint_module.subprocess, "run", side_effect=runner
        ):
            result = FoundationDataPlaneEntrypoint._platform_run_record()
        self.assertEqual(calls, [(
            ["multica", "issue", "runs", "source-issue", "--output", "json"],
            {"check": True, "capture_output": True, "text": True},
        )])
        self.assertEqual(result, {
            "id": "target-task", "status": "completed", "runtime_id": "authorized-runtime",
            "delivered_comment_ids": ["trigger-comment"], "work_dir": "/controlled/run",
            "trigger_comment_id": "trigger-comment",
        })

    def test_platform_run_record_is_order_independent(self) -> None:
        runs = self._six_run_shape()
        with self._patched_run_record(runs):
            first = FoundationDataPlaneEntrypoint._platform_run_record()
        with self._patched_run_record(list(reversed(runs))):
            second = FoundationDataPlaneEntrypoint._platform_run_record()
        self.assertEqual(first, second)

    def test_platform_run_record_rejects_missing_or_duplicate_target(self) -> None:
        cases = {
            "target_missing": [self._direct_root()],
            "target_duplicate": [self._valid_target(), self._valid_target(work_dir="/controlled/other")],
        }
        for name, runs in cases.items():
            with self.subTest(name=name), self._patched_run_record(runs):
                with self.assertRaisesRegex(FoundationDataPlaneError, "FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID"):
                    FoundationDataPlaneEntrypoint._platform_run_record()

    def test_platform_run_record_rejects_target_provenance_drift(self) -> None:
        target = self._valid_target()
        drop = lambda *keys: {key: value for key, value in target.items() if key not in keys}
        drift = {
            "empty_delivered": self._valid_target(delivered_comment_ids=[]),
            "wrong_delivered": self._valid_target(delivered_comment_ids=["wrong"]),
            "non_string_delivered": self._valid_target(delivered_comment_ids=[1]),
            "null_trigger": self._valid_target(trigger_comment_id=None),
            "wrong_trigger": self._valid_target(trigger_comment_id="wrong"),
            "wrong_agent": self._valid_target(agent_id="other-agent"),
            "wrong_runtime": self._valid_target(runtime_id="other-runtime"),
            "wrong_issue": self._valid_target(issue_id="other-issue"),
            "wrong_status": self._valid_target(status="failed"),
            "wrong_kind": self._valid_target(kind="direct"),
            "missing_status": drop("status"),
            "missing_runtime": drop("runtime_id"),
            "missing_work_dir": drop("work_dir"),
            "missing_agent": drop("agent_id"),
            "missing_issue": drop("issue_id"),
            "missing_kind": drop("kind"),
            "missing_trigger": drop("trigger_comment_id"),
            "missing_delivered": drop("delivered_comment_ids"),
        }
        for name, target_record in drift.items():
            with self.subTest(name=name), self._patched_run_record([target_record, self._direct_root()]):
                with self.assertRaisesRegex(FoundationDataPlaneError, "FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID"):
                    FoundationDataPlaneEntrypoint._platform_run_record()

    def test_platform_run_record_rejects_malformed_run_list(self) -> None:
        target = self._valid_target()
        cases = {
            "wrapper": {"runs": [target]},
            "empty": [],
            "scalar": "invalid",
            "non_object": ["invalid"],
            "missing_id": [{key: value for key, value in target.items() if key != "id"}],
            "empty_id": [{**target, "id": ""}],
            "wrong_type_id": [{**target, "id": 1}],
            "duplicate_id": [self._direct_root(), self._direct_root()],
        }
        for name, value in cases.items():
            with self.subTest(name=name), self._patched_run_record(value):
                with self.assertRaisesRegex(FoundationDataPlaneError, "FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID"):
                    FoundationDataPlaneEntrypoint._platform_run_record()

    def test_recovery_rejects_invalid_source_work_dir(self) -> None:
        runtime = self.entrypoint.provision(self.context)
        cases = {
            "relative": {"id": "source-task", "work_dir": "relative"},
            "missing": {"id": "source-task"},
        }
        for name, record in cases.items():
            with self.subTest(name=name), patch.object(self.entrypoint, "_verify_materialization"), patch.object(
                self.entrypoint, "_platform_run_record", return_value=record,
            ):
                with self.assertRaisesRegex(FoundationDataPlaneError, "FOUNDATION_PARENT_CHAIN_SOURCE_WORKDIR_INVALID"):
                    self.entrypoint.recover_parent_chain(runtime, {})
            self.assertFalse((runtime.runtime_root / "foundation-parent-chain-recovery-v1").exists())

    def test_recover_parent_chain_uses_only_the_accepted_run_record_and_bytes(self) -> None:
        """The public recovery seam accepts neither a source path nor packages."""
        runtime = self.entrypoint.provision(self.context)
        source = self.base / "accepted-source"; output = source / "eas113_rerun_out"; output.mkdir(parents=True)
        task_payload = {
            "schema_version": "v5.foundation-task-package/v1", "foundation_task_id": "fixture-task",
            "foundation_mode": "initial_seed", "trigger_reason": "fixture", "target_database_version": "d" * 64,
            "target_object_types": ["FIXTURE"], "target_table_field_scope": {"FIXTURE": ["ID"]},
            "target_counts": {"FIXTURE": 1}, "distribution_targets": {"FIXTURE": {"default": 1}},
            "hierarchy_asset_refs": [{"artifact_id": "TRG", "version": 1, "content_hash": "a" * 64}],
            "prohibited_record_types": ["EVENT_OWNED"], "resume_qa_ref": None,
            "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0",
        }
        # The production method verifies this controlled container before use.
        container = runtime.runtime_root / "foundation-parent-chain-container-v1"; container.mkdir()
        (container / "foundation_task_package_projection.json").write_text(json.dumps(task_payload))
        (container / "foundation_intent_package.json").write_text(json.dumps({"content_sha256": "b" * 64}))
        projection_hash = entrypoint_module.sha256(task_payload)
        (container / "manifest.json").write_text(json.dumps({"intent_content_sha256": "b" * 64, "task_projection_content_sha256": projection_hash}))
        (container / "approved-assets").mkdir()
        (container / "constraint-assets-runtime-manifest.json").write_text("{}")
        (container / "foundation-hierarchy-endpoint-mapping.json").write_text("{}")
        (container / "eas111-evidence.json").write_text("{}")
        expected_task = self.entrypoint._rehydrate_task(task_payload, {"run_id": "fixture", "trace_id": "fixture", "created_at": "2026-08-25T00:00:00+00:00"})
        task_ref = {key: expected_task["envelope"][key] for key in ("artifact_id", "version", "content_hash")}
        tables = ["FIXTURE", *[f"T{index}" for index in range(70)]]
        fields = ["FIXTURE.ID", *[f"T{index % 70}.F{index}" for index in range(836)]]
        closure = {
            "envelope": {"artifact_id": "fixture-closure", "artifact_type": "structure_closure", "run_id": "fixture", "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "220", "parent_artifact_refs": [task_ref, {"artifact_id": "fixture-000", "version": 1, "content_hash": "d" * 64}], "input_hashes": [expected_task["envelope"]["content_hash"], "d" * 64], "status": "candidate", "mode": "foundation", "created_at": "2026-08-25T00:00:00+00:00", "trace_id": "fixture", "storage_locator": None},
            "payload": {"schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "tables": tables, "fields": fields, "references": [{"type": "fixture"}] * 456, "foundation_task_ref": task_ref},
        }
        from east_v5.artifacts import content_hash
        closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"])
        (output / "structure_closure.json").write_text(json.dumps(closure))
        (output / "evidence.json").write_text(json.dumps({"asset_000": {"ref": {"content_hash": "d" * 64}}, "closure_220": {"ref": {"content_hash": closure["envelope"]["content_hash"]}}, "closure_self_validation": {"closure_envelope_hash_identical": True}, "forbidden_module_calls": {"230": 0, "251": 0, "252": 0}}))
        record = {"id": "accepted-task", "status": "completed", "runtime_id": "accepted-runtime", "trigger_comment_id": "accepted-comment", "delivered_comment_ids": ["accepted-comment"], "work_dir": str(source)}
        bytes_by_name = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in container.iterdir() if path.name in entrypoint_module._PARENT_CONTAINER_BYTES}
        config = self.base / "materializer-config.json"; config.write_text("{}")
        config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        attachments = [{"filename": name, "byte_sha256": digest} for name, digest in sorted(bytes_by_name.items())]
        evidence = {"id": "evidence", "filename": "eas111-evidence.json", "byte_sha256": "e" * 64}
        assets = []
        container_manifest = {"root_binding_id": runtime.root_binding_id, "config_sha256": config_hash, "attachments": attachments, "eas111_evidence": evidence, "assets": assets, "runtime_manifest_sha256": "r".replace("r", "a") * 64, "runtime_manifest_local_sha256": "c" * 64, "hierarchy_mapping_sha256": "m".replace("m", "b") * 64}
        container_manifest["container_sha256"] = entrypoint_module.sha256(container_manifest)
        (container / "foundation-parent-chain-container-manifest.json").write_text(json.dumps(container_manifest))
        key = runtime.runtime_root / ".foundation-parent-chain-materializer-v1.key"; key.write_bytes(b"k" * 32); key.chmod(0o600)
        body = {"schema_version": "foundation-parent-chain-materialization-receipt/v2", "root_binding_id": runtime.root_binding_id, "bootstrap": {}, "skill_manifest_sha256": "a" * 64, "config_sha256": config_hash, "attachments": attachments, "eas111_evidence": evidence, "assets": assets, "runtime_manifest_sha256": "a" * 64, "runtime_manifest_local_sha256": "c" * 64, "hierarchy_mapping_sha256": "b" * 64, "container_sha256": container_manifest["container_sha256"]}
        receipt = {**body, "receipt_sha256": entrypoint_module.sha256(body)}
        receipt["attestation"] = hmac.new(key.read_bytes(), entrypoint_module.canonical_bytes(receipt), hashlib.sha256).hexdigest()
        with patch.object(self.entrypoint, "_platform_run_record", return_value=record), patch.object(entrypoint_module, "_MATERIALIZER_CONFIG", str(config)), patch.object(entrypoint_module, "_PARENT_CONTAINER_BYTES", bytes_by_name), patch.object(entrypoint_module, "_INTENT_HASH", "b" * 64), patch.object(entrypoint_module, "_PROJECTION_HASH", projection_hash), patch.object(entrypoint_module, "_RECOVERED_CLOSURE_HASH", closure["envelope"]["content_hash"]), patch.object(entrypoint_module, "_RECOVERY_SOURCE_TASK", "accepted-task"), patch.object(entrypoint_module, "_RECOVERY_SOURCE_RUNTIME", "accepted-runtime"), patch.object(entrypoint_module, "_RECOVERY_SOURCE_TRIGGER_COMMENT", "accepted-comment"), patch.object(entrypoint_module, "_RECOVERY_SOURCE_COMMENT", "accepted-result-comment"), patch.object(entrypoint_module, "_RECOVERED_CLOSURE_BYTES", hashlib.sha256((output / "structure_closure.json").read_bytes()).hexdigest()), patch.object(entrypoint_module, "_RECOVERED_EVIDENCE_BYTES", hashlib.sha256((output / "evidence.json").read_bytes()).hexdigest()), patch.object(entrypoint_module, "_RECOVERED_TASK_HASH", expected_task["envelope"]["content_hash"]), patch.object(entrypoint_module, "_RECOVERED_000_ANCESTOR_HASH", "d" * 64):
            recovered = self.entrypoint.recover_parent_chain(runtime, receipt)
        self.assertEqual(recovered.task["envelope"]["content_hash"], expected_task["envelope"]["content_hash"])
        self.assertEqual(recovered.closure["envelope"]["content_hash"], closure["envelope"]["content_hash"])


if __name__ == "__main__":
    unittest.main()
