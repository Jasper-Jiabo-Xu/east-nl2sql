"""Deterministic, data-free restricted ORM generation for Agent 251."""
from __future__ import annotations

import ast
import copy
import hashlib
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver, ValidationError

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json


TRANSPORT_KEYS = {"envelope", "payload"}
STRUCTURE_KEYS = {"schema_version", "constraint_asset_version", "graph_version", "tables", "fields", "references"}
OPERATION_KEYS = {"schema_version", "mode", "operations", "consumers"}
PAYLOAD_KEYS = {"schema_version", "structure_closure_ref", "operation_closure_ref", "orm_source_code", "execution_contract", "operations", "orm_runtime_version", "code_hash"}
OP_TYPES = {"READ", "CHECK", "INSERT", "UPDATE"}
WRITE_TYPES = {"INSERT", "UPDATE"}
SAFE_IDENT = re.compile(r"[^a-z0-9_]+")


def _fail(code: str) -> None:
    raise ContractError(code)


class RestrictedOrmGenerator:
    """Turns one event-only operation closure into a zero-data Python ORM plan."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validator(self, name: str) -> Draft202012Validator:
        schema = load_json(self.repo_root / "contracts" / "packages" / name)
        common = load_json(self.repo_root / "contracts" / "common" / "common-envelope.schema.json")
        runtime = load_json(self.repo_root / "contracts" / "v5-runtime-packages.schema.json")
        return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store={schema["$id"]: schema, common["$id"]: common, runtime["$id"]: runtime}))

    @staticmethod
    def _field_map(payload: dict[str, Any]) -> dict[str, list[str]]:
        fields: dict[str, list[str]] = {table: [] for table in payload["tables"]}
        for path in payload["fields"]:
            if not isinstance(path, str) or path.count(".") != 1:
                _fail("FIELD_SCOPE_INVALID")
            table, field = path.split(".", 1)
            if table not in fields or not field:
                _fail("FIELD_SCOPE_INVALID")
            fields[table].append(field)
        if not all(fields.values()):
            _fail("STRUCTURE_CLOSURE_EMPTY")
        return {table: sorted(set(value)) for table, value in fields.items()}

    def validate_structure_closure(self, package: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(payload, dict) or set(payload) != STRUCTURE_KEYS:
            _fail("UNKNOWN_FIELD:STRUCTURE_CLOSURE")
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("structure_closure", "220", "event_data"):
            _fail("FOUNDATION_ORM_FORBIDDEN" if envelope.get("mode") == "foundation" else "STRUCTURE_CLOSURE_ENVELOPE_INVALID")
        try:
            self._validator("structure-closure-package.schema.json").validate(package)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:STRUCTURE_CLOSURE") from exc
        self._field_map(payload)
        return payload

    def validate_operation_closure(self, package: dict[str, Any], structure_ref: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(payload, dict) or set(payload) != OPERATION_KEYS:
            _fail("UNKNOWN_FIELD:OPERATION_CLOSURE")
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("operation_closure", "230", "event_data"):
            _fail("FOUNDATION_ORM_FORBIDDEN" if envelope.get("mode") == "foundation" else "OPERATION_CLOSURE_ENVELOPE_INVALID")
        if structure_ref not in envelope["parent_artifact_refs"]:
            _fail("STRUCTURE_CLOSURE_LINEAGE_MISMATCH")
        try:
            self._validator("operation-closure-package.schema.json").validate(package)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:OPERATION_CLOSURE") from exc
        previous: set[str] = set()
        for number, operation in enumerate(payload["operations"], 1):
            if operation["sequence_no"] != number or operation["operation_type"] not in OP_TYPES:
                _fail("OPERATION_ORDER_INVALID")
            if any(item not in previous for item in operation["dependencies"]):
                _fail("OPERATION_DEPENDENCY_INVALID")
            previous.add(operation["operation_step_id"])
        return payload

    @staticmethod
    def _slot_id(placeholder: str) -> str:
        suffix = SAFE_IDENT.sub("_", placeholder.removeprefix("placeholder:").lower()).strip("_")
        return "slot_" + suffix

    @staticmethod
    def _operation_placeholders(operation: dict[str, Any]) -> list[str]:
        placeholders = list(operation["data_placeholders"])
        if operation["operation_type"] == "UPDATE" and not placeholders:
            for obj in operation["object_refs"]:
                placeholders.extend(f"placeholder:{obj['table_id']}.{field}" for field in obj["field_scope"])
        return sorted(set(placeholders))

    def _slots_and_operations(self, closure: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        slots: list[dict[str, Any]] = []
        seen_slots: set[str] = set()
        operations: list[dict[str, Any]] = []
        source_to_orm: dict[str, str] = {}
        for number, source in enumerate(closure["operations"], 1):
            table = source["object_refs"][0]["table_id"]
            placeholder_refs = self._operation_placeholders(source)
            slot_ids: list[str] = []
            for placeholder in placeholder_refs:
                _, location = placeholder.split(":", 1)
                slot_id = self._slot_id(placeholder)
                if slot_id in seen_slots:
                    slot_ids.append(slot_id)
                    continue
                target, field = location.split(".", 1)
                slots.append({"slot_id": slot_id, "target_table_id": target, "field_ids": [field], "standard_type": "UNSPECIFIED", "cardinality": "one", "required": source["operation_type"] in WRITE_TYPES, "data_placeholder_ref": placeholder})
                seen_slots.add(slot_id)
                slot_ids.append(slot_id)
            operation_id = f"orm-op-{number:03d}"
            source_to_orm[source["operation_step_id"]] = operation_id
            operations.append({"operation_id": operation_id, "source_operation_step_id": source["operation_step_id"], "kind": source["operation_type"], "table_id": table, "binding_slot_ids": slot_ids, "where_ref": f"operation:{source['operation_step_id']}:object_refs" if source["operation_type"] in {"READ", "UPDATE"} else None, "depends_on": list(source["dependencies"]), "preconditions": list(source["preconditions"]), "postconditions": list(source["postconditions"]), "transaction_id": source["transaction_boundary"]["transaction_id"]})
        for operation in operations:
            operation["depends_on"] = [source_to_orm[item] for item in operation["depends_on"]]
        return slots, operations

    @staticmethod
    def _render_code(operations: list[dict[str, Any]], slots: list[dict[str, Any]]) -> str:
        slot_placeholder = {slot["slot_id"]: slot["data_placeholder_ref"].split(".", 1)[1] for slot in slots}
        lines = ["def apply(context, params):", "    if not params:", "        return {'execution_mode': 'empty', 'executed_operation_ids': [], 'write_count': 0}", "    with context.transaction() as transaction:"]
        writes = 0
        for operation in operations:
            method = operation["kind"].lower()
            mapping = {slot_placeholder[slot]: slot for slot in operation["binding_slot_ids"]}
            rendered = ", ".join(f"{field!r}: params[{slot!r}]" for field, slot in mapping.items())
            lines.append(f"        transaction.{method}({operation['table_id']!r}, {{{rendered}}})")
            if operation["kind"] in WRITE_TYPES:
                writes += 1
        ids = [operation["operation_id"] for operation in operations]
        lines.append(f"    return {{'execution_mode': 'bound', 'executed_operation_ids': {ids!r}, 'write_count': {writes}}}")
        return "\n".join(lines) + "\n"

    def _execution_contract(self, slots: list[dict[str, Any]]) -> dict[str, Any]:
        return {"entrypoint": {"module": "generated_orm", "callable": "apply", "signature": "apply(context, params)", "return_shape": "execution_report/v1"}, "binding_slots": slots, "empty_run_contract": {"input": {}, "write_count": 0, "database_side_effect": False, "return_shape": "execution_report/v1"}, "allowed_api": ["context.transaction", "transaction.read", "transaction.check", "transaction.insert", "transaction.update", "transaction.rollback"], "rollback_policy": "transaction_context_rolls_back_on_exception; no transaction is opened for empty params"}

    def _wrap(self, payload: dict[str, Any], structure: dict[str, Any], operation: dict[str, Any], *, version: int, attempt_no: int, supersedes_ref: dict[str, Any] | None, status: str) -> dict[str, Any]:
        source = operation["envelope"]
        parents = [artifact_ref(structure["envelope"]), artifact_ref(source)]
        envelope: dict[str, Any] = {"artifact_id": f"{source['artifact_id']}:restricted-orm", "artifact_type": "restricted_orm", "run_id": source["run_id"], "qa_id": source["qa_id"], "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": supersedes_ref, "attempt_no": attempt_no, "producer_id": "251", "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": status, "mode": "event_data", "created_at": source["created_at"], "trace_id": source["trace_id"], "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}

    def build(self, structure_closure: dict[str, Any], operation_closure: dict[str, Any], *, version: int = 1, attempt_no: int = 1, supersedes_ref: dict[str, Any] | None = None, status: str = "pending_validation") -> dict[str, Any]:
        self.validate_structure_closure(structure_closure)
        closure = self.validate_operation_closure(operation_closure, artifact_ref(structure_closure["envelope"]))
        if version < 1 or attempt_no not in (1, 2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        slots, operations = self._slots_and_operations(closure)
        code = self._render_code(operations, slots)
        payload = {"schema_version": "v5.restricted-orm/v2", "structure_closure_ref": artifact_ref(structure_closure["envelope"]), "operation_closure_ref": artifact_ref(operation_closure["envelope"]), "orm_source_code": code, "execution_contract": self._execution_contract(slots), "operations": operations, "orm_runtime_version": "v5.restricted-orm-runtime/v1", "code_hash": hashlib.sha256(code.encode("utf-8")).hexdigest()}
        package = self._wrap(payload, structure_closure, operation_closure, version=version, attempt_no=attempt_no, supersedes_ref=supersedes_ref, status=status)
        self.validate_restricted_orm(package, structure_closure, operation_closure)
        return package

    def validate_restricted_orm(self, package: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any]) -> None:
        before = copy.deepcopy((package, structure_closure, operation_closure))
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
            _fail("UNKNOWN_FIELD:RESTRICTED_ORM")
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("restricted_orm", "251", "event_data"):
            _fail("RESTRICTED_ORM_ENVELOPE_INVALID")
        self.validate_structure_closure(structure_closure)
        closure = self.validate_operation_closure(operation_closure, artifact_ref(structure_closure["envelope"]))
        if payload["structure_closure_ref"] != artifact_ref(structure_closure["envelope"]) or payload["operation_closure_ref"] != artifact_ref(operation_closure["envelope"]):
            _fail("UPSTREAM_REFERENCE_MISMATCH")
        try:
            self._validator("restricted-orm-package.schema.json").validate(package)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:RESTRICTED_ORM") from exc
        slots, expected_operations = self._slots_and_operations(closure)
        expected_code = self._render_code(expected_operations, slots)
        if payload["operations"] != expected_operations or payload["execution_contract"] != self._execution_contract(slots):
            _fail("OPERATION_OR_SLOT_MISMATCH")
        if payload["orm_source_code"] != expected_code or payload["code_hash"] != hashlib.sha256(expected_code.encode("utf-8")).hexdigest():
            _fail("CODE_HASH_OR_API_DRIFT")
        try:
            ast.parse(payload["orm_source_code"], mode="exec")
        except SyntaxError as exc:
            raise ContractError("ORM_SOURCE_NOT_COMPILABLE") from exc
        if before != (package, structure_closure, operation_closure):
            _fail("INPUT_MUTATED")

    def apply_252_feedback(self, previous: dict[str, Any], feedback: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any]) -> dict[str, Any]:
        self.validate_restricted_orm(previous, structure_closure, operation_closure)
        if not isinstance(feedback, dict) or set(feedback) != {"orm_plan_ref", "decision", "validation_types", "failed_items"} or feedback["decision"] != "fail" or not isinstance(feedback["validation_types"], list) or not isinstance(feedback["failed_items"], list) or not feedback["failed_items"]:
            _fail("ORM_VALIDATION_FEEDBACK_INVALID")
        if feedback["orm_plan_ref"] != artifact_ref(previous["envelope"]):
            _fail("FEEDBACK_PACKAGE_REF_MISMATCH")
        return self._revision(previous, structure_closure, operation_closure)

    def apply_260_feedback(self, previous: dict[str, Any], feedback: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any]) -> dict[str, Any]:
        self.validate_restricted_orm(previous, structure_closure, operation_closure)
        if not isinstance(feedback, dict) or feedback.get("route_target") != "251" or feedback.get("error_code") != "ORM_PLAN_ERROR" or feedback.get("input_orm_ref") != artifact_ref(previous["envelope"]):
            _fail("REGRESSION_NOT_ROUTED_TO_251")
        return self._revision(previous, structure_closure, operation_closure)

    def _revision(self, previous: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any]) -> dict[str, Any]:
        next_attempt = previous["envelope"]["attempt_no"] + 1
        if next_attempt > 3:
            _fail("ATTEMPT_OUT_OF_RANGE")
        status = "blocked_manual" if next_attempt == 3 else "pending_validation"
        return self.build(structure_closure, operation_closure, version=previous["envelope"]["version"] + 1, attempt_no=next_attempt, supersedes_ref=artifact_ref(previous["envelope"]), status=status)
