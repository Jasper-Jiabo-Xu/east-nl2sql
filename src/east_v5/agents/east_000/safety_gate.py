"""Hard-code SQL safety gate for Agent 000: asset retrieval agent.

Validates all candidate SQL queries before execution.
Only SELECT/CTE SELECT on whitelisted tables/views with parameterized
queries, mandatory LIMIT, and no injection patterns are allowed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from east_v5.governance import ContractError


class SafetyResult(Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class SafetyCheckOutcome:
    result: SafetyResult
    rejected_reasons: list[str] = field(default_factory=list)


# ---- Whitelisted objects from approved-assets.json ----

CA_V030_TABLES = frozenset({
    "decision_audit",
    "evidence",
    "excluded_constraint_audit",
    "field_master",
    "multifield_constraint",
    "multifield_constraint_field",
    "release_meta",
    "source_manifest",
})

CA_V030_VIEWS = frozenset({
    "approved_comparison_constraints",
    "approved_reference_constraints",
    "cross_table_constraints",
    "intra_table_constraints",
})

ALL_AUTHORIZED_OBJECTS = CA_V030_TABLES | CA_V030_VIEWS

# ---- Rejected SQL keywords ----

WRITE_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "TRUNCATE", "ATTACH", "PRAGMA",
})

SYSTEM_TABLES = frozenset({
    "sqlite_master", "sqlite_schema", "sqlite_temp_master",
    "sqlite_temp_schema", "sqlite_sequence",
})

# ---- Patterns ----

_MULTI_STMT = re.compile(r";\s*\S", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\?")
_STRING_LITERAL = re.compile(r"'[^']*'")
_LIMIT_CLAUSE = re.compile(r"\bLIMIT\s+\?", re.IGNORECASE)
_LIMIT_LITERAL = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)

_FROM_CLAUSE = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
_CTE_NAME = re.compile(
    r"\bWITH\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(",
    re.IGNORECASE,
)

MAX_LIMIT = 10_000
QUERY_TIMEOUT_SECONDS = 30


def _fail(code: str) -> None:
    raise ContractError(code)


def check_sql_safety(sql: str, max_rows: int = MAX_LIMIT) -> SafetyCheckOutcome:
    """Validate a candidate SQL query against all safety rules."""
    reasons: list[str] = []
    upper = sql.strip().upper()

    # 1. Check for write keywords (after removing string literals to avoid false positives)
    cleaned = _STRING_LITERAL.sub("", sql)
    cleaned_upper = cleaned.strip().upper()
    for kw in WRITE_KEYWORDS:
        if re.search(rf"\b{kw}\b", cleaned_upper):
            reasons.append(f"SAFETY_GATE_REJECTED:WRITE_OP - {kw} detected")

    # 2. Multi-statement check
    if _MULTI_STMT.search(sql):
        reasons.append("SAFETY_GATE_REJECTED:MULTI_STATEMENT")

    # 3. System table check
    for st in SYSTEM_TABLES:
        if re.search(rf"\b{st}\b", sql, re.IGNORECASE):
            reasons.append(f"SAFETY_GATE_REJECTED:SYSTEM_TABLE - {st}")

    # 4. Extract referenced objects and check whitelist
    cte_names = set(_CTE_NAME.findall(sql))
    from_objects = set(_FROM_CLAUSE.findall(sql))
    external_objects = from_objects - cte_names
    for obj in external_objects:
        if obj.lower() in SYSTEM_TABLES:
            continue
        if obj.lower() not in ALL_AUTHORIZED_OBJECTS:
            reasons.append(
                f"SAFETY_GATE_REJECTED:UNAUTHORIZED_OBJECT - {obj}"
            )

    # 5. Parameterization check (must run BEFORE string literal removal)
    # Check original SQL for unparameterized inline values like = 'value'
    _INLINE_VALUE = re.compile(r"=\s*'[\w]+'", re.IGNORECASE)
    if _INLINE_VALUE.search(sql):
        reasons.append("SAFETY_GATE_REJECTED:UNPARAMETERIZED")

    # 6. LIMIT clause check
    has_limit_placeholder = _LIMIT_CLAUSE.search(sql)
    has_limit_literal = _LIMIT_LITERAL.search(sql)
    if not has_limit_placeholder and not has_limit_literal:
        reasons.append("SAFETY_GATE_REJECTED:NO_LIMIT")

    # 7. Injection pattern check (on cleaned SQL to avoid false positives from data)
    _INJECTION_PATTERNS = re.compile(
        r"(?:UNION\s+ALL|UNION\s+SELECT|--|/\*|;\s*DROP|;\s*DELETE|;\s*UPDATE|;\s*INSERT|\bOR\s+1\s*=\s*1|\bAND\s+1\s*=\s*1)",
        re.IGNORECASE,
    )
    if _INJECTION_PATTERNS.search(cleaned):
        reasons.append("SAFETY_GATE_REJECTED:INJECTION")

    if reasons:
        return SafetyCheckOutcome(result=SafetyResult.FAIL, rejected_reasons=reasons)
    return SafetyCheckOutcome(result=SafetyResult.PASS)


def validate_request(request: dict[str, Any]) -> None:
    """Validate a CONSTRAINT-QUERY-REQUEST before processing."""
    caller = request.get("caller_agent_id")
    if caller not in ("130", "220"):
        _fail("INVALID_CALLER_AGENT_ID")

    stage = request.get("caller_stage")
    valid_stages = {"observable_fact", "structure_closure", "foundation_closure", "other"}
    if stage not in valid_stages:
        _fail("INVALID_CALLER_STAGE")

    purpose = request.get("query_purpose")
    valid_purposes = {
        "constraint_lookup", "field_explanation", "table_explanation",
        "relationship_lookup", "closure_expansion", "hierarchy_lookup",
    }
    if purpose not in valid_purposes:
        _fail("INVALID_QUERY_PURPOSE")

    max_rows = request.get("max_rows", 0)
    if not isinstance(max_rows, int) or max_rows < 1 or max_rows > MAX_LIMIT:
        _fail("MAX_ROWS_EXCEEDED")
