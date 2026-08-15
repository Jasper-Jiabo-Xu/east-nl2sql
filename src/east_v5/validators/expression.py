"""Parse and normalize the CA-V0.3.0 ``structured_expression_json`` rule language.

The approved multi-field rules encode four kinds of deterministic constraint:

* ``REFERENCE_EXISTENCE`` — a consumer value, when present, must exist in a
  provider table (``provider_match`` is ``ONE`` or ``ANY``).
* ``COMPARISON`` — an unconditional (or conditionally guarded) field-to-field
  comparison, e.g. ``余额 <= 授信额度``.
* ``CONDITIONAL_COMPARISON`` — apply a comparison only when a guard holds.
* ``CONDITIONAL_VALUE_EXCLUSION`` — forbid a value / value set when a guard holds.

This module validates the expression shape before any evaluation and exposes the
small, null-aware comparison primitives the validators share.  It never mutates
candidate data.
"""
from __future__ import annotations

import math
from typing import Any

from east_v5.governance import ContractError

from east_v5.validators.result import ERROR_EXPRESSION_INVALID, ERROR_INVALID_INPUT
from east_v5.validators.snapshot import is_empty, split_endpoint

KIND_REFERENCE_EXISTENCE = "REFERENCE_EXISTENCE"
KIND_COMPARISON = "COMPARISON"
KIND_CONDITIONAL_COMPARISON = "CONDITIONAL_COMPARISON"
KIND_CONDITIONAL_VALUE_EXCLUSION = "CONDITIONAL_VALUE_EXCLUSION"
KINDS = frozenset({KIND_REFERENCE_EXISTENCE, KIND_COMPARISON, KIND_CONDITIONAL_COMPARISON, KIND_CONDITIONAL_VALUE_EXCLUSION})

SCHEMA_VERSION = "EAS-MFC-1.0"
DIRECTION_PROVIDER_TO_CONSUMER = "PROVIDER_TO_CONSUMER"

_FIELD_OPS = frozenset({"<=", ">=", "!=", "="})
_VALUE_NE_OPS = frozenset({"!="})
_VALUE_NOT_IN_OPS = frozenset({"NOT_IN"})
_CONDITION_SINGLE_OPS = frozenset({"=", "!="})
_CONDITION_ALL_NOT_IN_OPS = frozenset({"ALL_NOT_IN"})
_CONDITION_NOT_ALL_EQUAL_OPS = frozenset({"NOT_ALL_EQUAL"})
_CONDITION_EXISTS_IN_OPS = frozenset({"EXISTS_IN"})
_JOIN_OPS = frozenset({"="})


def _fail(code: str) -> None:
    raise ContractError(code)


def _scalar(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, (str, int, float)):
        return not (isinstance(value, float) and not math.isfinite(value))
    return False


def _endpoint(value: Any) -> str:
    if not isinstance(value, str):
        _fail(ERROR_EXPRESSION_INVALID)
    split_endpoint(value)  # raises if malformed
    return value


def _endpoint_list(value: Any, *, non_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value):
        _fail(ERROR_EXPRESSION_INVALID)
    result = [_endpoint(item) for item in value]
    if len(set(result)) != len(result):
        _fail(ERROR_EXPRESSION_INVALID)
    return result


def _value_list(value: Any) -> list[Any]:
    if not isinstance(value, list) or not value:
        _fail(ERROR_EXPRESSION_INVALID)
    if not all(_scalar(item) for item in value):
        _fail(ERROR_EXPRESSION_INVALID)
    return list(value)


def _assertion(assertion: Any) -> dict[str, Any]:
    if not isinstance(assertion, dict) or "operator" not in assertion:
        _fail(ERROR_EXPRESSION_INVALID)
    operator = assertion["operator"]
    if "left" in assertion or "right" in assertion:
        if set(assertion) != {"left", "operator", "right"} or operator not in _FIELD_OPS:
            _fail(ERROR_EXPRESSION_INVALID)
        return {"shape": "field_vs_field", "left": _endpoint(assertion["left"]), "operator": operator, "right": _endpoint(assertion["right"])}
    if "field" in assertion:
        if "values" in assertion:
            if set(assertion) != {"field", "operator", "values"} or operator not in _VALUE_NOT_IN_OPS:
                _fail(ERROR_EXPRESSION_INVALID)
            return {"shape": "field_not_in", "field": _endpoint(assertion["field"]), "operator": operator, "values": _value_list(assertion["values"])}
        if "value" in assertion:
            if set(assertion) != {"field", "operator", "value"} or operator not in _VALUE_NE_OPS or not _scalar(assertion["value"]):
                _fail(ERROR_EXPRESSION_INVALID)
            return {"shape": "field_ne_value", "field": _endpoint(assertion["field"]), "operator": operator, "value": assertion["value"]}
    _fail(ERROR_EXPRESSION_INVALID)


