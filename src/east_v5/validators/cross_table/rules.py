"""Cross-table multi-field validators.

These consume CA-V0.3.0 ``CROSS_TABLE`` rules over a read-only ``Snapshot``:

* ``REFERENCE_EXISTENCE`` — a present consumer value must exist in the provider
  table (``provider_match`` ``ONE`` = single provider column, ``ANY`` = any of
  several provider columns).
* ``CONDITIONAL_COMPARISON`` — a field comparison guarded by a provider-side
  condition, joined on a key pair.
* ``CONDITIONAL_VALUE_EXCLUSION`` — forbid a value / value set when a consumer
  key exists in a provider column.

A cross-table rule always names one consumer table (the primary field's table)
and at most one provider table; multiple provider tables are ambiguous and are
rejected for human review rather than guessed.
"""
from __future__ import annotations

from typing import Any, Callable

from east_v5.governance import ContractError

from east_v5.validators.expression import (
    KIND_CONDITIONAL_COMPARISON,
    KIND_CONDITIONAL_VALUE_EXCLUSION,
    KIND_REFERENCE_EXISTENCE,
    evaluate_assertion,
    evaluate_condition,
    evaluate_join,
    primary_endpoint,
)
from east_v5.validators.result import (
    VIOLATION_CONDITIONAL_COMPARISON,
    VIOLATION_CONDITIONAL_VALUE_EXCLUSION,
    VIOLATION_REFERENCE_EXISTENCE,
    ERROR_EXPRESSION_INVALID,
    ERROR_INVALID_INPUT,
    ERROR_UNKNOWN_RULE_KIND,
    make_violation,
)
from east_v5.validators.snapshot import Snapshot, Table, is_empty, split_endpoint

CROSS_TABLE_RULE_KINDS = frozenset({KIND_REFERENCE_EXISTENCE, KIND_CONDITIONAL_COMPARISON, KIND_CONDITIONAL_VALUE_EXCLUSION})

