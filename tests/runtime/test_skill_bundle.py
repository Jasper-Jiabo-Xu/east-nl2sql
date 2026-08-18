from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "east-v5-runtime-bootstrap-v1"
RUNNER = SKILL / "scripts" / "skill_bundle_runner.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _inputs(directory: Path, *, manifest: Path = SKILL / "manifest.json", provider: str = "codex", manifest_hash: str | None = None) -> tuple[Path, Path]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    context = {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "workspace", "project_id": "project", "daemon_id": "daemon"}
    root_binding = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    envelope = {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": "EAS-70", "run_id": "claim-run", "trace_id": "claim-trace", "qa_id": "claim-qa", "attempt": 1, "target_agent_id": "010", "target_agent_uuid": "1d9153fc-4386-42e9-b2e5-56eb38f671af", "root_binding_id": root_binding, "input_ref": None, "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "110"}, "execution_bootstrap": {"bootstrap_version": "east-v5-runtime-bootstrap/v1", "candidate_base_sha": "9bdf86b1c5f369a8ee05bb94ba2e47aeb04414de", "candidate_head_sha": data["source_candidate_head"], "adapter_sha256": data["source_hashes"]["src/east_v5/runtime/adapter.py"], "bootstrap_sha256": data["source_hashes"]["src/east_v5/runtime/bootstrap.py"], "runner_sha256": data["source_hashes"]["scripts/runtime_bootstrap.py"], "runtime_context": context, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v1", "skill_version": "v1", "skill_manifest_sha256": manifest_hash or _sha(manifest)}}}
    claim = {"agent_uuid": "1d9153fc-4386-42e9-b2e5-56eb38f671af", "runtime_uuid": "6095ccd3-adc0-48a2-9a9e-140b16a3c2e1", "provider_id": provider, "instructions_sha256": "a" * 64, "enabled_skill_ids": ["east-v5-runtime-bootstrap-v1"]}
    envelope_path, claim_path = directory / "envelope.json", directory / "claim.json"
    _write(envelope_path, envelope); _write(claim_path, claim)
    return envelope_path, claim_path


class SkillBundleTests(unittest.TestCase):
    def _run(self, runner: Path, envelope: Path, claim: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(runner), "claim-preflight", "--envelope-file", str(envelope), "--claim-file", str(claim)], text=True, capture_output=True, check=False)

    def test_valid_claim_is_redacted_and_has_no_business_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            envelope, claim = _inputs(Path(tmp))
            result = self._run(RUNNER, envelope, claim)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["claim_status"], "accepted")
            self.assertNotIn("payload", result.stdout.lower())
            self.assertNotIn("receipt", result.stdout.lower())

    def test_manifest_hash_drift_rejects_before_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            envelope, claim = _inputs(Path(tmp), manifest_hash="0" * 64)
            result = self._run(RUNNER, envelope, claim)
            self.assertEqual(result.returncode, 2)
            self.assertIn("RUNTIME_SKILL_BUNDLE_DRIFT", result.stderr)

    def test_support_file_drift_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "skill"
            shutil.copytree(SKILL, clone)
            (clone / "SKILL.md").write_text((clone / "SKILL.md").read_text(encoding="utf-8") + "\n", encoding="utf-8")
            envelope, claim = _inputs(Path(tmp), manifest=clone / "manifest.json")
            result = self._run(clone / "scripts" / "skill_bundle_runner.py", envelope, claim)
            self.assertEqual(result.returncode, 2)
            self.assertIn("RUNTIME_SKILL_SUPPORT_FILE_HASH_DRIFT", result.stderr)

    def test_cross_provider_claim_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            envelope, claim = _inputs(Path(tmp), provider="claude")
            result = self._run(RUNNER, envelope, claim)
            self.assertEqual(result.returncode, 2)
            self.assertIn("RUNTIME_SKILL_TARGET_OR_PROVIDER_DRIFT", result.stderr)


if __name__ == "__main__":
    unittest.main()
