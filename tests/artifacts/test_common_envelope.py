from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash, validate_common_envelope_schema
from east_v5.governance import ContractError


def roots(base: Path) -> dict[str, object]:
    return {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}


def package(base: Path, kind: str = "constraint_asset_ref", artifact_id: str = "CA-V0.3.0", version: int = 1, parents: list[dict] | None = None, mode: str = "foundation", attempt: int = 1, status: str = "candidate", qa_id: str | None = None, runtime_run: str = "fixture-run", runtime_attempt: int = 1) -> tuple[dict, dict]:
    payload = {"fixture": kind, "value": version}
    locator = base / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-15" / runtime_run / str(runtime_attempt) / "locators" / f"{artifact_id}-{version}-{attempt}.json"
    locator.parent.mkdir(parents=True, exist_ok=True); locator.write_text("fixture", encoding="utf-8")
    envelope = {"artifact_id": artifact_id, "artifact_type": kind, "run_id": "fixture-run", "qa_id": qa_id if qa_id is not None else (None if mode == "foundation" else "QA-001"), "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": "fixture", "parent_artifact_refs": parents or [], "input_hashes": [x["content_hash"] for x in parents or []], "status": status, "mode": mode, "created_at": "2026-08-13T00:00:00+00:00", "trace_id": "fixture-trace", "storage_locator": str(locator)}
    envelope["content_hash"] = content_hash(envelope, payload)
    return envelope, payload


