"""220: deterministic, read-only structure closure construction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from east_v5.agents.east_000.extractor import validate_request_schema, validate_result_schema
from east_v5.agents.east_000.safety_gate import validate_request
from east_v5.artifacts import artifact_ref, validate_envelope
from east_v5.governance import ContractError


REPO_ROOT = Path(__file__).resolve().parents[4]
EVENT_QA_ID = "QA-EAS29-FIXTURE-001"
REVIEWED_QUESTION_SQL_FIELDS = {
    "qa_id", "candidate_ref", "query_spec_ref", "clear_question", "sql_gold",
    "sql_explanation", "approved_business_events", "specification_mapping",
    "evidence_refs", "precheck_report_ref", "deepseek_review_ref",
    "glm_review_ref", "package_hash", "approved_at",
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fail(code: str) -> None:
    raise ContractError(code)


def build_closure(profile: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    """Expand a 210 Foundation profile or event seed using 000 result records.

    This function never executes SQL or writes a database; `assets` are already
    returned by 000 and only their frozen structural fields are consumed.
    """
    if profile.get("schema_version") != "v5.foundation-profile/v1":
        _fail("SCHEMA_VERSION_UNSUPPORTED:FOUNDATION_PROFILE")
    if profile.get("constraint_asset_version") != "CA-V0.3.0" or profile.get("graph_version") != "TRG-V1.0.0":
        _fail("ASSET_VERSION_DRIFT")
    tables, fields, refs = set(), set(), []
    for target in profile.get("target_classes", []):
        if not isinstance(target, str) or not target:
            _fail("FOUNDATION_PROFILE_INVALID")
        if target.startswith("EVENT_OWNED:"):
            _fail("FOUNDATION_EVENT_OWNED_REJECTED")
        tables.add(target)
    for package in assets:
        if package.get("asset_version") not in {"CA-V0.3.0", "TRG-V1.0.0"}:
            _fail("ASSET_VERSION_DRIFT")
        for record in package.get("matched_records", []):
            data = record.get("data", {})
            if data.get("record_class") == "EVENT_OWNED":
                _fail("FOUNDATION_EVENT_OWNED_REJECTED")
            table = data.get("table_id") or data.get("table_code")
            field = data.get("field_id")
            if table: tables.add(table)
            if table and field: fields.add(f"{table}.{field}")
            if record.get("record_type") in {"cross_table", "hierarchy_reference"}:
                refs.append({"type": record["record_type"], "data": data})
    result = {"schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "tables": sorted(tables), "fields": sorted(fields), "references": sorted(refs, key=lambda x: _hash(x))}
    validate_closure(result)
    return result


def validate_closure(value: dict[str, Any]) -> None:
    required = {"schema_version", "constraint_asset_version", "graph_version", "tables", "fields", "references"}
    if set(value) != required:
        _fail("UNKNOWN_FIELD:STRUCTURE_CLOSURE")
    if value["schema_version"] != "v5.structure-closure/v1" or value["constraint_asset_version"] != "CA-V0.3.0" or value["graph_version"] != "TRG-V1.0.0":
        _fail("SCHEMA_VERSION_UNSUPPORTED:STRUCTURE_CLOSURE")
    if not all(isinstance(x, list) for x in (value["tables"], value["fields"], value["references"])):
        _fail("SCHEMA_VALIDATION_FAILED:STRUCTURE_CLOSURE")


def _validate_event_input(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the immutable 210 event package before any 000 lookup."""
    if set(event) != {"envelope", "payload"}:
        _fail("UNKNOWN_FIELD:QUESTION_SQL_DUAL_REVIEW_PASSED")
    envelope, payload = event["envelope"], event["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"], envelope["qa_id"]) != (
        "reviewed_question_sql", "210", "event_data", EVENT_QA_ID,
    ):
        _fail("EVENT_ENVELOPE_INVALID")
    if not isinstance(payload, dict) or set(payload) != REVIEWED_QUESTION_SQL_FIELDS:
        _fail("SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED")
    if payload["qa_id"] != EVENT_QA_ID:
        _fail("EVENT_QA_MISMATCH")
    if not all(isinstance(payload[key], str) and payload[key] for key in ("clear_question", "sql_gold", "sql_explanation", "approved_at")):
        _fail("SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED")
    expected = _hash({key: value for key, value in payload.items() if key != "package_hash"})
    if payload["package_hash"] != expected:
        _fail("CONTENT_HASH_DRIFT")
    return envelope, payload


