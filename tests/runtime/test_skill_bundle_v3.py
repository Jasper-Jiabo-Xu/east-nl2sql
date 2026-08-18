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
SOURCE = ROOT / "skills" / "east-v5-runtime-bootstrap-v3"
PACKAGER = SOURCE / "scripts" / "pack_skill.py"

SKILL_ID = "11111111-2222-4333-8444-555555555555"
TARGETS = {
    "010": ("1d9153fc-4386-42e9-b2e5-56eb38f671af", "codex"),
    "110": ("67f9cf29-cd45-4ef3-8c87-963fd3ff5898", "codex"),
    "120": ("22533152-db59-4a1b-8d01-5f251c618e6b", "claude"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


class RuntimeSkillV3Tests(unittest.TestCase):
    def _archive(self, directory: Path) -> tuple[Path, dict[str, object], Path]:
        repo = directory / "repo"
        skill = repo / "skills" / "east-v5-runtime-bootstrap-v3"
        skill.parent.mkdir(parents=True)
        shutil.copytree(SOURCE, skill)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "v3"], check=True)
        head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        archive = directory / "v3.skill.zip"
        result = subprocess.run([sys.executable, str(PACKAGER), "--repo-root", str(repo), "--head", head, "--output", str(archive)], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        installed = directory / "installed"
        with zipfile.ZipFile(archive) as package:
            package.extractall(installed)
        return archive, receipt, installed

    def _envelope(self, installed: Path, receipt: dict[str, object], target: str = "010", input_ref: dict[str, object] | None = None) -> dict[str, object]:
        manifest = json.loads((installed / "manifest.json").read_text(encoding="utf-8"))
        context = {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "workspace", "project_id": "project", "daemon_id": "daemon"}
        binding = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        route = {"010": ("penalty_source_package", "010", "110"), "110": ("penalty_source_package", "010", "120"), "120": ("penalty_fact_package", "120", "complete")}[target]
        return {"schema_version": "task_input_envelope/v3", "adapter_version": "east-v5-runtime-adapter/v3", "issue_id": "EAS-70", "platform_parent_issue_id": "42f91f92-9453-4e7a-a38e-30d629ae07d6", "project_id": "3495da30-28d4-4cc6-b249-1e186ade6872", "run_id": "recovery-probe", "trace_id": "recovery-trace", "qa_id": "EAS70-RECOVERY", "attempt": 1, "target_agent_id": target, "target_agent_uuid": TARGETS[target][0], "root_binding_id": binding, "input_ref": input_ref, "input_receipt": None, "expected_output": {"artifact_type": route[0], "producer_id": route[1], "route_target": route[2]}, "recovery_of": {"old_run_id": "real-probe-010-110-120-v2", "old_issue_id": "0081927a-f1e7-42fe-a9e7-0a080857c082", "old_task_id": "c73f6821-0c29-4941-9214-12986df0d389", "decision_comment_id": "7397c95e-15b6-4b60-9d2d-5693d1e2659c"}, "execution_bootstrap": {"bootstrap_version": "east-v5-runtime-bootstrap/v3", "candidate_base_sha": "9bdf86b1c5f369a8ee05bb94ba2e47aeb04414de", "candidate_head_sha": manifest["source_candidate_head"], "controller_sha256": manifest["source_hashes"]["scripts/controller.py"], "adapter_sha256": manifest["source_hashes"]["east_v5/runtime/controller_core.py"], "runtime_context": context, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v3", "skill_version": "v3", "skill_id": SKILL_ID, "skill_manifest_sha256": _sha(installed / "manifest.json"), "archive_sha256": receipt["archive_sha256"]}}}

    def _claim(self, target: str, receipt: dict[str, object]) -> dict[str, object]:
        return {"agent_uuid": TARGETS[target][0], "runtime_uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "provider_id": TARGETS[target][1], "instructions_sha256": "a" * 64, "enabled_skill_ids": [SKILL_ID], "archive_sha256": receipt["archive_sha256"]}

    def _call(self, installed: Path, command: str, envelope: dict[str, object], claim: dict[str, object], directory: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        envelope_path, claim_path = directory / f"{command}-envelope.json", directory / f"{command}-claim.json"
        _write(envelope_path, envelope); _write(claim_path, claim)
        env = {"PATH": os.environ["PATH"]}
        return subprocess.run([sys.executable, "-I", str(installed / "scripts" / "controller.py"), command, "--envelope-file", str(envelope_path), "--claim-file", str(claim_path), *extra], cwd=directory, env=env, text=True, capture_output=True, check=False)

    def test_packager_is_deterministic_and_binds_final_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, receipt, installed = self._archive(directory)
            second = directory / "again.skill.zip"
            repo = directory / "repo"
            result = subprocess.run([sys.executable, str(PACKAGER), "--repo-root", str(repo), "--head", receipt["source_candidate_head"], "--output", str(second)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_sha(archive), _sha(second))
            manifest = json.loads((installed / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_candidate_head"], receipt["source_candidate_head"])

    def test_isolated_full_chain_and_idempotent_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, receipt, installed = self._archive(directory)
            runtime, launches = directory / "runtime", directory / "launches.json"
            first = self._call(installed, "business-preflight", self._envelope(installed, receipt), self._claim("010", receipt), directory)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["business_operation"], "not_started")
            initial = self._envelope(installed, receipt)
            result_010 = self._call(installed, "run-task", initial, self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(launches))
            self.assertEqual(result_010.returncode, 0, result_010.stderr)
            out_010 = json.loads(result_010.stdout)
            second_010 = self._call(installed, "run-task", initial, self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(launches))
            self.assertEqual(second_010.returncode, 0, second_010.stderr)
            self.assertEqual(json.loads(second_010.stdout), out_010)
            e110 = out_010["next_task"]["envelope"]
            out_110_result = self._call(installed, "run-task", e110, self._claim("110", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000110", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(launches))
            self.assertEqual(out_110_result.returncode, 0, out_110_result.stderr)
            out_110 = json.loads(out_110_result.stdout)
            e120 = out_110["next_task"]["envelope"]
            out_120_result = self._call(installed, "run-task", e120, self._claim("120", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000120", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(launches))
            self.assertEqual(out_120_result.returncode, 0, out_120_result.stderr)
            out_120 = json.loads(out_120_result.stdout)
            self.assertIsNone(out_120["next_task"])
            self.assertEqual([item["envelope"]["target_agent_id"] for item in json.loads(launches.read_text(encoding="utf-8"))["launches"]], ["110", "120"])
            self.assertTrue(archive.is_file())

    def test_registry_conflict_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            runtime = directory / "runtime"
            initial = self._envelope(installed, receipt)
            first = self._call(installed, "run-task", initial, self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(directory / "launches.json"))
            self.assertEqual(first.returncode, 0, first.stderr)
            registry_path = runtime / "east-v5-runtime" / "issues" / "EAS-70" / "recovery-probe" / "1" / "artifact-registry.json"
            state = json.loads(registry_path.read_text(encoding="utf-8"))
            state["records"][0]["payload"]["conflict"] = True
            _write(registry_path, state)
            conflict = self._call(installed, "run-task", initial, self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000011", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(directory / "launches.json"))
            self.assertEqual(conflict.returncode, 2); self.assertIn("RUNTIME_REGISTRY_CONFLICT", conflict.stderr)

    def test_rejects_missing_dependency_and_binding_or_head_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            envelope = self._envelope(installed, receipt)
            envelope["execution_bootstrap"]["candidate_head_sha"] = "f" * 40
            bad_head = self._call(installed, "business-preflight", envelope, self._claim("010", receipt), directory)
            self.assertEqual(bad_head.returncode, 2); self.assertIn("RUNTIME_SKILL_CANDIDATE_HEAD_DRIFT", bad_head.stderr)
            envelope = self._envelope(installed, receipt)
            claim = self._claim("010", receipt); claim["enabled_skill_ids"] = []
            bad_id = self._call(installed, "business-preflight", envelope, claim, directory)
            self.assertEqual(bad_id.returncode, 2); self.assertIn("RUNTIME_SKILL_CLAIM_DRIFT", bad_id.stderr)
            envelope = self._envelope(installed, receipt)
            envelope["execution_bootstrap"]["skill_bundle"]["skill_manifest_sha256"] = "0" * 64
            bad_hash = self._call(installed, "business-preflight", envelope, self._claim("010", receipt), directory)
            self.assertEqual(bad_hash.returncode, 2); self.assertIn("RUNTIME_SKILL_BUNDLE_DRIFT", bad_hash.stderr)
            (installed / "east_v5" / "governance.py").unlink()
            missing = self._call(installed, "business-preflight", envelope, self._claim("010", receipt), directory)
            self.assertEqual(missing.returncode, 2); self.assertIn("RUNTIME_SKILL_DEPENDENCY_MISSING", missing.stderr)

    def test_rejects_out_of_sequence_input_and_launcher_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            runtime = directory / "runtime"
            bad_input = self._envelope(installed, receipt, "110", {"artifact_id": "missing", "version": 1, "content_hash": "a" * 64})
            result = self._call(installed, "run-task", bad_input, self._claim("110", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000110", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
            self.assertEqual(result.returncode, 2); self.assertIn("RUNTIME_RECEIPT_SEQUENCE_INVALID", result.stderr)
            launch_fail = self._call(installed, "run-task", self._envelope(installed, receipt), self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-fail")
            self.assertEqual(launch_fail.returncode, 2); self.assertIn("RUNTIME_LAUNCH_CREATE_FAILED", launch_fail.stderr)

    def test_launcher_failure_leaves_only_staged_nonconsumer_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            runtime = directory / "runtime"
            failed = self._call(installed, "run-task", self._envelope(installed, receipt), self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-fail")
            self.assertEqual(failed.returncode, 2)
            base = runtime / "east-v5-runtime" / "issues" / "EAS-70" / "recovery-probe" / "1"
            self.assertFalse((base / "artifact-registry.json").exists())
            self.assertFalse((base / "execution-journal.json").exists())
            entries = json.loads((base / "launch-outbox.json").read_text(encoding="utf-8"))["entries"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(next(iter(entries.values()))["state"], "intent")


if __name__ == "__main__":
    unittest.main()
