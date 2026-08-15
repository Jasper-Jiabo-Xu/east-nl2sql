"""EAS-17 deterministic, read-only data validators for the 242 agent.

Layers: ``field`` (single-field / data-element), ``table`` (intra-table) and
``cross_table`` (cross-table reference and comparison).  All validators return
``YES``/``NO`` verdicts through :mod:`east_v5.validators.result` and never
mutate candidate data or the formal store.
"""
from __future__ import annotations

from east_v5.validators.registry import build_registry, dispatch_validator, validators_for_rule_kind, verify_registry
from east_v5.validators.result import (
    RESULT_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION,
    VERDICT_FAIL,
    VERDICT_PASS,
    make_result,
    make_violation,
    verify_result,
)
from east_v5.validators.snapshot import Snapshot, Table, is_empty, split_endpoint

__all__ = [
    "build_registry",
    "dispatch_validator",
    "validators_for_rule_kind",
    "verify_registry",
    "RESULT_SCHEMA_VERSION",
    "REGISTRY_SCHEMA_VERSION",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "make_result",
    "make_violation",
    "verify_result",
    "Snapshot",
    "Table",
    "is_empty",
    "split_endpoint",
]
