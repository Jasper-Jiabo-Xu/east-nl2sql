"""Fail-closed Foundation-only 241 selection-contract checks.

The context is intentionally metadata-only: database values and locators remain
in the controlled data plane.  This module never chooses a business value; it
only proves that an actual 241 business-agent choice is reproducible from its
frozen context and that 242 can consume the same proof without mutation.
"""
from __future__ import annotations

import json
from typing import Any

from east_v5.artifacts import artifact_ref
from east_v5.governance import ContractError, sha256


PLACEHOLDER_MARKERS = ("示例值", "DEFAULT-", "脱敏值-")


def _fail(code: str) -> None:
    raise ContractError(code)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_context(
    context: dict[str, Any] | None, task: dict[str, Any], closure: dict[str, Any], snapshot: dict[str, Any] | None,
) -> None:
    if context is None:
        _fail("FOUNDATION_GENERATION_CONTEXT_REQUIRED")
    if set(context) != {"envelope", "payload"}:
        _fail("FOUNDATION_GENERATION_CONTEXT_INVALID")
    envelope, payload = context["envelope"], context["payload"]
    if (envelope.get("artifact_type"), envelope.get("producer_id"), envelope.get("mode")) != ("foundation_generation_context", "EAS-19", "foundation"):
        _fail("FOUNDATION_GENERATION_CONTEXT_ENVELOPE_INVALID")
    if payload.get("schema_version") != "v5.foundation-generation-context/v1":
        _fail("FOUNDATION_GENERATION_CONTEXT_SCHEMA_INVALID")
    if snapshot is None:
        _fail("FOUNDATION_SNAPSHOT_REQUIRED")
    if payload.get("foundation_task_ref") != artifact_ref(task["envelope"]):
        _fail("FOUNDATION_CONTEXT_TASK_REF_DRIFT")
    if payload.get("structure_closure_ref") != artifact_ref(closure["envelope"]):
        _fail("FOUNDATION_CONTEXT_CLOSURE_REF_DRIFT")
    if payload.get("database_snapshot_ref") != artifact_ref(snapshot["envelope"]):
        _fail("FOUNDATION_CONTEXT_SNAPSHOT_REF_DRIFT")
    snapshot_hash = snapshot["payload"].get("snapshot_hash")
    if not isinstance(snapshot_hash, str) or payload.get("snapshot_hash") != snapshot_hash:
        _fail("FOUNDATION_CONTEXT_SNAPSHOT_HASH_DRIFT")
    if snapshot_hash != sha256({key: value for key, value in snapshot["payload"].items() if key != "snapshot_hash"}):
        _fail("FOUNDATION_SNAPSHOT_HASH_INVALID")
    hierarchy = task["payload"].get("hierarchy_asset_refs", [])
    if payload.get("hierarchy_refs") != hierarchy:
        _fail("FOUNDATION_CONTEXT_HIERARCHY_REF_DRIFT")
    if not payload.get("catalog_refs") or not isinstance(payload.get("seed"), str) or not payload["seed"] or not isinstance(payload.get("base_date"), str):
        _fail("FOUNDATION_CONTEXT_CATALOG_OR_SEED_MISSING")
    if not isinstance(payload.get("parent_record_refs"), list) or not isinstance(payload.get("deterministic_rules"), list):
        _fail("FOUNDATION_CONTEXT_RUNTIME_DATA_INVALID")
    snapshot_refs = {(item["record_keys"]["table_id"], item["record_keys"]["primary_key"]) for item in snapshot["payload"].get("object_state_records", [])}
    for ref in payload["parent_record_refs"]:
        if not isinstance(ref, dict) or (ref.get("table_id"), ref.get("record_key")) not in snapshot_refs:
            _fail("FOUNDATION_CONTEXT_PARENT_RECORD_ORPHAN")


