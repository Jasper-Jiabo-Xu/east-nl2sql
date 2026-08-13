"""Deterministic SQLite boundaries for agents 241, 260 and fixed agent 010.

This module operates only on caller-supplied runtime paths.  It never opens the
repository/reference roots as databases and it deliberately has no API for an
agent to write a formal database directly.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator, ValidationError

from east_v5.governance import ContractError, canonical_bytes, load_json, validate_roots

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PREDICATE_OPS = {"=", "!=", "<", "<=", ">", ">=", "IS NULL", "IS NOT NULL"}
_BATCH_KEYS = {"schema_version", "base_database_version", "base_snapshot_sha256", "operations"}
_OPERATION_KEYS = {"sql", "parameters"}
_CANDIDATE_KEYS = {"schema_version", "candidate_hash", "target_version", "base_version", "idempotency_key", "approved", "database_locator", "database_sha256", "regression_hash", "problem_set_hash"}


def _fail(code: str) -> None:
    raise ContractError(code)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime_path(roots: dict[str, Any], value: Path, *, allow_delivery: bool = False) -> Path:
    _, runtime, _ = validate_roots(roots)
    resolved = value.resolve(strict=False)
    if runtime not in resolved.parents:
        _fail("LOCATOR_OUT_OF_RUNTIME_ROOT")
    delivery = runtime / "vnext" / "05_新版本交付层"
    if not allow_delivery and (resolved == delivery or delivery in resolved.parents):
        _fail("FORMAL_DATABASE_ACCESS_FORBIDDEN")
    return resolved


def _safe_file(roots: dict[str, Any], value: str | Path, *, allow_delivery: bool = False) -> Path:
    path = _runtime_path(roots, Path(value), allow_delivery=allow_delivery)
    if not path.is_file():
        _fail("DATABASE_LOCATOR_MISSING")
    return path


def _identifier(value: Any, code: str = "DATABASE_IDENTIFIER_INVALID") -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _fail(code)
    return value


def _validate_schema(repo_root: Path, schema: str, value: dict[str, Any], code: str) -> None:
    try:
        Draft202012Validator(load_json(repo_root / "contracts" / "db" / schema)).validate(value)
    except ValidationError as exc:
        raise ContractError(code) from exc


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0, isolation_level=None)


def _database_version(path: Path) -> str:
    try:
        with _connect_readonly(path) as connection:
            row = connection.execute("SELECT value FROM east_v5_database_metadata WHERE key='database_version'").fetchone()
    except sqlite3.Error as exc:
        raise ContractError("DATABASE_VERSION_UNAVAILABLE") from exc
    if row is None or not isinstance(row[0], str) or not row[0]:
        _fail("DATABASE_VERSION_UNAVAILABLE")
    return row[0]


def _copy_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".sqlite-copy-", suffix=".sqlite", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with _connect_readonly(source) as source_db, sqlite3.connect(temporary) as target_db:
            source_db.backup(target_db)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except (OSError, sqlite3.Error) as exc:
        temporary.unlink(missing_ok=True)
        raise ContractError("DATABASE_COPY_FAILED") from exc


def _snapshot_payload(path: Path, version: str, original_hash: str) -> dict[str, Any]:
    return {
        "schema_version": "DATABASE-READ-SNAPSHOT/v1",
        "database_version": version,
        "database_sha256": original_hash,
        "snapshot_sha256": _sha256_file(path),
        "created_at": _utcnow(),
        "storage_locator": str(path),
    }


class DatabaseService:
    """Creates isolated snapshots and exposes only structured read queries."""
    def __init__(self, repo_root: Path, roots: dict[str, Any], issue_id: str, run_id: str, attempt: int):
        self.repo_root, self.roots = repo_root.resolve(), roots
        self.issue_id, self.run_id, self.attempt = issue_id, run_id, attempt
        if attempt not in (1, 2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        _, runtime, _ = validate_roots(roots)
        self.directory = runtime / "vnext" / "03_构建过程层" / "issues" / issue_id / run_id / str(attempt) / "db"

    def create_snapshot(self, formal_locator: str | Path, expected_version: str, expected_hash: str) -> dict[str, Any]:
        if not isinstance(expected_version, str) or not expected_version or not isinstance(expected_hash, str) or not _HASH.fullmatch(expected_hash):
            _fail("SNAPSHOT_EXPECTATION_INVALID")
        formal = _safe_file(self.roots, formal_locator, allow_delivery=True)
        if _sha256_file(formal) != expected_hash:
            _fail("DATABASE_HASH_DRIFT")
        if _database_version(formal) != expected_version:
            _fail("DATABASE_VERSION_CONFLICT")
        snapshot = self.directory / "snapshots" / f"{expected_version}-{expected_hash[:16]}.sqlite"
        _copy_sqlite(formal, snapshot)
        # The process owns the snapshot, but it must be opened read-only by 241.
        snapshot.chmod(0o444)
        result = _snapshot_payload(snapshot, expected_version, expected_hash)
        _validate_schema(self.repo_root, "database-snapshot.schema.json", result, "SNAPSHOT_SCHEMA_INVALID")
        return result

    def query(self, snapshot: dict[str, Any], request: dict[str, Any], allowed_scope: dict[str, set[str]], timeout_steps: int = 100_000) -> dict[str, Any]:
        return query_snapshot(self.repo_root, self.roots, snapshot, request, allowed_scope, timeout_steps)

    def destroy_snapshot(self, snapshot: dict[str, Any]) -> None:
        _validate_schema(self.repo_root, "database-snapshot.schema.json", snapshot, "SNAPSHOT_SCHEMA_INVALID")
        path = _safe_file(self.roots, snapshot["storage_locator"])
        if _sha256_file(path) != snapshot["snapshot_sha256"]:
            _fail("SNAPSHOT_HASH_DRIFT")
        path.chmod(0o600)
        path.unlink()


def query_snapshot(repo_root: Path, roots: dict[str, Any], snapshot: dict[str, Any], request: dict[str, Any], allowed_scope: dict[str, set[str]], timeout_steps: int = 100_000) -> dict[str, Any]:
    """Execute a bounded SELECT built from a strict DSL; free SQL is impossible."""
    _validate_schema(repo_root, "database-snapshot.schema.json", snapshot, "SNAPSHOT_SCHEMA_INVALID")
    if set(request) != {"table", "columns", "predicates", "limit"}:
        _fail("UNKNOWN_FIELD:QUERY_REQUEST")
    table = _identifier(request["table"])
    allowed_columns = allowed_scope.get(table)
    if not allowed_columns:
        _fail("QUERY_SCOPE_FORBIDDEN")
    columns = request["columns"]
    if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)):
        _fail("QUERY_COLUMNS_INVALID")
    columns = [_identifier(column) for column in columns]
    if not set(columns).issubset(allowed_columns):
        _fail("QUERY_SCOPE_FORBIDDEN")
    limit = request["limit"]
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000:
        _fail("QUERY_LIMIT_INVALID")
    predicates = request["predicates"]
    if not isinstance(predicates, list) or len(predicates) > 16:
        _fail("QUERY_PREDICATES_INVALID")
    where: list[str] = []
    parameters: list[Any] = []
    for predicate in predicates:
        if not isinstance(predicate, dict) or set(predicate) != {"column", "op", "value"}:
            _fail("QUERY_PREDICATES_INVALID")
        column = _identifier(predicate["column"])
        op = predicate["op"]
        if column not in allowed_columns or op not in _PREDICATE_OPS:
            _fail("QUERY_SCOPE_FORBIDDEN")
        if op in {"IS NULL", "IS NOT NULL"}:
            if predicate["value"] is not None: _fail("QUERY_PREDICATES_INVALID")
            where.append(f'"{column}" {op}')
        else:
            if isinstance(predicate["value"], (list, dict)):
                _fail("QUERY_PREDICATES_INVALID")
            where.append(f'"{column}" {op} ?')
            parameters.append(predicate["value"])
    path = _safe_file(roots, snapshot["storage_locator"])
    if _sha256_file(path) != snapshot["snapshot_sha256"]:
        _fail("SNAPSHOT_HASH_DRIFT")
    selected_columns = ", ".join('"' + column + '"' for column in columns)
    sql = f'SELECT {selected_columns} FROM "{table}"'
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT ?"
    parameters.append(limit)
    steps = 0
    def progress() -> int:
        nonlocal steps
        steps += 1
        return int(steps > timeout_steps)
    try:
        with _connect_readonly(path) as connection:
            connection.set_progress_handler(progress, 1_000)
            rows = [dict(zip(columns, row)) for row in connection.execute(sql, parameters).fetchall()]
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower(): _fail("QUERY_TIMEOUT")
        raise ContractError("QUERY_EXECUTION_FAILED") from exc
    return {"schema_version": "BOUND-DATA-QUERY-RESULT/v1", "snapshot_sha256": snapshot["snapshot_sha256"], "rows": rows, "row_count": len(rows)}


def execute_sandbox_batch(roots: dict[str, Any], snapshot: dict[str, Any], batch: dict[str, Any], sandbox_locator: str | Path) -> dict[str, Any]:
    """Run a prevalidated 260 batch in an independent copy and roll back on failure."""
    if set(batch) != _BATCH_KEYS or batch.get("schema_version") != "DATABASE-SANDBOX-BATCH/v1":
        _fail("SANDBOX_BATCH_SCHEMA_INVALID")
    if batch.get("base_snapshot_sha256") != snapshot.get("snapshot_sha256") or batch.get("base_database_version") != snapshot.get("database_version"):
        _fail("SANDBOX_BASE_CONFLICT")
    operations = batch.get("operations")
    if not isinstance(operations, list) or not operations:
        _fail("SANDBOX_OPERATIONS_INVALID")
    source = _safe_file(roots, snapshot["storage_locator"])
    if _sha256_file(source) != snapshot["snapshot_sha256"]:
        _fail("SNAPSHOT_HASH_DRIFT")
    sandbox = _runtime_path(roots, Path(sandbox_locator))
    if sandbox == source or sandbox.exists(): _fail("SANDBOX_LOCATOR_CONFLICT")
    _copy_sqlite(source, sandbox)
    try:
        sandbox.chmod(0o600)
        with sqlite3.connect(sandbox, isolation_level=None) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            for operation in operations:
                if not isinstance(operation, dict) or set(operation) != _OPERATION_KEYS or not isinstance(operation["sql"], str) or not isinstance(operation["parameters"], list):
                    _fail("SANDBOX_OPERATIONS_INVALID")
                # 260 accepts only fixed/prevalidated DML, never schema/attach/transaction SQL.
                statement = operation["sql"].lstrip().upper()
                if not statement.startswith(("INSERT ", "UPDATE ", "DELETE ")) or ";" in statement.rstrip(";"):
                    _fail("SANDBOX_SQL_FORBIDDEN")
                connection.execute(operation["sql"], operation["parameters"])
            connection.execute("COMMIT")
    except (sqlite3.Error, ContractError) as exc:
        try:
            if sandbox.exists(): sandbox.unlink()
        except OSError:
            pass
        if isinstance(exc, ContractError): raise
        raise ContractError("SANDBOX_TRANSACTION_ROLLED_BACK") from exc
    return {"schema_version": "DATABASE-COPY-REGRESSION/v1", "status": "passed", "base_snapshot_sha256": snapshot["snapshot_sha256"], "sandbox_sha256": _sha256_file(sandbox), "sandbox_locator": str(sandbox), "operation_count": len(operations), "retention": "retain_until_010_or_expiry"}


def destroy_sandbox(roots: dict[str, Any], report: dict[str, Any]) -> None:
    if report.get("schema_version") != "DATABASE-COPY-REGRESSION/v1" or report.get("status") != "passed":
        _fail("SANDBOX_REPORT_INVALID")
    path = _safe_file(roots, report.get("sandbox_locator"))
    if _sha256_file(path) != report.get("sandbox_sha256"):
        _fail("SANDBOX_HASH_DRIFT")
    path.unlink()


@contextmanager
def _release_lock(delivery_root: Path) -> Iterator[None]:
    import fcntl
    delivery_root.mkdir(parents=True, exist_ok=True)
    with (delivery_root / ".formal-release.lock").open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _audit_manifest(candidate: dict[str, Any], formal_hash: str) -> dict[str, Any]:
    result = {"schema_version": "FORMAL-RELEASE-MANIFEST/v1", "candidate_hash": candidate["candidate_hash"], "target_version": candidate["target_version"], "base_version": candidate["base_version"], "database_sha256": formal_hash, "regression_hash": candidate["regression_hash"], "problem_set_hash": candidate["problem_set_hash"], "idempotency_key": candidate["idempotency_key"]}
    result["manifest_hash"] = hashlib.sha256(canonical_bytes(result)).hexdigest()
    return result


def _receipt(candidate: dict[str, Any], formal_hash: str, manifest: dict[str, Any]) -> dict[str, Any]:
    result = {"schema_version": "FORMAL-RELEASE-RECEIPT/v1", "candidate_hash": candidate["candidate_hash"], "target_version": candidate["target_version"], "idempotency_key": candidate["idempotency_key"], "database_sha256": formal_hash, "regression_hash": candidate["regression_hash"], "problem_set_hash": candidate["problem_set_hash"], "manifest_hash": manifest["manifest_hash"], "published_at": _utcnow()}
    result["receipt_hash"] = hashlib.sha256(canonical_bytes(result)).hexdigest()
    return result


def publish_release(repo_root: Path, roots: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """The sole fixed-code formal writer.  It requires an approved tested SQLite copy."""
    if set(candidate) != _CANDIDATE_KEYS:
        _fail("UNKNOWN_FIELD:FORMAL_RELEASE_CANDIDATE")
    _validate_schema(repo_root, "release-candidate.schema.json", candidate, "RELEASE_CANDIDATE_SCHEMA_INVALID")
    if candidate["candidate_hash"] != hashlib.sha256(canonical_bytes({key: candidate[key] for key in candidate if key != "candidate_hash"})).hexdigest():
        _fail("CANDIDATE_HASH_DRIFT")
    source = _safe_file(roots, candidate["database_locator"])
    if _sha256_file(source) != candidate["database_sha256"]:
        _fail("DATABASE_HASH_DRIFT")
    if _database_version(source) != candidate["target_version"]:
        _fail("DATABASE_VERSION_CONFLICT")
    _, runtime, _ = validate_roots(roots)
    delivery = runtime / "vnext" / "05_新版本交付层"
    formal = delivery / "formal.sqlite"
    receipt_path = delivery / "formal-release-receipt.json"
    manifest_path = delivery / "formal-release-manifest.json"
    with _release_lock(delivery):
        if receipt_path.exists():
            if not receipt_path.is_file():
                _fail("FORMAL_RELEASE_IO_FAILED")
            try:
                prior = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ContractError("FORMAL_RELEASE_IO_FAILED") from exc
            if prior.get("idempotency_key") == candidate["idempotency_key"]:
                if prior.get("candidate_hash") != candidate["candidate_hash"]: _fail("IDEMPOTENCY_CONFLICT")
                if not formal.is_file() or _sha256_file(formal) != prior.get("database_sha256"): _fail("FORMAL_STATE_DRIFT")
                if not manifest_path.is_file() or json.loads(manifest_path.read_text(encoding="utf-8")).get("manifest_hash") != prior.get("manifest_hash"):
                    _fail("FORMAL_STATE_DRIFT")
                return prior
            if prior.get("target_version") != candidate["base_version"]:
                _fail("FORMAL_VERSION_CONFLICT")
        elif formal.exists():
            _fail("FORMAL_RECEIPT_MISSING")
        elif candidate["base_version"] != "v0":
            _fail("FORMAL_VERSION_CONFLICT")
        staging = delivery / ".formal-staging.sqlite"
        staging.unlink(missing_ok=True)
        _copy_sqlite(source, staging)
        final_hash = _sha256_file(staging)
        manifest = _audit_manifest(candidate, final_hash)
        receipt = _receipt(candidate, final_hash, manifest)
        fd, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=delivery)
        temporary = Path(temporary_name)
        manifest_fd, temporary_manifest_name = tempfile.mkstemp(prefix=".manifest-", dir=delivery)
        temporary_manifest = Path(temporary_manifest_name)
        originals = {path: path.read_bytes() if path.is_file() else None for path in (formal, receipt_path, manifest_path)}
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical_bytes(receipt)); stream.flush(); os.fsync(stream.fileno())
            with os.fdopen(manifest_fd, "wb") as stream:
                stream.write(canonical_bytes(manifest)); stream.flush(); os.fsync(stream.fileno())
            os.replace(staging, formal)
            os.replace(temporary, receipt_path)
            os.replace(temporary_manifest, manifest_path)
        except OSError as exc:
            staging.unlink(missing_ok=True); temporary.unlink(missing_ok=True); temporary_manifest.unlink(missing_ok=True)
            # A three-file formal release cannot be made multi-file atomic by
            # the filesystem.  Restore the exact pre-call state under the same
            # lock before surfacing the failure.
            for path, original in originals.items():
                if original is None:
                    if path.is_file(): path.unlink()
                else:
                    fd, rollback_name = tempfile.mkstemp(prefix=".rollback-", dir=delivery)
                    rollback = Path(rollback_name)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(original); stream.flush(); os.fsync(stream.fileno())
                    os.replace(rollback, path)
            raise ContractError("FORMAL_RELEASE_IO_FAILED") from exc
    return receipt
