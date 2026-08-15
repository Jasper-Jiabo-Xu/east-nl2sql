"""EAS-17 unified validator result contract.

The 242 data validator consumes CA-V0.3.0 / CA-V0.2.0 rules and returns a
deterministic verdict.  A result is either ``YES`` (no violations) or ``NO``
with the complete list of violations.  Every violation carries a stable
``code``, the ``constraint_id`` and ``asset_id`` of the rule that produced it,
and a precise ``location`` so a human reviewer can find the offending cell.

Nothing in this module mutates candidate data; it only serializes the verdict.
"""
from __future__ import annotations

from typing import Any

from east_v5.governance import ContractError, sha256

RESULT_SCHEMA_VERSION = "v5.validator-result/v1"
REGISTRY_SCHEMA_VERSION = "v5.validator-registry/v1"

VERDICT_PASS = "YES"
VERDICT_FAIL = "NO"
VERDICTS = frozenset({VERDICT_PASS, VERDICT_FAIL})

# One stable violation code per supported rule kind.  Downstream 242/260
# consumers match on these codes; they must never be renamed.
VIOLATION_NULLABLE = "VIOLATION_NULLABLE"
VIOLATION_STRING_LENGTH = "VIOLATION_STRING_LENGTH"
VIOLATION_CODE_DOMAIN = "VIOLATION_CODE_DOMAIN"
VIOLATION_ENCODING_RULE = "VIOLATION_ENCODING_RULE"
VIOLATION_FORBIDDEN_VALUE = "VIOLATION_FORBIDDEN_VALUE"
VIOLATION_FORBIDDEN_CHARACTER_SET = "VIOLATION_FORBIDDEN_CHARACTER_SET"
VIOLATION_VALUE_RANGE = "VIOLATION_VALUE_RANGE"
VIOLATION_DATA_TYPE = "VIOLATION_DATA_TYPE"
VIOLATION_UNIQUE = "VIOLATION_UNIQUE"
VIOLATION_PRIMARY_KEY = "VIOLATION_PRIMARY_KEY"
VIOLATION_COMPARISON = "VIOLATION_COMPARISON"
VIOLATION_CONDITIONAL_COMPARISON = "VIOLATION_CONDITIONAL_COMPARISON"
VIOLATION_CONDITIONAL_VALUE_EXCLUSION = "VIOLATION_CONDITIONAL_VALUE_EXCLUSION"
VIOLATION_REFERENCE_EXISTENCE = "VIOLATION_REFERENCE_EXISTENCE"

VIOLATION_CODES = frozenset({
    VIOLATION_NULLABLE,
    VIOLATION_STRING_LENGTH,
    VIOLATION_CODE_DOMAIN,
    VIOLATION_ENCODING_RULE,
    VIOLATION_FORBIDDEN_VALUE,
    VIOLATION_FORBIDDEN_CHARACTER_SET,
    VIOLATION_VALUE_RANGE,
    VIOLATION_DATA_TYPE,
    VIOLATION_UNIQUE,
    VIOLATION_PRIMARY_KEY,
    VIOLATION_COMPARISON,
    VIOLATION_CONDITIONAL_COMPARISON,
    VIOLATION_CONDITIONAL_VALUE_EXCLUSION,
    VIOLATION_REFERENCE_EXISTENCE,
})

# Rejection codes for malformed input or drift detected before evaluation.
# These are raised as ContractError, not returned as violations.
ERROR_INVALID_INPUT = "INVALID_INPUT"
ERROR_UNKNOWN_RULE_KIND = "UNKNOWN_RULE_KIND"
ERROR_EXPRESSION_INVALID = "EXPRESSION_INVALID"
ERROR_VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
ERROR_RESULT_SCHEMA_DRIFT = "RESULT_SCHEMA_DRIFT"

_LOCATION_KEYS = frozenset({"table_code", "endpoint", "field_code", "record_index"})
_VIOLATION_KEYS = frozenset({"code", "constraint_id", "asset_id", "rule_kind", "location", "message"})
_RESULT_KEYS = frozenset({
    "schema_version", "validator_id", "registry_version", "constraint_asset_version",
    "verdict", "violations", "content_sha256",
})


def _require(value: Any, condition: bool, code: str) -> None:
    if not condition:
        raise ContractError(code)