def _condition(condition: Any) -> dict[str, Any]:
    if not isinstance(condition, dict) or "operator" not in condition:
        _fail(ERROR_EXPRESSION_INVALID)
    operator = condition["operator"]
    if "fields" in condition:
        if "values" in condition:
            if set(condition) != {"fields", "operator", "values"} or operator not in _CONDITION_ALL_NOT_IN_OPS:
                _fail(ERROR_EXPRESSION_INVALID)
            return {"shape": "all_not_in", "fields": _endpoint_list(condition["fields"]), "operator": operator, "values": _value_list(condition["values"])}
        if "value" in condition:
            if set(condition) != {"fields", "operator", "value"} or operator not in _CONDITION_NOT_ALL_EQUAL_OPS or not _scalar(condition["value"]):
                _fail(ERROR_EXPRESSION_INVALID)
            return {"shape": "not_all_equal", "fields": _endpoint_list(condition["fields"]), "operator": operator, "value": condition["value"]}
    if "left" in condition or "right" in condition:
        if set(condition) != {"left", "operator", "right"} or operator not in _CONDITION_EXISTS_IN_OPS:
            _fail(ERROR_EXPRESSION_INVALID)
        return {"shape": "exists_in", "left": _endpoint(condition["left"]), "operator": operator, "right": _endpoint(condition["right"])}
    if "field" in condition:
        if set(condition) != {"field", "operator", "value"} or operator not in _CONDITION_SINGLE_OPS or not _scalar(condition["value"]):
            _fail(ERROR_EXPRESSION_INVALID)
        return {"shape": "single_field", "field": _endpoint(condition["field"]), "operator": operator, "value": condition["value"]}
    _fail(ERROR_EXPRESSION_INVALID)


def _join(join: Any) -> dict[str, Any]:
    if not isinstance(join, dict) or set(join) != {"left", "operator", "right"} or join["operator"] not in _JOIN_OPS:
        _fail(ERROR_EXPRESSION_INVALID)
    return {"shape": "join", "left": _endpoint(join["left"]), "operator": join["operator"], "right": _endpoint(join["right"])}


def _collect_fields(*parts: dict[str, Any] | None) -> list[str]:
    endpoints: set[str] = set()
    for part in parts:
        if not part:
            continue
        if part.get("shape") in {"field_vs_field", "join", "exists_in"}:
            endpoints.add(part["left"]); endpoints.add(part["right"])
        elif part["shape"] in {"field_not_in", "field_ne_value", "single_field"}:
            endpoints.add(part["field"])
        elif part["shape"] in {"all_not_in", "not_all_equal"}:
            endpoints.update(part["fields"])
    return sorted(endpoints)


def parse_expression(expression: Any) -> dict[str, Any]:
    """Validate and normalize a ``structured_expression_json`` object."""
    if not isinstance(expression, dict) or set(expression) & {"kind", "schema_version"} != {"kind", "schema_version"}:
        _fail(ERROR_EXPRESSION_INVALID)
    kind = expression["kind"]
    if kind not in KINDS or expression["schema_version"] != SCHEMA_VERSION:
        _fail(ERROR_EXPRESSION_INVALID)
    if kind == KIND_REFERENCE_EXISTENCE:
        if set(expression) != {"kind", "schema_version", "condition_text", "consumer_field", "direction", "provider_fields", "provider_match"}:
            _fail(ERROR_EXPRESSION_INVALID)
        if expression["direction"] != DIRECTION_PROVIDER_TO_CONSUMER or not isinstance(expression["condition_text"], str):
            _fail(ERROR_EXPRESSION_INVALID)
        if expression["provider_match"] not in {"ONE", "ANY"}:
            _fail(ERROR_EXPRESSION_INVALID)
        consumer = _endpoint(expression["consumer_field"])
        providers = _endpoint_list(expression["provider_fields"])
        return {
            "kind": kind,
            "assertion": None,
            "condition": None,
            "join": None,
            "consumer_field": consumer,
            "provider_fields": providers,
            "provider_match": expression["provider_match"],
            "fields": sorted({consumer, *providers}),
        }
    allowed = {"kind", "schema_version", "assertion", "condition", "join"}
    if not set(expression).issubset(allowed) or "assertion" not in expression:
        _fail(ERROR_EXPRESSION_INVALID)
    assertion = _assertion(expression["assertion"])
    condition = _condition(expression["condition"]) if "condition" in expression else None
    join = _join(expression["join"]) if "join" in expression else None
    if kind == KIND_COMPARISON and join is not None:
        _fail(ERROR_EXPRESSION_INVALID)
    if kind in {KIND_CONDITIONAL_COMPARISON, KIND_CONDITIONAL_VALUE_EXCLUSION} and condition is None:
        _fail(ERROR_EXPRESSION_INVALID)
    return {
        "kind": kind,
        "assertion": assertion,
        "condition": condition,
        "join": join,
        "consumer_field": None,
        "provider_fields": None,
        "provider_match": None,
        "fields": _collect_fields(assertion, condition, join),
    }


