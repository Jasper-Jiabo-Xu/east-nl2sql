"""Asset retrieval extractor for Agent 000: 资产检索agent.

Main entry point: receives CONSTRAINT-QUERY-REQUEST, decomposes the
query plan via LLM, validates each candidate SQL through the safety gate,
executes approved queries against frozen constraint asset SQLite, and
assembles the CONSTRAINT-ASSET-PACKAGE result.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.agents.east_000.safety_gate import (
    check_sql_safety,
    validate_request,
    SafetyResult,
    ALL_AUTHORIZED_OBJECTS,
    MAX_LIMIT,
)
from east_v5.agents.east_000.query_executor import (
    QueryResult,
    ExecutedQueryRecord,
    TraceEntry,
    execute_safe_query,
    verify_asset_hash,
    compute_file_sha256,
)
from east_v5.governance import ContractError, load_json


# ---- Schema version constants ----
SCHEMA_VERSION_REQUEST = "constraint-query-request/v1"
SCHEMA_VERSION_PACKAGE = "constraint-asset-package/v1"

# ---- Asset type mapping ----
ASSET_TYPE_TO_TABLE: dict[str, list[str]] = {
    "data_element": ["field_master"],
    "single_field": ["field_master", "approved_comparison_constraints", "approved_reference_constraints"],
    "within_table": ["intra_table_constraints", "multifield_constraint", "multifield_constraint_field"],
    "cross_table": ["cross_table_constraints"],
    "object_detail_state": ["excluded_constraint_audit", "decision_audit", "evidence"],
    "hierarchy_reference": [],  # TRG, not SQLite
}

# ---- Query purpose to SQL template mapping ----
QUERY_TEMPLATES: dict[str, str] = {
    "constraint_lookup": "SELECT * FROM {table} WHERE {where_clause} LIMIT ?",
    "field_explanation": "SELECT * FROM field_master WHERE field_id = ? LIMIT ?",
    "table_explanation": "SELECT * FROM field_master WHERE table_id = ? LIMIT ?",
    "relationship_lookup": "SELECT * FROM {table} WHERE {where_clause} LIMIT ?",
    "closure_expansion": "SELECT * FROM {table} WHERE {where_clause} LIMIT ?",
    "hierarchy_lookup": "SELECT * FROM {table} WHERE {where_clause} LIMIT ?",
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _content_hash(data: dict[str, Any]) -> str:
    """Compute SHA-256 of canonical JSON for content_hash field."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_request_schema(repo_root: Path, payload: dict[str, Any]) -> None:
    """Validate CONSTRAINT-QUERY-REQUEST against its JSON Schema."""
    schema = load_json(
        repo_root / "contracts" / "packages" / "constraint-query-request.schema.json"
    )
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        raise ContractError(f"SCHEMA_VALIDATION_FAILED:CONSTRAINT_QUERY_REQUEST - {exc.message}")


def validate_result_schema(repo_root: Path, payload: dict[str, Any]) -> None:
    """Validate CONSTRAINT-ASSET-PACKAGE against its JSON Schema."""
    schema = load_json(
        repo_root / "contracts" / "packages" / "constraint-asset-package.schema.json"
    )
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        raise ContractError(f"SCHEMA_VALIDATION_FAILED:CONSTRAINT_ASSET_PACKAGE - {exc.message}")


