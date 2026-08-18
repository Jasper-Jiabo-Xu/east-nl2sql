from __future__ import annotations

import hashlib
import json
import importlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref
from east_v5.runtime import RuntimeAdapter, RuntimeAdapterError, RuntimeBootstrap, RuntimeBootstrapError, root_binding_id
from east_v5.runtime.bootstrap import BootstrapEvidence

bootstrap_mod = importlib.import_module("east_v5.runtime.bootstrap")

FormalReleaseCommitter = importlib.import_module("east_v5.agents.010.committer").FormalReleaseCommitter


def _bootstrap() -> dict[str, object]:
    context = {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "workspace", "project_id": "project", "daemon_id": "daemon"}
    return {"bootstrap_version": "east-v5-runtime-bootstrap/v1", "candidate_base_sha": "a" * 40, "candidate_head_sha": "b" * 40, "adapter_sha256": "c" * 64, "bootstrap_sha256": "d" * 64, "runner_sha256": "e" * 64, "runtime_context": context, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v1", "skill_version": "v1", "skill_manifest_sha256": "f" * 64}}


def _envelope(package: dict[str, object] | None = None) -> dict[str, object]:
    bootstrap = _bootstrap()
    return {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": "EAS-70", "run_id": "probe-run", "trace_id": "probe-trace", "qa_id": package["envelope"]["qa_id"] if package else "QA", "attempt": 2, "target_agent_id": "010", "target_agent_uuid": "010-uuid", "root_binding_id": root_binding_id(bootstrap["runtime_context"]), "input_ref": None, "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "110"}, "execution_bootstrap": bootstrap}


def _evidence(envelope: dict[str, object]) -> BootstrapEvidence:
    declaration = envelope["execution_bootstrap"]
    return BootstrapEvidence(candidate_head_sha=declaration["candidate_head_sha"], adapter_sha256=declaration["adapter_sha256"], bootstrap_sha256=declaration["bootstrap_sha256"], runner_sha256=declaration["runner_sha256"], root_binding_id=envelope["root_binding_id"], runner_entrypoint="scripts/runtime_bootstrap.py")


