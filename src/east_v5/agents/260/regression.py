"""Strict Foundation database-copy regression gate for Agent 260."""
from __future__ import annotations

import hashlib
import importlib
import sqlite3
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, validate_envelope
from east_v5.foundation.compiler import compile_insert_batch
from east_v5.governance import ContractError, load_json, sha256

_closure = importlib.import_module("east_v5.agents.220.closure")

_PLAN_KEYS = {
    "task_ref", "structure_closure_ref", "verified_data_ref", "verified_validated_hash",
    "snapshot_ref", "snapshot_hash", "record_counts", "input_sha256",
    "writes_formal_store", "plan_sha256",
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _registry(repo_root: Path) -> Registry:
    resources = []
    for relative in ("contracts/common/common-envelope.schema.json", "contracts/v5-runtime-packages.schema.json"):
        schema = load_json(repo_root / relative)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    for path in (repo_root / "contracts" / "packages").glob("*.schema.json"):
        schema = load_json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _validate_schema(repo_root: Path, package: dict[str, Any], relative: str, label: str) -> None:
    try:
        Draft202012Validator(load_json(repo_root / relative), registry=_registry(repo_root)).validate(package)
    except ValidationError as exc:
        raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc


def _records(verified: dict[str, Any]) -> list[dict[str, Any]]:
    return [record for group in verified["payload"]["validated_data_package"]["data_groups"] for record in group["records"]]


def _distribution(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    actual: dict[str, dict[str, int]] = {}
    for record in records:
        labels = [item.removeprefix("distribution:") for item in record["target_condition_refs"] if item.startswith("distribution:")]
        if len(labels) > 1:
            _fail("FOUNDATION_DISTRIBUTION_AMBIGUOUS")
        label = labels[0] if labels else "default"
        actual.setdefault(record["table_id"], {})[label] = actual.setdefault(record["table_id"], {}).get(label, 0) + 1
    return actual


def _hierarchy_refs(closure: dict[str, Any]) -> set[tuple[str, int, str]]:
    found = set()
    for reference in closure["payload"]["references"]:
        ref = reference.get("artifact_ref") if isinstance(reference, dict) and reference.get("type") == "hierarchy_asset" else None
        if isinstance(ref, dict) and set(ref) == {"artifact_id", "version", "content_hash"}:
            found.add((ref["artifact_id"], ref["version"], ref["content_hash"]))
    return found


def _validate_references(data: dict[str, Any], snapshot: dict[str, Any]) -> None:
    records = [record for group in data["data_groups"] for record in group["records"]]
    ids = {record["record_id"] for record in records}
    if len(ids) != len(records):
        _fail("FOUNDATION_RECORD_ID_DUPLICATE")
    snapshot_keys = {(item["record_keys"]["table_id"], item["record_keys"]["primary_key"]) for item in snapshot["object_state_records"]}
    for group in data["data_groups"]:
        for record in group["records"]:
            if any(ref["record_id"] not in ids for ref in record["temporary_record_refs"]):
                _fail("FOUNDATION_REFERENTIAL_INTEGRITY_FAILED")
            if any((ref["table_id"], ref["record_key"]) not in snapshot_keys for ref in record["existing_record_refs"]):
                _fail("FOUNDATION_REFERENTIAL_INTEGRITY_FAILED")
        for link in group["record_links"]:
            if link["source_record_id"] not in ids or link["target_record_id"] not in ids:
                _fail("FOUNDATION_REFERENTIAL_INTEGRITY_FAILED")


def _plan_hash(plan: dict[str, Any]) -> str:
    return sha256({key: value for key, value in plan.items() if key != "plan_sha256"})


def _validate_execution_plan(plan: dict[str, Any]) -> None:
    if set(plan) != _PLAN_KEYS:
        _fail("REGRESSION_PLAN_FIELDS_INVALID")
    if plan["writes_formal_store"] is not False or not isinstance(plan["record_counts"], dict):
        _fail("REGRESSION_PLAN_INVALID")
    expected_input = sha256({"task": plan["task_ref"], "closure": plan["structure_closure_ref"], "verified": plan["verified_data_ref"], "snapshot": plan["snapshot_ref"]})
    if plan["input_sha256"] != expected_input:
        _fail("REGRESSION_PLAN_INPUT_DRIFT")
    if plan["plan_sha256"] != _plan_hash(plan):
        _fail("REGRESSION_PLAN_HASH_DRIFT")


def _validate_execution_package(repo_root: Path, plan: dict[str, Any], verified_bound_data: dict[str, Any]) -> None:
    """Re-authenticate the package at the write boundary, rather than trusting plan construction."""
    _validate_schema(repo_root, verified_bound_data, "contracts/packages/verified-bound-data-package.schema.json", "VERIFIED_BOUND_DATA")
    envelope, payload = verified_bound_data["envelope"], verified_bound_data["payload"]
    validate_envelope(repo_root, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"], envelope["status"]) != ("verified_bound_data", "242", "foundation", "validated"):
        _fail("VERIFIED_BOUND_DATA_ENVELOPE_INVALID")
    data = payload["validated_data_package"]
    if artifact_ref(envelope) != plan["verified_data_ref"]:
        _fail("EXECUTION_VERIFIED_REF_DRIFT")
    if payload["validated_hash"] != plan["verified_validated_hash"] or payload["validated_hash"] != sha256(data):
        _fail("EXECUTION_VERIFIED_HASH_DRIFT")
    if data["foundation_task_ref"] != plan["task_ref"]:
        _fail("EXECUTION_TASK_REF_DRIFT")
    if data["structure_closure_ref"] != plan["structure_closure_ref"]:
        _fail("EXECUTION_CLOSURE_REF_DRIFT")
    if data["database_snapshot_ref"] != plan["snapshot_ref"] or plan["snapshot_hash"] == "":
        _fail("EXECUTION_SNAPSHOT_REF_DRIFT")
    if data["operation_closure_ref"] is not None:
        _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
    if payload["source_data_package_ref"] not in envelope["parent_artifact_refs"]:
        _fail("SOURCE_DATA_LINEAGE_MISSING")


def _database_files(connection: sqlite3.Connection) -> set[Path]:
    """Return physical database files; private :memory: databases deliberately have no file identity."""
    return {Path(row[2]).resolve() for row in connection.execute("PRAGMA database_list") if row[2]}


def _assert_copy_formal_isolated(copy_connection: sqlite3.Connection, formal_connection: sqlite3.Connection) -> None:
    if copy_connection is formal_connection or _database_files(copy_connection) & _database_files(formal_connection):
        _fail("DATABASE_COPY_FORMAL_NOT_ISOLATED")


def validate_foundation_regression_inputs(repo_root: Path, task_package: dict[str, Any], structure_closure: dict[str, Any], verified_bound_data: dict[str, Any], database_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate complete schema-valid inputs.  No profile or inferred intent is accepted."""
    task_envelope, task = _closure.validate_foundation_task_package(task_package)
    _closure.validate_structure_closure_package(structure_closure)
    _validate_schema(repo_root, verified_bound_data, "contracts/packages/verified-bound-data-package.schema.json", "VERIFIED_BOUND_DATA")
    _validate_schema(repo_root, database_snapshot, "contracts/packages/database-read-snapshot.schema.json", "DATABASE_READ_SNAPSHOT")
    envelope, payload = verified_bound_data["envelope"], verified_bound_data["payload"]
    snapshot_envelope, snapshot = database_snapshot["envelope"], database_snapshot["payload"]
    validate_envelope(repo_root, envelope, payload)
    validate_envelope(repo_root, snapshot_envelope, snapshot)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"], envelope["status"]) != ("verified_bound_data", "242", "foundation", "validated"):
        _fail("VERIFIED_BOUND_DATA_ENVELOPE_INVALID")
    if (snapshot_envelope["artifact_type"], snapshot_envelope["producer_id"], snapshot_envelope["mode"]) != ("database_read_snapshot", "EAS-19", "foundation"):
        _fail("DATABASE_SNAPSHOT_INVALID")
    task_ref = artifact_ref(task_envelope)
    if structure_closure["envelope"]["mode"] != "foundation" or structure_closure["payload"].get("foundation_task_ref") != task_ref:
        _fail("FOUNDATION_TASK_REF_DRIFT")
    data = payload["validated_data_package"]
    if data["foundation_task_ref"] != task_ref:
        _fail("FOUNDATION_TASK_REF_DRIFT")
    if data["structure_closure_ref"] != artifact_ref(structure_closure["envelope"]):
        _fail("STRUCTURE_CLOSURE_REFERENCE_MISMATCH")
    if data["operation_closure_ref"] is not None:
        _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
    if payload["validated_hash"] != sha256(data):
        _fail("VERIFIED_DATA_HASH_DRIFT")
    if payload["source_data_package_ref"] not in envelope["parent_artifact_refs"]:
        _fail("SOURCE_DATA_LINEAGE_MISSING")
    if snapshot["base_database_version"] != task["target_database_version"]:
        _fail("DATABASE_VERSION_DRIFT")
    if data["database_snapshot_ref"] != artifact_ref(snapshot_envelope):
        _fail("DATABASE_SNAPSHOT_REF_DRIFT")
    if snapshot["snapshot_hash"] != sha256({key: value for key, value in snapshot.items() if key != "snapshot_hash"}):
        _fail("DATABASE_SNAPSHOT_HASH_DRIFT")
    records = _records(verified_bound_data)
    _validate_references(data, snapshot)
    actual_counts: dict[str, int] = {}
    for record in records:
        table = record["table_id"]
        if table not in task["target_table_field_scope"] or record["case_role"] != "foundation":
            _fail("FOUNDATION_SCOPE_OUT_OF_BOUNDS")
        if record.get("record_type") in task["prohibited_record_types"]:
            _fail("FOUNDATION_PROHIBITED_TYPE_HIT")
        if not {value["field_id"] for value in record["field_values"]} <= set(task["target_table_field_scope"][table]):
            _fail("FOUNDATION_SCOPE_OUT_OF_BOUNDS")
        actual_counts[table] = actual_counts.get(table, 0) + 1
    if actual_counts != task["target_counts"]:
        _fail("FOUNDATION_TARGET_COUNT_MISMATCH")
    if _distribution(records) != task["distribution_targets"]:
        _fail("FOUNDATION_DISTRIBUTION_MISMATCH")
    expected_hierarchy = {(ref["artifact_id"], ref["version"], ref["content_hash"]) for ref in task["hierarchy_asset_refs"]}
    if not expected_hierarchy <= _hierarchy_refs(structure_closure):
        _fail("FOUNDATION_HIERARCHY_REFERENCE_INVALID")
    plan = {
        "task_ref": task_ref,
        "structure_closure_ref": artifact_ref(structure_closure["envelope"]),
        "verified_data_ref": artifact_ref(envelope),
        "verified_validated_hash": payload["validated_hash"],
        "snapshot_ref": artifact_ref(snapshot_envelope),
        "snapshot_hash": snapshot["snapshot_hash"],
        "record_counts": actual_counts,
        "input_sha256": sha256({"task": task_ref, "closure": artifact_ref(structure_closure["envelope"]), "verified": artifact_ref(envelope), "snapshot": artifact_ref(snapshot_envelope)}),
        "writes_formal_store": False,
    }
    plan["plan_sha256"] = _plan_hash(plan)
    return plan


def run_database_copy_regression(repo_root: Path, plan: dict[str, Any], verified_bound_data: dict[str, Any], copy_connection: sqlite3.Connection, formal_connection: sqlite3.Connection, event_owned_tables: set[str]) -> dict[str, Any]:
    """Execute only the authenticated plan on a physically separate copy in one atomic transaction."""
    _validate_execution_plan(plan)
    _validate_execution_package(repo_root, plan, verified_bound_data)
    _assert_copy_formal_isolated(copy_connection, formal_connection)
    formal_before = "\n".join(formal_connection.iterdump()).encode()
    source_records = _records(verified_bound_data)
    ids = {record["record_id"]: f"r{index}" for index, record in enumerate(source_records, start=1)}
    records = [{"record_id": ids[record["record_id"]], "table": record["table_id"], "values": {value["field_id"]: None if value["is_null"] else value["value"] for value in record["field_values"]}, "depends_on": [ids[item["record_id"]] for item in record["temporary_record_refs"]]} for record in source_records]
    batch = compile_insert_batch({"schema_version": "v5.foundation-verified-data/v1", "mode": "foundation", "base_database_version": "copy-bound", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "records": records}, event_owned_tables)
    before = {table: copy_connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in plan["record_counts"]}
    committed = False
    try:
        copy_connection.execute("BEGIN IMMEDIATE")
        for operation in batch["operations"]:
            copy_connection.execute(operation["sql"], operation["parameters"])
        delta = {table: copy_connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] - before[table] for table in before}
        if delta != plan["record_counts"]:
            _fail("DATABASE_DELTA_MISMATCH")
        formal_after = "\n".join(formal_connection.iterdump()).encode()
        if hashlib.sha256(formal_before).digest() != hashlib.sha256(formal_after).digest():
            _fail("FORMAL_STORE_MUTATED")
        copy_connection.commit()
        committed = True
    except Exception:
        copy_connection.rollback()
        raise
    if not committed:
        _fail("DATABASE_COPY_COMMIT_FAILED")
    return {"batch": batch, "database_delta": delta, "rollback_verified": False, "formal_store_sha256": hashlib.sha256(formal_after).hexdigest()}
