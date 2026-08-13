"""EAS-16 read-only constraint asset contract.

This module deliberately knows no legacy registry and never creates, alters, or
copies an asset.  Runtime manifests locate approved immutable assets only under
the local runtime data plane; their version and every payload hash are verified
before a query is made.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, ValidationError

from east_v5.governance import ContractError, load_json, validate_roots


CONTROL_PATH = Path("contracts/constraint_assets/approved-assets.json")
RUNTIME_SCHEMA_PATH = Path("contracts/constraint_assets/runtime-manifest.schema.json")
RESULT_SCHEMA_PATH = Path("contracts/constraint_assets/query-result.schema.json")
RESULT_SCHEMA_VERSION = "v5.constraint-asset-query-result/v1"
_SHA = __import__("re").compile(r"^[0-9a-f]{64}$")


def _fail(code: str) -> None:
    raise ContractError(code)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_file(runtime_root: Path, locator: Any) -> Path:
    if not isinstance(locator, str) or not locator:
        _fail("ASSET_LOCATOR_INVALID")
    candidate = Path(locator)
    if not candidate.is_absolute():
        _fail("ASSET_LOCATOR_INVALID")
    resolved = candidate.resolve(strict=False)
    if runtime_root not in resolved.parents or not resolved.is_file():
        _fail("ASSET_LOCATOR_OUT_OF_RUNTIME")
    return resolved


def _control_index(repo_root: Path, control_path: Path = CONTROL_PATH) -> dict[str, dict[str, Any]]:
    control = load_json(repo_root / control_path)
    if set(control) != {"schema_version", "assets"} or control["schema_version"] != "v5.constraint-assets-control/v1":
        _fail("ASSET_CONTROL_DRIFT")
    records = control["assets"]
    if not isinstance(records, list):
        _fail("ASSET_CONTROL_DRIFT")
    indexed = {record.get("asset_version"): record for record in records if isinstance(record, dict)}
    if set(indexed) != {"CA-V0.3.0", "TRG-V1.0.0"}:
        _fail("ASSET_CONTROL_DRIFT")
    return indexed


def _runtime_entries(repo_root: Path, roots: dict[str, Any], manifest_path: Path) -> dict[str, dict[str, Any]]:
    _, runtime_root, _ = validate_roots(roots)
    path = _runtime_file(runtime_root, str(manifest_path))
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        schema = load_json(repo_root / RUNTIME_SCHEMA_PATH)
        Draft202012Validator(schema).validate(manifest)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ContractError("ASSET_RUNTIME_MANIFEST_INVALID") from exc
    entries = {entry["asset_version"]: entry for entry in manifest["assets"]}
    if len(entries) != len(manifest["assets"]):
        _fail("ASSET_RUNTIME_MANIFEST_DUPLICATE")
    return entries


def validate_runtime_manifest(repo_root: Path, roots: dict[str, Any], manifest_path: Path, *, control_path: Path = CONTROL_PATH) -> dict[str, dict[str, Any]]:
    """Validate approved assets before use and return immutable runtime entries."""
    control, entries = _control_index(repo_root, control_path), _runtime_entries(repo_root, roots, manifest_path)
    _, runtime_root, _ = validate_roots(roots)
    if set(entries) != set(control):
        _fail("ASSET_VERSION_UNSUPPORTED")
    for version, expected in control.items():
        actual = entries[version]
        for key in ("artifact_id", "artifact_type", "asset_version", "content_hash"):
            if actual[key] != expected[key]:
                _fail("ASSET_VERSION_OR_CONTENT_HASH_DRIFT")
        if set(actual["payload"]) != set(expected["required_payload"]):
            _fail("ASSET_PAYLOAD_SET_DRIFT")
        for role, expected_hash in expected["required_payload"].items():
            item = actual["payload"][role]
            if item["sha256"] != expected_hash or not _SHA.fullmatch(item["sha256"]):
                _fail("ASSET_PAYLOAD_HASH_DRIFT")
            path = _runtime_file(runtime_root, item["locator"])
            if _hash(path) != item["sha256"]:
                _fail("ASSET_PAYLOAD_HASH_DRIFT")
    return entries


def _sqlite_objects(connection: sqlite3.Connection) -> tuple[set[str], set[str]]:
    objects = connection.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return ({name for kind, name in objects if kind == "table"}, {name for kind, name in objects if kind == "view"})


def _validate_ca_database(path: Path, expected: dict[str, Any]) -> None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                _fail("ASSET_SQLITE_READONLY_INVALID")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                _fail("ASSET_SQLITE_INTEGRITY_FAILED")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                _fail("ASSET_SQLITE_FOREIGN_KEY_FAILED")
            tables, views = _sqlite_objects(connection)
            if not set(expected["required_sqlite_tables"]).issubset(tables) or not set(expected["required_sqlite_views"]).issubset(views):
                _fail("ASSET_SQLITE_SCHEMA_DRIFT")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ContractError("ASSET_SQLITE_INVALID") from exc


def _result(entry: dict[str, Any], records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    result = {"schema_version": RESULT_SCHEMA_VERSION, "artifact_type": entry["artifact_type"], "artifact_id": entry["artifact_id"], "asset_version": entry["asset_version"], "content_hash": entry["content_hash"], "records": list(records)}
    try:
        Draft202012Validator(load_json(Path(__file__).resolve().parents[3] / RESULT_SCHEMA_PATH)).validate(result)
    except ValidationError as exc:
        raise ContractError("ASSET_QUERY_RESULT_INVALID") from exc
    return result


class ConstraintAssetService:
    """A manifest-bound, parameterized query interface for 000/220 consumers."""

    def __init__(self, repo_root: Path, roots: dict[str, Any], manifest_path: Path, *, control_path: Path = CONTROL_PATH):
        self.repo_root = repo_root.resolve()
        self.roots = roots
        self.entries = validate_runtime_manifest(self.repo_root, roots, manifest_path, control_path=control_path)
        self.control = _control_index(self.repo_root, control_path)
        _, self.runtime_root, _ = validate_roots(roots)
        self.ca = self.entries["CA-V0.3.0"]
        _validate_ca_database(_runtime_file(self.runtime_root, self.ca["payload"]["sqlite"]["locator"]), self.control["CA-V0.3.0"])

    def asset_ref(self, asset_version: str) -> dict[str, Any]:
        if asset_version not in self.entries:
            _fail("ASSET_VERSION_UNSUPPORTED")
        return _result(self.entries[asset_version], [])

    def constraints_for_table(self, table_code: str, *, limit: int = 100) -> dict[str, Any]:
        if not isinstance(table_code, str) or not table_code or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            _fail("ASSET_QUERY_INVALID")
        path = _runtime_file(self.runtime_root, self.ca["payload"]["sqlite"]["locator"])
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                """SELECT c.constraint_id, c.constraint_item_type, c.scope, c.structured_expression_json,
                          c.evidence_refs_json, c.content_sha256
                     FROM multifield_constraint AS c
                     JOIN multifield_constraint_field AS f ON f.constraint_id = c.constraint_id
                     JOIN field_master AS m ON m.endpoint = f.field_ref
                    WHERE m.table_code = ? AND c.approval_status = 'APPROVED'
                    ORDER BY c.constraint_id LIMIT ?""",
                (table_code, limit),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError("ASSET_SQLITE_QUERY_FAILED") from exc
        finally:
            if "connection" in locals(): connection.close()
        return _result(self.ca, [dict(row) for row in rows])

    def graph_edges_for_table(self, table_code: str, *, limit: int = 100) -> dict[str, Any]:
        if not isinstance(table_code, str) or not table_code or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            _fail("ASSET_QUERY_INVALID")
        graph = self.entries["TRG-V1.0.0"]
        path = _runtime_file(self.runtime_root, graph["payload"]["edges"]["locator"])
        records: list[dict[str, Any]] = []
        try:
            with path.open(encoding="utf-8") as source:
                for line in source:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
                    if table_code in encoded:
                        records.append(record)
                        if len(records) == limit: break
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("ASSET_GRAPH_QUERY_FAILED") from exc
        return _result(graph, records)
