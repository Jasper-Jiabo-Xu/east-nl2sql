from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash
from east_v5.governance import ContractError


def roots(base: Path) -> dict[str, object]:
    return {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}


def package(kind: str = "constraint_asset_ref", artifact_id: str = "CA-V0.3.0", version: int = 1, parents: list[dict] | None = None, mode: str = "foundation") -> tuple[dict, dict]:
    payload = {"fixture": kind, "value": version}
    envelope = {"artifact_id": artifact_id, "artifact_type": kind, "run_id": "fixture-run", "qa_id": None if mode == "foundation" else "QA-001", "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "fixture", "parent_artifact_refs": parents or [], "input_hashes": [x["content_hash"] for x in parents or []], "status": "candidate", "mode": mode, "created_at": "2026-08-13T00:00:00+00:00", "trace_id": "fixture-trace", "storage_locator": "candidate/fixture.json"}
    envelope["content_hash"] = content_hash(envelope, payload)
    return envelope, payload


class CommonEnvelopeTests(unittest.TestCase):
    def registry(self, base: Path) -> ArtifactRegistry:
        return ArtifactRegistry(ROOT, roots(base), "EAS-15", "fixture-run", 1)

    def test_register_resolve_supersede_and_locator_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            registry = self.registry(Path(temp)); first, payload = package(); registry.register(first, payload)
            self.assertEqual(registry.resolve(artifact_ref(first))["payload"], payload)
            self.assertEqual(registry.register(first, payload)["content_hash"], first["content_hash"])
            second, payload2 = package(version=2); second["supersedes_ref"] = artifact_ref(first); second["content_hash"] = content_hash(second, payload2); registry.register(second, payload2)
            migrated = registry.migrate_locator(artifact_ref(first), "migrated/fixture.json")
            self.assertEqual(migrated["content_hash"], first["content_hash"])
            self.assertEqual(registry.lineage(artifact_ref(second))["supersedes_ref"], artifact_ref(first))

    def test_committed_fixture_is_canonical_and_valid(self) -> None:
        fixture = __import__("json").loads((ROOT / "fixtures/artifacts/common-envelope-valid.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            self.registry(Path(temp)).register(fixture["envelope"], fixture["payload"])

    def test_strict_rejections_are_write_free(self) -> None:
        cases = []
        first, payload = package(); cases.append((lambda e: e.update({"unexpected": 1}), "UNKNOWN_FIELD"))
        cases.append((lambda e: e.update({"attempt_no": 4}), "ATTEMPT_OUT_OF_RANGE"))
        cases.append((lambda e: e.update({"schema_version": "old"}), "SCHEMA_VERSION_UNSUPPORTED"))
        cases.append((lambda e: e.update({"created_at": "2026-08-13"}), "TIMESTAMP_INVALID"))
        cases.append((lambda e: e.update({"artifact_type": "old_registry"}), "ARTIFACT_TYPE_INVALID"))
        with tempfile.TemporaryDirectory() as temp:
            for mutate, code in cases:
                registry = self.registry(Path(temp)); candidate = copy.deepcopy(first); mutate(candidate)
                with self.assertRaisesRegex(ContractError, code): registry.register(candidate, payload)
                self.assertFalse(registry.path.exists())

    def test_versions_parents_status_and_release_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            registry = self.registry(Path(temp)); first, data = package(); registry.register(first, data)
            skipped, skipped_data = package(version=3)
            with self.assertRaisesRegex(ContractError, "VERSION_NOT_CONTIGUOUS"): registry.register(skipped, skipped_data)
            orphan, orphan_data = package(artifact_id="TRG-V1.0.0", kind="typed_reference_graph_ref", parents=[{"artifact_id": "missing", "version": 1, "content_hash": "a" * 64}])
            orphan["content_hash"] = content_hash(orphan, orphan_data)
            with self.assertRaisesRegex(ContractError, "PARENT_ORPHAN"): registry.register(orphan, orphan_data)
            with self.assertRaisesRegex(ContractError, "FORMAL_RELEASE_FORBIDDEN"): registry.transition(artifact_ref(first), "released")
            with self.assertRaisesRegex(ContractError, "STATUS_TRANSITION_INVALID"): registry.transition(artifact_ref(first), "approved")
            self.assertEqual(registry.transition(artifact_ref(first), "pending_validation")["status"], "pending_validation")

    def test_concurrent_replay_is_atomic_and_singleton(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            registry = self.registry(Path(temp)); envelope, payload = package(); answers: list[str] = []
            def worker() -> None: answers.append(registry.register(copy.deepcopy(envelope), copy.deepcopy(payload))["content_hash"])
            threads = [threading.Thread(target=worker) for _ in range(8)]
            [thread.start() for thread in threads]; [thread.join() for thread in threads]
            self.assertEqual(answers, [envelope["content_hash"]] * 8)
            self.assertEqual(len(registry.resolve(artifact_ref(envelope))["payload"]), 2)

    def test_eas16_eas19_eas21_real_consumer_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            registry = self.registry(Path(temp))
            ca, ca_payload = package("constraint_asset_ref", "CA-V0.3.0"); registry.register(ca, ca_payload)
            trg, trg_payload = package("typed_reference_graph_ref", "TRG-V1.0.0", parents=[artifact_ref(ca)])
            trg["content_hash"] = content_hash(trg, trg_payload); registry.register(trg, trg_payload)
            registry.migrate_locator(artifact_ref(ca), "migrated/ca.json")
            self.assertEqual(registry.resolve(artifact_ref(trg))["envelope"]["parent_artifact_refs"], [artifact_ref(ca)])
            snapshot, snapshot_payload = package("database_snapshot_ref", "SNAP-001")
            registry.register(snapshot, snapshot_payload)
            conflict = copy.deepcopy(snapshot); conflict_payload = {"fixture": "database_snapshot_ref", "value": "other"}; conflict["content_hash"] = content_hash(conflict, conflict_payload)
            with self.assertRaisesRegex(ContractError, "IDENTITY_CONTENT_CONFLICT"): registry.register(conflict, conflict_payload)
            self.assertEqual(registry.resolve(artifact_ref(snapshot))["payload"], snapshot_payload)
            penalty, penalty_payload = package("penalty_source_package", "PENALTY-001", mode="question_sql")
            registry.register(penalty, penalty_payload)
            self.assertEqual(registry.resolve(artifact_ref(penalty))["payload"], penalty_payload)


if __name__ == "__main__": unittest.main()
