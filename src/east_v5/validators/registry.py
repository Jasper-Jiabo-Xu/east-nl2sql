"""Immutable validator registry: version, rule-kind index and coverage matrix.

The registry is the single place that maps every supported rule kind to the
validator module(s) that evaluate it and the frozen asset version that supplies
it.  Two comparison kinds (``CONDITIONAL_COMPARISON`` and
``CONDITIONAL_VALUE_EXCLUSION``) can occur either inside one table or across
tables, so the registry also exposes the scope-aware dispatch used by the rule
loader.  ``content_sha256`` is a deterministic digest so downstream 242/252
consumers can detect drift before trusting a validator set.
"""
from __future__ import annotations

from typing import Any

from east_v5.governance import ContractError, sha256

from east_v5.validators.cross_table import CROSS_TABLE_RULE_KINDS
from east_v5.validators.expression import (
    KIND_COMPARISON,
    KIND_CONDITIONAL_COMPARISON,
    KIND_CONDITIONAL_VALUE_EXCLUSION,
    KIND_REFERENCE_EXISTENCE,
)
from east_v5.validators.field import (
    FIELD_RULE_KINDS,
    KIND_CODE_DOMAIN,
    KIND_DATA_TYPE,
    KIND_ENCODING_RULE,
    KIND_FORBIDDEN_CHARACTER_SET,
    KIND_FORBIDDEN_VALUE,
    KIND_NULLABLE,
    KIND_PRIMARY_KEY,
    KIND_STRING_LENGTH,
    KIND_UNIQUE,
    KIND_VALUE_RANGE,
)
from east_v5.validators.result import REGISTRY_SCHEMA_VERSION, ERROR_RESULT_SCHEMA_DRIFT
from east_v5.validators.table import TABLE_RULE_KINDS

REGISTRY_VERSION = REGISTRY_SCHEMA_VERSION

FIELD_VALIDATOR = "east_v5.validators.field"
TABLE_VALIDATOR = "east_v5.validators.table"
CROSS_TABLE_VALIDATOR = "east_v5.validators.cross_table"

VALIDATOR_DEFINITIONS = [
    {
        "validator_id": FIELD_VALIDATOR,
        "layer": "field",
        "asset_version": "CA-V0.2.0",
        "rule_kinds": sorted(FIELD_RULE_KINDS),
    },
    {
        "validator_id": TABLE_VALIDATOR,
        "layer": "table",
        "asset_version": "CA-V0.3.0",
        "rule_kinds": sorted(TABLE_RULE_KINDS),
    },
    {
        "validator_id": CROSS_TABLE_VALIDATOR,
        "layer": "cross_table",
        "asset_version": "CA-V0.3.0",
        "rule_kinds": sorted(CROSS_TABLE_RULE_KINDS),
    },
]

# The ten acceptance coverage categories, mapped to the rule kinds that satisfy
# them.  "级联" is realized through referential-existence and EXISTS_IN
# exclusion (a value is checked against a joined provider relation), not through
# physical foreign-key cascades (which V5 forbids auto-generating).
COVERAGE_CATEGORIES = {
    "type": [KIND_DATA_TYPE],
    "format": [KIND_ENCODING_RULE, KIND_FORBIDDEN_CHARACTER_SET],
    "code_value": [KIND_CODE_DOMAIN, KIND_FORBIDDEN_VALUE],
    "null": [KIND_NULLABLE],
    "range": [KIND_VALUE_RANGE, KIND_STRING_LENGTH],
    "conditional_required": [KIND_REFERENCE_EXISTENCE, KIND_CONDITIONAL_COMPARISON, KIND_CONDITIONAL_VALUE_EXCLUSION],
    "comparison": [KIND_COMPARISON, KIND_CONDITIONAL_COMPARISON],
    "cascade": [KIND_REFERENCE_EXISTENCE, KIND_CONDITIONAL_VALUE_EXCLUSION],
    "reference": [KIND_REFERENCE_EXISTENCE],
    "cross_record": [KIND_UNIQUE, KIND_PRIMARY_KEY, KIND_CONDITIONAL_COMPARISON, KIND_CONDITIONAL_VALUE_EXCLUSION],
}

_ALL_KINDS = sorted(FIELD_RULE_KINDS | TABLE_RULE_KINDS | CROSS_TABLE_RULE_KINDS)

_RULE_KIND_VALIDATORS = {
    kind: sorted({definition["validator_id"] for definition in VALIDATOR_DEFINITIONS if kind in definition["rule_kinds"]})
    for kind in _ALL_KINDS
}


def validators_for_rule_kind(rule_kind: str) -> list[str]:
    """The validator module(s) able to evaluate a rule kind."""
    if rule_kind not in _RULE_KIND_VALIDATORS:
        raise ContractError("UNKNOWN_RULE_KIND")
    return list(_RULE_KIND_VALIDATORS[rule_kind])


def dispatch_validator(rule_kind: str, scope: str | None = None) -> str:
    """Resolve the single validator for a rule, using scope for the split kinds."""
    if rule_kind in FIELD_RULE_KINDS:
        return FIELD_VALIDATOR
    if rule_kind == KIND_REFERENCE_EXISTENCE:
        return CROSS_TABLE_VALIDATOR
    if rule_kind in TABLE_RULE_KINDS and rule_kind in CROSS_TABLE_RULE_KINDS:
        if scope == "INTRA_TABLE":
            return TABLE_VALIDATOR
        if scope == "CROSS_TABLE":
            return CROSS_TABLE_VALIDATOR
        raise ContractError("SCOPE_REQUIRED")
    if rule_kind in TABLE_RULE_KINDS:
        return TABLE_VALIDATOR
    if rule_kind in CROSS_TABLE_RULE_KINDS:
        return CROSS_TABLE_VALIDATOR
    raise ContractError("UNKNOWN_RULE_KIND")


def build_registry() -> dict[str, Any]:
    """Assemble the deterministic registry with coverage matrix and hash."""
    coverage = {
        category: sorted({validator for kind in kinds for validator in validators_for_rule_kind(kind)})
        for category, kinds in COVERAGE_CATEGORIES.items()
    }
    registry: dict[str, Any] = {
        "schema_version": REGISTRY_VERSION,
        "validators": VALIDATOR_DEFINITIONS,
        "rule_kind_index": dict(sorted(_RULE_KIND_VALIDATORS.items())),
        "coverage_categories": coverage,
        "content_sha256": "",
    }
    registry["content_sha256"] = sha256(registry)
    return registry


def verify_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Validate the registry's shape and content hash before use."""
    if not isinstance(registry, dict) or set(registry) != {"schema_version", "validators", "rule_kind_index", "coverage_categories", "content_sha256"}:
        raise ContractError(ERROR_RESULT_SCHEMA_DRIFT)
    if registry["schema_version"] != REGISTRY_VERSION:
        raise ContractError(ERROR_RESULT_SCHEMA_DRIFT)
    if registry["validators"] != VALIDATOR_DEFINITIONS:
        raise ContractError(ERROR_RESULT_SCHEMA_DRIFT)
    if registry["rule_kind_index"] != dict(sorted(_RULE_KIND_VALIDATORS.items())):
        raise ContractError(ERROR_RESULT_SCHEMA_DRIFT)
    supplied = registry["content_sha256"]
    probe = dict(registry)
    probe["content_sha256"] = ""
    if not isinstance(supplied, str) or supplied != sha256(probe):
        raise ContractError(ERROR_RESULT_SCHEMA_DRIFT)
    return registry
