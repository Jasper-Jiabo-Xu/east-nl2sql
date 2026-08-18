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
SOURCE = ROOT / "skills" / "east-v5-runtime-bootstrap-v8"
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


def _materialize(path: Path) -> None:
    value = path.read_bytes().replace(
        b"description: Execute the EAST V5 v8 instruction-bound controller for 010, 110, and 120: accepted preflight must be followed by same-task committed run-task.\n",
        b'description: "Execute the EAST V5 v8 instruction-bound controller for 010, 110, and 120: accepted preflight must be followed by same-task committed run-task."\n',
        1,
    ).replace(b"---\n\n# EAST V5 Runtime Bootstrap V8", b"---\n\n\n# EAST V5 Runtime Bootstrap V8", 1)
    path.write_bytes(value)


class RuntimeSkillV8Tests(unittest.TestCase):
    def _archive(self, directory: Path) -> tuple[Path, dict[str, object], Path]:
        repo = directory / "repo"
        skill = repo / "skills" / "east-v5-runtime-bootstrap-v8"
        skill.parent.mkdir(parents=True)
        shutil.copytree(SOURCE, skill)
        for target in TARGETS:
            prompt = repo / "agents" / target / "prompt.md"
            prompt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "agents" / target / "prompt.md", prompt)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "v8"], check=True)
        head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        archive = directory / "v8.skill.zip"
        result = subprocess.run([sys.executable, str(PACKAGER), "--repo-root", str(repo), "--head", head, "--output", str(archive)], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        installed = directory / "installed"
        with zipfile.ZipFile(archive) as package:
            package.extractall(installed)
        _materialize(installed / "SKILL.md")
        return archive, receipt, installed

    def _envelope(self, installed: Path, receipt: dict[str, object], target: str = "010", input_ref: dict[str, object] | None = None) -> dict[str, object]:
        manifest = json.loads((installed / "manifest.json").read_text(encoding="utf-8"))
        context = {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "workspace", "project_id": "project", "daemon_id": "daemon"}
        binding = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        route = {"010": ("penalty_source_package", "010", "110"), "110": ("penalty_source_package", "010", "120"), "120": ("penalty_fact_package", "120", "complete")}[target]
        return {"schema_version": "task_input_envelope/v8", "adapter_version": "east-v5-runtime-adapter/v8", "issue_id": "EAS-70", "platform_parent_issue_id": "42f91f92-9453-4e7a-a38e-30d629ae07d6", "project_id": "3495da30-28d4-4cc6-b249-1e186ade6872", "run_id": "recovery-probe", "trace_id": "recovery-trace", "qa_id": "EAS70-RECOVERY", "attempt": 3, "target_agent_id": target, "target_agent_uuid": TARGETS[target][0], "root_binding_id": binding, "input_ref": input_ref, "input_receipt": None, "expected_output": {"artifact_type": route[0], "producer_id": route[1], "route_target": route[2]}, "recovery_of": {"old_run_id": "real-probe-010-110-120-v2", "old_issue_id": "0081927a-f1e7-42fe-a9e7-0a080857c082", "old_task_id": "c73f6821-0c29-4941-9214-12986df0d389", "decision_comment_id": "7397c95e-15b6-4b60-9d2d-5693d1e2659c", "run_id": "recovery-probe", "trace_id": "recovery-trace", "qa_id": "EAS70-RECOVERY"}, "supersedes": ["124a3e9d-fff7-4492-8c7b-9991d018795d", "a7d894d2-1c82-4bf2-a66a-5035404a6b4c", "f2a38505-ee38-45df-ab19-79987355a299"], "execution_bootstrap": {"bootstrap_version": "east-v5-runtime-bootstrap/v8", "candidate_base_sha": "9bdf86b1c5f369a8ee05bb94ba2e47aeb04414de", "candidate_head_sha": manifest["source_candidate_head"], "controller_sha256": manifest["source_hashes"]["scripts/controller.py"], "adapter_sha256": manifest["source_hashes"]["east_v5/runtime/controller_core.py"], "runtime_context": context, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v8", "skill_version": "v8", "skill_id": SKILL_ID, "skill_manifest_sha256": _sha(installed / "manifest.json"), "archive_sha256": receipt["archive_sha256"]}}}

    def _claim(self, target: str, receipt: dict[str, object]) -> dict[str, object]:
        return {"agent_uuid": TARGETS[target][0], "runtime_uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "provider_id": TARGETS[target][1], "instructions_sha256": _sha(ROOT / "agents" / target / "prompt.md"), "enabled_skill_ids": [SKILL_ID], "archive_sha256": receipt["archive_sha256"]}

    def _control(self, installed: Path, receipt: dict[str, object], target: str = "010", key_seed: bytes = b"v8-real-control-preflight") -> dict[str, object]:
        business = self._envelope(installed, receipt)
        key = hashlib.sha256(key_seed).hexdigest()
        return {"schema_version": "launcher_control_envelope/v8", "execution_mode": "launcher-control-preflight/v8", "issue_id": business["issue_id"], "platform_parent_issue_id": business["platform_parent_issue_id"], "project_id": business["project_id"], "run_id": "launcher-control-v8", "trace_id": "launcher-control-trace", "qa_id": "EAS70-LAUNCHER-CONTROL", "target_agent_id": target, "target_agent_uuid": TARGETS[target][0], "root_binding_id": business["root_binding_id"], "launch_idempotency_key": key, "execution_bootstrap": business["execution_bootstrap"], "callback": {"callback_agent_id": "1cdd93d3-b5fa-4dae-9b09-8320055c3072", "callback_issue_id": "42f91f92-9453-4e7a-a38e-30d629ae07d6", "callback_condition": "consume control task"}}

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
            self.assertEqual(manifest["instruction_hashes"], {target: _sha(ROOT / "agents" / target / "prompt.md") for target in TARGETS})
            self.assertEqual(manifest["skill_metadata"]["name"], "east-v5-runtime-bootstrap-v8")
            self.assertEqual(manifest["skill_identity"]["source_skill_sha256"], manifest["skill_identity"]["archive_skill_sha256"])
            self.assertNotEqual(manifest["skill_identity"]["source_skill_sha256"], manifest["skill_identity"]["runtime_materialized_skill_sha256"])
            self.assertEqual(_sha(installed / "SKILL.md"), manifest["skill_identity"]["runtime_materialized_skill_sha256"])

    def test_two_clean_materializations_preserve_five_hash_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, receipt, installed = self._archive(directory)
            raw = directory / "raw"
            with zipfile.ZipFile(archive) as package:
                package.extractall(raw)
            manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(_sha(raw / "SKILL.md"), manifest["skill_identity"]["source_skill_sha256"])
            self.assertEqual(receipt["archive_sha256"], _sha(archive))
            self.assertEqual(receipt["manifest_sha256"], _sha(raw / "manifest.json"))
            self.assertEqual(receipt["supporting_files"], manifest["files"])
            second = directory / "second"
            with zipfile.ZipFile(archive) as package:
                package.extractall(second)
            _materialize(raw / "SKILL.md"); _materialize(second / "SKILL.md")
            self.assertEqual(_sha(raw / "SKILL.md"), manifest["skill_identity"]["runtime_materialized_skill_sha256"])
            self.assertEqual(raw.joinpath("SKILL.md").read_bytes(), second.joinpath("SKILL.md").read_bytes())
            self.assertEqual(_sha(installed / "scripts" / "controller.py"), manifest["source_hashes"]["scripts/controller.py"])

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
            registry_path = runtime / "east-v5-runtime" / "issues" / "EAS-70" / "recovery-probe" / "3" / "artifact-registry.json"
            state = json.loads(registry_path.read_text(encoding="utf-8"))
            state["records"][0]["payload"]["conflict"] = True
            _write(registry_path, state)
            conflict = self._call(installed, "run-task", initial, self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000011", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(directory / "launches.json"))
            self.assertEqual(conflict.returncode, 2); self.assertIn("RUNTIME_LAUNCH_OUTBOX_BINDING_DRIFT", conflict.stderr)

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

    def test_metadata_materialization_drift_rejects_before_business_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            original = (installed / "SKILL.md").read_text(encoding="utf-8")
            (installed / "SKILL.md").write_text(original.replace("name: east-v5-runtime-bootstrap-v8", "name: east-v5-runtime-bootstrap-v8-skill", 1), encoding="utf-8")
            rejected = self._call(installed, "business-preflight", self._envelope(installed, receipt), self._claim("010", receipt), directory)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("RUNTIME_SKILL_MATERIALIZED_HASH_DRIFT", rejected.stderr)

    def test_v6_or_v7_envelope_is_rejected_before_business_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            envelope = self._envelope(installed, receipt)
            envelope["schema_version"] = "task_input_envelope/v7"
            envelope["adapter_version"] = "east-v5-runtime-adapter/v7"
            rejected = self._call(installed, "business-preflight", envelope, self._claim("010", receipt), directory)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("RUNTIME_TASK_INPUT_INVALID", rejected.stderr)

    def test_rejects_claim_instruction_hash_and_supersedes_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            envelope = self._envelope(installed, receipt)
            claim = self._claim("010", receipt); claim["instructions_sha256"] = "0" * 64
            mismatch = self._call(installed, "business-preflight", envelope, claim, directory)
            self.assertEqual(mismatch.returncode, 2); self.assertIn("RUNTIME_SKILL_CLAIM_INSTRUCTIONS_DRIFT", mismatch.stderr)
            envelope = self._envelope(installed, receipt); envelope["supersedes"] = list(reversed(envelope["supersedes"]))
            supersedes = self._call(installed, "business-preflight", envelope, self._claim("010", receipt), directory)
            self.assertEqual(supersedes.returncode, 2); self.assertIn("RUNTIME_RECOVERY_SUPERSEDES_INVALID", supersedes.stderr)
            envelope = self._envelope(installed, receipt); envelope["attempt"] = 1
            wrong_attempt = self._call(installed, "business-preflight", envelope, self._claim("010", receipt), directory)
            self.assertEqual(wrong_attempt.returncode, 2); self.assertIn("RUNTIME_TASK_INPUT_INVALID", wrong_attempt.stderr)
            envelope = self._envelope(installed, receipt); envelope["recovery_of"]["trace_id"] = "different-trace"
            lineage = self._call(installed, "business-preflight", envelope, self._claim("010", receipt), directory)
            self.assertEqual(lineage.returncode, 2); self.assertIn("RUNTIME_RECOVERY_LINEAGE_DRIFT", lineage.stderr)

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
            base = runtime / "east-v5-runtime" / "issues" / "EAS-70" / "recovery-probe" / "3"
            self.assertFalse((base / "artifact-registry.json").exists())
            self.assertFalse((base / "execution-journal.json").exists())
            entries = json.loads((base / "launch-outbox.json").read_text(encoding="utf-8"))["entries"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(next(iter(entries.values()))["state"], "intent")

    def test_real_control_entrypoint_reuses_outbox_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            runtime, launches = directory / "runtime", directory / "launches.json"
            control = self._control(installed, receipt)
            first = self._call(installed, "launcher-control-preflight", control, self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(launches))
            self.assertEqual(first.returncode, 0, first.stderr)
            second = self._call(installed, "launcher-control-preflight", control, self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(launches))
            self.assertEqual(second.returncode, 0, second.stderr)
            one, two = json.loads(first.stdout), json.loads(second.stdout)
            self.assertEqual((one["control_issue_id"], one["control_task_id"]), (two["control_issue_id"], two["control_task_id"]))
            self.assertEqual(len(json.loads(launches.read_text(encoding="utf-8"))["launches"]), 1)
            child = json.loads(launches.read_text(encoding="utf-8"))["launches"][0]["envelope"]
            consumed = self._call(installed, "launcher-control-preflight", child, self._claim("110", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000110", "--runtime-id", "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
            self.assertEqual(consumed.returncode, 0, consumed.stderr)
            self.assertEqual(json.loads(consumed.stdout)["status"], "launcher_control_consumed")

    def test_control_envelope_rejects_business_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            control = self._control(installed, receipt)
            control["input_ref"] = {"artifact_id": "forbidden"}
            rejected = self._call(installed, "launcher-control-preflight", control, self._claim("010", receipt), directory, "--runtime-root", str(directory / "runtime"), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("RUNTIME_LAUNCH_CONTROL_ENVELOPE_INVALID", rejected.stderr)

    def test_different_control_keys_do_not_share_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            runtime, launches = directory / "runtime", directory / "launches.json"
            results = []
            for seed in (b"control-a", b"control-b"):
                run = self._call(installed, "launcher-control-preflight", self._control(installed, receipt, key_seed=seed), self._claim("010", receipt), directory, "--runtime-root", str(runtime), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "--launcher-record", str(launches))
                self.assertEqual(run.returncode, 0, run.stderr)
                results.append(json.loads(run.stdout)["control_issue_id"])
            self.assertNotEqual(*results)
            self.assertEqual(len(json.loads(launches.read_text(encoding="utf-8"))["launches"]), 2)

    def test_production_launcher_uses_private_cwd_descriptor_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _archive, receipt, installed = self._archive(directory)
            bin_dir = directory / "bin"; bin_dir.mkdir()
            fake = bin_dir / "multica"
            fake.write_text("#!/usr/bin/env python3\nimport json, os, pathlib, sys\nlog=pathlib.Path(os.getcwd())/'fake-launch-log.json'\na=sys.argv[1:]\nif a[:2] == ['issue','create']:\n p=pathlib.Path(a[a.index('--description-file')+1]).resolve(); log.write_text(json.dumps({'args':a,'parent':str(p.parent),'mode':p.stat().st_mode & 0o777})); print(json.dumps({'id':'fake-issue'}))\nelif a[:2] == ['issue','runs']:\n print(json.dumps([{'id':'00000000-0000-4000-8000-000000000110','issue_id':'fake-issue','agent_id':'67f9cf29-cd45-4ef3-8c87-963fd3ff5898'}]))\nelse: raise SystemExit(2)\n", encoding="utf-8")
            fake.chmod(0o700)
            previous = os.environ["PATH"]; os.environ["PATH"] = str(bin_dir) + os.pathsep + previous
            try:
                result = self._call(installed, "launcher-control-preflight", self._control(installed, receipt), self._claim("010", receipt), directory, "--runtime-root", str(directory / "outside-runtime"), "--task-id", "00000000-0000-4000-8000-000000000010", "--runtime-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
            finally:
                os.environ["PATH"] = previous
            self.assertEqual(result.returncode, 0, result.stderr)
            log = json.loads((directory / "fake-launch-log.json").read_text(encoding="utf-8"))
            self.assertEqual(log["parent"], str(directory.resolve()))
            self.assertEqual(log["mode"], 0o600)
            self.assertNotIn("--allow-external-file", log["args"])
            self.assertFalse(list(directory.glob(".east-launch-*.json")))


if __name__ == "__main__":
    unittest.main()
