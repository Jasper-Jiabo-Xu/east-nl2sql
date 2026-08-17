"""EAS-16 read-only constraint asset contract.

This module deliberately knows no legacy registry and never creates, alters, or
copies an asset.  Runtime manifests locate approved immutable assets only under
the local runtime data plane; their version and every payload hash are verified
before a query is made.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import base64
import secrets
import threading
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, ValidationError

from east_v5.governance import ContractError, load_json, validate_roots


CONTROL_PATH = Path("contracts/constraint_assets/approved-assets.json")
RUNTIME_SCHEMA_PATH = Path("contracts/constraint_assets/runtime-manifest.schema.json")
RESULT_SCHEMA_PATH = Path("contracts/constraint_assets/query-result.schema.json")
RECONCILIATION_PATH = Path("contracts/constraint_assets/reconciliation-manifest.json")
RECONCILIATION_SCHEMA_PATH = Path("contracts/constraint_assets/reconciliation-manifest.schema.json")
RECONCILIATION_SCHEMA_VERSION = "v5.constraint-assets-reconciliation/v1"
RESULT_SCHEMA_VERSION = "v5.constraint-asset-query-result/v2"
_SHA = __import__("re").compile(r"^[0-9a-f]{64}$")

# CA-V0.2.0 frozen input lock (Sol freeze, 2026-08-13).  These are the only
# authoritative payload hashes for the reconciliation manifest; any drift from
# them is a rejection, not an auto-fix.
FROZEN_CA_V020 = {
    "content_hash": "6cb0f385adae4e3e9e24558dc432132b43c437e8c2db5ce329c46bf68e8c4188",
    "single_field": "f137be03a3814428b76507373d2032705dcacb1ff804adbddd96e397da5c1d6f",
    "local_codes": "3f9f05b47e6f4bfaada37abe06e17286228bf351edbd0a34ade2b09a2d5e82bd",
    "national_codes": "0932aecbdb4ed263ff5d8887cf1f833ecf76b220aec4fda42613df0b905de274",
}


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
    if set(indexed) != {"CA-V0.2.0", "CA-V0.3.0", "TRG-V1.0.0"}:
        _fail("ASSET_CONTROL_DRIFT")
    if control_path == CONTROL_PATH:
        foundation = indexed["CA-V0.2.0"]
        if foundation.get("artifact_id") != "CA-FOUNDATION-20260805-002" or foundation.get("content_hash") != FROZEN_CA_V020["content_hash"] or foundation.get("required_payload") != {"single_field": FROZEN_CA_V020["single_field"]}:
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


def validate_reconciliation_manifest(repo_root: Path, *, path: Path = RECONCILIATION_PATH) -> dict[str, Any]:
    """Validate the CA-V0.2 reconciliation manifest and return its frozen registration."""
    try:
        manifest = load_json(repo_root / path)
        schema = load_json(repo_root / RECONCILIATION_SCHEMA_PATH)
        Draft202012Validator(schema).validate(manifest)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ContractError("ASSET_RECONCILIATION_MANIFEST_INVALID") from exc
    if manifest["schema_version"] != RECONCILIATION_SCHEMA_VERSION:
        _fail("ASSET_RECONCILIATION_MANIFEST_INVALID")
    if manifest["human_override"]["effective_role"] != "single_field_and_code_tables":
        _fail("ASSET_RECONCILIATION_OVERRIDE_DRIFT")
    roles = {record["role"] for record in manifest["registered_files"]}
    if roles != {"single_field", "local_codes", "national_codes"}:
        _fail("ASSET_RECONCILIATION_ROLE_DRIFT")
    if manifest["asset"]["content_hash"] != FROZEN_CA_V020["content_hash"]:
        _fail("ASSET_RECONCILIATION_HASH_DRIFT")
    for record in manifest["registered_files"]:
        if record["sha256"] != FROZEN_CA_V020[record["role"]]:
            _fail("ASSET_RECONCILIATION_HASH_DRIFT")
    return manifest


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


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _normalized_json_text(value: Any) -> str:
    try:
        return _canonical(json.loads(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ContractError("ASSET_RECORD_NORMALIZATION_FAILED") from exc


def _mac(key: bytes, value: Any) -> str:
    return hmac.new(key, _canonical(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _decode_cursor(cursor: str, key: bytes) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode((cursor + padding).encode("ascii")))
        signature = decoded.pop("signature")
    except (ValueError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
        raise ContractError("ASSET_QUERY_CURSOR_INVALID") from exc
    if not isinstance(decoded, dict) or not isinstance(signature, str) or not hmac.compare_digest(signature, _mac(key, decoded)):
        _fail("ASSET_QUERY_CURSOR_INVALID")
    return decoded


def _encode_cursor(payload: dict[str, Any], key: bytes) -> str:
    signed = dict(payload)
    signed["signature"] = _mac(key, payload)
    return base64.urlsafe_b64encode(_canonical(signed).encode("utf-8")).decode("ascii").rstrip("=")


def _result(
    entry: dict[str, Any],
    records: Iterable[dict[str, Any]],
    *,
    query_method: str = "asset_ref",
    table_code: str = "asset_ref",
    page_size: int = 1,
    total: int = 0,
    cursor: str | None = None,
    next_cursor: str | None = None,
    service_proof: str = "0" * 64,
) -> dict[str, Any]:
    materialized = list(records)
    source = {key: entry[key] for key in ("artifact_type", "artifact_id", "asset_version", "content_hash")}
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        **source,
        "query_method": query_method,
        "table_code": table_code,
        "query_parameters": {"page_size": page_size},
        "total": total,
        "returned_count": len(materialized),
        "complete": next_cursor is None,
        "cursor": cursor,
        "next_cursor": next_cursor,
        "source_hash": _digest(source),
        "records_hash": _digest(materialized),
        "receipt_hash": "0" * 64,
        "service_proof": service_proof,
        "records": materialized,
    }
    result["receipt_hash"] = _digest({key: value for key, value in result.items() if key not in {"receipt_hash", "service_proof"}})
    try:
        Draft202012Validator(load_json(Path(__file__).resolve().parents[3] / RESULT_SCHEMA_PATH)).validate(result)
    except ValidationError as exc:
        raise ContractError("ASSET_QUERY_RESULT_INVALID") from exc
    return result


def validate_query_receipt_contract(
    result: dict[str, Any],
    *,
    query_method: str | None = None,
    table_code: str | None = None,
) -> dict[str, Any]:
    """Validate public receipt shape and hashes, without proving service origin.

    This is deliberately only a fixture/schema helper.  Production consumers
    must use :func:`consume_complete_table`, which binds a live service instance
    and verifies its private capability proof plus the full cursor chain.
    """
    try:
        Draft202012Validator(load_json(Path(__file__).resolve().parents[3] / RESULT_SCHEMA_PATH)).validate(result)
    except (ValidationError, OSError, json.JSONDecodeError) as exc:
        raise ContractError("ASSET_QUERY_RECEIPT_INVALID") from exc
    if result["returned_count"] != len(result["records"]) or result["records_hash"] != _digest(result["records"]):
        _fail("ASSET_QUERY_RECEIPT_INVALID")
    if result["complete"] != (result["next_cursor"] is None):
        _fail("ASSET_QUERY_RECEIPT_INVALID")
    if result["receipt_hash"] != _digest({key: value for key, value in result.items() if key not in {"receipt_hash", "service_proof"}}):
        _fail("ASSET_QUERY_RECEIPT_INVALID")
    source = {key: result[key] for key in ("artifact_type", "artifact_id", "asset_version", "content_hash")}
    if result["source_hash"] != _digest(source):
        _fail("ASSET_QUERY_RECEIPT_INVALID")
    if query_method is not None and result["query_method"] != query_method:
        _fail("ASSET_QUERY_RECEIPT_METHOD_MISMATCH")
    if table_code is not None and result["table_code"] != table_code:
        _fail("ASSET_QUERY_RECEIPT_TABLE_MISMATCH")
    return result


class ConstraintAssetService:
    """A manifest-bound, parameterized query interface for 000/220 consumers."""

    def __init__(self, repo_root: Path, roots: dict[str, Any], manifest_path: Path, *, control_path: Path = CONTROL_PATH):
        self.repo_root = repo_root.resolve()
        self.roots = roots
        self.manifest_path = manifest_path
        self.control_path = control_path
        # This private, process-local capability is intentionally absent from
        # every receipt and cursor.  Public SHA-256 values aid auditability but
        # are not authority proofs; only this live, validated service can issue
        # or continue a query chain.
        self._capability = secrets.token_bytes(32)
        self._cursor_lock = threading.Lock()
        self._cursor_sessions: set[str] = set()
        self._consumed_cursors: set[str] = set()
        self.entries = validate_runtime_manifest(self.repo_root, roots, manifest_path, control_path=control_path)
        self.control = _control_index(self.repo_root, control_path)
        validate_reconciliation_manifest(self.repo_root)
        _, self.runtime_root, _ = validate_roots(roots)
        self.ca = self.entries["CA-V0.3.0"]
        self.single_field = self.entries["CA-V0.2.0"]
        self._verify_sources()

    def _verify_sources(self) -> None:
        """Re-verify immutable sources before each authoritative read.

        The manifest is not a cache: a post-construction payload replacement must
        be rejected before it can change an otherwise valid pagination receipt.
        """
        self.entries = validate_runtime_manifest(self.repo_root, self.roots, self.manifest_path, control_path=self.control_path)
        self.ca = self.entries["CA-V0.3.0"]
        self.single_field = self.entries["CA-V0.2.0"]
        _validate_ca_database(_runtime_file(self.runtime_root, self.ca["payload"]["sqlite"]["locator"]), self.control["CA-V0.3.0"])
        _validate_ca_database(_runtime_file(self.runtime_root, self.single_field["payload"]["single_field"]["locator"]), self.control["CA-V0.2.0"])

    def reconciliation_manifest(self) -> dict[str, Any]:
        return validate_reconciliation_manifest(self.repo_root)

    def asset_ref(self, asset_version: str) -> dict[str, Any]:
        if asset_version not in self.entries:
            _fail("ASSET_VERSION_UNSUPPORTED")
        return self._issue_receipt(_result(self.entries[asset_version], [], total=0))

    def _issue_receipt(self, result: dict[str, Any]) -> dict[str, Any]:
        result["service_proof"] = _mac(self._capability, {key: value for key, value in result.items() if key != "service_proof"})
        return result

    def _verify_service_receipt(self, result: dict[str, Any], *, query_method: str, table_code: str) -> dict[str, Any]:
        validate_query_receipt_contract(result, query_method=query_method, table_code=table_code)
        source = {key: result[key] for key in ("artifact_type", "artifact_id", "asset_version", "content_hash")}
        entry = self.entries.get(result["asset_version"])
        if entry is None or source != {key: entry[key] for key in source}:
            _fail("ASSET_QUERY_RECEIPT_SOURCE_MISMATCH")
        expected = _mac(self._capability, {key: value for key, value in result.items() if key != "service_proof"})
        if not hmac.compare_digest(result["service_proof"], expected):
            _fail("ASSET_QUERY_RECEIPT_ORIGIN_INVALID")
        return result

    @staticmethod
    def _request(table_code: str, limit: int, cursor: str | None) -> None:
        if not isinstance(table_code, str) or not table_code or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            _fail("ASSET_QUERY_INVALID")
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            _fail("ASSET_QUERY_INVALID")

    def _page(self, entry: dict[str, Any], method: str, table_code: str, records: list[dict[str, Any]], limit: int, cursor: str | None) -> dict[str, Any]:
        binding = _digest({"query_method": method, "table_code": table_code, "page_size": limit, "source": {key: entry[key] for key in ("artifact_id", "asset_version", "content_hash")}})
        offset = 0
        session_id: str | None = None
        if cursor is not None:
            with self._cursor_lock:
                if cursor in self._consumed_cursors:
                    _fail("ASSET_QUERY_CURSOR_INVALID")
                decoded = _decode_cursor(cursor, self._capability)
                if set(decoded) != {"binding", "offset", "total", "session_id"} or decoded["binding"] != binding or not isinstance(decoded["offset"], int) or not isinstance(decoded["total"], int) or not isinstance(decoded["session_id"], str) or decoded["session_id"] not in self._cursor_sessions:
                    _fail("ASSET_QUERY_CURSOR_INVALID")
                if decoded["total"] != len(records):
                    _fail("ASSET_QUERY_CURSOR_STALE")
                # A valid capability is still single-use.  Mark it atomically
                # before returning a page so a concurrent replay cannot win.
                self._consumed_cursors.add(cursor)
                offset, session_id = decoded["offset"], decoded["session_id"]
        if offset < 0 or offset >= len(records) and not (offset == 0 and not records):
            _fail("ASSET_QUERY_CURSOR_INVALID")
        page = records[offset:offset + limit]
        next_cursor = None
        if offset + len(page) < len(records):
            if session_id is None:
                session_id = secrets.token_urlsafe(24)
                with self._cursor_lock:
                    self._cursor_sessions.add(session_id)
            next_cursor = _encode_cursor({"binding": binding, "offset": offset + len(page), "total": len(records), "session_id": session_id}, self._capability)
        return self._verify_service_receipt(self._issue_receipt(_result(entry, page, query_method=method, table_code=table_code, page_size=limit, total=len(records), cursor=cursor, next_cursor=next_cursor)), query_method=method, table_code=table_code)

    def complete_table_query(self, query_method: str, table_code: str, *, page_size: int = 100) -> dict[str, Any]:
        """Read a complete, contiguous query through this live service only."""
        queries = {
            "constraints_for_table": self.constraints_for_table,
            "field_rules_for_table": self.field_rules_for_table,
            "graph_edges_for_table": self.graph_edges_for_table,
        }
        query = queries.get(query_method)
        if query is None:
            _fail("ASSET_QUERY_METHOD_UNSUPPORTED")
        expected_cursor: str | None = None
        expected_total: int | None = None
        expected_source: dict[str, Any] | None = None
        records: list[dict[str, Any]] = []
        seen_records: set[str] = set()
        page_receipt_hashes: list[str] = []
        while True:
            page = query(table_code, limit=page_size, cursor=expected_cursor)
            self._verify_service_receipt(page, query_method=query_method, table_code=table_code)
            if page["cursor"] != expected_cursor:
                _fail("ASSET_QUERY_CHAIN_GAP")
            source = {key: page[key] for key in ("artifact_type", "artifact_id", "asset_version", "content_hash")}
            if expected_total is None:
                expected_total, expected_source = page["total"], source
            elif page["total"] != expected_total or source != expected_source:
                _fail("ASSET_QUERY_CHAIN_DRIFT")
            for record in page["records"]:
                fingerprint = _digest(record)
                if fingerprint in seen_records:
                    _fail("ASSET_QUERY_CHAIN_DUPLICATE")
                seen_records.add(fingerprint)
                records.append(record)
            page_receipt_hashes.append(page["receipt_hash"])
            if page["complete"]:
                if page["next_cursor"] is not None or len(records) != expected_total:
                    _fail("ASSET_QUERY_CHAIN_INCOMPLETE")
                return {
                    "query_method": query_method,
                    "table_code": table_code,
                    "total": expected_total,
                    "returned_count": len(records),
                    "complete": True,
                    "source": expected_source,
                    "records": records,
                    "page_receipt_hashes": page_receipt_hashes,
                }
            if not page["next_cursor"] or page["next_cursor"] == expected_cursor:
                _fail("ASSET_QUERY_CHAIN_GAP")
            expected_cursor = page["next_cursor"]

    def constraints_for_table(self, table_code: str, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        self._request(table_code, limit, cursor)
        self._verify_sources()
        path = _runtime_file(self.runtime_root, self.ca["payload"]["sqlite"]["locator"])
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                """SELECT DISTINCT c.constraint_id, c.constraint_item_type, c.scope, c.structured_expression_json,
                          c.evidence_refs_json, c.content_sha256
                     FROM multifield_constraint AS c
                     JOIN multifield_constraint_field AS f ON f.constraint_id = c.constraint_id
                     JOIN field_master AS m ON m.endpoint = f.field_ref
                    WHERE m.table_code = ? AND c.approval_status = 'APPROVED'
                    ORDER BY c.constraint_id""",
                (table_code,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError("ASSET_SQLITE_QUERY_FAILED") from exc
        finally:
            if "connection" in locals(): connection.close()
        records = []
        for row in rows:
            record = dict(row)
            record["structured_expression_json"] = _normalized_json_text(record["structured_expression_json"])
            record["evidence_refs_json"] = _normalized_json_text(record["evidence_refs_json"])
            record["canonical_rule_hash"] = _digest({key: value for key, value in record.items() if key != "content_sha256"})
            records.append(record)
        return self._page(self.ca, "constraints_for_table", table_code, records, limit, cursor)

    def field_rules_for_table(self, table_code: str, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        self._request(table_code, limit, cursor)
        self._verify_sources()
        path = _runtime_file(self.runtime_root, self.single_field["payload"]["single_field"]["locator"])
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                """SELECT f.field_id, f.table_code, f.field_code, s.constraint_item_type,
                          s.value_json, s.evidence_refs_json, s.review_status
                     FROM single_field_constraints AS s
                     JOIN field_master AS f ON f.field_id = s.field_id
                    WHERE f.table_code = ? AND s.review_status = 'APPROVED'
                    ORDER BY f.field_code, s.constraint_item_type, s.id""",
                (table_code,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError("ASSET_SQLITE_QUERY_FAILED") from exc
        finally:
            if "connection" in locals(): connection.close()
        records = []
        for row in rows:
            record = dict(row)
            record["value_json"] = _normalized_json_text(record["value_json"])
            record["evidence_refs_json"] = _normalized_json_text(record["evidence_refs_json"])
            record["canonical_rule_hash"] = _digest(record)
            records.append(record)
        return self._page(self.single_field, "field_rules_for_table", table_code, records, limit, cursor)

    def graph_edges_for_table(self, table_code: str, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        self._request(table_code, limit, cursor)
        self._verify_sources()
        graph = self.entries["TRG-V1.0.0"]
        path = _runtime_file(self.runtime_root, graph["payload"]["edges"]["locator"])
        records: list[dict[str, Any]] = []
        try:
            with path.open(encoding="utf-8") as source:
                for line in source:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict) or not all(isinstance(record.get(key), str) and record[key] for key in ("provider_table_code", "consumer_table_code", "edge_type")):
                        _fail("ASSET_GRAPH_RECORD_INVALID")
                    if table_code in {record["provider_table_code"], record["consumer_table_code"]}:
                        normalized = json.loads(_canonical(record))
                        normalized["canonical_edge_hash"] = _digest(normalized)
                        records.append(normalized)
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("ASSET_GRAPH_QUERY_FAILED") from exc
        records.sort(key=lambda record: (record["provider_table_code"], record["consumer_table_code"], record["edge_type"], record["canonical_edge_hash"]))
        return self._page(graph, "graph_edges_for_table", table_code, records, limit, cursor)


def consume_complete_table(service: ConstraintAssetService, query_method: str, table_code: str, *, page_size: int = 100) -> dict[str, Any]:
    """Production-only consumer boundary: no Protocol or fixture may stand in."""
    if type(service) is not ConstraintAssetService:
        _fail("ASSET_QUERY_SERVICE_REQUIRED")
    return service.complete_table_query(query_method, table_code, page_size=page_size)
