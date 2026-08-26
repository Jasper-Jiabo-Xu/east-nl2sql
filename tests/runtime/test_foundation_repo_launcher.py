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

from east_v5.governance import canonical_bytes, sha256
from east_v5.runtime.bootstrap import RuntimeBootstrap, root_binding_id
from east_v5.runtime.foundation_repo_launcher import FoundationRepoLauncher, FoundationRepoLauncherError


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
        self._write_inputs()
        self.identity = {
            "workspace_id": "workspace", "project_id": "project", "issue_id": "EAS-114",
            "task_id": "task-241", "agent_uuid": "7df640f9-973f-4c46-8302-df1256f60146",
            "runtime_id": "0e5e9dd9-5135-4937-bb03-92b77adb8395", "run_id": "accepted-run", "attempt": 1,
        }

    def tearDown(self) -> None:
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
        receipt = launcher.launch(self.identity)
        self.assertEqual(receipt["skill_manifest_sha256"], "0de0623e2a3922e22dfe25703eb76e4ccd38bb96b6b010a78fcc6c15da59746f")
        self.assertEqual(receipt["runtime_graph_envelope"]["target_agent_id"], "241")
        self.assertEqual(receipt["route"], {"241": "242", "242": "260"})
        self.assertEqual(launcher.launch(self.identity), receipt)  # idempotent launch replay
        self.assertEqual(launcher.verify_downstream(receipt, target_agent_id="242", task_id="task-241", runtime_id=self.identity["runtime_id"])["status"], "accepted")
        self.assertEqual(launcher.verify_downstream(receipt, target_agent_id="260", task_id="task-241", runtime_id=self.identity["runtime_id"])["target_agent_id"], "260")

    def test_rejects_input_forgery_before_bundle_or_graph_state(self) -> None:
        path = self.root / "foundation-launch-inputs-v1.json"
        value = json.loads(path.read_text()); value["run_id"] = "forged"; path.write_text(json.dumps(value)); path.chmod(0o600)
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_REPO_LAUNCHER_INPUTS_INVALID|FOUNDATION_REPO_LAUNCHER_INPUTS_FORGED"):
            self.bootstrap.foundation_repo_launcher().launch(self.identity)
        self.assertFalse((self.root / "foundation-v12-bundle").exists())
        self.assertFalse((self.root / "east-v5-full-runtime-v12-state.json").exists())

    def test_rejects_cross_task_and_cross_runtime_consumption(self) -> None:
        receipt = self.bootstrap.foundation_repo_launcher().launch(self.identity)
        launcher = self.bootstrap.foundation_repo_launcher()
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_REPO_LAUNCHER_DOWNSTREAM_DRIFT"):
            launcher.verify_downstream(receipt, target_agent_id="242", task_id="other", runtime_id=self.identity["runtime_id"])
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_REPO_LAUNCHER_DOWNSTREAM_DRIFT"):
            launcher.verify_downstream(receipt, target_agent_id="242", task_id="task-241", runtime_id="other-runtime")

    def test_rejects_environment_root_override(self) -> None:
        overridden = RuntimeBootstrap(ROOT, self.envelope, environ={"V5_RUNTIME_ROOT": str(self.root)}, platform="darwin", home=self.home, runner=self.bootstrap.runner)
        with self.assertRaisesRegex(FoundationRepoLauncherError, "FOUNDATION_REPO_LAUNCHER_ENV_OVERRIDE_FORBIDDEN"):
            overridden.foundation_repo_launcher().launch(self.identity)


if __name__ == "__main__":
    unittest.main()
