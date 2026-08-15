"""Read-only query executor for Agent 000: 资产检索agent.

Executes safety-gate-approved SQL queries against the frozen constraint
asset SQLite database. All queries are read-only, timed, and traced.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from east_v5.agents.east_000.safety_gate import (
    check_sql_safety,
    SafetyResult,
    MAX_LIMIT,
)
from east_v5.governance import ContractError


@dataclass(frozen=True)
class QueryResult:
    """Result of a single SQL query execution."""
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    elapsed_ms: float = 0.0
    exception: str | None = None


@dataclass(frozen=True)
class ExecutedQueryRecord:
    """Audit record for a query that passed/failed the safety gate."""
    sql: str
    query_parameters: tuple
    safety_check_result: str  # "pass" or "fail"


@dataclass(frozen=True)
class TraceEntry:
    """Trace entry for query execution logging."""
    sql: str
    elapsed_ms: float
    row_count: int
    exception: str | None


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file for asset verification."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_asset_hash(sqlite_path: Path, expected_sha256: str) -> None:
    """Verify constraint asset SQLite file hash matches expected value."""
    actual = compute_file_sha256(sqlite_path)
    if actual != expected_sha256:
        raise ContractError(f"ASSET_HASH_MISMATCH:expected={expected_sha256[:16]}...got={actual[:16]}...")


def execute_query(
    sqlite_path: Path,
    sql: str,
    params: tuple = (),
) -> QueryResult:
    """Execute a read-only SQL query against the constraint asset SQLite.

    Returns QueryResult with rows, row_count, elapsed_ms, and exception.
    """
    start = time.monotonic()
    rows: list[dict[str, Any]] = []
    exception: str | None = None

    try:
        conn = sqlite3.connect(str(sqlite_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        for row in cur.fetchall():
            rows.append(dict(row))
        conn.close()
    except Exception as exc:
        exception = str(exc)

    elapsed_ms = (time.monotonic() - start) * 1000
    return QueryResult(rows=rows, row_count=len(rows), elapsed_ms=elapsed_ms, exception=exception)


def execute_safe_query(
    sqlite_path: Path,
    sql: str,
    params: tuple = (),
    max_rows: int = MAX_LIMIT,
) -> tuple[QueryResult, ExecutedQueryRecord, TraceEntry]:
    """Execute a query only after it passes the safety gate.

    Returns (QueryResult, ExecutedQueryRecord, TraceEntry).
    If the safety gate rejects, returns empty result with fail record.
    """
    # Safety gate check
    safety_outcome = check_sql_safety(sql, max_rows)

    if safety_outcome.result == SafetyResult.FAIL:
        record = ExecutedQueryRecord(
            sql=sql,
            query_parameters=params,
            safety_check_result="fail",
        )
        trace = TraceEntry(sql=sql, elapsed_ms=0, row_count=0, exception="; ".join(safety_outcome.rejected_reasons))
        return QueryResult(), record, trace

    # Execute the approved query
    result = execute_query(sqlite_path, sql, params)

    record = ExecutedQueryRecord(
        sql=sql,
        query_parameters=params,
        safety_check_result="pass",
    )
    trace = TraceEntry(
        sql=sql,
        elapsed_ms=result.elapsed_ms,
        row_count=result.row_count,
        exception=result.exception,
    )

    return result, record, trace
