from __future__ import annotations

import copy
import math
import re
from collections import defaultdict
from typing import Any, Collection

from east_v5.governance import ContractError, sha256

COMPILER_VERSION = "east-foundation-insert-compiler/v1"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_PACKAGE_KEYS = {
    "schema_version", "mode", "base_database_version",
    "constraint_asset_version", "graph_version", "records",
}
_RECORD_KEYS = {"record_id", "table", "values", "depends_on"}


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ContractError("FOUNDATION_IDENTIFIER_INVALID")
    return value


def _scalar(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("FOUNDATION_VALUE_NOT_BINDABLE")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ContractError("FOUNDATION_VALUE_NOT_BINDABLE")


def _ordered_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {}
    for record in records:
        if set(record) != _RECORD_KEYS:
            raise ContractError("UNKNOWN_FIELD:foundation_record")
        record_id = _identifier(record["record_id"])
        if record_id in by_id:
            raise ContractError("FOUNDATION_RECORD_DUPLICATE")
        by_id[record_id] = record
        dependencies = record["depends_on"]
        if not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies):
            raise ContractError("FOUNDATION_DEPENDENCY_INVALID")
        indegree[record_id] = len(dependencies)
    for record_id, record in by_id.items():
        for dependency in record["depends_on"]:
            _identifier(dependency)
            if dependency not in by_id:
                raise ContractError("FOUNDATION_DEPENDENCY_MISSING")
            children[dependency].append(record_id)
    ready = sorted(record_id for record_id, degree in indegree.items() if degree == 0)
    ordered: list[dict[str, Any]] = []
    while ready:
        record_id = ready.pop(0)
        ordered.append(by_id[record_id])
        for child in sorted(children[record_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(ordered) != len(records):
        raise ContractError("FOUNDATION_DEPENDENCY_CYCLE")
    return ordered


def compile_insert_batch(package: dict[str, Any], event_owned_tables: Collection[str]) -> dict[str, Any]:
    """Compile verified Foundation rows to parameterized INSERT operations; never execute SQL."""
    source = copy.deepcopy(package)
    if set(source) != _PACKAGE_KEYS:
        raise ContractError("UNKNOWN_FIELD:foundation_package")
    expected = {
        "schema_version": "v5.foundation-verified-data/v1",
        "mode": "foundation",
        "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0",
    }
    if any(source.get(key) != value for key, value in expected.items()):
        raise ContractError("FOUNDATION_CONTRACT_VERSION_MISMATCH")
    if not isinstance(source["base_database_version"], str) or not source["base_database_version"]:
        raise ContractError("FOUNDATION_DATABASE_VERSION_INVALID")
    if not isinstance(source["records"], list) or not source["records"]:
        raise ContractError("FOUNDATION_RECORDS_EMPTY")
    prohibited = {_identifier(table) for table in event_owned_tables}
    operations: list[dict[str, Any]] = []
    for index, record in enumerate(_ordered_records(source["records"]), start=1):
        table = _identifier(record["table"])
        if table in prohibited:
            raise ContractError("FOUNDATION_EVENT_OWNED_REJECTED")
        values = record["values"]
        if not isinstance(values, dict) or not values:
            raise ContractError("FOUNDATION_VALUES_EMPTY")
        columns = sorted(_identifier(column) for column in values)
        quoted_table = f'"{table}"'
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        operations.append({
            "index": index,
            "record_id": record["record_id"],
            "table": table,
            "columns": columns,
            "sql": f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders});",
            "parameters": [_scalar(values[column]) for column in columns],
            "depends_on": sorted(record["depends_on"]),
        })
    result: dict[str, Any] = {
        "schema_version": "v5.foundation-write-batch/v1",
        "compiler_version": COMPILER_VERSION,
        "base_database_version": source["base_database_version"],
        "source_sha256": sha256(source),
        "transaction": {"begin": "IMMEDIATE", "atomic": True, "rollback_on_error": True},
        "operations": operations,
    }
    result["content_sha256"] = sha256(result)
    return result
