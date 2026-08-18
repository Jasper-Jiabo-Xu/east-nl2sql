"""220: deterministic, read-only construction of registered structure closures."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, RefResolver, ValidationError

from east_v5.agents.east_000.extractor import validate_request_schema, validate_result_schema
from east_v5.agents.east_000.safety_gate import validate_request
from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json


REPO_ROOT = Path(__file__).resolve().parents[4]
REVIEWED_QUESTION_SQL_FIELDS = {
    "qa_id", "candidate_ref", "query_spec_ref", "clear_question", "sql_gold",
    "sql_explanation", "approved_business_events", "specification_mapping",
    "evidence_refs", "precheck_report_ref", "deepseek_review_ref",
    "glm_review_ref", "package_hash", "approved_at",
}
EVENT_QUERY_CONTEXT_FIELDS = {"schema_version", "source_query_spec_ref", "source_question_sql_ref", "query_parameter_binding_ref", "reviewed_question_sql_ref", "field_projection", "projection_hash"}
STRUCTURE_FIELDS = {"schema_version", "constraint_asset_version", "graph_version", "tables", "fields", "references"}
FOUNDATION_STRUCTURE_FIELDS = STRUCTURE_FIELDS | {"foundation_task_ref"}
FIELD_PATH = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")
SQL_TABLE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.IGNORECASE)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fail(code: str) -> None:
    raise ContractError(code)


def _ref_for_request(request: dict[str, Any]) -> dict[str, Any]:
    return {"artifact_id": request["request_id"], "version": 1, "content_hash": _hash(request)}


def _package_schema_validator(name: str) -> Draft202012Validator:
    schema = load_json(REPO_ROOT / "contracts" / "packages" / name)
    common = load_json(REPO_ROOT / "contracts" / "common" / "common-envelope.schema.json")
    runtime = load_json(REPO_ROOT / "contracts" / "v5-runtime-packages.schema.json")
    store = {schema["$id"]: schema, common["$id"]: common, runtime["$id"]: runtime}
    return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=store))


def _validate_foundation_input(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if set(package) != {"envelope", "payload"}:
        _fail("UNKNOWN_FIELD:FOUNDATION_PROFILE_PACKAGE")
    envelope, payload = package["envelope"], package["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    try:
        _package_schema_validator("foundation-profile-package.schema.json").validate(package)
    except ValidationError as exc:
        raise ContractError("FOUNDATION_PROFILE_SCHEMA_INVALID") from exc
    return envelope, payload


def validate_foundation_task_package(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the only complete Foundation intent accepted by runtime consumers."""
    if set(package) != {"envelope", "payload"}:
        _fail("UNKNOWN_FIELD:FOUNDATION_TASK_PACKAGE")
    envelope, payload = package["envelope"], package["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    try:
        _package_schema_validator("foundation-task-package.schema.json").validate(package)
    except ValidationError as exc:
        raise ContractError("FOUNDATION_TASK_PACKAGE_SCHEMA_INVALID") from exc
    if envelope["artifact_id"] != payload["foundation_task_id"]:
        _fail("FOUNDATION_TASK_IDENTITY_MISMATCH")
    if set(payload["target_counts"]) - set(payload["target_object_types"]):
        _fail("FOUNDATION_TARGET_COUNT_SCOPE_MISMATCH")
    if set(payload["target_table_field_scope"]) - set(payload["target_object_types"]):
        _fail("FOUNDATION_TABLE_SCOPE_OBJECT_MISMATCH")
    return envelope, payload


def project_foundation_profile(task_package: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic compatibility projection; never accept it as intent."""
    envelope, task = validate_foundation_task_package(task_package)
    profile = {
        "schema_version": "v5.foundation-profile/v1", "foundation_task_ref": artifact_ref(envelope),
        "base_database_version": task["target_database_version"],
        "target_classes": task["target_object_types"], "target_counts": task["target_counts"],
        "constraint_asset_version": task["constraint_asset_version"], "graph_version": task["graph_version"],
    }
    return profile


def validate_foundation_profile_projection(profile_package: dict[str, Any], task_package: dict[str, Any]) -> None:
    _, profile = _validate_foundation_input(profile_package)
    if profile != project_foundation_profile(task_package):
        _fail("FOUNDATION_PROFILE_PROJECTION_DRIFT")


def _validate_event_input(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if set(package) != {"envelope", "payload"}:
        _fail("UNKNOWN_FIELD:QUESTION_SQL_DUAL_REVIEW_PASSED")
    envelope, payload = package["envelope"], package["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != (
        "reviewed_question_sql", "210", "event_data",
    ):
        _fail("EVENT_ENVELOPE_INVALID")
    if not isinstance(payload, dict) or set(payload) != REVIEWED_QUESTION_SQL_FIELDS:
        _fail("SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED")
    if payload["qa_id"] != envelope["qa_id"]:
        _fail("EVENT_QA_MISMATCH")
    if not all(isinstance(payload[key], str) and payload[key] for key in ("clear_question", "sql_gold", "sql_explanation", "approved_at")):
        _fail("SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED")
    expected = _hash({key: value for key, value in payload.items() if key != "package_hash"})
    if payload["package_hash"] != expected:
        _fail("CONTENT_HASH_DRIFT")
    return envelope, payload


def validate_reviewed_question_sql(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Public, strict consumer validation for the frozen 220 dual-review output."""
    return _validate_event_input(package)


def _walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_values(nested)
    else:
        yield value


def validate_event_query_context(context: dict[str, Any], reviewed: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate the 210-only field projection used by every event consumer."""
    reviewed_envelope, _ = _validate_event_input(reviewed)
    if set(context) != {"envelope", "payload"}:
        _fail("EVENT_CONTEXT_TRANSPORT_INVALID")
    envelope, payload = context["envelope"], context["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    if set(payload) != EVENT_QUERY_CONTEXT_FIELDS:
        _fail("EVENT_CONTEXT_SCHEMA_INVALID")
    if payload["projection_hash"] != _hash({key: value for key, value in payload.items() if key != "projection_hash"}):
        _fail("EVENT_CONTEXT_HASH_DRIFT")
    if payload["schema_version"] != "v5.event-query-context/v2":
        _fail("EVENT_CONTEXT_VERSION_REJECTED")
    if payload["reviewed_question_sql_ref"] != artifact_ref(reviewed_envelope):
        _fail("EVENT_CONTEXT_REVIEWED_LINEAGE_REJECTED")
    if reviewed_envelope["parent_artifact_refs"] != [payload["source_question_sql_ref"]]:
        _fail("EVENT_CONTEXT_SOURCE_QUESTION_LINEAGE_REJECTED")
    refs = [payload["source_query_spec_ref"], payload["source_question_sql_ref"], payload["query_parameter_binding_ref"], payload["reviewed_question_sql_ref"]]
    if envelope["parent_artifact_refs"] != refs or envelope["input_hashes"] != [ref["content_hash"] for ref in refs]:
        _fail("EVENT_CONTEXT_PARENT_LINEAGE_REJECTED")
    for key in ("run_id", "qa_id", "trace_id", "attempt_no"):
        if envelope[key] != reviewed_envelope[key]:
            _fail("EVENT_CONTEXT_LINEAGE_MISMATCH")
    seen: set[str] = set()
    for item in payload["field_projection"]:
        if item["spec_item"] in seen or item["fields"] != sorted(set(item["fields"])):
            _fail("EVENT_CONTEXT_PROJECTION_INVALID")
        seen.add(item["spec_item"])
    return envelope, payload


def _field_paths(context_payload: dict[str, Any]) -> list[str]:
    """Read only the frozen 210 projection; never parse mapping/SQL in 220."""
    fields = sorted({field for item in context_payload["field_projection"] for field in item["fields"]})
    if not fields or any(not FIELD_PATH.fullmatch(field) for field in fields):
        _fail("EVENT_CONTEXT_PROJECTION_INVALID")
    return fields


def _relationships(payload: dict[str, Any]) -> list[str]:
    matches: set[str] = set()
    for value in _walk_values([payload["specification_mapping"], payload["approved_business_events"]]):
        if not isinstance(value, str):
            continue
        compact = value.replace(" ", "")
        if "->" not in compact:
            continue
        left, right = compact.split("->", 1)
        if FIELD_PATH.fullmatch(left) and FIELD_PATH.fullmatch(right):
            matches.add(f"{left}->{right}")
    return sorted(matches)


def event_query_rounds(event: dict[str, Any], context: dict[str, Any], first_result: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build query requests from the authenticated 210 projection only."""
    envelope, payload = _validate_event_input(event)
    _, context_payload = validate_event_query_context(context, event)
    fields = _field_paths(context_payload)
    relations = _relationships(payload)
    tables = sorted({path.split(".", 1)[0] for path in fields} | {table for table in SQL_TABLE.findall(payload["sql_gold"])})
    first = {
        "request_id": f"{envelope['run_id']}:220:1", "caller_agent_id": "220",
        "caller_stage": "structure_closure", "query_purpose": "constraint_lookup",
        "natural_language_intent": f"查询获批 SQL 的字段约束：{', '.join(fields)}",
        "target_asset_types": ["single_field"], "table_scope": tables,
        "field_scope": fields, "relationship_scope": [],
        "required_output_fields": ["field_id"], "previous_request_refs": [], "max_rows": 100,
    }
    validate_request_schema(REPO_ROOT, first)
    validate_request(first)
    second = {
        "request_id": f"{envelope['run_id']}:220:2", "caller_agent_id": "220",
        "caller_stage": "structure_closure", "query_purpose": "relationship_lookup",
        "natural_language_intent": "查询获批 SQL 和业务事件所需的表关系",
        "target_asset_types": ["cross_table"], "table_scope": tables, "field_scope": [],
        "relationship_scope": relations, "required_output_fields": ["references"],
        "previous_request_refs": [], "max_rows": 100,
    }
    if first_result is not None:
        first_ref = _validate_asset_result(first_result, envelope, first["request_id"], _ref_for_request(first))
        second["previous_request_refs"] = [first_ref]
    validate_request_schema(REPO_ROOT, second)
    validate_request(second)
    return [first, second]


def _validate_asset_result(result: dict[str, Any], source: dict[str, Any], request_id: str, required_parent: dict[str, Any]) -> dict[str, Any]:
    if set(result) != {"envelope", "payload"}:
        _fail("UNKNOWN_FIELD:CONSTRAINT_ASSET_PACKAGE")
    envelope, payload = result["envelope"], result["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("constraint_asset_package", "000", source["mode"]):
        _fail("ASSET_RESULT_ENVELOPE_INVALID")
    for key in ("qa_id", "run_id", "trace_id", "attempt_no"):
        if envelope[key] != source[key]:
            _fail("ASSET_RESULT_LINEAGE_MISMATCH")
    validate_result_schema(REPO_ROOT, payload)
    if payload["request_id"] != request_id:
        _fail("ASSET_REQUEST_ID_MISMATCH")
    if payload["asset_version"] not in {"CA-V0.3.0", "TRG-V1.0.0"}:
        _fail("ASSET_VERSION_DRIFT")
    if required_parent not in envelope["parent_artifact_refs"]:
        _fail("ASSET_RESULT_PARENT_MISSING")
    return artifact_ref(envelope)


def _payload_from_assets(seed_tables: set[str], seed_fields: set[str], assets: list[dict[str, Any]], foundation_task_ref: dict[str, Any] | None = None) -> dict[str, Any]:
    tables, fields, references = set(seed_tables), set(seed_fields), []
    for package in assets:
        for record in package["payload"]["matched_records"]:
            data = record["data"]
            if data.get("record_class") == "EVENT_OWNED":
                _fail("FOUNDATION_EVENT_OWNED_REJECTED")
            table, field = data.get("table_id") or data.get("table_code"), data.get("field_id")
            if table:
                tables.add(table)
            if table and field:
                fields.add(f"{table}.{field}")
            if record["record_type"] in {"cross_table", "hierarchy_reference"}:
                references.append({"type": record["record_type"], "data": data})
            if record["record_type"] == "cross_table":
                for endpoint in (data.get("from"), data.get("to")):
                    if not isinstance(endpoint, str) or not FIELD_PATH.fullmatch(endpoint):
                        _fail("SCHEMA_VALIDATION_FAILED:CROSS_TABLE_RELATIONSHIP")
                    relation_table, relation_field = endpoint.split(".", 1)
                    tables.add(relation_table)
                    fields.add(f"{relation_table}.{relation_field}")
    payload = {
        "schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0", "tables": sorted(tables), "fields": sorted(fields),
        "references": sorted(references, key=_hash),
    }
    if foundation_task_ref is not None:
        payload["foundation_task_ref"] = foundation_task_ref
    validate_closure(payload)
    return payload


def _wrap_closure(source: dict[str, Any], parents: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "artifact_id": f"{source['artifact_id']}:structure-closure", "artifact_type": "structure_closure",
        "run_id": source["run_id"], "qa_id": source["qa_id"], "version": source["version"],
        "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
        "attempt_no": source["attempt_no"], "producer_id": "220", "parent_artifact_refs": parents,
        "input_hashes": [item["content_hash"] for item in parents], "status": "candidate",
        "mode": source["mode"], "created_at": source["created_at"], "trace_id": source["trace_id"],
        "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    package = {"envelope": envelope, "payload": payload}
    validate_structure_closure_package(package)
    return package


def build_event_closure(event: dict[str, Any], context: dict[str, Any], first_result: dict[str, Any], second_result: dict[str, Any]) -> dict[str, Any]:
    source, _ = _validate_event_input(event)
    _, context_payload = validate_event_query_context(context, event)
    first, second = event_query_rounds(event, context, first_result)
    first_ref = artifact_ref(first_result["envelope"])
    second_ref = _validate_asset_result(second_result, source, second["request_id"], first_ref)
    seeds = _field_paths(context_payload)
    closure_payload = _payload_from_assets(
        {path.split(".", 1)[0] for path in seeds}, set(seeds), [first_result, second_result]
    )
    return _wrap_closure(source, [artifact_ref(source), artifact_ref(context["envelope"]), first_ref, second_ref], closure_payload)


def build_closure(profile_package: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    """Build Foundation closure from complete frozen intent, not the profile projection."""
    source, task = validate_foundation_task_package(profile_package)
    source_ref = artifact_ref(source)
    asset_refs = []
    for package in assets:
        asset_refs.append(_validate_asset_result(package, source, package["payload"].get("request_id", ""), source_ref))
    seeds = set(task["target_object_types"])
    if any(not isinstance(target, str) or not target for target in seeds):
        _fail("FOUNDATION_TASK_INVALID")
    if any(target.startswith("EVENT_OWNED:") for target in seeds):
        _fail("FOUNDATION_EVENT_OWNED_REJECTED")
    return _wrap_closure(source, [source_ref, *asset_refs], _payload_from_assets(seeds, set(), assets, source_ref))


def validate_closure(value: dict[str, Any]) -> None:
    allowed = FOUNDATION_STRUCTURE_FIELDS if "foundation_task_ref" in value else STRUCTURE_FIELDS
    if not isinstance(value, dict) or set(value) != allowed:
        _fail("UNKNOWN_FIELD:STRUCTURE_CLOSURE")
    try:
        schema = load_json(REPO_ROOT / "contracts" / "packages" / "structure-closure-package.schema.json")
        Draft202012Validator(schema["properties"]["payload"]).validate(value)
    except (ValidationError, KeyError) as exc:
        raise ContractError("SCHEMA_VALIDATION_FAILED:STRUCTURE_CLOSURE") from exc


def validate_structure_closure_package(package: dict[str, Any]) -> None:
    if set(package) != {"envelope", "payload"}:
        _fail("UNKNOWN_FIELD:STRUCTURE_CLOSURE_PACKAGE")
    envelope, payload = package["envelope"], package["payload"]
    validate_envelope(REPO_ROOT, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"]) != ("structure_closure", "220"):
        _fail("STRUCTURE_CLOSURE_ENVELOPE_INVALID")
    if envelope["mode"] not in {"event_data", "foundation"}:
        _fail("MODE_INVALID")
    if envelope["mode"] == "foundation" and "foundation_task_ref" not in payload:
        _fail("FOUNDATION_TASK_REF_REQUIRED")
    if envelope["mode"] != "foundation" and "foundation_task_ref" in payload:
        _fail("FOUNDATION_TASK_REF_FORBIDDEN")
    validate_closure(payload)


def consume_downstream_stub(consumer: str, closure_package: dict[str, Any]) -> dict[str, str]:
    """Minimal deterministic consumption proof of a complete registered package."""
    validate_structure_closure_package(closure_package)
    mode = closure_package["envelope"]["mode"]
    if consumer not in downstream_route(mode):
        _fail("FOUNDATION_DOWNSTREAM_REJECTED" if mode == "foundation" else "DOWNSTREAM_CONSUMER_REJECTED")
    return {"consumer": consumer, "mode": mode, "closure_hash": closure_package["envelope"]["content_hash"]}


def retry_status(attempt_no: int) -> str:
    if attempt_no not in (1, 2, 3):
        _fail("ATTEMPT_OUT_OF_RANGE")
    return "blocked_manual" if attempt_no == 3 else "candidate"


def downstream_route(mode: str) -> list[str]:
    if mode == "event_data":
        return ["230", "241", "251", "252", "260"]
    if mode == "foundation":
        return ["241", "260"]
    _fail("MODE_INVALID")
