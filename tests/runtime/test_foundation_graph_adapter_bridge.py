from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash
from east_v5.runtime import (
    FoundationGraphAdapterBridge,
    FoundationGraphAdapterBridgeError,
    FoundationRuntimeAssembly,
    RuntimeAdapter,
    root_binding_id,
)
from east_v5.runtime.bootstrap import BootstrapEvidence


SKILL = ROOT / "skills/east-v5-runtime-bootstrap-v12"
PACK = SKILL / "scripts/pack_skill.py"
SKILL_ID = "f42ba062-5a2d-430f-812e-c147322cc79e"
AGENTS = ("010", "110", "120", "130", "140", "150", "160", "170", "180", "210", "220", "230", "241", "242", "251", "252", "260")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write(path: Path, value: object) -> None:
    path.write_text(canonical(value), encoding="utf-8")


class FoundationGraphAdapterBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = tempfile.TemporaryDirectory(dir=ROOT.parent)
        self.base = Path(self.raw.name)
        self.candidate = self.base / "candidate"
        shutil.copytree(ROOT, self.candidate, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        subprocess.run(["git", "init", "-q", str(self.candidate)], check=True)
        subprocess.run(["git", "-C", str(self.candidate), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.candidate), "-c", "user.email=bridge@example.invalid", "-c", "user.name=bridge", "commit", "-qm", "bridge"], check=True)
        self.head = subprocess.check_output(["git", "-C", str(self.candidate), "rev-parse", "HEAD"], text=True).strip()
        archive = self.base / "v12.skill.zip"
        packed = subprocess.run([sys.executable, str(self.candidate / "skills/east-v5-runtime-bootstrap-v12/scripts/pack_skill.py"), "--repo-root", str(self.candidate), "--head", self.head, "--output", str(archive)], capture_output=True, text=True)
        self.assertEqual(packed.returncode, 0, packed.stderr)
        self.installed = self.base / "installed"
        with zipfile.ZipFile(archive) as package:
            package.extractall(self.installed)
        skill_md = self.installed / "SKILL.md"
        skill_md.write_bytes(skill_md.read_bytes().replace(
            b"description: Execute the EAST V5 v12 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight.\n",
            b'description: "Execute the EAST V5 v12 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight."\n',
        ).replace(b"---\n\n# EAST V5 Runtime Bootstrap V12", b"---\n\n\n# EAST V5 Runtime Bootstrap V12"))
        self.runtime = self.base / "daemon"; self.runtime.mkdir(mode=0o700); os.chmod(self.runtime, 0o700)
        context = {"daemon": "bridge", "workspace": "test"}
        self.v12_binding = hashlib.sha256(canonical(context).encode()).hexdigest()
        marker = {"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": self.v12_binding}
        write(self.runtime / "daemon-root-binding-v12.json", marker); os.chmod(self.runtime / "daemon-root-binding-v12.json", 0o600)
        self.roots = {"repo_root": str(self.candidate / "repo"), "runtime_root": str(self.runtime), "reference_root": str(self.base / "reference"), "reference_read_only": True}
        self.manifest = json.loads((self.installed / "manifest.json").read_text())
        self.graph = json.loads((self.installed / "config/full-runtime-graph.json").read_text())
        authority = json.loads((self.installed / "config/authority-matrix-v2.json").read_text())
        resolver = json.loads((self.installed / "config/skill-identity-resolver-v1.json").read_text())
        self.approved_skills = {row["agent_id"]: [resolver["workspace_skill_ids"][name] for name in row["approved_skill_bindings"]] for row in authority["rows"]}
        self._preflight()

    def tearDown(self) -> None:
        self.raw.cleanup()

    def _claim(self, target: str) -> dict[str, object]:
        return {"agent_uuid": self.graph["real_agents"][target]["uuid"], "runtime_id": self.graph["real_agents"][target]["runtime_id"], "instructions_sha256": self.manifest["instruction_hashes"][target], "enabled_skill_ids": sorted([*self.approved_skills[target], SKILL_ID])}

    def _preflight(self) -> None:
        config_hash = hashlib.sha256((self.installed / "config/full-runtime-graph.json").read_bytes()).hexdigest()
        claims = {"schema_version": "east-v5-full-claims/v12", "skill_id": SKILL_ID, "skill_manifest_sha256": hashlib.sha256((self.installed / "manifest.json").read_bytes()).hexdigest(), "config_sha256": config_hash, "agents": {target: self._claim(target) for target in AGENTS}}
        self.claims = claims
        component = {"schema_version": "east-v5-fixed-component-receipt/v1", "component_id": "000", "root_binding_id": self.v12_binding, "config_sha256": config_hash}
        component["receipt_sha256"] = hashlib.sha256(canonical(component).encode()).hexdigest()
        self.component = component
        claims_file, component_file = self.base / "claims.json", self.base / "component.json"; write(claims_file, claims); write(component_file, component)
        result = self._controller("full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.token = json.loads(result.stdout)["preflight_token"]

    def _controller(self, command: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-I", str(self.installed / "scripts/controller.py"), command, "--runtime-root", str(self.runtime), *args], cwd=self.base, capture_output=True, text=True)

    def _graph_envelope(self, target: str, inputs: list[str]) -> dict[str, object]:
        return {"schema_version": "runtime_graph_envelope/v12", "run_id": "bridge-run", "mode": "foundation", "attempt": 1, "target_agent_id": target, "target_agent_uuid": self.graph["real_agents"][target]["uuid"], "root_binding_id": self.v12_binding, "preflight_token": self.token, "input_receipt_hashes": inputs, "outcome": "success"}

    def _run(self, envelope: dict[str, object], task_id: str) -> dict[str, object]:
        env, claim = self.base / f"{task_id}.env.json", self.base / f"{task_id}.claim.json"; write(env, envelope); write(claim, self._claim(str(envelope["target_agent_id"])))
        result = self._controller("run-task", "--envelope-file", str(env), "--claim-file", str(claim), "--task-id", task_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def _package(self, artifact_id: str, artifact_type: str, producer: str, *, run: str = "bridge-run") -> dict[str, object]:
        envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": run, "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": producer, "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "foundation", "created_at": "2026-08-26T00:00:00+00:00", "trace_id": "bridge-trace", "storage_locator": None}
        payload = {"sanitized": True, "producer": producer}
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}

    def _adapter_220(self) -> RuntimeAdapter:
        context = {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "bridge", "project_id": "test", "daemon_id": "adapter"}
        binding = root_binding_id(context)
        declaration = {"bootstrap_version": "east-v5-runtime-bootstrap/v1", "candidate_base_sha": self.head, "candidate_head_sha": self.head, "adapter_sha256": hashlib.sha256((self.candidate / "src/east_v5/runtime/adapter.py").read_bytes()).hexdigest(), "bootstrap_sha256": hashlib.sha256((self.candidate / "src/east_v5/runtime/bootstrap.py").read_bytes()).hexdigest(), "runner_sha256": hashlib.sha256((self.candidate / "scripts/runtime_bootstrap.py").read_bytes()).hexdigest(), "runtime_context": context, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v1", "skill_version": "v1", "skill_manifest_sha256": "f" * 64}}
        seed = self._package("bridge-seed", "foundation_task_package", "210")
        ArtifactRegistry(self.candidate, self.roots, "EAS-115", "bridge-run", 1).register(seed["envelope"], seed["payload"])
        envelope = {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": "EAS-115", "run_id": "bridge-run", "trace_id": "bridge-trace", "qa_id": None, "attempt": 1, "target_agent_id": "220", "target_agent_uuid": self.graph["real_agents"]["220"]["uuid"], "root_binding_id": binding, "input_ref": artifact_ref(seed["envelope"]), "expected_output": {"artifact_type": "structure_closure", "producer_id": "220", "route_target": "241"}, "execution_bootstrap": declaration}
        evidence = BootstrapEvidence(candidate_head_sha=self.head, adapter_sha256=declaration["adapter_sha256"], bootstrap_sha256=declaration["bootstrap_sha256"], runner_sha256=declaration["runner_sha256"], root_binding_id=binding, runner_entrypoint="scripts/runtime_bootstrap.py")
        return RuntimeAdapter(self.candidate, self.roots, envelope, preflight=evidence)

    def test_bridge_derives_241_then_242_and_a_260_stub_from_real_receipts(self) -> None:
        r010 = self._run(self._graph_envelope("010", []), "v12-010")
        r210 = self._run(r010["next_tasks"][0], "v12-210")
        r220 = self._run(r210["next_tasks"][0], "v12-220")
        package220 = self._package("bridge-220", "structure_closure", "220")
        receipt220 = self._adapter_220().register_output(package220, task_id="real-220", runtime_id="220-runtime")["receipt"]
        bridge = FoundationGraphAdapterBridge(self.candidate, self.roots, self.installed)
        registry = ArtifactRegistry(self.candidate, self.roots, "EAS-115", "bridge-run", 1)
        snapshot = self._package("bridge-snapshot", "database_read_snapshot", "EAS-19")
        context = self._package("bridge-context", "foundation_generation_context", "EAS-19")
        registry.register(snapshot["envelope"], snapshot["payload"]); registry.register(context["envelope"], context["payload"])
        gate = bridge.runtime_input_gate(issue_id="EAS-115", run_id="bridge-run", attempt=1, v12_claims=self.claims, component_receipt=self.component, snapshot_ref=artifact_ref(snapshot["envelope"]), generation_context_ref=artifact_ref(context["envelope"]))
        adapter241 = bridge.adapter_for("241", task_id="bridge-241", v12_envelope=r220["next_tasks"][0], v12_claims=self.claims, v12_220_receipt=r220["receipt"], task_receipt=receipt220, input_gate=gate)
        assembly = FoundationRuntimeAssembly.from_runtime_adapter(adapter241, task_id="bridge-241", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
        self.assertEqual(assembly.invocation_service.target_agent_id, "241")
        package241 = self._package("bridge-241", "bound_data", "241")
        registered241 = adapter241.register_output(package241, task_id="real-241", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
        r241 = self._run(r220["next_tasks"][0], "v12-241")
        adapter242 = bridge.adapter_for("242", task_id="bridge-242", v12_envelope=r241["next_tasks"][0], v12_claims=self.claims, v12_220_receipt=r220["receipt"], task_receipt=registered241["receipt"], input_gate=gate)
        verifier = FoundationRuntimeAssembly.from_runtime_adapter(adapter242, task_id="bridge-242", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
        self.assertEqual(verifier.invocation_service.target_agent_id, "242")
        package242 = self._package("bridge-242", "verified_bound_data", "242")
        registered242 = adapter242.register_output(package242, task_id="real-242", runtime_id="0e5e9dd9-5135-4937-bb03-92b77adb8395")
        stub = bridge.consume_260_stub(registered242["receipt"])
        self.assertEqual(stub["input_ref"], artifact_ref(package242["envelope"]))
        self.assertTrue((self.runtime / "foundation-graph-adapter-bridge-v1" / "bridge.key").is_file())

    def test_bridge_rejects_event_mode_and_unknown_receipts(self) -> None:
        r010 = self._run(self._graph_envelope("010", []), "reject-010")
        r210 = self._run(r010["next_tasks"][0], "reject-210")
        r220 = self._run(r210["next_tasks"][0], "reject-220")
        bridge = FoundationGraphAdapterBridge(self.candidate, self.roots, self.installed)
        event = dict(r220["next_tasks"][0]); event["mode"] = "event"
        with self.assertRaisesRegex(FoundationGraphAdapterBridgeError, "FOUNDATION_BRIDGE_V12_TASK_INVALID"):
            bridge.adapter_for("241", task_id="reject", v12_envelope=event, v12_claims=self.claims, v12_220_receipt=r220["receipt"], task_receipt={}, input_gate={})
        with self.assertRaisesRegex(FoundationGraphAdapterBridgeError, "FOUNDATION_BRIDGE_INPUT_GATE_INVALID"):
            bridge.adapter_for("241", task_id="reject", v12_envelope=r220["next_tasks"][0], v12_claims=self.claims, v12_220_receipt=r220["receipt"], task_receipt={}, input_gate={})


if __name__ == "__main__":
    unittest.main()