class RuntimeAdapterTests(unittest.TestCase):
    def test_registers_then_reads_back_before_dispatch(self) -> None:
        payload = json.loads((ROOT / "fixtures" / "penalty" / "matched.json").read_text(encoding="utf-8"))
        package = FormalReleaseCommitter(ROOT).build_penalty_source_package(payload, run_id="probe-run", trace_id="probe-trace", created_at="2026-08-18T00:00:00+00:00", attempt_no=2)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            roots = {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}
            envelope = _envelope(package)
            result = RuntimeAdapter(ROOT, roots, envelope, preflight=_evidence(envelope)).register_output(package, task_id="task-010", runtime_id="runtime-010")
            self.assertEqual(result["next_dispatch"]["target"], "110")
            self.assertEqual(result["receipt"]["output_ref"], artifact_ref(package["envelope"]))
            self.assertIsNone(result["receipt"]["input_ref"])
            consumer_envelope = {**envelope, "target_agent_id": "110", "target_agent_uuid": "110-uuid", "input_ref": artifact_ref(package["envelope"]), "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "120"}}
            consumed = RuntimeAdapter(ROOT, roots, consumer_envelope, preflight=_evidence(consumer_envelope)).consume_input(task_id="task-110", runtime_id="runtime-110")
            self.assertEqual(consumed["next_dispatch"]["target"], "120")
            self.assertEqual(consumed["receipt"]["output_ref"], artifact_ref(package["envelope"]))

            calls = []
            def runner(command, **_kwargs):
                calls.append(command)
                if command[2] == "create": return SimpleNamespace(stdout='{"id":"next-issue"}')
                return SimpleNamespace(stdout='[{"id":"next-task","issue_id":"next-issue","agent_id":"120-uuid"}]')
            launched = RuntimeAdapter(ROOT, roots, consumer_envelope, preflight=_evidence(consumer_envelope)).launch_next_task(receipt=consumed["receipt"], platform_parent_issue_id="parent-issue", project_id="project", target_agent_id="120", target_agent_uuid="120-uuid", expected_output={"artifact_type": "penalty_fact_package", "producer_id": "120", "route_target": "130"}, runner=runner)
            self.assertEqual((launched["issue_id"], launched["task_id"]), ("next-issue", "next-task"))
            self.assertEqual([call[2] for call in calls], ["create", "runs"])

    def test_rejects_route_before_registry_write(self) -> None:
        envelope = _envelope()
        envelope["input_ref"] = {"artifact_id": "source", "version": 1, "content_hash": "a" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            adapter = RuntimeAdapter(ROOT, roots, envelope, preflight=_evidence(envelope))
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_OUTPUT_TRANSPORT_INVALID"):
                adapter.register_output({}, task_id="task", runtime_id="runtime")

    def test_missing_bootstrap_rejects_before_registry_creation(self) -> None:
        envelope = _envelope()
        del envelope["execution_bootstrap"]
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_BOOTSTRAP_MISSING"):
                RuntimeAdapter(ROOT, roots, envelope)
            self.assertFalse((Path(tmp) / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-70" / "probe-run" / "2" / "artifact-registry.json").exists())

    def test_missing_skill_bundle_rejects_before_registry_creation(self) -> None:
        envelope = _envelope()
        del envelope["execution_bootstrap"]["skill_bundle"]
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_SKILL_BUNDLE_MISSING"):
                RuntimeAdapter(ROOT, roots, envelope)
            self.assertFalse((Path(tmp) / "runtime").exists())

    def test_bootstrap_verifies_checkout_hashes_and_root_binding_before_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "candidate"
            adapter_path = checkout / "src" / "east_v5" / "runtime" / "adapter.py"
            bootstrap_path = checkout / "src" / "east_v5" / "runtime" / "bootstrap.py"
            runner_path = checkout / "scripts" / "runtime_bootstrap.py"
            adapter_path.parent.mkdir(parents=True)
            adapter_path.write_text("adapter-v1", encoding="utf-8")
            bootstrap_path.write_text("bootstrap-v1", encoding="utf-8")
            runner_path.parent.mkdir(parents=True)
            runner_path.write_text("print('runner-v1')", encoding="utf-8")
            import subprocess
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "candidate"], check=True)
            head = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            bootstrap = _bootstrap()
            bootstrap["candidate_head_sha"] = head
            bootstrap["adapter_sha256"] = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
            bootstrap["bootstrap_sha256"] = hashlib.sha256(bootstrap_path.read_bytes()).hexdigest()
            bootstrap["runner_sha256"] = hashlib.sha256(runner_path.read_bytes()).hexdigest()
            envelope = _envelope()
            envelope["execution_bootstrap"] = bootstrap
            envelope["root_binding_id"] = root_binding_id(bootstrap["runtime_context"])
            with patch.object(bootstrap_mod.tempfile, "gettempdir", return_value="/not-the-test-directory"), patch.object(bootstrap_mod, "_contains_symlink", return_value=False):
                evidence = RuntimeBootstrap(checkout, envelope, environ={"V5_RUNTIME_ROOT": str(Path(tmp) / "runtime")}).preflight()
            self.assertEqual(evidence.candidate_head_sha, head)
            self.assertEqual(evidence.root_binding_id, envelope["root_binding_id"])

    def test_bootstrap_code_hash_drift_has_zero_root_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "candidate"
            adapter_path = checkout / "src" / "east_v5" / "runtime" / "adapter.py"
            bootstrap_path = checkout / "src" / "east_v5" / "runtime" / "bootstrap.py"
            runner_path = checkout / "scripts" / "runtime_bootstrap.py"
            adapter_path.parent.mkdir(parents=True); runner_path.parent.mkdir(parents=True)
            adapter_path.write_text("adapter-v1", encoding="utf-8"); bootstrap_path.write_text("bootstrap-v1", encoding="utf-8"); runner_path.write_text("pass", encoding="utf-8")
            import subprocess
            subprocess.run(["git", "init", "-q", str(checkout)], check=True); subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "candidate"], check=True)
            bootstrap = _bootstrap()
            bootstrap["candidate_head_sha"] = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            bootstrap["bootstrap_sha256"] = hashlib.sha256(bootstrap_path.read_bytes()).hexdigest()
            bootstrap["runner_sha256"] = hashlib.sha256(runner_path.read_bytes()).hexdigest()
            envelope = _envelope(); envelope["execution_bootstrap"] = bootstrap; envelope["root_binding_id"] = root_binding_id(bootstrap["runtime_context"])
            root = Path(tmp) / "runtime"
            with patch.object(bootstrap_mod.tempfile, "gettempdir", return_value="/not-the-test-directory"), patch.object(bootstrap_mod, "_contains_symlink", return_value=False):
                with self.assertRaisesRegex(RuntimeBootstrapError, "RUNTIME_BOOTSTRAP_CODE_HASH_DRIFT"):
                    RuntimeBootstrap(checkout, envelope, environ={"V5_RUNTIME_ROOT": str(root)}).preflight()
            self.assertFalse(root.exists())

    def test_registry_unresolvable_has_no_next_dispatch(self) -> None:
        envelope = _envelope()
        envelope.update({"target_agent_id": "110", "target_agent_uuid": "110-uuid", "input_ref": {"artifact_id": "source", "version": 1, "content_hash": "a" * 64}, "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "120"}})
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_INPUT_RESOLUTION_REJECTED"):
                RuntimeAdapter(ROOT, roots, envelope, preflight=_evidence(envelope)).consume_input(task_id="task-110", runtime_id="runtime-110")
            self.assertFalse((Path(tmp) / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-70" / "probe-run" / "2" / "artifact-registry.json").exists())

    def test_unverified_bootstrap_has_zero_registry_write(self) -> None:
        envelope = _envelope()
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_BOOTSTRAP_UNVERIFIED"):
                RuntimeAdapter(ROOT, roots, envelope)
            self.assertFalse((Path(tmp) / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
