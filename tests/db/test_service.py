from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.db.service import DatabaseService, destroy_sandbox, execute_sandbox_batch, publish_release
from east_v5.governance import ContractError, canonical_bytes


def roots(base: Path) -> dict[str, object]:
    return {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database(path: Path, version: str = "v0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE east_v5_database_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE account(id INTEGER PRIMARY KEY, name TEXT NOT NULL, amount INTEGER); INSERT INTO east_v5_database_metadata VALUES ('database_version', '" + version + "'); INSERT INTO account VALUES (1, 'a', 10);")


def candidate(source: Path, target: str = "v1", base: str = "v0", key: str = "release-1") -> dict:
    result = {"schema_version": "FORMAL-RELEASE-CANDIDATE/v1", "candidate_hash": "0" * 64, "target_version": target, "base_version": base, "idempotency_key": key, "approved": True, "database_locator": str(source), "database_sha256": sha(source), "regression_hash": "a" * 64, "problem_set_hash": "b" * 64}
    result["candidate_hash"] = hashlib.sha256(canonical_bytes({k: v for k, v in result.items() if k != "candidate_hash"})).hexdigest()
    return result


class DatabaseServiceTests(unittest.TestCase):
    def test_snapshot_isolated_readonly_and_parameterized_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); formal = base / "runtime/vnext/05_新版本交付层/formal.sqlite"; database(formal)
            service = DatabaseService(ROOT, roots(base), "EAS-19", "run", 1)
            snapshot = service.create_snapshot(formal, "v0", sha(formal))
            self.assertEqual(snapshot["database_sha256"], sha(formal))
            result = service.query(snapshot, {"table": "account", "columns": ["id", "name"], "predicates": [{"column": "amount", "op": ">=", "value": 10}], "limit": 5}, {"account": {"id", "name", "amount"}})
            self.assertEqual(result["rows"], [{"id": 1, "name": "a"}])
            with self.assertRaisesRegex(ContractError, "QUERY_SCOPE_FORBIDDEN"):
                service.query(snapshot, {"table": "account", "columns": ["name"], "predicates": [], "limit": 1}, {})
            with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD"):
                service.query(snapshot, {"table": "account", "columns": ["name"], "predicates": [], "limit": 1, "sql": "DROP TABLE account"}, {"account": {"name"}})
            with sqlite3.connect(snapshot["storage_locator"]) as conn:
                with self.assertRaises(sqlite3.OperationalError): conn.execute("INSERT INTO account VALUES (2, 'b', 2)")
            service.destroy_snapshot(snapshot); self.assertFalse(Path(snapshot["storage_locator"]).exists())

    def test_snapshot_rejects_version_and_hash_drift_without_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); formal = base / "runtime/vnext/05_新版本交付层/formal.sqlite"; database(formal)
            service = DatabaseService(ROOT, roots(base), "EAS-19", "run", 1)
            with self.assertRaisesRegex(ContractError, "DATABASE_HASH_DRIFT"): service.create_snapshot(formal, "v0", "a" * 64)
            with self.assertRaisesRegex(ContractError, "DATABASE_VERSION_CONFLICT"): service.create_snapshot(formal, "v1", sha(formal))
            self.assertFalse(service.directory.exists())

    def test_260_commits_copy_or_rolls_back_and_cleans_failed_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); formal = base / "runtime/vnext/05_新版本交付层/formal.sqlite"; database(formal)
            service = DatabaseService(ROOT, roots(base), "EAS-19", "run", 1); snapshot = service.create_snapshot(formal, "v0", sha(formal))
            batch = {"schema_version": "DATABASE-SANDBOX-BATCH/v1", "base_database_version": "v0", "base_snapshot_sha256": snapshot["snapshot_sha256"], "operations": [{"sql": "INSERT INTO account(id, name, amount) VALUES (?, ?, ?)", "parameters": [2, "b", 20]}]}
            sandbox = service.directory / "sandboxes/passed.sqlite"; report = execute_sandbox_batch(roots(base), snapshot, batch, sandbox)
            with sqlite3.connect(sandbox) as conn: self.assertEqual(conn.execute("SELECT COUNT(*) FROM account").fetchone()[0], 2)
            with sqlite3.connect(formal) as conn: self.assertEqual(conn.execute("SELECT COUNT(*) FROM account").fetchone()[0], 1)
            destroy_sandbox(roots(base), report); self.assertFalse(sandbox.exists())
            failed = service.directory / "sandboxes/failed.sqlite"; bad = {**batch, "operations": batch["operations"] + [{"sql": "INSERT INTO no_table VALUES (?)", "parameters": [1]}]}
            with self.assertRaisesRegex(ContractError, "SANDBOX_TRANSACTION_ROLLED_BACK"): execute_sandbox_batch(roots(base), snapshot, bad, failed)
            self.assertFalse(failed.exists())

    def test_260_rejects_schema_sql_base_conflict_and_unknown_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); formal = base / "runtime/vnext/05_新版本交付层/formal.sqlite"; database(formal)
            service = DatabaseService(ROOT, roots(base), "EAS-19", "run", 1); snapshot = service.create_snapshot(formal, "v0", sha(formal)); out = service.directory / "sandboxes/out.sqlite"
            batch = {"schema_version": "DATABASE-SANDBOX-BATCH/v1", "base_database_version": "wrong", "base_snapshot_sha256": snapshot["snapshot_sha256"], "operations": [{"sql": "VACUUM", "parameters": []}]}
            with self.assertRaisesRegex(ContractError, "SANDBOX_BASE_CONFLICT"): execute_sandbox_batch(roots(base), snapshot, batch, out)
            batch["base_database_version"] = "v0"
            with self.assertRaisesRegex(ContractError, "SANDBOX_SQL_FORBIDDEN"): execute_sandbox_batch(roots(base), snapshot, batch, out)
            self.assertFalse(out.exists())

    def test_010_is_idempotent_and_rejects_conflict_hash_drift_and_unapproved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); source = base / "runtime/vnext/03_构建过程层/issues/EAS-19/run/1/db/promote.sqlite"; database(source, "v1")
            first = candidate(source); receipt = publish_release(ROOT, roots(base), first); self.assertEqual(receipt["target_version"], "v1")
            manifest = json.loads((base / "runtime/vnext/05_新版本交付层/formal-release-manifest.json").read_text())
            self.assertEqual(receipt["manifest_hash"], manifest["manifest_hash"])
            self.assertEqual(publish_release(ROOT, roots(base), first)["receipt_hash"], receipt["receipt_hash"])
            conflict = candidate(source, target="v1", base="v0", key="release-2")
            with self.assertRaisesRegex(ContractError, "FORMAL_VERSION_CONFLICT"): publish_release(ROOT, roots(base), conflict)
            drift = candidate(source, key="release-3"); drift["database_sha256"] = "0" * 64; drift["candidate_hash"] = hashlib.sha256(canonical_bytes({k: v for k, v in drift.items() if k != "candidate_hash"})).hexdigest()
            with self.assertRaisesRegex(ContractError, "DATABASE_HASH_DRIFT"): publish_release(ROOT, roots(base), drift)
            unapproved = candidate(source, key="release-4"); unapproved["approved"] = False; unapproved["candidate_hash"] = hashlib.sha256(canonical_bytes({k: v for k, v in unapproved.items() if k != "candidate_hash"})).hexdigest()
            with self.assertRaisesRegex(ContractError, "RELEASE_CANDIDATE_SCHEMA_INVALID"): publish_release(ROOT, roots(base), unapproved)

    def test_010_rolls_back_when_receipt_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); source = base / "runtime/vnext/03_构建过程层/issues/EAS-19/run/1/db/promote.sqlite"; database(source, "v1")
            delivery = base / "runtime/vnext/05_新版本交付层"; delivery.mkdir(parents=True)
            # Receipt target is a directory: the second atomic replacement fails;
            # staged database is removed and no formal database appears.
            (delivery / "formal-release-receipt.json").mkdir()
            with self.assertRaisesRegex(ContractError, "FORMAL_RELEASE_IO_FAILED"): publish_release(ROOT, roots(base), candidate(source))
            self.assertFalse((delivery / "formal.sqlite").exists())


if __name__ == "__main__": unittest.main()
