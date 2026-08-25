"""Sanitized EAS-114 Foundation-agent contract fixtures for integration tests."""
from __future__ import annotations

from typing import Any

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import sha256


def context(task: dict[str, Any], closure: dict[str, Any], snapshot: dict[str, Any], *, created_at: str) -> dict[str, Any]:
    payload = {
        "schema_version": "v5.foundation-generation-context/v1", "context_id": "eas114-sanitized-context",
        "foundation_task_ref": artifact_ref(task["envelope"]), "structure_closure_ref": artifact_ref(closure["envelope"]),
        "resolver_universe_ref": {"artifact_id": "eas114-sanitized-universe", "version": 1, "content_hash": "u".replace("u", "d") * 64},
        "database_snapshot_ref": artifact_ref(snapshot["envelope"]), "snapshot_hash": snapshot["payload"]["snapshot_hash"],
        "hierarchy_refs": task["payload"]["hierarchy_asset_refs"],
        "catalog_refs": [{"artifact_id": "eas114-sanitized-catalog", "version": 1, "content_hash": "c" * 64}],
        "base_date": "2026-08-25", "seed": "eas114-sanitized-seed", "parent_record_refs": [], "deterministic_rules": [],
    }
    envelope = {
        "artifact_id": "eas114-sanitized-context", "artifact_type": "foundation_generation_context", "run_id": task["envelope"]["run_id"], "qa_id": None,
        "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1,
        "producer_id": "EAS-19", "parent_artifact_refs": [artifact_ref(task["envelope"]), artifact_ref(closure["envelope"]), artifact_ref(snapshot["envelope"])],
        "input_hashes": [task["envelope"]["content_hash"], closure["envelope"]["content_hash"], snapshot["envelope"]["content_hash"]], "status": "candidate", "mode": "foundation", "created_at": created_at, "trace_id": task["envelope"]["trace_id"], "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def groups_and_traces(closure: dict[str, Any], *, values: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    values = values or {}
    records, traces = [], []
    for table in closure["payload"]["tables"]:
        record_id = f"agent-{table.lower()}"
        fields = sorted(field.split(".", 1)[1] for field in closure["payload"]["fields"] if field.startswith(table + "."))
        field_values = []
        for index, field in enumerate(fields, 1):
            value = values.get(f"{table}.{field}", f"EAS114-{table}-{index:03d}")
            field_values.append({"field_id": field, "value": value, "standard_type": "STRING", "is_null": False})
            traces.append({"record_id": record_id, "field_id": f"{table}.{field}", "feasible_values": [value], "deterministic_rule_id": None, "chosen_value": value, "business_reason": "满足冻结约束并保持本批对象语义一致", "constraint_refs": ["EAS114-sanitized-constraint"], "source_refs": ["EAS114-sanitized-catalog"], "tie_break_seed": None, "batch_distribution_before": {}, "batch_distribution_after": {}})
        records.append({"record_id": record_id, "record_type": "foundation_object", "table_id": table, "field_values": field_values, "existing_record_refs": [], "temporary_record_refs": [], "value_provenance": [{"source_type": "foundation_task_package", "source_ref": "EAS114-sanitized"}], "case_role": "foundation", "target_condition_refs": [], "constraint_refs": []})
    summary = {"table_record_counts": {record["table_id"]: 1 for record in records}, "positive_count": 0, "hard_negative_count": 0, "background_count": 0, "foundation_count": len(records), "object_count": len(records)}
    return [{"data_group_id": "eas114-sanitized-group", "records": records, "record_links": [], "group_summary": summary}], traces


def receipt(context_package: dict[str, Any], groups: list[dict[str, Any]], traces: list[dict[str, Any]]) -> dict[str, Any]:
    return {"agent_id": "241-初始数据生成与修改agent", "generation_kind": "business_agent", "input_context_ref": artifact_ref(context_package["envelope"]), "output_hash": sha256({"data_groups": groups, "selection_traces": traces})}
