"""Contract tests for the controlled Foundation repo-side launcher."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from east_v5.governance import canonical_bytes, sha256
from east_v5.runtime.bootstrap import RuntimeBootstrap, root_binding_id
from east_v5.runtime.foundation_repo_launcher import FoundationRepoLauncher, FoundationRepoLauncherError
from east_v5.runtime import foundation_fixed_component_000 as issuer_module
from east_v5.runtime import foundation_data_plane_entrypoint as entrypoint_module
from east_v5.runtime import foundation_task_identity as identity_module
from east_v5.runtime.foundation_task_context import VerifiedFoundationTask


ROOT = Path(__file__).resolve().parents[2]


class FoundationRepoLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        # RuntimeBootstrap rightly rejects OS temporary roots.  Keep the test
        # root adjacent to (but never inside) the checkout, as production does.
        self.home = ROOT.parent / f".launcher-test-{os.urandom(8).hex()}"; self.home.mkdir()
        self.context = {
            "resolver_version": "daemon_local_platform_data_resolver_v1",
            "workspace_id": "workspace", "project_id": "project", "daemon_id": "daemon",
        }
        head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()

        def digest(relative: str) -> str:
            return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()

        self.envelope = {
            "root_binding_id": root_binding_id(self.context),
            "execution_bootstrap": {
                "bootstrap_version": "east-v5-runtime-bootstrap/v1",
                "candidate_base_sha": head, "candidate_head_sha": head,
                "adapter_sha256": digest("src/east_v5/runtime/adapter.py"),
                "bootstrap_sha256": digest("src/east_v5/runtime/bootstrap.py"),
                "runner_sha256": digest("scripts/runtime_bootstrap.py"),
                "runtime_context": self.context,
                "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v1", "skill_version": "v1", "skill_manifest_sha256": "a" * 64},
            },
        }

        def git_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, stdout=head)
            if args[-3:] == ["status", "--porcelain", "--untracked-files=no"]:
                return subprocess.CompletedProcess(args, 0, stdout="")
            raise AssertionError(args)

        self.bootstrap = RuntimeBootstrap(ROOT, self.envelope, environ={}, platform="darwin", home=self.home, runner=git_runner)
        self.root = self.bootstrap.resolve_runtime_root()
        (self.root / "daemon-root-binding-v12.json").write_text(json.dumps({"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": self.envelope["root_binding_id"]}))
        (self.root / "daemon-root-binding-v12.json").chmod(0o600)
        (self.root / ".foundation-parent-chain-materializer-v1.key").write_bytes(b"k" * 32)
        (self.root / ".foundation-parent-chain-materializer-v1.key").chmod(0o600)
        container = self.root / "foundation-parent-chain-container-v1"; container.mkdir(mode=0o700)
        (container / "foundation-parent-chain-container-manifest.json").write_text(json.dumps({"root_binding_id": self.envelope["root_binding_id"], "container_sha256": "c" * 64}))
        (container / "foundation-parent-chain-container-manifest.json").chmod(0o600)
        materialization = {"schema_version": "foundation-parent-chain-materialization-receipt/v2", "root_binding_id": self.envelope["root_binding_id"], "container_sha256": "c" * 64}
        materialization["receipt_sha256"] = sha256(materialization)
        materialization["attestation"] = hmac.new(b"k" * 32, canonical_bytes({key: value for key, value in materialization.items() if key != "attestation"}), hashlib.sha256).hexdigest()
        (self.root / "foundation-parent-chain-materialization-receipt-v2.json").write_text(json.dumps(materialization))
        (self.root / "foundation-parent-chain-materialization-receipt-v2.json").chmod(0o600)
        recovery = self.root / "foundation-parent-chain-recovery-v1"; recovery.mkdir(mode=0o700)
        (recovery / "recovery-receipt.json").write_text(json.dumps({"root_binding_id": self.envelope["root_binding_id"], "receipt_sha256": "d" * 64}))
        (recovery / "recovery-receipt.json").chmod(0o600)
        self._write_inputs()
        self.current_role = "241"
        self.current_attempt = 1
        self.task_context = patch.object(identity_module, "current_foundation_task", side_effect=self._current_task)
        self.task_context.start()
        # This suite isolates the launch/task-edge contract.  Full immutable
        # parent-chain byte checks are exercised by the data-plane suite.
        self.parent_chain = patch("east_v5.runtime.foundation_fixed_component_000.FoundationFixedComponent000Issuer._verify_parent_chain", return_value=(
            {"root_binding_id": self.envelope["root_binding_id"], "container_sha256": "c" * 64},
            {"receipt_sha256": "d" * 64},
        ))
        self.parent_chain.start()

    def _current_task(self) -> VerifiedFoundationTask:
        agents = {
            "241": "7df640f9-973f-4c46-8302-df1256f60146",
            "242": "4e801c18-7048-4227-a5c7-515f51a5e5ba",
            "260": "f89e7039-e213-4e1e-9204-64f7ce69ac1c",
        }
        return VerifiedFoundationTask(
            task_id=f"real-{self.current_role}-task",
            issue_id="01a0389c-5fe5-7a27-a214-574cd66d9a2e",
            agent_id=agents[self.current_role],
            role=self.current_role,
            workspace_id="workspace",
            runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395",
            attempt=self.current_attempt,
            trigger_comment_id="controlled-trigger",
            work_dir=ROOT.parent,
        )

    def _issue_000(self) -> None:
        self.bootstrap.foundation_fixed_component_issuer().issue()
        self.current_role = "241"
        self.bootstrap.foundation_task_identity_issuer().issue()

    def _task_bootstrap(self, agent_id: str) -> RuntimeBootstrap:
        self.current_role = agent_id
        return RuntimeBootstrap(ROOT, self.envelope, environ={}, platform="darwin", home=self.home, runner=self.bootstrap.runner)

    def tearDown(self) -> None:
        self.parent_chain.stop()
        self.task_context.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _write_inputs(self) -> None:
        ref = lambda name: {"artifact_id": name, "version": 1, "content_hash": hashlib.sha256(name.encode()).hexdigest()}
        body = {
            "schema_version": "foundation-launch-inputs/v1", "root_binding_id": self.envelope["root_binding_id"],
            "issue_id": "EAS-114", "run_id": "accepted-run", "attempt": 1,
            "resolver_universe_hash": "b" * 64, "foundation_task_ref": ref("task"),
            "structure_closure_ref": ref("closure"), "database_snapshot_ref": ref("snapshot"),
            "generation_context_ref": ref("context"),
        }
        body["inputs_sha256"] = sha256(body)
        value = {**body, "attestation": hmac.new(b"k" * 32, canonical_bytes({key: value for key, value in body.items() if key != "inputs_sha256"}), hashlib.sha256).hexdigest()}
        # The entrypoint signs its body before adding inputs_sha256; mirror
        # that production contract rather than injecting a launcher bypass.
        signed = {key: value for key, value in value.items() if key not in {"inputs_sha256", "attestation"}}
        value["inputs_sha256"] = sha256(signed)
        value["attestation"] = hmac.new(b"k" * 32, canonical_bytes(signed), hashlib.sha256).hexdigest()
        path = self.root / "foundation-launch-inputs-v1.json"
        path.write_text(json.dumps(value, sort_keys=True)); path.chmod(0o600)

    def test_derives_frozen_bundle_claims_000_and_fixed_foundation_route(self) -> None:
        launcher = self.bootstrap.foundation_repo_launcher()
        self._issue_000()
        receipt = launcher.launch()
        self.assertEqual(receipt["skill_manifest_sha256"], "0de0623e2a3922e22dfe25703eb76e4ccd38bb96b6b010a78fcc6c15da59746f")
        self.assertEqual(receipt["runtime_graph_envelope"]["target_agent_id"], "241")
        self.assertEqual(receipt["route"], {"241": "242", "242": "260"})
        self.assertEqual(launcher.launch(), receipt)  # idempotent launch replay
        self.current_role = "242"
        self.bootstrap.foundation_task_identity_issuer().issue()
        validator = self._task_bootstrap("242")
        accepted_242 = validator.foundation_242_launcher().verify_downstream(receipt)
        self.assertEqual(accepted_242["status"], "accepted")
        self.current_role = "260"
        self.bootstrap.foundation_task_identity_issuer().issue()
        regression = self._task_bootstrap("260")
        accepted_260 = regression.foundation_260_launcher().verify_downstream(accepted_242)
        self.assertEqual(accepted_260["upstream_agent_id"], "242")

    def test_rejects_input_forgery_before_bundle_or_graph_state(self) -> None:
        path = self.root / "foundation-launch-inputs-v1.json"
        value = json.loads(path.read_text()); value["run_id"] = "forged"; path.write_text(json.dumps(value)); path.chmod(0o600)
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_REPO_LAUNCHER_INPUTS_INVALID|FOUNDATION_REPO_LAUNCHER_INPUTS_FORGED"):
            self.bootstrap.foundation_repo_launcher().launch()
        self.assertFalse((self.root / "foundation-v12-bundle").exists())
        self.assertFalse((self.root / "east-v5-full-runtime-v12-state.json").exists())

    def test_rejects_cross_task_and_cross_runtime_consumption(self) -> None:
        self._issue_000()
        receipt = self.bootstrap.foundation_repo_launcher().launch()
        # 260 cannot consume the 241 receipt directly, even with a valid
        # task-local identity and root key.
        self.current_role = "260"
        self.bootstrap.foundation_task_identity_issuer().issue()
        regression = self._task_bootstrap("260")
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_REPO_LAUNCHER_DOWNSTREAM_DRIFT"):
            regression.foundation_260_launcher().verify_downstream(receipt)

    def test_rejects_environment_root_override(self) -> None:
        overridden = RuntimeBootstrap(ROOT, self.envelope, environ={"V5_RUNTIME_ROOT": str(self.root)}, platform="darwin", home=self.home, runner=self.bootstrap.runner)
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_REPO_LAUNCHER_ENV_OVERRIDE_FORBIDDEN"):
            overridden.foundation_repo_launcher().launch()

    def test_identity_cannot_be_injected_into_launch(self) -> None:
        self._issue_000()
        with self.assertRaises(TypeError):
            self.bootstrap.foundation_repo_launcher().launch({})  # type: ignore[call-arg]

    def test_signed_task_identity_tamper_fails_before_241_launch(self) -> None:
        self._issue_000()
        identity_dir = self.root / "foundation-task-identities-v1"
        record = next(identity_dir.glob("241-*.json"))
        value = json.loads(record.read_text()); value["run_id"] = "forged-run"
        record.write_text(json.dumps(value)); record.chmod(0o600)
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_TASK_IDENTITY_DRIFT"):
            self.bootstrap.foundation_repo_launcher().launch()

    def test_identity_binds_real_task_coordinates_not_sealed_input_coordinates(self) -> None:
        self._issue_000()
        identity_dir = self.root / "foundation-task-identities-v1"
        record = json.loads(next(identity_dir.glob("241-*.json")).read_text())
        self.assertEqual(
            (record["issue_id"], record["task_id"], record["run_id"], record["attempt"]),
            ("01a0389c-5fe5-7a27-a214-574cd66d9a2e", "real-241-task", "real-241-task", 1),
        )
        self.assertEqual((record["input_issue_key"], record["input_run_id"], record["input_attempt"]), ("EAS-114", "accepted-run", 1))
        # Recomputing a self-hash does not make a forged task/issue coordinate
        # acceptable: the current authenticated task remains authoritative.
        record["issue_id"] = "forged-issue"
        body = {key: value for key, value in record.items() if key not in {"content_hash", "attestation"}}
        record["content_hash"] = sha256(body)
        key = (self.root / ".foundation-task-identity-v1.key").read_bytes()
        record["attestation"] = hmac.new(key, canonical_bytes({key: value for key, value in record.items() if key != "attestation"}), hashlib.sha256).hexdigest()
        path = next(identity_dir.glob("241-*.json")); path.write_text(json.dumps(record)); path.chmod(0o600)
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_TASK_IDENTITY_DRIFT"):
            self.bootstrap.foundation_repo_launcher().launch()

    def test_task_attempt_is_not_substituted_by_the_sealed_package_attempt(self) -> None:
        self.current_attempt = 2
        self._issue_000()
        identity = json.loads(next((self.root / "foundation-task-identities-v1").glob("241-*.json")).read_text())
        self.assertEqual((identity["attempt"], identity["input_attempt"]), (2, 1))
        self.assertEqual(self.bootstrap.foundation_repo_launcher().launch()["task_identity"]["attempt"], 2)

    def test_launcher_requires_preissued_production_record(self) -> None:
        with self.assertRaisesRegex(Exception, "FOUNDATION_TASK_IDENTITY_REQUIRED|FOUNDATION_000_ISSUER_KEY_UNSAFE|FOUNDATION_000_REGISTRY_REQUIRED"):
            self.bootstrap.foundation_repo_launcher().launch()

    def test_issuer_is_idempotent_and_tampered_registry_record_is_rejected(self) -> None:
        issuer = self.bootstrap.foundation_fixed_component_issuer()
        first = issuer.issue()
        self.assertEqual(first, issuer.issue())
        self.current_role = "241"
        self.bootstrap.foundation_task_identity_issuer().issue()
        self.assertNotEqual((self.root / ".foundation-fixed-component-000-v1.key").read_bytes(), (self.root / ".foundation-parent-chain-materializer-v1.key").read_bytes())
        record = self.root / "foundation-fixed-component-000-v1" / f"{first['issued_replay_key']}.json"
        tampered = json.loads(record.read_text()); tampered["git_head"] = "0" * 40
        record.write_text(json.dumps(tampered)); record.chmod(0o600)
        with self.assertRaisesRegex(Exception, "FOUNDATION_000_PRODUCTION_RECORD_DRIFT"):
            self.bootstrap.foundation_repo_launcher().launch()

    def test_issuer_recomputes_recovery_before_signing(self) -> None:
        self.parent_chain.stop()
        with self.assertRaisesRegex(Exception, "FOUNDATION_000_RECOVERY_INVALID"):
            self.bootstrap.foundation_fixed_component_issuer().issue()

    def test_unmocked_parent_chain_success_and_tamper_rejections(self) -> None:
        """Exercise the issuer's real parent-chain verifier on a sealed fixture."""
        self.parent_chain.stop()
        container = self.root / "foundation-parent-chain-container-v1"
        recovery_dir = self.root / "foundation-parent-chain-recovery-v1"
        closure = {"envelope": {"content_hash": "c" * 64}, "payload": {"foundation_task_ref": {"content_hash": "t" * 64}}}
        evidence = {"sanitized": True}
        (recovery_dir / "structure_closure.json").write_text(json.dumps(closure)); (recovery_dir / "structure_closure.json").chmod(0o600)
        (recovery_dir / "evidence.json").write_text(json.dumps(evidence)); (recovery_dir / "evidence.json").chmod(0o600)
        mapping = {"schema_version": "foundation-hierarchy-endpoint-mapping/v1", "intent_content_hash": "i" * 64, "field_mapping": {"child": "parent"}}
        mapping["content_hash"] = sha256(mapping)
        attachment = {"filename": "foundation_intent_package.json", "byte_sha256": hashlib.sha256(b"fixture-intent").hexdigest(), "semantic_sha256": "i" * 64}
        config = {"attachments": [attachment], "assets": [], "runtime_manifest_without_locators_sha256": sha256({}), "hierarchy_mapping_sha256": mapping["content_hash"]}
        config_hash = sha256(config)
        manifest = {"root_binding_id": self.envelope["root_binding_id"], "config_sha256": config_hash, "attachments": [attachment], "assets": [], "runtime_manifest_sha256": config["runtime_manifest_without_locators_sha256"], "hierarchy_mapping_sha256": mapping["content_hash"]}
        manifest["container_sha256"] = sha256(manifest)
        (container / "foundation-parent-chain-container-manifest.json").write_text(json.dumps(manifest)); (container / "foundation-parent-chain-container-manifest.json").chmod(0o600)
        (container / attachment["filename"]).write_bytes(b"fixture-intent"); (container / attachment["filename"]).chmod(0o600)
        (container / "constraint-assets-runtime-manifest.json").write_text(json.dumps({"schema_version": "fixture", "assets": []})); (container / "constraint-assets-runtime-manifest.json").chmod(0o600)
        (container / "foundation-hierarchy-endpoint-mapping.json").write_text(json.dumps(mapping)); (container / "foundation-hierarchy-endpoint-mapping.json").chmod(0o600)
        receipt = {"schema_version": "foundation_parent_chain_recovery_receipt/v1", "source_issue_id": "issue", "source_task_id": "task", "source_runtime_id": "runtime", "source_comment_id": entrypoint_module._RECOVERY_SOURCE_COMMENT, "closure_sha256": hashlib.sha256((recovery_dir / "structure_closure.json").read_bytes()).hexdigest(), "evidence_sha256": hashlib.sha256((recovery_dir / "evidence.json").read_bytes()).hexdigest(), "foundation_task_hash": "t" * 64, "closure_hash": "c" * 64, "ancestor_000_hash": "a" * 64, "root_binding_id": self.envelope["root_binding_id"], "registry_task_ref": {"content_hash": "t" * 64}, "rehydrated_from_accepted_projection": True}
        receipt["receipt_sha256"] = sha256(receipt)
        (recovery_dir / "recovery-receipt.json").write_text(json.dumps(receipt)); (recovery_dir / "recovery-receipt.json").chmod(0o600)
        materialization = {"schema_version": "foundation-parent-chain-materialization-receipt/v2", "root_binding_id": self.envelope["root_binding_id"], "assets": [], "container_sha256": manifest["container_sha256"], "runtime_manifest_sha256": manifest["runtime_manifest_sha256"], "hierarchy_mapping_sha256": mapping["content_hash"]}
        materialization["receipt_sha256"] = sha256(materialization)
        materialization["attestation"] = hmac.new(b"k" * 32, canonical_bytes(materialization), hashlib.sha256).hexdigest()
        path = self.root / "foundation-parent-chain-materialization-receipt-v2.json"; path.write_text(json.dumps(materialization)); path.chmod(0o600)
        constants = {"_TASK_HASH": "t" * 64, "_CLOSURE_HASH": "c" * 64, "_RESOLVER_HASH": "b" * 64, "_RECOVERY_SOURCE_ISSUE": "issue", "_RECOVERY_SOURCE_TASK": "task", "_RECOVERY_SOURCE_RUNTIME": "runtime", "_RECOVERY_CLOSURE_BYTES": receipt["closure_sha256"], "_RECOVERY_EVIDENCE_BYTES": receipt["evidence_sha256"], "_RECOVERY_ANCESTOR_000": "a" * 64}
        def clear_issuer_state() -> None:
            key, registry = self.root / ".foundation-fixed-component-000-v1.key", self.root / "foundation-fixed-component-000-v1"
            if key.exists():
                key.unlink()
            if registry.exists():
                shutil.rmtree(registry)

        with patch.multiple(issuer_module, **constants), patch.object(self.bootstrap, "_materializer_config", return_value=(config, config_hash)), patch.object(self.bootstrap, "_semantic_attachment", return_value=None), patch.object(self.bootstrap, "_without_locators", return_value={}), patch.object(self.bootstrap, "_hierarchy_mapping", return_value=mapping), patch.object(issuer_module.importlib, "import_module", return_value=SimpleNamespace(validate_structure_closure_package=lambda _value: None)):
            issuer = self.bootstrap.foundation_fixed_component_issuer()
            first = issuer.issue()
            self.assertEqual(first, issuer.issue())
            self.assertEqual(first, issuer.load())
            clear_issuer_state()
            broken_mapping = dict(mapping); broken_mapping["field_mapping"] = {"child": "attacker"}
            (container / "foundation-hierarchy-endpoint-mapping.json").write_text(json.dumps(broken_mapping)); (container / "foundation-hierarchy-endpoint-mapping.json").chmod(0o600)
            with self.assertRaisesRegex(Exception, "FOUNDATION_000_MATERIALIZER_DRIFT"):
                issuer.issue()
            self.assertFalse((self.root / ".foundation-fixed-component-000-v1.key").exists())
            self.assertFalse((self.root / "foundation-fixed-component-000-v1").exists())
            (container / "foundation-hierarchy-endpoint-mapping.json").write_text(json.dumps(mapping)); (container / "foundation-hierarchy-endpoint-mapping.json").chmod(0o600)
            receipt["source_comment_id"] = "attacker-result-comment"; receipt["receipt_sha256"] = sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
            (recovery_dir / "recovery-receipt.json").write_text(json.dumps(receipt)); (recovery_dir / "recovery-receipt.json").chmod(0o600)
            clear_issuer_state()
            with self.assertRaisesRegex(Exception, "FOUNDATION_000_RECOVERY_INVALID"):
                issuer.issue()
            self.assertFalse((self.root / ".foundation-fixed-component-000-v1.key").exists())
            self.assertFalse((self.root / "foundation-fixed-component-000-v1").exists())
            receipt["source_comment_id"] = entrypoint_module._RECOVERY_SOURCE_COMMENT
            receipt["source_task_id"] = "attacker"; receipt["receipt_sha256"] = sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
            (recovery_dir / "recovery-receipt.json").write_text(json.dumps(receipt)); (recovery_dir / "recovery-receipt.json").chmod(0o600)
            with self.assertRaisesRegex(Exception, "FOUNDATION_000_RECOVERY_INVALID"):
                issuer.issue()
            self.assertFalse((self.root / ".foundation-fixed-component-000-v1.key").exists())
            self.assertFalse((self.root / "foundation-fixed-component-000-v1").exists())


if __name__ == "__main__":
    unittest.main()
