"""Intra-table multi-field validators (one evaluator per EAST table).

These consume CA-V0.3.0 ``INTRA_TABLE`` rules and a read-only ``Table`` view of
candidate rows, and return a violation per offending record.  Comparison
operands that are empty make the rule "not applicable" for that record, which
mirrors the approved "when present" semantics of the EAST validation rules.
"""
from __future__ import annotations

from typing import Any

from east_v5.governance import ContractError

from east_v5.validators.expression import (
    KIND_COMPARISON,
    KIND_CONDITIONAL_COMPARISON,
    KIND_CONDITIONAL_VALUE_EXCLUSION,
    evaluate_assertion,
    evaluate_condition,
    primary_endpoint,
)
from east_v5.validators.result import (
    VIOLATION_COMPARISON,
    VIOLATION_CONDITIONAL_COMPARISON,
    VIOLATION_CONDITIONAL_VALUE_EXCLUSION,
    ERROR_INVALID_INPUT,
    ERROR_UNKNOWN_RULE_KIND,
    make_violation,
)
from east_v5.validators.snapshot import Table, split_endpoint

TABLE_RULE_KINDS = frozenset({KIND_COMPARISON, KIND_CONDITIONAL_COMPARISON, KIND_CONDITIONAL_VALUE_EXCLUSION})

_VIOLATION_CODE = {
    KIND_COMPARISON: VIOLATION_COMPARISON,
    KIND_CONDITIONAL_COMPARISON: VIOLATION_CONDITIONAL_COMPARISON,
    KIND_CONDITIONAL_VALUE_EXCLUSION: VIOLATION_CONDITIONAL_VALUE_EXCLUSION,
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _intra_value(table: Table, endpoint: str, record_index: int) -> Any:
    table_code, field_code = split_endpoint(endpoint)
    if table_code != table.table_code:
        _fail(ERROR_INVALID_INPUT)
    return table.value(field_code, record_index)


def _intra_contains(table: Table, endpoint: str, value: Any) -> bool:
    table_code, field_code = split_endpoint(endpoint)
    if table_code != table.table_code:
        _fail(ERROR_INVALID_INPUT)
    return value in table.present_values(field_code)


def validate_table_rule(rule: dict[str, Any], table: Table) -> list[dict[str, Any]]:
    """Validate all records of one table against one INTRA_TABLE rule."""
    if not isinstance(rule, dict) or rule.get("scope") != "INTRA_TABLE":
        _fail(ERROR_INVALID_INPUT)
    kind = rule.get("kind")
    if kind not in TABLE_RULE_KINDS:
        _fail(ERROR_UNKNOWN_RULE_KIND)
    for key in ("constraint_id", "asset_id", "asset_version"):
        if not isinstance(rule.get(key), str) or not rule.get(key):
            _fail(ERROR_INVALID_INPUT)
    violations: list[dict[str, Any]] = []
    for record_index in range(len(table)):
        if rule["condition"] is not None and not evaluate_condition(
            rule["condition"],
            lambda endpoint: _intra_value(table, endpoint, record_index),
            lambda endpoint, value: _intra_contains(table, endpoint, value),
        ):
            continue
        if not evaluate_assertion(rule["assertion"], lambda endpoint: _intra_value(table, endpoint, record_index)):
            endpoint = primary_endpoint(rule["assertion"])
            table_code, field_code = split_endpoint(endpoint)
            violations.append(make_violation(
                _VIOLATION_CODE[kind],
                rule["constraint_id"],
                rule["asset_id"],
                kind,
                table_code=table_code,
                endpoint=endpoint,
                field_code=field_code,
                record_index=record_index,
                message=f"intra-table rule {kind} violated at {endpoint}",
            ))
    return violations
