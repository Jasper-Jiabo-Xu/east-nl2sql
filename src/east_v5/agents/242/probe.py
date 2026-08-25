"""Sanitized, independently replayable Agent-242 runtime probe.

It uses only committed desensitized constants and never touches the formal
store, the reference root, or any real database.  ``run_sanitized_probe`` walks
the full 242 contract — event and foundation validation driven by a real
``ConstraintAssetService`` (constructed via ``build_constraint_asset_resolver``
over a sanitized runtime fixture, never a ``FixtureQueryService``) plus field /
intra-table / cross-table failure feedback — proving fail-closed,
control-plane-bound enumeration and deterministic identity.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256

from .resolver import AssetBoundResolver
from .sanitized_fixture import SanitizedRuntime
from .validator import DataValidator

_agent_241_probe = importlib.import_module("east_v5.agents.241.probe")
BoundDataGenerator = importlib.import_module("east_v5.agents.241.generator").BoundDataGenerator

ROOT = Path(__file__).resolve().parents[4]
FIXED_TIME = "2026-08-16T00:00:00+00:00"


def _wrap(
    artifact_type: str, artifact_id: str, payload: dict[str, Any], *, producer: str, mode: str,
    qa_id: str | None, parents: list[dict[str, Any]] | None = None, status: str = "candidate",
) -> dict[str, Any]:
    parents = list(parents or [])
    envelope: dict[str, Any] = {
        "artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "eas32-sanitized-run",
        "qa_id": qa_id, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": 1, "producer_id": producer,
        "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents],
        "status": status, "mode": mode, "created_at": FIXED_TIME, "trace_id": "eas32-sanitized-trace",
        "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _structure() -> dict[str, Any]:
    payload = {
        "schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0", "tables": ["FIXTURE_T001", "FIXTURE_T002"],
        "fields": ["FIXTURE_T001.F001", "FIXTURE_T001.F002", "FIXTURE_T002.PK001"],
        "references": [{"type": "cross_table", "data": {"from": "FIXTURE_T001.F001", "to": "FIXTURE_T002.PK001"}}],
    }
    return _wrap("structure_closure", "eas32-structure", payload, producer="220", mode="event_data", qa_id="QA-EAS32")


def _foundation_structure() -> dict[str, Any]:
    payload = {
        "schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0", "tables": ["FIXTURE_CUSTOMER"],
        "fields": ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"], "references": [],
        "foundation_task_ref": {"artifact_id": "eas32-foundation-task", "version": 1, "content_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"},
    }
    return _wrap("structure_closure", "eas32-foundation-structure", payload, producer="220", mode="foundation", qa_id=None)


def _operation() -> dict[str, Any]:
    payload = {
        "schema_version": "v5.operation-closure/v1", "mode": "event",
        "operations": [{"op": "insert", "table": "FIXTURE_T001"}], "consumers": ["241", "251"],
    }
    return _wrap("operation_closure", "eas32-operation", payload, producer="230", mode="event_data", qa_id="QA-EAS32")


def _bound_data(
    structure: dict[str, Any], *, operation: dict[str, Any] | None = None, records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    mode = "foundation" if operation is None else "event_data"
    data_groups = [{
        "data_group_id": "group-eas32",
        "records": records or [],
        "record_links": [],
        "group_summary": _summarize(records or []),
    }]
    parents = [artifact_ref(structure["envelope"])]
    if operation is not None:
        parents.append(artifact_ref(operation["envelope"]))
    payload: dict[str, Any] = {
        "schema_version": "v5.bound-data/v1",
        "data_package_id": "bound-data-eas32",
        "structure_closure_ref": artifact_ref(structure["envelope"]),
        "operation_closure_ref": artifact_ref(operation["envelope"]) if operation is not None else None,
        "database_snapshot_ref": None,
        "foundation_task_ref": structure["payload"].get("foundation_task_ref"),
        "data_groups": data_groups,
    }
    return _wrap("bound_data", "bound-data-eas32", payload, producer="241", mode=mode, qa_id="QA-EAS32" if operation is not None else None, parents=parents)


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    table_counts: dict[str, int] = {}
    positive = hard_negative = background = foundation = 0
    for record in records:
        table_counts[record["table_id"]] = table_counts.get(record["table_id"], 0) + 1
        role = record["case_role"]
        if role == "positive": positive += 1
        elif role == "hard_negative": hard_negative += 1
        elif role == "background": background += 1
        else: foundation += 1
    return {
        "table_record_counts": table_counts, "positive_count": positive, "hard_negative_count": hard_negative,
        "background_count": background, "foundation_count": foundation, "object_count": len(table_counts),
    }


def _record(table_id: str, record_id: str, field_values: list[dict[str, Any]], *, role: str = "positive") -> dict[str, Any]:
    return {
        "record_id": record_id, "table_id": table_id, "field_values": field_values,
        "existing_record_refs": [], "temporary_record_refs": [],
        "value_provenance": [{"source_type": "structure_closure_constraint", "source_ref": "CA-V0.3.0"}],
        "case_role": role, "target_condition_refs": [], "constraint_refs": [],
    }


def _field_value(field_id: str, value: Any, *, standard_type: str = "STRING", is_null: bool = False) -> dict[str, Any]:
    return {"field_id": field_id, "value": value, "standard_type": standard_type, "is_null": is_null}


def _valid_event_records() -> list[dict[str, Any]]:
    return [
        _record("FIXTURE_T001", "rec-t1", [_field_value("F001", "A"), _field_value("F002", "B")]),
        _record("FIXTURE_T002", "rec-t2", [_field_value("PK001", "A")], role="background"),
    ]


def _valid_foundation_records() -> list[dict[str, Any]]:
    return [
        _record("FIXTURE_CUSTOMER", "rec-c1", [_field_value("C001", "X"), _field_value("C002", "Y")], role="foundation"),
    ]


def _eas114_foundation_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """A sanitized stand-in for the runtime-only EAS-19 context, never DB data."""
    task = _agent_241_probe._foundation_task()
    closure = _agent_241_probe._foundation_structure_closure(task)
    profile = _agent_241_probe._foundation_profile(task)
    snapshot = _agent_241_probe._snapshot("foundation")
    payload = {"schema_version": "v5.foundation-generation-context/v1", "context_id": "eas114-probe-context", "foundation_task_ref": artifact_ref(task["envelope"]), "structure_closure_ref": artifact_ref(closure["envelope"]), "resolver_universe_ref": {"artifact_id": "eas114-probe-universe", "version": 1, "content_hash": "d" * 64}, "database_snapshot_ref": artifact_ref(snapshot["envelope"]), "snapshot_hash": snapshot["payload"]["snapshot_hash"], "hierarchy_refs": task["payload"]["hierarchy_asset_refs"], "catalog_refs": [{"artifact_id": "eas114-probe-catalog", "version": 1, "content_hash": "c" * 64}], "base_date": "2026-08-25", "seed": "eas114-probe-seed", "parent_record_refs": [], "deterministic_rules": []}
    context = _wrap("foundation_generation_context", "eas114-probe-context", payload, producer="EAS-19", mode="foundation", qa_id=None, parents=[artifact_ref(task["envelope"]), artifact_ref(closure["envelope"]), artifact_ref(snapshot["envelope"])])
    records = _valid_foundation_records()
    groups = [{"data_group_id": "eas114-probe-group", "records": records, "record_links": [], "group_summary": _summarize(records)}]
    traces = [{"record_id": "rec-c1", "field_id": f"FIXTURE_CUSTOMER.{item['field_id']}", "feasible_values": [item["value"]], "deterministic_rule_id": None, "chosen_value": item["value"], "business_reason": "满足冻结约束并保持基础对象语义一致", "constraint_refs": ["EAS114-probe-constraint"], "source_refs": ["EAS114-probe-catalog"], "tie_break_seed": None, "batch_distribution_before": {}, "batch_distribution_after": {}} for item in records[0]["field_values"]]
    receipt = {"agent_id": "241-初始数据生成与修改agent", "generation_kind": "business_agent", "input_context_ref": artifact_ref(context["envelope"]), "output_hash": sha256({"data_groups": groups, "selection_traces": traces})}
    return task, closure, profile, snapshot, groups, traces, context | {"receipt": receipt}


def run_sanitized_probe(repo_root: Path) -> dict[str, Any]:
    runtime = SanitizedRuntime()
    try:
        resolver: AssetBoundResolver = runtime.resolver()
        validator = DataValidator(repo_root)
        structure = _structure()
        operation = _operation()

        event = _bound_data(structure, operation=operation, records=_valid_event_records())
        frozen = validator.freeze_bound_data(event, structure, resolver)
        repeated = validator.freeze_bound_data(event, structure, resolver)
        input_immutable = (frozen == repeated)

        task, foundation_structure, profile, foundation_snapshot, foundation_groups, foundation_traces, context_data = _eas114_foundation_inputs()
        context, receipt = {key: value for key, value in context_data.items() if key != "receipt"}, context_data["receipt"]
        foundation = BoundDataGenerator(repo_root).build_bound_data(foundation_structure, foundation_task_package=task, foundation_profile=profile, snapshot=foundation_snapshot, foundation_generation_context=context, proposed_data_groups=foundation_groups, selection_traces=foundation_traces, generation_receipt=receipt, created_at=FIXED_TIME)
        foundation_frozen = validator.freeze_bound_data(foundation, foundation_structure, resolver, foundation_task_package=task, database_snapshot=foundation_snapshot, foundation_generation_context=context)

        defective = _bound_data(structure, operation=operation, records=[
            _record("FIXTURE_T001", "rec-t1", [_field_value("F001", "A"), _field_value("F002", None, is_null=True)]),
            _record("FIXTURE_T002", "rec-t2", [_field_value("PK001", "A")], role="background"),
        ])

        def rejected(callback) -> bool:
            try:
                callback()
            except ContractError:
                return True
            return False

        feedback = validator.build_validation_feedback(defective, structure, resolver)

        report = frozen["payload"]["data_validation_report"]
        universe = report["constraint_universe"]
        return {
            "transport": frozen,
            "summary": {
                "verified_artifact_ref": artifact_ref(frozen["envelope"]),
                "validated_hash": frozen["payload"]["validated_hash"],
                "validated_at": frozen["payload"]["validated_at"],
                "total_checks": report["total_checks"],
                "universe_sources": sorted(universe["sources"].keys()),
                "universe_constraint_count": len(universe["constraints"]),
                "module_results": report["module_results"],
                "input_immutable": input_immutable,
                "field_defect_rejected": rejected(lambda: validator.freeze_bound_data(defective, structure, resolver)),
                "feedback_decision": feedback["payload"]["decision"],
                "feedback_items": len(feedback["payload"]["failed_items"]),
                "foundation_total_checks": foundation_frozen["payload"]["data_validation_report"]["total_checks"],
            },
        }
    finally:
        runtime.close()


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe(ROOT)["summary"], ensure_ascii=False, sort_keys=True))
