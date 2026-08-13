from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.artifacts import ArtifactRegistry, content_hash
from east_v5.governance import ContractError, attempt_path, canonical_bytes, governed_manifest, validate_roots, verify_governed_manifest, verify_input_lock


class GovernanceTests(unittest.TestCase):
    def roots(self, base: Path) -> dict[str, object]:
        return {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}

    def test_canonical_manifest_is_reproducible(self) -> None:
        first, second = governed_manifest(ROOT), governed_manifest(ROOT)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(verify_governed_manifest(ROOT)["content_sha256"], first["content_sha256"])

    def test_rejects_overlap_unknown_and_bad_attempt_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            roots = self.roots(base)
            for attempt in (0, 4):
                with self.assertRaisesRegex(ContractError, "ATTEMPT_OUT_OF_RANGE"):
                    attempt_path(roots, "EAS-15", "run", attempt)
            roots["runtime_root"] = roots["repo_root"]
            with self.assertRaisesRegex(ContractError, "ROOT_BOUNDARY_VIOLATION"):
                validate_roots(roots)
            with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD"):
                validate_roots({**self.roots(base), "unknown": True})

    def test_eas15_registry_isolated_idempotent_and_conflict_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            roots = self.roots(base)
            locator = base / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-15" / "stub-run" / "1" / "locators" / "test.json"
            locator.parent.mkdir(parents=True); locator.write_text("fixture", encoding="utf-8")
            envelope = {"artifact_id": "governance-fixture", "artifact_type": "constraint_asset_ref", "run_id": "stub-run", "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "test", "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "foundation", "created_at": "2026-08-13T00:00:00+00:00", "trace_id": "test", "storage_locator": str(locator)}
            payload = {"fixture": "governance"}
            envelope["content_hash"] = content_hash(envelope, payload)
            registry = ArtifactRegistry(ROOT, roots, "EAS-15", "stub-run", 1)
            first = registry.register(envelope, payload)
            second = registry.register(envelope, payload)
            self.assertEqual(first, second)
            self.assertFalse((base / "reference").exists())
            self.assertNotIn("05_新版本交付层", str(registry.path))
            conflict = dict(envelope)
            conflict_payload = {"fixture": "different"}
            conflict["content_hash"] = content_hash(conflict, conflict_payload)
            with self.assertRaisesRegex(ContractError, "IDENTITY_CONTENT_CONFLICT"):
                registry.register(conflict, conflict_payload)

    def test_manifest_drift_is_rejected_without_write(self) -> None:
        manifest_path = ROOT / "governance-manifest.json"
        original = manifest_path.read_bytes()
        try:
            value = json.loads(original)
            value["content_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
                verify_governed_manifest(ROOT)
        finally:
            manifest_path.write_bytes(original)

    def test_input_lock_rejects_reference_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            reference = Path(temp)
            target = reference / "结构图"
            target.mkdir()
            for item in json.loads((ROOT / "config/input-lock.json").read_text(encoding="utf-8"))["inputs"]:
                if item["locator"].startswith("reference:"):
                    relative = item["locator"].removeprefix("reference:")
                    path = reference / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("not frozen", encoding="utf-8")
                    with self.assertRaisesRegex(ContractError, "INPUT_VERSION_DRIFT"):
                        verify_input_lock(ROOT, reference)
                    break


if __name__ == "__main__":
    unittest.main()