def validate_traces(
    data_groups: list[dict[str, Any]], traces: Any, context: dict[str, Any], generation_receipt: Any,
) -> None:
    if not isinstance(traces, list):
        _fail("FOUNDATION_SELECTION_TRACES_REQUIRED")
    if not isinstance(generation_receipt, dict):
        _fail("FOUNDATION_GENERATION_RECEIPT_REQUIRED")
    context_ref = artifact_ref(context["envelope"])
    if generation_receipt.get("agent_id") != "241-初始数据生成与修改agent" or generation_receipt.get("generation_kind") != "business_agent":
        _fail("FOUNDATION_241_REAL_AGENT_REQUIRED")
    if generation_receipt.get("input_context_ref") != context_ref:
        _fail("FOUNDATION_GENERATION_RECEIPT_CONTEXT_DRIFT")
    fields: dict[tuple[str, str], Any] = {}
    record_tables: dict[str, str] = {}
    for group in data_groups:
        for record in group["records"]:
            record_id, table = record["record_id"], record["table_id"]
            record_tables[record_id] = table
            for item in record["field_values"]:
                fields[(record_id, f"{table}.{item['field_id']}")] = None if item["is_null"] else item["value"]
    expected = set(fields)
    seen: set[tuple[str, str]] = set()
    rule_results = {item["rule_id"]: item["result"] for item in context["payload"]["deterministic_rules"] if isinstance(item, dict) and isinstance(item.get("rule_id"), str)}
    for trace in traces:
        if not isinstance(trace, dict):
            _fail("FOUNDATION_SELECTION_TRACE_INVALID")
        record_id, field_id = trace.get("record_id"), trace.get("field_id")
        key = (record_id, field_id)
        if key not in expected:
            _fail("FOUNDATION_SELECTION_TRACE_OUT_OF_SCOPE")
        if key in seen:
            _fail("FOUNDATION_SELECTION_TRACE_DUPLICATE")
        seen.add(key)
        chosen = trace.get("chosen_value")
        if chosen != fields[key]:
            _fail("FOUNDATION_SELECTION_TRACE_CHOSEN_VALUE_DRIFT")
        reason = trace.get("business_reason")
        if not isinstance(reason, str) or not reason.strip() or any(marker in reason for marker in PLACEHOLDER_MARKERS):
            _fail("FOUNDATION_SELECTION_REASON_INVALID")
        feasible = trace.get("feasible_values")
        rule_id = trace.get("deterministic_rule_id")
        if isinstance(feasible, list) and feasible:
            if _canonical(chosen) not in {_canonical(value) for value in feasible}:
                _fail("FOUNDATION_SELECTION_OUTSIDE_FEASIBLE_SET")
            if len({_canonical(value) for value in feasible}) > 1 and not isinstance(trace.get("tie_break_seed"), str):
                _fail("FOUNDATION_SELECTION_TIE_BREAK_SEED_REQUIRED")
        elif isinstance(rule_id, str) and rule_id:
            if rule_results.get(rule_id, object()) != chosen:
                _fail("FOUNDATION_SELECTION_DETERMINISTIC_RULE_DRIFT")
        else:
            _fail("FOUNDATION_SELECTION_BASIS_MISSING")
        if not trace.get("constraint_refs") or not trace.get("source_refs"):
            _fail("FOUNDATION_SELECTION_EVIDENCE_MISSING")
        if not isinstance(trace.get("batch_distribution_before"), dict) or not isinstance(trace.get("batch_distribution_after"), dict):
            _fail("FOUNDATION_SELECTION_DISTRIBUTION_MISSING")
    if seen != expected:
        _fail("FOUNDATION_SELECTION_TRACE_COVERAGE_MISMATCH")
    canonical_output = {"data_groups": data_groups, "selection_traces": traces}
    if generation_receipt.get("output_hash") != sha256(canonical_output):
        _fail("FOUNDATION_241_GENERATION_RECEIPT_HASH_DRIFT")