def _validate_event_result(result: dict[str, Any], request_id: str, parent: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a 000 result and its immutable event-data lineage."""
    if set(result) != {"envelope", "payload"}:
        _fail("UNKNOWN_FIELD:CONSTRAINT_ASSET_PACKAGE")
    envelope, payload = result["envelope"], result["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"], envelope["qa_id"]) != (
        "constraint_asset_package", "000", "event_data", EVENT_QA_ID,
    ):
        _fail("EVENT_RESULT_ENVELOPE_INVALID")
    validate_result_schema(REPO_ROOT, payload)
    if payload["request_id"] != request_id:
        _fail("ASSET_REQUEST_ID_MISMATCH")
    if payload["asset_version"] != "CA-V0.3.0":
        _fail("ASSET_VERSION_DRIFT")
    if parent is not None and parent not in envelope["parent_artifact_refs"]:
        _fail("ASSET_RESULT_PARENT_MISSING")
    return artifact_ref(envelope)


def event_query_rounds(event: dict[str, Any], first_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Produce two schema-checked 220→000 requests for the EAS-29 event flow."""
    _validate_event_input(event)
    first = {
        "request_id": "220-event-r1", "caller_agent_id": "220", "caller_stage": "structure_closure",
        "query_purpose": "constraint_lookup", "natural_language_intent": "查询 FIXTURE_T001.F001 的字段约束",
        "target_asset_types": ["single_field"], "table_scope": ["FIXTURE_T001"],
        "field_scope": ["FIXTURE_T001.F001"], "relationship_scope": [],
        "required_output_fields": ["field_id"], "previous_request_refs": [], "max_rows": 100,
    }
    validate_request_schema(REPO_ROOT, first)
    validate_request(first)
    first_ref = _validate_event_result(first_result, first["request_id"])
    second = {
        "request_id": "220-event-r2", "caller_agent_id": "220", "caller_stage": "structure_closure",
        "query_purpose": "relationship_lookup", "natural_language_intent": "查询 FIXTURE_T001 与 FIXTURE_T002 的跨表关系",
        "target_asset_types": ["cross_table"], "table_scope": ["FIXTURE_T001", "FIXTURE_T002"],
        "field_scope": [], "relationship_scope": ["FIXTURE_T001.F001->FIXTURE_T002.PK001"],
        "required_output_fields": ["references"], "previous_request_refs": [first_ref], "max_rows": 100,
    }
    validate_request_schema(REPO_ROOT, second)
    validate_request(second)
    return [first, second]


def validate_second_event_result(result: dict[str, Any], first_result: dict[str, Any]) -> None:
    """Require the second 000 result to retain the first result as its parent."""
    first_ref = _validate_event_result(first_result, "220-event-r1")
    _validate_event_result(result, "220-event-r2", first_ref)


def build_event_closure(event: dict[str, Any], first_result: dict[str, Any], second_result: dict[str, Any]) -> dict[str, Any]:
    """Build the strict six-field closure after both event-data 000 rounds pass."""
    _validate_event_input(event)
    _validate_event_result(first_result, "220-event-r1")
    validate_second_event_result(second_result, first_result)
    tables, fields, references = set(), set(), []
    for record in first_result["payload"]["matched_records"]:
        data = record["data"]
        table, field = data.get("table_id"), data.get("field_id")
        if table:
            tables.add(table)
        if table and field:
            fields.add(f"{table}.{field}")
    for record in second_result["payload"]["matched_records"]:
        if record["record_type"] != "cross_table":
            continue
        data = record["data"]
        for endpoint in (data.get("from"), data.get("to")):
            if not isinstance(endpoint, str) or "." not in endpoint:
                _fail("SCHEMA_VALIDATION_FAILED:CROSS_TABLE_RELATIONSHIP")
            table, field = endpoint.split(".", 1)
            tables.add(table)
            fields.add(f"{table}.{field}")
        references.append({"type": "cross_table", "data": data})
    result = {
        "schema_version": "v5.structure-closure/v1",
        "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0",
        "tables": sorted(tables), "fields": sorted(fields),
        "references": sorted(references, key=_hash),
    }
    validate_closure(result)
    return result


def consume_downstream_stub(mode: str, consumer: str, closure: dict[str, Any]) -> dict[str, str]:
    """Minimal deterministic consumption proof; it never executes downstream work."""
    validate_closure(closure)
    if consumer not in downstream_route(mode):
        _fail("FOUNDATION_DOWNSTREAM_REJECTED" if mode == "foundation" else "DOWNSTREAM_CONSUMER_REJECTED")
    return {"consumer": consumer, "mode": mode, "closure_hash": _hash(closure)}


def retry_status(attempt_no: int) -> str:
    if attempt_no not in (1, 2, 3):
        _fail("ATTEMPT_OUT_OF_RANGE")
    return "blocked_manual" if attempt_no == 3 else "candidate"


def downstream_route(mode: str) -> list[str]:
    if mode == "event_data": return ["230", "241", "251", "252", "260"]
    if mode == "foundation": return ["241", "260"]
    _fail("MODE_INVALID")
