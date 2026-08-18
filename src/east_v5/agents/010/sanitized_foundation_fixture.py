"""Stable, sanitized 260 Foundation-regression material for the 010 contract.

This helper intentionally uses the real 210→220→241→242→260 production
implementations.  It contains no test-package imports, no external I/O, and no
real database; callers receive the immutable package output that 010 must
authenticate before committing a Foundation candidate.
"""
from __future__ import annotations

import importlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from east_v5.artifacts import content_hash
from east_v5.governance import ContractError, sha256

TIME = "2026-08-18T00:00:00+00:00"
HIERARCHY_REF = {"artifact_id": "TRG-V1.0.0", "version": 1, "content_hash": "b" * 64}
CA_REF = {"artifact_id": "CA-V0.3.0", "version": 1, "content_hash": "a" * 64}


@dataclass(frozen=True)
class SanitizedFoundation260Material:
    """The complete, validated 260 input lineage plus its frozen report."""

    task: dict[str, Any]
    closure: dict[str, Any]
    verified_data: dict[str, Any]
    snapshot: dict[str, Any]
    regression_report: dict[str, Any]


def _wrap_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    envelope = {"artifact_id": "eas37-foundation-snapshot", "artifact_type": "database_read_snapshot", "run_id": "eas37-foundation", "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "EAS-19", "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "foundation", "created_at": TIME, "trace_id": "eas37-foundation", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def build_sanitized_foundation_260_material(repo_root: Path) -> SanitizedFoundation260Material:
    """Build one real, fully authenticated 260 Foundation report in memory."""
    root = repo_root.resolve()
    producer = importlib.import_module("east_v5.agents.210.foundation")
    closure_mod = importlib.import_module("east_v5.agents.220.closure")
    generator_mod = importlib.import_module("east_v5.agents.241.generator")
    validator_mod = importlib.import_module("east_v5.agents.242.validator")
    runtime_mod = importlib.import_module("east_v5.agents.242.sanitized_fixture")
    regression_mod = importlib.import_module("east_v5.agents.260.regression")
    runtime = runtime_mod.SanitizedRuntime()
    copy_db = sqlite3.connect(":memory:")
    formal_db = sqlite3.connect(":memory:")
    try:
        task = producer.build_foundation_task_package({"schema_version": "v5.foundation-task-package/v1", "foundation_task_id": "eas37-foundation-initial", "foundation_mode": "initial_seed", "trigger_reason": "sanitized fixture", "target_database_version": "fixture-db-v1", "target_object_types": ["FIXTURE_CUSTOMER"], "target_table_field_scope": {"FIXTURE_CUSTOMER": ["C001", "C002"]}, "target_counts": {"FIXTURE_CUSTOMER": 1}, "distribution_targets": {"FIXTURE_CUSTOMER": {"default": 1}}, "hierarchy_asset_refs": [HIERARCHY_REF], "prohibited_record_types": ["EVENT_OWNED"], "resume_qa_ref": None, "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0"}, run_id="eas37-foundation", trace_id="eas37-foundation", created_at=TIME, parents=[CA_REF, HIERARCHY_REF])
        profile = producer.build_foundation_profile(task)
        closure = closure_mod.build_closure(task, [])
        closure["payload"]["fields"] = ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"]
        closure["payload"]["references"] = [{"type": "hierarchy_asset", "artifact_ref": HIERARCHY_REF}]
        closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"])
        snapshot_payload = {"schema_version": "v5.database-read-snapshot/v1", "snapshot_id": "eas37-foundation-snapshot", "base_database_version": "fixture-db-v1", "query_time": TIME, "query_scope": "sanitized", "executed_queries": ["SELECT 1"], "object_state_records": [], "snapshot_hash": ""}
        snapshot_payload["snapshot_hash"] = sha256({key: value for key, value in snapshot_payload.items() if key != "snapshot_hash"})
        snapshot = _wrap_snapshot(snapshot_payload)
        bound = generator_mod.BoundDataGenerator(root).build_bound_data(closure, foundation_task_package=task, foundation_profile=profile, snapshot=snapshot, created_at=TIME)
        verified = validator_mod.DataValidator(root).freeze_bound_data(bound, closure, runtime.resolver())
        for database in (copy_db, formal_db):
            database.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        report = regression_mod.run_foundation_regression(root, task, closure, verified, snapshot, copy_db, formal_db, set())
        if report["payload"].get("regression_status") != "passed":
            raise ContractError("EAS37_SANITIZED_260_REGRESSION_FAILED")
        return SanitizedFoundation260Material(task, closure, verified, snapshot, report)
    finally:
        copy_db.close()
        formal_db.close()
        runtime.close()