def plan_queries(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Decompose a CONSTRAINT-QUERY-REQUEST into candidate SQL query plans.

    This is the LLM-augmented part: the model interprets the natural
    language intent and maps it to parameterized SQL against whitelisted
    tables/views.  The output is a list of candidate queries that must
    still pass the safety gate.

    Returns list of {"sql": str, "params": tuple}.
    """
    purpose = request["query_purpose"]
    asset_types = request["target_asset_types"]
    max_rows = min(request.get("max_rows", MAX_LIMIT), MAX_LIMIT)
    table_scope = request.get("table_scope", [])
    field_scope = request.get("field_scope", [])
    relationship_scope = request.get("relationship_scope", [])

    candidates: list[dict[str, Any]] = []

    for asset_type in asset_types:
        tables = ASSET_TYPE_TO_TABLE.get(asset_type, [])
        for table in tables:
            if table not in ALL_AUTHORIZED_OBJECTS:
                continue

            if purpose == "field_explanation" and field_scope:
                for fscope in field_scope:
                    parts = fscope.split(".", 1)
                    if len(parts) == 2:
                        candidates.append({
                            "sql": f"SELECT * FROM {table} WHERE table_id = ? AND field_id = ? LIMIT ?",
                            "params": (parts[0], parts[1], max_rows),
                        })
                    else:
                        candidates.append({
                            "sql": f"SELECT * FROM {table} WHERE field_id = ? LIMIT ?",
                            "params": (fscope, max_rows),
                        })
            elif purpose == "table_explanation" and table_scope:
                for tscope in table_scope:
                    candidates.append({
                        "sql": f"SELECT * FROM {table} WHERE table_id = ? LIMIT ?",
                        "params": (tscope, max_rows),
                    })
            else:
                # Generic constraint/relationship lookup
                if table_scope:
                    for tscope in table_scope:
                        candidates.append({
                            "sql": f"SELECT * FROM {table} WHERE table_id = ? LIMIT ?",
                            "params": (tscope, max_rows),
                        })
                else:
                    candidates.append({
                        "sql": f"SELECT * FROM {table} LIMIT ?",
                        "params": (max_rows,),
                    })

    return candidates


def execute_retrieval(
    request: dict[str, Any],
    sqlite_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    """Full retrieval pipeline: validate → plan → safety gate → execute → assemble.

    Returns a CONSTRAINT-ASSET-PACKAGE dict.
    """
    # Step 1: Validate request
    validate_request_schema(repo_root, request)
    validate_request(request)

    # Step 2: Plan queries
    candidates = plan_queries(request)
    if not candidates:
        return _empty_result(request, "UNMATCHED_QUERY - no candidate queries generated")

    # Step 3: Execute each candidate through safety gate
    all_records: list[dict[str, Any]] = []
    all_executed: list[dict[str, Any]] = []
    all_traces: list[dict[str, Any]] = []
    all_unmatched: list[dict[str, Any]] = []

    for i, candidate in enumerate(candidates, 1):
        sql = candidate["sql"]
        params = tuple(candidate.get("params", ()))

        result, record, trace = execute_safe_query(
            sqlite_path, sql, params, max_rows=request.get("max_rows", MAX_LIMIT)
        )

        all_executed.append({
            "sql": record.sql,
            "query_parameters": list(record.query_parameters),
            "safety_check_result": record.safety_check_result,
        })

        all_traces.append({
            "round": i,
            "sql": trace.sql,
            "elapsed_ms": trace.elapsed_ms,
            "row_count": trace.row_count,
            "exception": trace.exception,
        })

        if record.safety_check_result == "fail":
            all_unmatched.append({
                "target": sql,
                "reason": f"Safety gate rejected: {trace.exception}",
            })
            continue

        if result.exception:
            all_unmatched.append({
                "target": sql,
                "reason": f"Execution error: {result.exception}",
            })
            continue

        for row in result.rows:
            all_records.append({
                "record_type": _infer_record_type(sql),
                "data": row,
                "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}],
                "hierarchy_refs": [],
            })

    # Step 4: Assemble CONSTRAINT-ASSET-PACKAGE
    asset_types_covered = list(set(
        rec["record_type"] for rec in all_records
    )) if all_records else []

    package = {
        "request_id": request["request_id"],
        "asset_version": "CA-V0.3.0",
        "executed_queries": all_executed,
        "matched_records": all_records,
        "constraint_summary": {
            "total_matched": len(all_records),
            "asset_types_covered": asset_types_covered,
        },
        "unmatched_items": all_unmatched,
        "query_trace": all_traces,
    }

    # Step 5: Validate output
    validate_result_schema(repo_root, package)

    return package


def _infer_record_type(sql: str) -> str:
    """Infer asset record type from the SQL table/view name."""
    sql_lower = sql.lower()
    if "field_master" in sql_lower:
        return "single_field"
    if "intra_table" in sql_lower or "multifield_constraint" in sql_lower:
        return "within_table"
    if "cross_table" in sql_lower:
        return "cross_table"
    if "approved_comparison" in sql_lower or "approved_reference" in sql_lower:
        return "single_field"
    if "decision_audit" in sql_lower or "evidence" in sql_lower:
        return "object_detail_state"
    if "excluded_constraint" in sql_lower:
        return "object_detail_state"
    return "data_element"


def _empty_result(request: dict[str, Any], reason: str) -> dict[str, Any]:
    """Return a valid CONSTRAINT-ASSET-PACKAGE with no matches."""
    return {
        "request_id": request.get("request_id", ""),
        "asset_version": "CA-V0.3.0",
        "executed_queries": [],
        "matched_records": [],
        "constraint_summary": {"total_matched": 0, "asset_types_covered": []},
        "unmatched_items": [{"target": request.get("natural_language_intent", ""), "reason": reason}],
        "query_trace": [],
    }