class CommonEnvelopeTests(unittest.TestCase):
    def registry(self, base: Path) -> ArtifactRegistry:
        return ArtifactRegistry(ROOT, roots(base), "EAS-15", "fixture-run", 1)

    def test_register_resolve_supersede_and_locator_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base); first, payload = package(base); registry.register(first, payload)
            self.assertEqual(registry.resolve(artifact_ref(first))["payload"], payload)
            self.assertEqual(registry.register(first, payload)["content_hash"], first["content_hash"])
            second, payload2 = package(base, version=2); second["supersedes_ref"] = artifact_ref(first); second["content_hash"] = content_hash(second, payload2); registry.register(second, payload2)
            migrated_path = registry.directory / "locators" / "migrated.json"; migrated_path.parent.mkdir(exist_ok=True); migrated_path.write_text("migrated", encoding="utf-8")
            migrated = registry.migrate_locator(artifact_ref(first), str(migrated_path))
            self.assertEqual(migrated["content_hash"], first["content_hash"])
            self.assertEqual(registry.lineage(artifact_ref(second))["supersedes_ref"], artifact_ref(first))

    def test_committed_fixture_is_canonical_and_valid(self) -> None:
        fixture = __import__("json").loads((ROOT / "fixtures/artifacts/common-envelope-valid.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); locator = base / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-15" / "fixture-run" / "1" / "fixture.json"; locator.parent.mkdir(parents=True); locator.write_text("fixture")
            fixture["envelope"]["storage_locator"] = str(locator); fixture["envelope"]["content_hash"] = content_hash(fixture["envelope"], fixture["payload"])
            self.registry(base).register(fixture["envelope"], fixture["payload"])

    def test_strict_rejections_are_write_free(self) -> None:
        cases = []
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); first, payload = package(base); cases.append((lambda e: e.update({"unexpected": 1}), "UNKNOWN_FIELD"))
            for mutate, code in cases:
                registry = self.registry(base); candidate = copy.deepcopy(first); mutate(candidate)
                with self.assertRaisesRegex(ContractError, code): registry.register(candidate, payload)
                self.assertFalse(registry.path.exists())

    def test_versions_parents_status_and_release_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base); first, data = package(base); registry.register(first, data)
            skipped, skipped_data = package(base, version=3)
            with self.assertRaisesRegex(ContractError, "VERSION_NOT_CONTIGUOUS"): registry.register(skipped, skipped_data)
            orphan, orphan_data = package(base, artifact_id="TRG-V1.0.0", kind="typed_reference_graph_ref", parents=[{"artifact_id": "missing", "version": 1, "content_hash": "a" * 64}])
            orphan["content_hash"] = content_hash(orphan, orphan_data)
            with self.assertRaisesRegex(ContractError, "PARENT_ORPHAN"): registry.register(orphan, orphan_data)
            with self.assertRaisesRegex(ContractError, "FORMAL_RELEASE_FORBIDDEN"): registry.transition(artifact_ref(first), "released")
            with self.assertRaisesRegex(ContractError, "STATUS_TRANSITION_INVALID"): registry.transition(artifact_ref(first), "approved")
            self.assertEqual(registry.transition(artifact_ref(first), "pending_validation")["status"], "pending_validation")

    def test_concurrent_replay_is_atomic_and_singleton(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base); envelope, payload = package(base); answers: list[str] = []
            def worker() -> None: answers.append(registry.register(copy.deepcopy(envelope), copy.deepcopy(payload))["content_hash"])
            threads = [threading.Thread(target=worker) for _ in range(8)]
            [thread.start() for thread in threads]; [thread.join() for thread in threads]
            self.assertEqual(answers, [envelope["content_hash"]] * 8)
            self.assertEqual(len(registry.resolve(artifact_ref(envelope))["payload"]), 2)

    def test_eas16_eas19_eas21_real_consumer_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base)
            ca, ca_payload = package(base, "constraint_asset_ref", "CA-V0.3.0"); registry.register(ca, ca_payload)
            trg, trg_payload = package(base, "typed_reference_graph_ref", "TRG-V1.0.0", parents=[artifact_ref(ca)])
            trg["content_hash"] = content_hash(trg, trg_payload); registry.register(trg, trg_payload)
            migrated = registry.directory / "locators" / "ca-migrated.json"; migrated.parent.mkdir(exist_ok=True); migrated.write_text("migrated")
            registry.migrate_locator(artifact_ref(ca), str(migrated))
            self.assertEqual(registry.resolve(artifact_ref(trg))["envelope"]["parent_artifact_refs"], [artifact_ref(ca)])
            snapshot, snapshot_payload = package(base, "database_snapshot_ref", "SNAP-001")
            registry.register(snapshot, snapshot_payload)
            conflict = copy.deepcopy(snapshot); conflict_payload = {"fixture": "database_snapshot_ref", "value": "other"}; conflict["content_hash"] = content_hash(conflict, conflict_payload)
            with self.assertRaisesRegex(ContractError, "IDENTITY_CONTENT_CONFLICT"): registry.register(conflict, conflict_payload)
            self.assertEqual(registry.resolve(artifact_ref(snapshot))["payload"], snapshot_payload)
            penalty, penalty_payload = package(base, "penalty_source_package", "PENALTY-001", mode="question_sql")
            registry.register(penalty, penalty_payload)
            self.assertEqual(registry.resolve(artifact_ref(penalty))["payload"], penalty_payload)

    def test_locator_boundary_missing_and_failed_writes_are_rollback_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base); envelope, payload = package(base)
            missing = copy.deepcopy(envelope); Path(missing["storage_locator"]).unlink()
            with self.assertRaisesRegex(ContractError, "LOCATOR_MISSING"): registry.register(missing, payload)
            self.assertFalse(registry.path.exists())
            outside = copy.deepcopy(envelope); outside_path = base / "outside.json"; outside_path.write_text("x"); outside["storage_locator"] = str(outside_path)
            with self.assertRaisesRegex(ContractError, "LOCATOR_OUT_OF_RUNTIME_ROOT"): registry.register(outside, payload)
            self.assertFalse(registry.path.exists())
            formal = base / "runtime" / "vnext" / "05_新版本交付层" / "forbidden.json"; formal.parent.mkdir(parents=True); formal.write_text("x")
            with self.assertRaisesRegex(ContractError, "LOCATOR_OUT_OF_ATTEMPT_SCOPE"): registry.validate_locator(str(formal))
            foreign = base / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-16" / "fixture-run" / "1" / "foreign.json"; foreign.parent.mkdir(parents=True); foreign.write_text("x")
            with self.assertRaisesRegex(ContractError, "LOCATOR_OUT_OF_ATTEMPT_SCOPE"): registry.validate_locator(str(foreign))
            other_attempt = base / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-15" / "fixture-run" / "2" / "other.json"; other_attempt.parent.mkdir(parents=True); other_attempt.write_text("x")
            with self.assertRaisesRegex(ContractError, "LOCATOR_OUT_OF_ATTEMPT_SCOPE"): registry.validate_locator(str(other_attempt))
            envelope, payload = package(base)
            with mock.patch.object(registry, "_save", side_effect=ContractError("RUNTIME_TEMPORARY")):
                with self.assertRaisesRegex(ContractError, "RUNTIME_TEMPORARY"): registry.register(envelope, payload)
            self.assertFalse(registry.path.exists())
            with mock.patch.object(registry, "_append_audit", side_effect=OSError("audit")):
                with self.assertRaisesRegex(ContractError, "RUNTIME_TEMPORARY"): registry.register(envelope, payload)
            self.assertFalse(registry.path.exists())
            good, good_payload = package(base, artifact_id="DISAPPEARING")
            registry.register(good, good_payload); Path(good["storage_locator"]).unlink()
            with self.assertRaisesRegex(ContractError, "LOCATOR_MISSING"): registry.resolve(artifact_ref(good))

    def test_supersedes_and_parent_cycles_are_rejected_without_partial_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base)
            a, ap = package(base, artifact_id="A"); registry.register(a, ap)
            b, bp = package(base, artifact_id="B", parents=[artifact_ref(a)]); b["content_hash"] = content_hash(b, bp); registry.register(b, bp)
            c, cp = package(base, artifact_id="C", parents=[artifact_ref(b)]); c["content_hash"] = content_hash(c, cp); registry.register(c, cp)
            cross_cycle, cross_payload = package(base, artifact_id="A", version=2, parents=[artifact_ref(c)])
            cross_cycle["supersedes_ref"] = artifact_ref(a); cross_cycle["content_hash"] = content_hash(cross_cycle, cross_payload)
            with self.assertRaisesRegex(ContractError, "PARENT_CYCLE"): registry.register(cross_cycle, cross_payload)
            bad_supersedes, bsp = package(base, artifact_id="A", version=2)
            bad_supersedes["supersedes_ref"] = artifact_ref(b); bad_supersedes["content_hash"] = content_hash(bad_supersedes, bsp)
            with self.assertRaisesRegex(ContractError, "SUPERSEDES_INVALID"): registry.register(bad_supersedes, bsp)
            self.assertEqual(len(registry.audit()), 3)

    def test_attempt_retry_isolated_and_third_failure_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for attempt in (1, 2, 3):
                result = ArtifactRegistry(ROOT, roots(base), "EAS-15", "retry-run", attempt).record_transient_failure("register", "RUNTIME_TEMPORARY")
                self.assertEqual(result["failures"][-1]["status"], "blocked_manual" if attempt == 3 else "retryable")
            state = json.loads((base / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-15" / "retry-run" / "retry-state.json").read_text())
            self.assertEqual([item["attempt"] for item in state["failures"]], ["1", "2", "3"])
            direct_three = ArtifactRegistry(ROOT, roots(base), "EAS-15", "direct-three", 3).record_transient_failure("register", "TOOL_TRANSIENT")
            self.assertEqual(direct_three["failures"][-1]["status"], "blocked_manual")
            with self.assertRaisesRegex(ContractError, "ATTEMPT_SEQUENCE_INVALID"):
                ArtifactRegistry(ROOT, roots(base), "EAS-15", "missing-two", 2).record_transient_failure("register", "TOOL_TRANSIENT")
            with self.assertRaisesRegex(ContractError, "ATTEMPT_REPLAY_FORBIDDEN"):
                ArtifactRegistry(ROOT, roots(base), "EAS-15", "retry-run", 3).record_transient_failure("register", "RUNTIME_TEMPORARY")
            with self.assertRaisesRegex(ContractError, "RETRY_ERROR_NOT_TRANSIENT"):
                ArtifactRegistry(ROOT, roots(base), "EAS-15", "retry-run", 3).record_transient_failure("register", "BAD")

    def test_modes_statuses_qa_and_executable_rejection_fixtures(self) -> None:
        invalid = json.loads((ROOT / "fixtures/artifacts/common-envelope-invalid.json").read_text())
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for mode in ("foundation", "event_data", "question_sql"):
                envelope, payload = package(base, artifact_id=f"MODE-{mode}", mode=mode)
                self.registry(base).register(envelope, payload)
            for attempt in (1, 2, 3):
                envelope, payload = package(base, artifact_id=f"ATTEMPT-{attempt}", attempt=attempt, runtime_run=f"attempt-{attempt}", runtime_attempt=attempt)
                ArtifactRegistry(ROOT, roots(base), "EAS-15", f"attempt-{attempt}", attempt).register(envelope, payload)
            foundation, fpayload = package(base, artifact_id="FOUNDATION-QA", mode="foundation", qa_id="RESTORE-1")
            self.registry(base).register(foundation, fpayload)
            status_envelope, status_payload = package(base, artifact_id="STATUS-FLOW")
            active = self.registry(base); active.register(status_envelope, status_payload)
            self.assertEqual(active.transition(artifact_ref(status_envelope), "pending_validation")["status"], "pending_validation")
            self.assertEqual(active.transition(artifact_ref(status_envelope), "validated")["status"], "validated")
            self.assertEqual(active.transition(artifact_ref(status_envelope), "pending_review")["status"], "pending_review")
            self.assertEqual(active.transition(artifact_ref(status_envelope), "approved")["status"], "approved")
            rejected, rejected_payload = package(base, artifact_id="STATUS-REJECTED")
            active.register(rejected, rejected_payload); self.assertEqual(active.transition(artifact_ref(rejected), "rejected")["status"], "rejected")
            blocked, blocked_payload = package(base, artifact_id="STATUS-BLOCKED")
            active.register(blocked, blocked_payload); self.assertEqual(active.transition(artifact_ref(blocked), "blocked_manual")["status"], "blocked_manual")
            for case in invalid["cases"]:
                envelope, payload = package(base, artifact_id="BAD-" + case["name"])
                for key, value in case["set"].items(): envelope[key] = value
                with self.assertRaisesRegex(ContractError, case["error"]): self.registry(base).register(envelope, payload)

    def test_multiple_parents_and_draft_202012_schema_execute(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base)
            first, first_payload = package(base, artifact_id="PARENT-A"); registry.register(first, first_payload)
            second, second_payload = package(base, artifact_id="PARENT-B"); registry.register(second, second_payload)
            child, child_payload = package(base, artifact_id="MULTI-PARENT", parents=[artifact_ref(first), artifact_ref(second)])
            child["content_hash"] = content_hash(child, child_payload); registry.register(child, child_payload)
            self.assertEqual(len(registry.resolve(artifact_ref(child))["envelope"]["parent_artifact_refs"]), 2)
            valid, _ = package(base)
            validate_common_envelope_schema(ROOT, valid)
            cases = [
                (lambda e: e.update({"qa_id": None, "mode": "event_data"})),
                (lambda e: e.update({"unknown": True})),
                (lambda e: e.update({"parent_artifact_refs": [{"artifact_id": "bad", "version": 0, "content_hash": "a" * 64}]})),
                (lambda e: e.update({"mode": "bad"})),
                (lambda e: e.update({"attempt_no": 0})),
            ]
            for mutate in cases:
                invalid = copy.deepcopy(valid); mutate(invalid)
                with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"): validate_common_envelope_schema(ROOT, invalid)

    def test_cli_end_to_end_all_machine_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); registry = self.registry(base); envelope, payload = package(base, runtime_run="cli")
            roots_file, package_file, ref_file = base / "roots.json", base / "package.json", base / "ref.json"
            roots_file.write_text(json.dumps(roots(base))); package_file.write_text(json.dumps({"envelope": envelope, "payload": payload})); ref_file.write_text(json.dumps(artifact_ref(envelope)))
            command = [sys.executable, "-m", "east_v5.artifacts.cli"]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            common = ["--repo-root", str(ROOT), "--roots-json", str(roots_file), "--issue-id", "EAS-15", "--run-id", "cli", "--attempt", "1"]
            def call(operation, *tail): return subprocess.check_output(command + [operation, *common, *tail], text=True, env=env)
            call("register", "--package-json", str(package_file)); call("resolve", "--ref-json", str(ref_file)); call("verify", "--ref-json", str(ref_file)); call("lineage", "--ref-json", str(ref_file)); call("transition", "--ref-json", str(ref_file), "--target-status", "pending_validation")
            new_locator = base / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-15" / "cli" / "1" / "locators" / "cli-migrated.json"; new_locator.parent.mkdir(parents=True, exist_ok=True); new_locator.write_text("x")
            call("validate-locator", "--locator", str(new_locator)); call("migrate-locator", "--ref-json", str(ref_file), "--locator", str(new_locator)); self.assertIn("registered", call("audit", "--ref-json", str(ref_file)))


if __name__ == "__main__": unittest.main()
