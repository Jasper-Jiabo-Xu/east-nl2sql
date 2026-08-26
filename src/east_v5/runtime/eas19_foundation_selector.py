"""Production EAS-19 selector for Foundation's local-only parent snapshot.

EAS-19 already owns the generic read-snapshot contract.  This module is the
Foundation adapter: it turns an authenticated 210/220 scope and an exact,
read-only SQLite baseline into *all* feasible parent tuples.  It deliberately
does not select a tuple or generate a business value; that remains 241's job.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, canonical_bytes, sha256

_closure_contract = importlib.import_module("east_v5.agents.220.closure")
validate_foundation_task_package = _closure_contract.validate_foundation_task_package
validate_foundation_closure_task_scope = _closure_contract.validate_foundation_closure_task_scope


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Eas19FoundationSelectorError(ContractError):
    """A Foundation parent-tuple snapshot cannot be safely produced."""


def _fail(code: str) -> None:
    raise Eas19FoundationSelectorError(code)


def _quoted(identifier: str) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        _fail("EAS19_FOUNDATION_SELECTOR_IDENTIFIER_INVALID")
    return '"' + identifier + '"'


def _ref(envelope: dict[str, Any]) -> dict[str, Any]:
    return artifact_ref(envelope)


@dataclass(frozen=True)
class FoundationSnapshotSelection:
    """The two EAS-19 packages and the resolver-universe reference they share."""

    database_snapshot: dict[str, Any]
    generation_context: dict[str, Any]
    resolver_universe_ref: dict[str, Any]


class Eas19FoundationParentSnapshotSelector:
    """Derive complete parent feasible sets from authenticated Foundation input.

    The constructor gets no query text, table name, tuple, mapping override or
    locator from a business caller.  Its caller must have already established
    the runtime root and passed the fixed, hash-verified baseline.
    """

    def __init__(self, baseline_path: Path, baseline_sha256: str, baseline_size_bytes: int) -> None:
        self.baseline_path = Path(baseline_path).resolve()
        self.baseline_sha256 = baseline_sha256
        self.baseline_size_bytes = baseline_size_bytes
        if (not self.baseline_path.is_file() or self.baseline_path.is_symlink()
                or not isinstance(baseline_sha256, str) or len(baseline_sha256) != 64
                or not isinstance(baseline_size_bytes, int) or baseline_size_bytes < 0):
            _fail("EAS19_FOUNDATION_SELECTOR_BASELINE_INVALID")
        observed = self._file_sha(self.baseline_path)
        if observed != baseline_sha256 or self.baseline_path.stat().st_size != baseline_size_bytes:
            _fail("EAS19_FOUNDATION_SELECTOR_BASELINE_DRIFT")

    @staticmethod
    def _file_sha(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _parent_columns(task: dict[str, Any], closure: dict[str, Any]) -> dict[str, set[str]]:
        """Use only closure edges directed from a target record to a parent.

        A relationship whose direction/scope cannot mechanically establish one
        target-side and one existing-parent side is rejected, rather than being
        guessed from names or database ordering.
        """
        target_tables = set(task["payload"]["target_object_types"])
        result: dict[str, set[str]] = {}
        for reference in closure["payload"]["references"]:
            if not isinstance(reference, dict) or reference.get("type") != "cross_table":
                continue
            data = reference.get("data")
            if not isinstance(data, dict) or set(data) != {"from", "to"}:
                _fail("EAS19_FOUNDATION_SELECTOR_CLOSURE_EDGE_INVALID")
            source, target = data["from"], data["to"]
            if not isinstance(source, str) or not isinstance(target, str) or source.count(".") != 1 or target.count(".") != 1:
                _fail("EAS19_FOUNDATION_SELECTOR_CLOSURE_EDGE_INVALID")
            source_table, source_column = source.split(".", 1)
            target_table, target_column = target.split(".", 1)
            if not all(_IDENTIFIER.fullmatch(item) for item in (source_table, source_column, target_table, target_column)):
                _fail("EAS19_FOUNDATION_SELECTOR_CLOSURE_EDGE_INVALID")
            # Typed-graph direction is provider -> consumer.  A parent is
            # therefore the source endpoint when the consumer is a target
            # table; the opposite orientation is a contract drift.
            if target_table in target_tables and source_table not in target_tables:
                result.setdefault(source_table, set()).add(source_column)
            elif source_table in target_tables and target_table not in target_tables:
                _fail("EAS19_FOUNDATION_SELECTOR_CLOSURE_DIRECTION_INVALID")
            elif source_table in target_tables or target_table in target_tables:
                _fail("EAS19_FOUNDATION_SELECTOR_CLOSURE_SCOPE_AMBIGUOUS")
        if not result:
            _fail("EAS19_FOUNDATION_SELECTOR_PARENT_EDGE_MISSING")
        return result

    @staticmethod
    def _primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
        try:
            rows = connection.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
        except sqlite3.Error as exc:
            raise Eas19FoundationSelectorError("EAS19_FOUNDATION_SELECTOR_DDL_UNAVAILABLE") from exc
        columns = [(row[1], row[5]) for row in rows if isinstance(row[1], str)]
        primary = [column for column, index in sorted(columns, key=lambda item: item[1]) if isinstance(index, int) and index > 0]
        if not primary:
            _fail("EAS19_FOUNDATION_SELECTOR_PARENT_KEY_UNPROVEN")
        return primary

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (str, int, float)) or value is None:
            return value
        _fail("EAS19_FOUNDATION_SELECTOR_VALUE_UNSUPPORTED")

    @staticmethod
    def _package(artifact_type: str, artifact_id: str, payload: dict[str, Any], *, task: dict[str, Any], parents: list[dict[str, Any]]) -> dict[str, Any]:
        task_envelope = task["envelope"]
        envelope = {
            "artifact_id": artifact_id, "artifact_type": artifact_type,
            "run_id": task_envelope["run_id"], "qa_id": task_envelope["qa_id"], "version": 1,
            "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
            "supersedes_ref": None, "attempt_no": task_envelope["attempt_no"],
            "producer_id": "EAS-19", "parent_artifact_refs": parents,
            "input_hashes": [item["content_hash"] for item in parents], "status": "candidate",
            "mode": "foundation", "created_at": task_envelope["created_at"],
            "trace_id": task_envelope["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}

    def select(self, task: dict[str, Any], closure: dict[str, Any], *, hierarchy_mapping: dict[str, Any], resolver_universe: dict[str, Any]) -> FoundationSnapshotSelection:
        """Build deterministic EAS-19 packages without choosing a business tuple."""
        validate_foundation_task_package(task)
        validate_foundation_closure_task_scope(task, closure)
        if (not isinstance(hierarchy_mapping, dict)
                or set(hierarchy_mapping) != {"schema_version", "intent_content_hash", "field_mapping", "content_hash"}
                or hierarchy_mapping.get("schema_version") != "foundation-hierarchy-endpoint-mapping/v1"
                or not isinstance(hierarchy_mapping.get("field_mapping"), dict)
                or hierarchy_mapping.get("content_hash") != sha256({key: value for key, value in hierarchy_mapping.items() if key != "content_hash"})):
            _fail("EAS19_FOUNDATION_SELECTOR_HIERARCHY_MAPPING_DRIFT")
        parent_columns = self._parent_columns(task, closure)
        if not isinstance(resolver_universe, dict) or not isinstance(resolver_universe.get("content_sha256"), str):
            _fail("EAS19_FOUNDATION_SELECTOR_RESOLVER_UNIVERSE_INVALID")
        universe_ref = {
            "artifact_id": f"{task['envelope']['artifact_id']}:resolver-universe",
            "version": 1, "content_hash": resolver_universe["content_sha256"],
        }
        records: list[dict[str, Any]] = []
        queries: list[str] = []
        try:
            connection = sqlite3.connect(f"file:{self.baseline_path}?mode=ro&immutable=1", uri=True)
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                _fail("EAS19_FOUNDATION_SELECTOR_READONLY_UNPROVEN")
            for table in sorted(parent_columns):
                key_columns = self._primary_key(connection, table)
                selected = key_columns + sorted(parent_columns[table] - set(key_columns))
                quoted = ", ".join(_quoted(column) for column in selected)
                statement = f"SELECT DISTINCT {quoted} FROM {_quoted(table)}"
                try:
                    rows = connection.execute(statement).fetchall()
                except sqlite3.Error as exc:
                    raise Eas19FoundationSelectorError("EAS19_FOUNDATION_SELECTOR_PARENT_QUERY_FAILED") from exc
                if not rows:
                    _fail("EAS19_FOUNDATION_SELECTOR_PARENT_SET_EMPTY")
                canonical_rows = sorted(
                    ({column: self._json_value(value) for column, value in zip(selected, row)} for row in rows),
                    key=canonical_bytes,
                )
                queries.append(statement)
                for row in canonical_rows:
                    tuple_ref = sha256({"table_id": table, "columns": selected, "tuple": row})
                    records.append({
                        "record_keys": {"table_id": table, "primary_key": tuple_ref,
                                        "reference_keys": [f"{table}.{column}" for column in sorted(parent_columns[table])]},
                        "data": row,
                    })
        except sqlite3.Error as exc:
            raise Eas19FoundationSelectorError("EAS19_FOUNDATION_SELECTOR_BASELINE_OPEN_FAILED") from exc
        finally:
            try:
                connection.close()
            except UnboundLocalError:
                pass
        snapshot_payload = {
            "schema_version": "v5.database-read-snapshot/v1",
            "snapshot_id": f"{task['envelope']['artifact_id']}:foundation-parent-snapshot",
            "base_database_version": self.baseline_sha256,
            "query_time": task["envelope"]["created_at"],
            "query_scope": "foundation-parent-reference-tuples/v1",
            "executed_queries": queries,
            "object_state_records": records,
        }
        snapshot_payload["snapshot_hash"] = sha256(snapshot_payload)
        snapshot = self._package(
            "database_read_snapshot", snapshot_payload["snapshot_id"], snapshot_payload,
            task=task, parents=[_ref(task["envelope"]), _ref(closure["envelope"])],
        )
        parent_refs = [
            {"table_id": item["record_keys"]["table_id"], "record_key": item["record_keys"]["primary_key"]}
            for item in records
        ]
        context_payload = {
            "schema_version": "v5.foundation-generation-context/v1",
            "context_id": f"{task['envelope']['artifact_id']}:foundation-context",
            "foundation_task_ref": _ref(task["envelope"]), "structure_closure_ref": _ref(closure["envelope"]),
            "resolver_universe_ref": universe_ref, "database_snapshot_ref": _ref(snapshot["envelope"]),
            "snapshot_hash": snapshot_payload["snapshot_hash"],
            "hierarchy_refs": task["payload"]["hierarchy_asset_refs"],
            "catalog_refs": task["payload"]["hierarchy_asset_refs"],
            "base_date": task["envelope"]["created_at"][:10],
            "seed": sha256({"task": _ref(task["envelope"]), "closure": _ref(closure["envelope"]), "snapshot": snapshot_payload["snapshot_hash"]}),
            "parent_record_refs": parent_refs, "deterministic_rules": [],
        }
        context = self._package(
            "foundation_generation_context", context_payload["context_id"], context_payload,
            task=task, parents=[_ref(task["envelope"]), _ref(closure["envelope"]), _ref(snapshot["envelope"])],
        )
        return FoundationSnapshotSelection(snapshot, context, universe_ref)