# ---------------------------------------------------------------------------
# Null-aware value primitives shared by the table / cross-table validators.
# ---------------------------------------------------------------------------

def to_number(value: Any) -> int | float | None:
    """Coerce a candidate value to a number when it looks numeric, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return None
    return None


def equals(left: Any, right: Any) -> bool:
    """Numeric-aware equality: ``4 == "4"`` is true, otherwise string compare."""
    left_number, right_number = to_number(left), to_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left) == str(right)


def compare(left: Any, operator: str, right: Any) -> bool:
    """Evaluate ``<= / >= / != / =`` between two non-empty values."""
    if operator == "=":
        return equals(left, right)
    if operator == "!=":
        return not equals(left, right)
    left_number, right_number = to_number(left), to_number(right)
    if left_number is not None and right_number is not None:
        if operator == "<=":
            return left_number <= right_number
        if operator == ">=":
            return left_number >= right_number
    left_text, right_text = str(left), str(right)
    if operator == "<=":
        return left_text <= right_text
    if operator == ">=":
        return left_text >= right_text
    _fail(ERROR_EXPRESSION_INVALID)


def in_values(value: Any, values: list[Any]) -> bool:
    """Membership where a null entry matches an empty candidate value."""
    for candidate in values:
        if candidate is None:
            if is_empty(value):
                return True
        elif equals(value, candidate):
            return True
    return False


def make_rule(
    constraint_id: str,
    asset_id: str,
    asset_version: str,
    constraint_item_type: str,
    scope: str,
    expression: Any,
) -> dict[str, Any]:
    """Bind a parsed expression to its asset identity for evaluation and audit."""
    if constraint_item_type not in {"REFERENCE_EXISTENCE", "COMPARISON"} or scope not in {"INTRA_TABLE", "CROSS_TABLE"}:
        _fail(ERROR_INVALID_INPUT)
    if not isinstance(constraint_id, str) or not constraint_id or not isinstance(asset_id, str) or not asset_id or not isinstance(asset_version, str) or not asset_version:
        _fail(ERROR_INVALID_INPUT)
    normalized = parse_expression(expression)
    return {
        "constraint_id": constraint_id,
        "asset_id": asset_id,
        "asset_version": asset_version,
        "constraint_item_type": constraint_item_type,
        "scope": scope,
        **normalized,
    }


def evaluate_condition(condition: dict[str, Any], value, contains) -> bool:
    """True when a guard condition holds for the current record context.

    ``value(endpoint)`` returns the endpoint value for the record; ``contains(
    endpoint, value)`` reports cross-record membership.
    """
    shape = condition["shape"]
    if shape == "single_field":
        current = value(condition["field"])
        if is_empty(current):
            return False
        if condition["operator"] == "=":
            return equals(current, condition["value"])
        return not equals(current, condition["value"])
    if shape == "all_not_in":
        return all(not in_values(value(field), condition["values"]) for field in condition["fields"])
    if shape == "not_all_equal":
        return not all(equals(value(field), condition["value"]) for field in condition["fields"])
    if shape == "exists_in":
        current = value(condition["left"])
        if is_empty(current):
            return False
        return contains(condition["right"], current)
    _fail(ERROR_EXPRESSION_INVALID)


def evaluate_assertion(assertion: dict[str, Any], value) -> bool:
    """True when an assertion holds; empty operands make the rule not apply."""
    shape = assertion["shape"]
    if shape == "field_vs_field":
        left, right = value(assertion["left"]), value(assertion["right"])
        if is_empty(left) or is_empty(right):
            return True
        return compare(left, assertion["operator"], right)
    if shape == "field_ne_value":
        current = value(assertion["field"])
        if is_empty(current):
            return True
        return not equals(current, assertion["value"])
    if shape == "field_not_in":
        current = value(assertion["field"])
        if is_empty(current):
            return True
        return not in_values(current, assertion["values"])
    _fail(ERROR_EXPRESSION_INVALID)


def evaluate_join(join: dict[str, Any], value) -> bool:
    left, right = value(join["left"]), value(join["right"])
    if is_empty(left) or is_empty(right):
        return False
    return equals(left, right)


def primary_endpoint(assertion: dict[str, Any]) -> str:
    """The endpoint a violation should point at for a given assertion."""
    if assertion["shape"] == "field_vs_field":
        return assertion["left"]
    return assertion["field"]
