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

from east_v5.artifacts import artifact_ref, content_hash
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
    probe_241_mod = importlib.import_module("east_v5.agents.241.probe")
    validator_mod = importlib.import_module("east_v5.agents.242.validator")
    runtime_mod = importlib.import_module("east_v5.agents.242.sanitized_fixture")
    regression_mod = importlib.import_module("east_v5.agents.260.regression")
    runtime = runtime_mod.SanitizedRuntime()
    invocation_runtime = probe_241_mod.SanitizedProbeInvocationRuntime()
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
        context_payload = {"schema_version": "v5.foundation-generation-context/v1", "context_id": "eas37-sanitized-context", "foundation_task_ref": artifact_ref(task["envelope"]), "structure_closure_ref": artifact_ref(closure["envelope"]), "resolver_universe_ref": {"artifact_id": "eas37-sanitized-universe", "version": 1, "content_hash": "d" * 64}, "database_snapshot_ref": artifact_ref(snapshot["envelope"]), "snapshot_hash": snapshot_payload["snapshot_hash"], "hierarchy_refs": task["payload"]["hierarchy_asset_refs"], "catalog_refs": [{"artifact_id": "eas37-sanitized-catalog", "version": 1, "content_hash": "c" * 64}], "base_date": "2026-08-25", "seed": "eas37-sanitized-seed", "parent_record_refs": [], "deterministic_rules": []}
        context_envelope = {"artifact_id": "eas37-sanitized-context", "artifact_type": "foundation_generation_context", "run_id": task["envelope"]["run_id"], "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "EAS-19", "parent_artifact_refs": [artifact_ref(task["envelope"]), artifact_ref(closure["envelope"]), artifact_ref(snapshot["envelope"])], "input_hashes": [task["envelope"]["content_hash"], closure["envelope"]["content_hash"], snapshot["envelope"]["content_hash"]], "status": "candidate", "mode": "foundation", "created_at": TIME, "trace_id": task["envelope"]["trace_id"], "storage_locator": None}
        context_envelope["content_hash"] = content_hash(context_envelope, context_payload)
        context = {"envelope": context_envelope, "payload": context_payload}
        record = {"record_id": "eas37-customer", "record_type": "foundation_object", "table_id": "FIXTURE_CUSTOMER", "field_values": [{"field_id": "C001", "value": "EAS37-CUST-001", "standard_type": "STRING", "is_null": False}, {"field_id": "C002", "value": "EAS37-CUSTOMER", "standard_type": "STRING", "is_null": False}], "existing_record_refs": [], "temporary_record_refs": [], "value_provenance": [{"source_type": "foundation_task_package", "source_ref": "EAS37-sanitized"}], "case_role": "foundation", "target_condition_refs": [], "constraint_refs": []}
        groups = [{"data_group_id": "eas37-sanitized-group", "records": [record], "record_links": [], "group_summary": generator_mod.BoundDataGenerator._summarize([record])}]
        traces = [{"record_id": record["record_id"], "field_id": f"FIXTURE_CUSTOMER.{item['field_id']}", "feasible_values": [item["value"]], "deterministic_rule_id": None, "chosen_value": item["value"], "business_reason": "满足冻结约束并保持基础对象语义一致", "constraint_refs": ["EAS37-sanitized-constraint"], "source_refs": ["EAS37-sanitized-catalog"], "tie_break_seed": None, "batch_distribution_before": {}, "batch_distribution_after": {}} for item in record["field_values"]]
        receipt = invocation_runtime.issue(task, context, groups, traces)
        bound = generator_mod.BoundDataGenerator(root, foundation_invocation_verifier=invocation_runtime).build_bound_data(closure, foundation_task_package=task, foundation_profile=profile, snapshot=snapshot, foundation_generation_context=context, proposed_data_groups=groups, selection_traces=traces, generation_receipt=receipt, created_at=TIME)
        verified = validator_mod.DataValidator(root, foundation_invocation_verifier=invocation_runtime).freeze_bound_data(bound, closure, runtime.resolver(), foundation_task_package=task, database_snapshot=snapshot, foundation_generation_context=context)
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
