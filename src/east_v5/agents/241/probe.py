"""A sanitized, independently replayable Agent-241 runtime probe.

It uses only committed desensitized constants.  The default CLI prints a
non-sensitive summary; ``--emit-transport`` prints the validated transport
package locally so an Agent task can prove a real package was made without
copying the payload into an Issue comment.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

from .generator import BoundDataGenerator

FIXED_TIME = "2026-08-16T00:00:00+00:00"


def _wrap(artifact_type: str, artifact_id: str, payload: dict[str, Any], *, producer: str, mode: str, qa_id: str | None, parents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parents = list(parents or [])
    envelope: dict[str, Any] = {
        "artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "eas31-sanitized-run",
        "qa_id": qa_id, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": 1, "producer_id": producer,
        "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents],
        "status": "candidate", "mode": mode, "created_at": FIXED_TIME, "trace_id": "eas31-sanitized-trace",
        "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _event_structure_closure() -> dict[str, Any]:
    payload = {
        "schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0", "tables": ["FIXTURE_T001", "FIXTURE_T002"],
        "fields": ["FIXTURE_T001.F001", "FIXTURE_T001.F002", "FIXTURE_T002.PK001"],
        "references": [{"type": "cross_table", "data": {"from": "FIXTURE_T001.F001", "to": "FIXTURE_T002.PK001"}}],
    }
    return _wrap("structure_closure", "eas31-structure", payload, producer="220", mode="event_data", qa_id="QA-EAS31")


def _foundation_structure_closure() -> dict[str, Any]:
    payload = {
        "schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0", "tables": ["FIXTURE_CUSTOMER"],
        "fields": ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"], "references": [],
    }
    return _wrap("structure_closure", "eas31-foundation-structure", payload, producer="220", mode="foundation", qa_id=None)


def _operation_closure() -> dict[str, Any]:
    payload = {
        "schema_version": "v5.operation-closure/v1", "mode": "event", "operations": [{"op": "insert", "table": "FIXTURE_T001"}],
        "consumers": ["241", "251"],
    }
    return _wrap("operation_closure", "eas31-operation", payload, producer="230", mode="event_data", qa_id="QA-EAS31")


def _foundation_profile() -> dict[str, Any]:
    payload = {
        "schema_version": "v5.foundation-profile/v1", "base_database_version": "fixture-db-v1",
        "target_classes": ["FIXTURE_CUSTOMER"], "target_counts": {"FIXTURE_CUSTOMER": 1},
        "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0",
    }
    return _wrap("foundation_profile", "eas31-profile", payload, producer="210", mode="foundation", qa_id=None)


def _snapshot(mode: str) -> dict[str, Any]:
    payload = {
        "schema_version": "v5.database-read-snapshot/v1", "snapshot_id": "eas31-snapshot",
        "base_database_version": "fixture-db-v1", "query_time": FIXED_TIME, "query_scope": "脱敏现有对象",
        "executed_queries": ["SELECT * FROM FIXTURE_T002 WHERE 1=0"],
        "object_state_records": [
            {"record_keys": {"table_id": "FIXTURE_T002", "primary_key": "PK-1"}, "data": {"PK001": "脱敏主键"}},
        ],
        "snapshot_hash": "e" * 64,
    }
    return _wrap("database_read_snapshot", "eas31-snapshot", payload, producer="EAS-19", mode=mode, qa_id="QA-EAS31" if mode == "event_data" else None)


def _fixed_groups(package: dict[str, Any]) -> list[dict[str, Any]]:
    groups = copy.deepcopy(package["payload"]["data_groups"])
    first = groups[0]["records"][0]["field_values"][0]
    first["value"] = f"{first['value']}-修订"
    groups[0]["group_summary"] = BoundDataGenerator._summarize(groups[0]["records"])
    return groups


def _validation_feedback(previous: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_version": "v5.data-validation-failed-feedback/v1",
        "data_package_ref": artifact_ref(previous["envelope"]), "decision": "fail",
        "validator_registry_version": "v5.validator-registry/v1",
        "failed_items": [{
            "failed_module_ids": ["east_v5.validators.field"],
            "constraint_ids": ["C-001"],
            "record_field_locations": [{"data_group_id": previous["payload"]["data_groups"][0]["data_group_id"], "record_id": previous["payload"]["data_groups"][0]["records"][0]["record_id"], "table_id": "FIXTURE_T001", "field_id": "F001"}],
            "expected_values": ["脱敏期望"], "actual_values": ["脱敏实际"],
            "error_details": "脱敏字段值校验失败",
        }],
    }
    return _wrap("data_validation_failed_feedback", "eas31-vfeedback", payload, producer="242", mode="event_data", qa_id="QA-EAS31", parents=[artifact_ref(previous["envelope"])])


def _regression_feedback(previous: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data",
        "input_data_refs": [artifact_ref(previous["envelope"])], "input_orm_ref": None,
        "sandbox_snapshot_id": "eas31-sandbox",
        "failure_details": {
            "error_code": "DATA_VALUE_ERROR", "error_stage": "sql_execution", "error_location": "FIXTURE_T001.F001",
            "expected_values": ["脱敏期望"], "actual_values": ["脱敏实际"], "sql_error_detail": None,
            "regression_metrics": {"positive_hit": 0},
        },
        "route_target": "241", "retry_count": 2,
    }
    return _wrap("sql_regression_failed_feedback", "eas31-rfeedback", payload, producer="260", mode="event_data", qa_id="QA-EAS31", parents=[artifact_ref(previous["envelope"])])


def _consume_242_stub(package: dict[str, Any], builder: BoundDataGenerator) -> str:
    builder.validate_bound_data(package)
    groups = package["payload"]["data_groups"]
    if not groups or not groups[0]["records"]:
        raise ContractError("242_CONSUMPTION_REJECTED")
    summary = groups[0]["group_summary"]
    if summary != BoundDataGenerator._summarize(groups[0]["records"]):
        raise ContractError("242_SUMMARY_REJECTED")
    return f"{len(groups[0]['records'])}-records"


def run_sanitized_probe(repo_root: Path) -> dict[str, Any]:
    builder = BoundDataGenerator(repo_root)
    event_closure = _event_structure_closure()
    operation = _operation_closure()
    event_snapshot = _snapshot("event_data")
    event = builder.build_bound_data(event_closure, operation_closure=operation, snapshot=event_snapshot, created_at=FIXED_TIME)
    builder.validate_bound_data(event)

    foundation_closure = _foundation_structure_closure()
    profile = _foundation_profile()
    foundation = builder.build_bound_data(foundation_closure, foundation_profile=profile, created_at=FIXED_TIME)
    builder.validate_bound_data(foundation)

    feedback = _validation_feedback(event)
    remapped = builder.apply_validation_feedback(event, feedback, event_closure, snapshot=event_snapshot, proposed_data_groups=_fixed_groups(event), created_at=FIXED_TIME)
    builder.validate_bound_data(remapped)

    regression = _regression_feedback(remapped)
    blocked = builder.apply_regression_feedback(remapped, regression, event_closure, snapshot=event_snapshot, proposed_data_groups=_fixed_groups(remapped), created_at=FIXED_TIME)
    builder.validate_bound_data(blocked)

    manifest = builder.build_manifest(blocked, issue_key="EAS-31")

    corrupted = copy.deepcopy(event_closure)
    corrupted["payload"]["fields"] = ["FIXTURE_T001.F001"]
    try:
        builder.validate_structure_closure(corrupted)
    except ContractError as exc:
        bad_hash_rejection = str(exc) == "CONTENT_HASH_DRIFT"
    else:
        bad_hash_rejection = False
    if not bad_hash_rejection:
        raise ContractError("SANITIZED_PROBE_BAD_HASH_NOT_REJECTED")

    consumer_242 = _consume_242_stub(event, builder)

    foundation_no_operation = False
    try:
        builder.build_bound_data(foundation_closure, foundation_profile=profile, operation_closure=operation, created_at=FIXED_TIME)
    except ContractError as exc:
        foundation_no_operation = str(exc) == "FOUNDATION_OPERATION_CLOSURE_FORBIDDEN"
    if not foundation_no_operation:
        raise ContractError("SANITIZED_PROBE_FOUNDATION_OP_FORBIDDEN_NOT_REJECTED")

    return {
        "transport": blocked,
        "summary": {
            "artifact_ref": artifact_ref(blocked["envelope"]),
            "content_hash": blocked["envelope"]["content_hash"],
            "event_records": len(event["payload"]["data_groups"][0]["records"]),
            "foundation_records": len(foundation["payload"]["data_groups"][0]["records"]),
            "attempt2_remapped": remapped["envelope"]["attempt_no"] == 2 and remapped["envelope"]["version"] == 2,
            "attempt3_blocked": blocked["envelope"]["attempt_no"] == 3 and blocked["envelope"]["status"] == "blocked_manual",
            "bad_hash_rejected": bad_hash_rejection,
            "foundation_operation_rejected": foundation_no_operation,
            "stub_242_consumed": consumer_242,
            "manifest_ok": manifest["issue_key"] == "EAS-31",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-transport", action="store_true")
    args = parser.parse_args()
    result = run_sanitized_probe(Path(__file__).resolve().parents[4])
    print(json.dumps(result["transport"] if args.emit_transport else result["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