def make_violation(
    code: str,
    constraint_id: str,
    asset_id: str,
    rule_kind: str,
    *,
    table_code: str | None = None,
    endpoint: str | None = None,
    field_code: str | None = None,
    record_index: int | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Build one located violation.  Location fields are optional but every
    supported rule should fill in at least the table and endpoint it touched."""
    _require(code, code in VIOLATION_CODES, ERROR_INVALID_INPUT)
    _require(constraint_id, isinstance(constraint_id, str) and bool(constraint_id), ERROR_INVALID_INPUT)
    _require(asset_id, isinstance(asset_id, str) and bool(asset_id), ERROR_INVALID_INPUT)
    _require(rule_kind, isinstance(rule_kind, str) and bool(rule_kind), ERROR_INVALID_INPUT)
    _require(message, isinstance(message, str), ERROR_INVALID_INPUT)
    _require(record_index, record_index is None or (isinstance(record_index, int) and not isinstance(record_index, bool) and record_index >= 0), ERROR_INVALID_INPUT)
    location = {
        "table_code": table_code if isinstance(table_code, str) and table_code else None,
        "endpoint": endpoint if isinstance(endpoint, str) and endpoint else None,
        "field_code": field_code if isinstance(field_code, str) and field_code else None,
        "record_index": record_index,
    }
    return {
        "code": code,
        "constraint_id": constraint_id,
        "asset_id": asset_id,
        "rule_kind": rule_kind,
        "location": location,
        "message": message,
    }


def _verify_violation(violation: dict[str, Any]) -> None:
    _require(violation, isinstance(violation, dict) and set(violation) == _VIOLATION_KEYS, ERROR_RESULT_SCHEMA_DRIFT)
    _require(violation["code"], violation["code"] in VIOLATION_CODES, ERROR_RESULT_SCHEMA_DRIFT)
    location = violation["location"]
    _require(location, isinstance(location, dict) and set(location) == _LOCATION_KEYS, ERROR_RESULT_SCHEMA_DRIFT)
    for key in ("table_code", "endpoint", "field_code"):
        _require(key, location[key] is None or (isinstance(location[key], str) and bool(location[key])), ERROR_RESULT_SCHEMA_DRIFT)
    _require("record_index", location["record_index"] is None or (isinstance(location["record_index"], int) and not isinstance(location["record_index"], bool) and location["record_index"] >= 0), ERROR_RESULT_SCHEMA_DRIFT)
    for key in ("constraint_id", "asset_id", "rule_kind", "message"):
        _require(key, isinstance(violation[key], str), ERROR_RESULT_SCHEMA_DRIFT)


def make_result(validator_id: str, constraint_asset_version: str, violations: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble an immutable result; the verdict is derived, never supplied."""
    _require(validator_id, isinstance(validator_id, str) and bool(validator_id), ERROR_INVALID_INPUT)
    _require(constraint_asset_version, isinstance(constraint_asset_version, str) and bool(constraint_asset_version), ERROR_INVALID_INPUT)
    _require(violations, isinstance(violations, list), ERROR_INVALID_INPUT)
    for violation in violations:
        _verify_violation(violation)
    verdict = VERDICT_FAIL if violations else VERDICT_PASS
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "validator_id": validator_id,
        "registry_version": REGISTRY_SCHEMA_VERSION,
        "constraint_asset_version": constraint_asset_version,
        "verdict": verdict,
        "violations": list(violations),
        "content_sha256": "",
    }
    result["content_sha256"] = sha256(result)
    return result


def verify_result(result: dict[str, Any]) -> dict[str, Any]:
    """Validate a result's shape and content hash before a downstream consumes it."""
    _require(result, isinstance(result, dict) and set(result) == _RESULT_KEYS, ERROR_RESULT_SCHEMA_DRIFT)
    _require(result["schema_version"], result["schema_version"] == RESULT_SCHEMA_VERSION, ERROR_RESULT_SCHEMA_DRIFT)
    _require(result["registry_version"], result["registry_version"] == REGISTRY_SCHEMA_VERSION, ERROR_RESULT_SCHEMA_DRIFT)
    _require(result["verdict"], result["verdict"] in VERDICTS, ERROR_RESULT_SCHEMA_DRIFT)
    _require(result["validator_id"], isinstance(result["validator_id"], str) and bool(result["validator_id"]), ERROR_RESULT_SCHEMA_DRIFT)
    _require(result["constraint_asset_version"], isinstance(result["constraint_asset_version"], str) and bool(result["constraint_asset_version"]), ERROR_RESULT_SCHEMA_DRIFT)
    violations = result["violations"]
    _require(violations, isinstance(violations, list), ERROR_RESULT_SCHEMA_DRIFT)
    for violation in violations:
        _verify_violation(violation)
    if (result["verdict"] == VERDICT_PASS) != (not violations):
        raise ContractError(ERROR_RESULT_SCHEMA_DRIFT)
    supplied = result["content_sha256"]
    _require(supplied, isinstance(supplied, str) and len(supplied) == 64, ERROR_RESULT_SCHEMA_DRIFT)
    probe = dict(result)
    probe["content_sha256"] = ""
    if supplied != sha256(probe):
        raise ContractError(ERROR_RESULT_SCHEMA_DRIFT)
    return result
