"""Deterministic single-field and data-element validators.

Each validator maps one approved rule kind to a pure check over a single value
(or a single column, for uniqueness / primary-key rules).  A rule is traceable
to its asset through ``constraint_id`` and ``asset_id``; every violation echoes
those identifiers and points at the offending ``endpoint``.

The single-field rule kinds come from CA-V0.2.0 (``single_field_constraints``)
and the data-element kinds (``DATA_TYPE`` / encoding / range) come from the
approved ``data_element`` asset; the two are joined through
``field_master.data_element_code`` upstream, so a field rule always knows its
data element.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from east_v5.governance import ContractError

from east_v5.validators.expression import to_number
from east_v5.validators.result import (
    VIOLATION_CODE_DOMAIN,
    VIOLATION_DATA_TYPE,
    VIOLATION_ENCODING_RULE,
    VIOLATION_FORBIDDEN_CHARACTER_SET,
    VIOLATION_FORBIDDEN_VALUE,
    VIOLATION_NULLABLE,
    VIOLATION_PRIMARY_KEY,
    VIOLATION_STRING_LENGTH,
    VIOLATION_UNIQUE,
    VIOLATION_VALUE_RANGE,
    ERROR_INVALID_INPUT,
    ERROR_UNKNOWN_RULE_KIND,
    make_violation,
)
from east_v5.validators.snapshot import is_empty, split_endpoint

# Normalized single-field / data-element rule kind names.
KIND_DATA_TYPE = "DATA_TYPE"
KIND_NULLABLE = "NULLABLE"
KIND_STRING_LENGTH = "STRING_LENGTH"
KIND_CODE_DOMAIN = "CODE_DOMAIN"
KIND_ENCODING_RULE = "ENCODING_RULE"
KIND_FORBIDDEN_VALUE = "FORBIDDEN_VALUE"
KIND_FORBIDDEN_CHARACTER_SET = "FORBIDDEN_CHARACTER_SET"
KIND_VALUE_RANGE = "VALUE_RANGE"
KIND_UNIQUE = "UNIQUE"
KIND_PRIMARY_KEY = "PRIMARY_KEY"

FIELD_RULE_KINDS = frozenset({
    KIND_DATA_TYPE, KIND_NULLABLE, KIND_STRING_LENGTH, KIND_CODE_DOMAIN,
    KIND_ENCODING_RULE, KIND_FORBIDDEN_VALUE, KIND_FORBIDDEN_CHARACTER_SET,
    KIND_VALUE_RANGE, KIND_UNIQUE, KIND_PRIMARY_KEY,
})

_COLUMN_KINDS = frozenset({KIND_UNIQUE, KIND_PRIMARY_KEY})

_VIOLATION_CODE = {
    KIND_DATA_TYPE: VIOLATION_DATA_TYPE,
    KIND_NULLABLE: VIOLATION_NULLABLE,
    KIND_STRING_LENGTH: VIOLATION_STRING_LENGTH,
    KIND_CODE_DOMAIN: VIOLATION_CODE_DOMAIN,
    KIND_ENCODING_RULE: VIOLATION_ENCODING_RULE,
    KIND_FORBIDDEN_VALUE: VIOLATION_FORBIDDEN_VALUE,
    KIND_FORBIDDEN_CHARACTER_SET: VIOLATION_FORBIDDEN_CHARACTER_SET,
    KIND_VALUE_RANGE: VIOLATION_VALUE_RANGE,
    KIND_UNIQUE: VIOLATION_UNIQUE,
    KIND_PRIMARY_KEY: VIOLATION_PRIMARY_KEY,
}

_DATE_TOKENS = frozenset("YMDHhSsmndy")
_CLASS_CHARS = {
    "DIGIT": "0-9",
    "LETTER": "A-Za-z",
    "UPPERCASE_LETTER": "A-Z",
    "LOWERCASE_LETTER": "a-z",
    "ALPHANUMERIC": "A-Za-z0-9",
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


def _rule(rule: dict[str, Any]) -> tuple[str, str, str, str, str | None, dict[str, Any]]:
    if not isinstance(rule, dict):
        _fail(ERROR_INVALID_INPUT)
    kind = rule.get("rule_kind")
    if kind not in FIELD_RULE_KINDS:
        _fail(ERROR_UNKNOWN_RULE_KIND)
    for key in ("constraint_id", "asset_id", "asset_version"):
        _require(isinstance(rule.get(key), str) and bool(rule.get(key)), ERROR_INVALID_INPUT)
    endpoint = rule.get("endpoint")
    if endpoint is not None:
        split_endpoint(endpoint)
    spec = rule.get("spec")
    _require(isinstance(spec, dict), ERROR_INVALID_INPUT)
    return kind, rule["constraint_id"], rule["asset_id"], rule["asset_version"], endpoint, spec


def _location(endpoint: str | None) -> tuple[str | None, str | None, str | None]:
    if endpoint is None:
        return None, None, None
    table_code, field_code = split_endpoint(endpoint)
    return table_code, endpoint, field_code


def _violation(kind: str, rule: dict[str, Any], message: str, *, record_index: int | None = None) -> dict[str, Any]:
    table_code, endpoint, field_code = _location(rule["endpoint"])
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


def _validate_nullable(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    if spec.get("nullable") != "NO":
        return []
    if is_empty(value):
        return [_violation(KIND_NULLABLE, rule, "field must not be null or empty")]
    return []


def _validate_string_length(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    if is_empty(value):
        return []
    text = str(value)
    length = len(text)
    mode = spec.get("mode")
    if mode == "EXACT":
        required = spec.get("length")
        if isinstance(required, int) and not isinstance(required, bool) and length != required:
            return [_violation(KIND_STRING_LENGTH, rule, f"length {length} != required {required}")]
    elif mode == "MAX":
        maximum = spec.get("length")
        if isinstance(maximum, int) and not isinstance(maximum, bool) and length > maximum:
            return [_violation(KIND_STRING_LENGTH, rule, f"length {length} > maximum {maximum}")]
    maximum = spec.get("length_max")
    if isinstance(maximum, int) and not isinstance(maximum, bool) and length > maximum:
        return [_violation(KIND_STRING_LENGTH, rule, f"length {length} > maximum {maximum}")]
    minimum = spec.get("length_min")
    if isinstance(minimum, int) and not isinstance(minimum, bool) and length < minimum:
        return [_violation(KIND_STRING_LENGTH, rule, f"length {length} < minimum {minimum}")]
    return []


def _validate_code_domain(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = spec.get("allowed_values")
    if not isinstance(allowed, list):
        return []
    if is_empty(value):
        return []
    if value not in allowed and not any(str(value) == str(item) for item in allowed):
        return [_violation(KIND_CODE_DOMAIN, rule, f"value {value!r} outside allowed domain")]
    return []


def _format_pattern(spec: dict[str, Any]) -> re.Pattern[str] | None:
    classes = spec.get("character_classes")
    if isinstance(classes, list) and classes:
        atoms = [_CLASS_CHARS.get(item) for item in classes]
        if None in atoms:
            _fail(ERROR_INVALID_INPUT)
        charset = "".join(atoms)
        length = spec.get("exact_length")
        quantifier = f"{{{length}}}" if isinstance(length, int) and not isinstance(length, bool) and length >= 1 else "+"
        return re.compile(f"^[{charset}]{quantifier}$" if charset != "" else "^.+$")
    fmt = spec.get("format")
    if not isinstance(fmt, str) or not fmt:
        return None
    parts: list[str] = []
    for char in fmt:
        if char in _DATE_TOKENS:
            parts.append(r"\d")
        elif char.isascii() and char.isalpha():
            parts.append(r"[A-Za-z0-9]")
        else:
            parts.append(re.escape(char))
    return re.compile("^" + "".join(parts) + "$")


def _validate_encoding_rule(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    if is_empty(value):
        return []
    pattern = _format_pattern(spec)
    if pattern is None:
        return []
    text = str(value)
    if not pattern.fullmatch(text):
        return [_violation(KIND_ENCODING_RULE, rule, f"value {value!r} does not match encoding rule")]
    return []


def _validate_forbidden_value(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    if is_empty(value) or "forbidden_value" not in spec:
        return []
    forbidden = spec["forbidden_value"]
    if str(value) == str(forbidden) or to_number(value) is not None and to_number(forbidden) is not None and to_number(value) == to_number(forbidden):
        return [_violation(KIND_FORBIDDEN_VALUE, rule, f"value {value!r} is forbidden")]
    return []


def _validate_forbidden_character_set(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    if is_empty(value):
        return []
    characters = spec.get("characters")
    if not isinstance(characters, list):
        return []
    text = str(value)
    if spec.get("normalization") == "NFKC":
        text = unicodedata.normalize("NFKC", text)
    for character in characters:
        if isinstance(character, str) and character and character in text:
            return [_violation(KIND_FORBIDDEN_CHARACTER_SET, rule, f"value contains forbidden character {character!r}")]
    return []


def _validate_value_range(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    if is_empty(value):
        return []
    number = to_number(value)
    if number is None:
        return [_violation(KIND_VALUE_RANGE, rule, f"value {value!r} is not numeric")]
    minimum, maximum = spec.get("min"), spec.get("max")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and number < minimum:
        return [_violation(KIND_VALUE_RANGE, rule, f"value {number} < minimum {minimum}")]
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and number > maximum:
        return [_violation(KIND_VALUE_RANGE, rule, f"value {number} > maximum {maximum}")]
    minimum_exclusive = spec.get("min_exclusive")
    if isinstance(minimum_exclusive, (int, float)) and not isinstance(minimum_exclusive, bool) and number <= minimum_exclusive:
        return [_violation(KIND_VALUE_RANGE, rule, f"value {number} <= exclusive minimum {minimum_exclusive}")]
    maximum_exclusive = spec.get("max_exclusive")
    if isinstance(maximum_exclusive, (int, float)) and not isinstance(maximum_exclusive, bool) and number >= maximum_exclusive:
        return [_violation(KIND_VALUE_RANGE, rule, f"value {number} >= exclusive maximum {maximum_exclusive}")]
    return []


def _validate_data_type(spec: dict[str, Any], value: Any, rule: dict[str, Any]) -> list[dict[str, Any]]:
    if is_empty(value):
        return []
    data_type = spec.get("data_type")
    number = to_number(value)
    if data_type == "INTEGER":
        if number is None or (isinstance(number, float) and not number.is_integer()):
            return [_violation(KIND_DATA_TYPE, rule, f"value {value!r} is not an integer")]
        maximum_digits = spec.get("integer_max_digits")
        if isinstance(maximum_digits, int) and not isinstance(maximum_digits, bool):
            digits = len(str(abs(int(number))))
            if digits > maximum_digits:
                return [_violation(KIND_DATA_TYPE, rule, f"integer has {digits} digits > maximum {maximum_digits}")]
    elif data_type == "DECIMAL":
        if number is None:
            return [_violation(KIND_DATA_TYPE, rule, f"value {value!r} is not a decimal")]
        maximum_fraction = spec.get("decimal_max_fraction_digits")
        if isinstance(maximum_fraction, int) and not isinstance(maximum_fraction, bool):
            text = str(value).strip()
            fraction = text.split(".", 1)[1] if "." in text else ""
            if len(fraction) > maximum_fraction:
                return [_violation(KIND_DATA_TYPE, rule, f"decimal has {len(fraction)} fraction digits > maximum {maximum_fraction}")]
    elif data_type == "STRING":
        exact = spec.get("string_length_exact")
        if isinstance(exact, int) and not isinstance(exact, bool) and len(str(value)) != exact:
            return [_violation(KIND_DATA_TYPE, rule, f"string length {len(str(value))} != required {exact}")]
        maximum = spec.get("string_length_max")
        if isinstance(maximum, int) and not isinstance(maximum, bool) and len(str(value)) > maximum:
            return [_violation(KIND_DATA_TYPE, rule, f"string length {len(str(value))} > maximum {maximum}")]
    else:
        _fail(ERROR_UNKNOWN_RULE_KIND)
    return []


def _validate_unique(spec: dict[str, Any], values: list[Any], rule: dict[str, Any], *, primary: bool) -> list[dict[str, Any]]:
    if spec.get("unique") is not True and spec.get("primary_key") is not True:
        return []
    kind = KIND_PRIMARY_KEY if primary else KIND_UNIQUE
    violations: list[dict[str, Any]] = []
    seen: set[Any] = set()
    seen_numeric: set[int | float] = set()
    for index, value in enumerate(values):
        if is_empty(value):
            if primary:
                violations.append(_violation(kind, rule, "primary-key field must not be null or empty", record_index=index))
            continue
        number = to_number(value)
        duplicate = value in seen or (number is not None and number in seen_numeric)
        if duplicate:
            violations.append(_violation(kind, rule, f"duplicate value {value!r}", record_index=index))
        seen.add(value)
        if number is not None:
            seen_numeric.add(number)
    return violations


_SINGLE_VALUE_VALIDATORS = {
    KIND_NULLABLE: _validate_nullable,
    KIND_STRING_LENGTH: _validate_string_length,
    KIND_CODE_DOMAIN: _validate_code_domain,
    KIND_ENCODING_RULE: _validate_encoding_rule,
    KIND_FORBIDDEN_VALUE: _validate_forbidden_value,
    KIND_FORBIDDEN_CHARACTER_SET: _validate_forbidden_character_set,
    KIND_VALUE_RANGE: _validate_value_range,
    KIND_DATA_TYPE: _validate_data_type,
}


def validate_field(rule: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    """Validate a single field value against one approved field rule."""
    kind, _constraint_id, _asset_id, _asset_version, _endpoint, spec = _rule(rule)
    if kind in _COLUMN_KINDS:
        _fail(ERROR_INVALID_INPUT)
    validator = _SINGLE_VALUE_VALIDATORS[kind]
    return validator(spec, value, rule)


def validate_field_column(rule: dict[str, Any], values: list[Any]) -> list[dict[str, Any]]:
    """Validate a whole column for UNIQUE / PRIMARY_KEY rules."""
    kind, _constraint_id, _asset_id, _asset_version, _endpoint, spec = _rule(rule)
    if kind not in _COLUMN_KINDS:
        _fail(ERROR_INVALID_INPUT)
    if not isinstance(values, list):
        _fail(ERROR_INVALID_INPUT)
    return _validate_unique(spec, values, rule, primary=(kind == KIND_PRIMARY_KEY))
