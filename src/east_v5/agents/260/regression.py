"""Foundation-only regression input gate for Agent 260.

The function deliberately returns a plan, not a database handle: callers can
execute the fixed compiler output only against their isolated database copy.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, validate_envelope
from east_v5.governance import ContractError, sha256


def _fail(code: str) -> None:
    raise ContractError(code)


_closure = importlib.import_module("east_v5.agents.220.closure")


def _records(verified: dict[str, Any]) -> list[dict[str, Any]]:
    return [record for group in verified["payload"]["validated_data_package"]["data_groups"] for record in group["records"]]


def validate_foundation_regression_inputs(
    repo_root: Path, task_package: dict[str, Any], structure_closure: dict[str, Any],
    verified_bound_data: dict[str, Any], database_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Verify all frozen Foundation inputs without inferring intent from data."""
    task_envelope, task = _closure.validate_foundation_task_package(task_package)
    _closure.validate_structure_closure_package(structure_closure)
    if structure_closure["envelope"]["mode"] != "foundation":
        _fail("FOUNDATION_MODE_REQUIRED")
    task_ref = artifact_ref(task_envelope)
    if structure_closure["payload"].get("foundation_task_ref") != task_ref:
        _fail("FOUNDATION_TASK_REF_DRIFT")
    if set(verified_bound_data) != {"envelope", "payload"}:
        _fail("TRANSPORT_PACKAGE_INVALID")
    envelope, payload = verified_bound_data["envelope"], verified_bound_data["payload"]
    validate_envelope(repo_root, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"], envelope["status"]) != ("verified_bound_data", "242", "foundation", "validated"):
        _fail("VERIFIED_BOUND_DATA_ENVELOPE_INVALID")
    data = payload.get("validated_data_package")
    if not isinstance(data, dict) or data.get("foundation_task_ref") != task_ref:
        _fail("FOUNDATION_TASK_REF_DRIFT")
    if data.get("structure_closure_ref") != artifact_ref(structure_closure["envelope"]):
        _fail("STRUCTURE_CLOSURE_REFERENCE_MISMATCH")
    if data.get("operation_closure_ref") is not None:
        _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
    if set(database_snapshot) != {"envelope", "payload"}:
        _fail("DATABASE_SNAPSHOT_INVALID")
    snapshot_envelope, snapshot = database_snapshot["envelope"], database_snapshot["payload"]
    validate_envelope(repo_root, snapshot_envelope, snapshot)
    if (snapshot_envelope["artifact_type"], snapshot_envelope["producer_id"]) != ("database_read_snapshot", "EAS-19"):
        _fail("DATABASE_SNAPSHOT_INVALID")
    if snapshot.get("base_database_version") != task["target_database_version"]:
        _fail("DATABASE_VERSION_DRIFT")
    if data.get("database_snapshot_ref") != artifact_ref(snapshot_envelope):
        _fail("DATABASE_SNAPSHOT_REF_DRIFT")
    if not isinstance(snapshot.get("snapshot_hash"), str) or len(snapshot["snapshot_hash"]) != 64:
        _fail("DATABASE_SNAPSHOT_HASH_INVALID")

    records = _records(verified_bound_data)
    actual_counts: dict[str, int] = {}
    for record in records:
        table = record["table_id"]
        if table not in task["target_table_field_scope"]:
            _fail("FOUNDATION_SCOPE_OUT_OF_BOUNDS")
        if record["case_role"] != "foundation":
            _fail("FOUNDATION_RECORD_ROLE_INVALID")
        fields = {value["field_id"] for value in record["field_values"]}
        if not fields <= set(task["target_table_field_scope"][table]):
            _fail("FOUNDATION_SCOPE_OUT_OF_BOUNDS")
        if record.get("record_type") in task["prohibited_record_types"]:
            _fail("FOUNDATION_PROHIBITED_TYPE_HIT")
        actual_counts[table] = actual_counts.get(table, 0) + 1
    if actual_counts != task["target_counts"]:
        _fail("FOUNDATION_TARGET_COUNT_MISMATCH")
    if not task["distribution_targets"] or not task["hierarchy_asset_refs"]:
        _fail("FOUNDATION_TASK_ASSET_INVALID")
    return {
        "task_ref": task_ref, "verified_data_ref": artifact_ref(envelope),
        "snapshot_ref": artifact_ref(snapshot_envelope), "snapshot_hash": snapshot["snapshot_hash"],
        "record_counts": actual_counts, "input_sha256": sha256({"task": task_ref, "closure": artifact_ref(structure_closure["envelope"]), "verified": artifact_ref(envelope), "snapshot": artifact_ref(snapshot_envelope)}),
        "writes_formal_store": False,
    }