_VIOLATION_CODE = {
    KIND_REFERENCE_EXISTENCE: VIOLATION_REFERENCE_EXISTENCE,
    KIND_CONDITIONAL_COMPARISON: VIOLATION_CONDITIONAL_COMPARISON,
    KIND_CONDITIONAL_VALUE_EXCLUSION: VIOLATION_CONDITIONAL_VALUE_EXCLUSION,
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _check_rule(rule: dict[str, Any]) -> str:
    if not isinstance(rule, dict) or rule.get("scope") != "CROSS_TABLE":
        _fail(ERROR_INVALID_INPUT)
    kind = rule.get("kind")
    if kind not in CROSS_TABLE_RULE_KINDS:
        _fail(ERROR_UNKNOWN_RULE_KIND)
    for key in ("constraint_id", "asset_id", "asset_version"):
        if not isinstance(rule.get(key), str) or not rule.get(key):
            _fail(ERROR_INVALID_INPUT)
    return kind


def _consumer_table(rule: dict[str, Any]) -> str:
    if rule["kind"] == KIND_REFERENCE_EXISTENCE:
        return split_endpoint(rule["consumer_field"])[0]
    return split_endpoint(primary_endpoint(rule["assertion"]))[0]


def _provider_table(rule: dict[str, Any], consumer_table: str) -> str | None:
    others = {split_endpoint(field)[0] for field in rule["fields"]} - {consumer_table}
    if not others:
        return None
    if len(others) > 1:
        _fail(ERROR_EXPRESSION_INVALID)
    return others.pop()


def _violation(rule: dict[str, Any], kind: str, endpoint: str, record_index: int, message: str) -> dict[str, Any]:
    table_code, field_code = split_endpoint(endpoint)
    return make_violation(
        _VIOLATION_CODE[kind],
        rule["constraint_id"],
        rule["asset_id"],
        kind,
        table_code=table_code,
        endpoint=endpoint,
        field_code=field_code,
        record_index=record_index,
        message=message,
    )


def _snapshot_contains(snapshot: Snapshot, endpoint: str, value: Any) -> bool:
    table_code, field_code = split_endpoint(endpoint)
    return value in snapshot.table(table_code).present_values(field_code)


def _validate_reference(rule: dict[str, Any], snapshot: Snapshot) -> list[dict[str, Any]]:
    consumer_table, consumer_field = split_endpoint(rule["consumer_field"])
    consumer = snapshot.table(consumer_table)
    provider_fields = rule["provider_fields"]
    match = rule["provider_match"]
    violations: list[dict[str, Any]] = []
    for record_index in range(len(consumer)):
        current = consumer.value(consumer_field, record_index)
        if is_empty(current):
            continue
        if match == "ONE":
            present = current in snapshot.table(split_endpoint(provider_fields[0])[0]).present_values(split_endpoint(provider_fields[0])[1])
        else:
            present = any(current in snapshot.table(split_endpoint(field)[0]).present_values(split_endpoint(field)[1]) for field in provider_fields)
        if not present:
            violations.append(_violation(
                rule, KIND_REFERENCE_EXISTENCE, rule["consumer_field"], record_index,
                f"consumer value {current!r} missing from provider {', '.join(provider_fields)}",
            ))
    return violations


def _validate_comparison_family(rule: dict[str, Any], snapshot: Snapshot) -> list[dict[str, Any]]:
    kind = rule["kind"]
    consumer_table = _consumer_table(rule)
    provider_table = _provider_table(rule, consumer_table)
    consumer = snapshot.table(consumer_table)
    violations: list[dict[str, Any]] = []
    join = rule["join"]
    if join is not None:
        if provider_table is None:
            _fail(ERROR_EXPRESSION_INVALID)
        provider = snapshot.table(provider_table)

        def value(endpoint: str, i: int, j: int) -> Any:
            table_code, field_code = split_endpoint(endpoint)
            if table_code == consumer_table:
                return consumer.value(field_code, i)
            if table_code == provider_table:
                return provider.value(field_code, j)
            _fail(ERROR_EXPRESSION_INVALID)

        for i in range(len(consumer)):
            for j in range(len(provider)):
                if not evaluate_join(join, lambda endpoint: value(endpoint, i, j)):
                    continue
                if rule["condition"] is not None and not evaluate_condition(
                    rule["condition"],
                    lambda endpoint: value(endpoint, i, j),
                    lambda endpoint, v: _snapshot_contains(snapshot, endpoint, v),
                ):
                    continue
                if not evaluate_assertion(rule["assertion"], lambda endpoint: value(endpoint, i, j)):
                    endpoint = primary_endpoint(rule["assertion"])
                    violations.append(_violation(rule, kind, endpoint, i, f"cross-table rule {kind} violated at {endpoint}"))
        return violations
    # No join: the assertion lives in the consumer table; the condition's
    # EXISTS_IN membership is resolved across the snapshot.
    for i in range(len(consumer)):
        if rule["condition"] is not None and not evaluate_condition(
            rule["condition"],
            lambda endpoint: _consumer_value(consumer, consumer_table, endpoint, i),
            lambda endpoint, v: _snapshot_contains(snapshot, endpoint, v),
        ):
            continue
        if not evaluate_assertion(rule["assertion"], lambda endpoint: _consumer_value(consumer, consumer_table, endpoint, i)):
            endpoint = primary_endpoint(rule["assertion"])
            violations.append(_violation(rule, kind, endpoint, i, f"cross-table rule {kind} violated at {endpoint}"))
    return violations


def _consumer_value(consumer: Table, consumer_table: str, endpoint: str, record_index: int) -> Any:
    table_code, field_code = split_endpoint(endpoint)
    if table_code != consumer_table:
        _fail(ERROR_EXPRESSION_INVALID)
    return consumer.value(field_code, record_index)


def validate_cross_table_rule(rule: dict[str, Any], snapshot: Snapshot) -> list[dict[str, Any]]:
    """Validate one CROSS_TABLE rule against a multi-table snapshot."""
    kind = _check_rule(rule)
    if kind == KIND_REFERENCE_EXISTENCE:
        return _validate_reference(rule, snapshot)
    return _validate_comparison_family(rule, snapshot)
