"""Deterministic operation-closure construction for the V5 event path only."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver, ValidationError

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json


TRANSPORT_KEYS = {"envelope", "payload"}
STRUCTURE_KEYS = {"schema_version", "constraint_asset_version", "graph_version", "tables", "fields", "references"}
PAYLOAD_KEYS = {"schema_version", "mode", "operations", "consumers"}
OP_TYPES = {"READ", "CHECK", "INSERT", "UPDATE"}


def _fail(code: str) -> None:
    raise ContractError(code)


class OperationClosureBuilder:
    """Creates one strict, replayable plan jointly consumed by 241 and 251."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validator(self, name: str) -> Draft202012Validator:
        schema = load_json(self.repo_root / "contracts" / "packages" / name)
        common = load_json(self.repo_root / "contracts" / "common" / "common-envelope.schema.json")
        runtime = load_json(self.repo_root / "contracts" / "v5-runtime-packages.schema.json")
        return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store={schema["$id"]: schema, common["$id"]: common, runtime["$id"]: runtime}))

    @staticmethod
    def _field_map(payload: dict[str, Any]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        for path in payload["fields"]:
            if not isinstance(path, str) or path.count(".") != 1:
                _fail("FIELD_SCOPE_INVALID")
            table, field = path.split(".", 1)
            if table not in payload["tables"] or not table or not field:
                _fail("FIELD_SCOPE_INVALID")
            result[table].append(field)
        if set(result) != set(payload["tables"]):
            _fail("STRUCTURE_CLOSURE_EMPTY")
        return {table: sorted(set(fields)) for table, fields in result.items()}

    def _validate_structure(self, package: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(payload, dict) or set(payload) != STRUCTURE_KEYS:
            _fail("UNKNOWN_FIELD:STRUCTURE_CLOSURE")
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("structure_closure", "220", "event_data"):
            _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN" if envelope["mode"] == "foundation" else "STRUCTURE_CLOSURE_ENVELOPE_INVALID")
        try:
            self._validator("structure-closure-package.schema.json").validate(package)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:STRUCTURE_CLOSURE") from exc
        if not payload["tables"] or not payload["fields"]:
            _fail("STRUCTURE_CLOSURE_EMPTY")
        self._field_map(payload)
        return payload

    def _references(self, payload: dict[str, Any]) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
        known_fields = set(payload["fields"])
        cross, state = [], []
        for reference in payload["references"]:
            if not isinstance(reference, dict) or set(reference) != {"type", "data"} or not isinstance(reference["data"], dict):
                _fail("REFERENCE_INVALID")
            if reference["type"] == "cross_table":
                start, end = reference["data"].get("from"), reference["data"].get("to")
                if not isinstance(start, str) or not isinstance(end, str) or start not in known_fields or end not in known_fields:
                    _fail("REFERENCE_FIELD_OUT_OF_CLOSURE")
                cross.append((start, end))
            elif reference["type"] == "object_detail_state":
                state_field = reference["data"].get("state_field")
                if not isinstance(state_field, str) or state_field not in known_fields:
                    _fail("ODS_REFERENCE_INVALID")
                state.append(reference["data"])
            else:
                _fail("ODS_REFERENCE_CONFLICT")
        return sorted(set(cross)), state

    @staticmethod
    def _topological_tables(tables: list[str], cross: list[tuple[str, str]]) -> list[str]:
        # child.from -> parent.to: parent must exist before child is inserted.
        edges: dict[str, set[str]] = {table: set() for table in tables}
        for start, end in cross:
            child, parent = start.split(".", 1)[0], end.split(".", 1)[0]
            if child != parent:
                edges[parent].add(child)
        incoming = {table: 0 for table in tables}
        for children in edges.values():
            for child in children:
                incoming[child] += 1
        ready, result = sorted(table for table, count in incoming.items() if count == 0), []
        while ready:
            current = ready.pop(0)
            result.append(current)
            for child in sorted(edges[current]):
                incoming[child] -= 1
                if incoming[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(result) != len(tables):
            _fail("DEPENDENCY_CYCLE")
        return result

    @staticmethod
    def _step(number: int, operation_type: str, table: str, fields: list[str], dependencies: list[str], placeholders: list[str], state_refs: list[str], phase: str) -> dict[str, Any]:
        return {
            "operation_step_id": f"op-{number:03d}", "sequence_no": number, "operation_type": operation_type,
            "object_refs": [{"table_id": table, "field_scope": fields}],
            "preconditions": [f"{item}:completed" for item in dependencies],
            "postconditions": [f"{operation_type.lower()}:{table}:planned"], "dependencies": dependencies,
            "transaction_boundary": {"transaction_id": "txn-001", "phase": phase},
            "data_placeholders": placeholders, "object_detail_state_rule_refs": state_refs,
        }

    def build(self, structure_closure: dict[str, Any]) -> dict[str, Any]:
        closure = self._validate_structure(structure_closure)
        fields, cross, state = self._field_map(closure), *self._references(closure)
        operations: list[dict[str, Any]] = []
        number = 1
        read_tables = sorted({end.split(".", 1)[0] for _, end in cross} or {sorted(closure["tables"])[0]})
        for table in read_tables:
            operations.append(self._step(number, "READ", table, fields[table], [], [], [], "begin"))
            number += 1
        read_ids = [item["operation_step_id"] for item in operations]
        for start, end in cross:
            table = start.split(".", 1)[0]
            operations.append(self._step(number, "CHECK", table, fields[table], read_ids, [], [f"ods:{start}->{end}"], "inside"))
            number += 1
        check_ids = [item["operation_step_id"] for item in operations if item["operation_type"] == "CHECK"]
        insert_ids: dict[str, str] = {}
        for table in self._topological_tables(sorted(closure["tables"]), cross):
            parent_dependencies = list(check_ids)
            for start, end in cross:
                if start.split(".", 1)[0] == table and end.split(".", 1)[0] in insert_ids:
                    parent_dependencies.append(insert_ids[end.split(".", 1)[0]])
            placeholders = [f"placeholder:{table}.{field}" for field in fields[table]]
            operations.append(self._step(number, "INSERT", table, fields[table], sorted(set(parent_dependencies)), placeholders, [], "inside"))
            insert_ids[table] = operations[-1]["operation_step_id"]
            number += 1
        for rule in state:
            table = rule["state_field"].split(".", 1)[0]
            operations.append(self._step(number, "UPDATE", table, fields[table], [insert_ids[table]], [], [f"ods:{rule['state_field']}"], "commit"))
            number += 1
        payload = {"schema_version": "v5.operation-closure/v1", "mode": "event", "operations": operations, "consumers": ["241", "251"]}
        source = structure_closure["envelope"]
        envelope = {
            "artifact_id": f"{source['artifact_id']}:operation-closure", "artifact_type": "operation_closure", "run_id": source["run_id"], "qa_id": source["qa_id"],
            "version": source["version"], "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
            "attempt_no": source["attempt_no"], "producer_id": "230", "parent_artifact_refs": [artifact_ref(source)], "input_hashes": [source["content_hash"]],
            "status": self.retry_status(source["attempt_no"]), "mode": "event_data", "created_at": source["created_at"], "trace_id": source["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self.validate_operation_closure_package(package)
        return package

    def validate_operation_closure_package(self, package: dict[str, Any]) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
            _fail("UNKNOWN_FIELD:OPERATION_CLOSURE")
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("operation_closure", "230", "event_data"):
            _fail("OPERATION_CLOSURE_ENVELOPE_INVALID")
        if payload.get("consumers") != ["241", "251"]:
            _fail("CONSUMER_ROUTE_INVALID")
        try:
            self._validator("operation-closure-package.schema.json").validate(package)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:OPERATION_CLOSURE") from exc
        ops, ids = payload["operations"], set()
        known_placeholders = {placeholder for item in ops if item["operation_type"] == "INSERT" for placeholder in item["data_placeholders"]}
        for expected, operation in enumerate(ops, 1):
            if operation["sequence_no"] != expected or operation["operation_type"] not in OP_TYPES:
                _fail("OPERATION_ORDER_INVALID")
            step_id = operation["operation_step_id"]
            if step_id in ids or any(dep not in ids for dep in operation["dependencies"]):
                _fail("DEPENDENCY_CYCLE")
            ids.add(step_id)
            if any(placeholder not in known_placeholders for placeholder in operation["data_placeholders"]):
                _fail("PLACEHOLDER_DANGLING")

    def consume_downstream_stub(self, consumer: str, package: dict[str, Any]) -> dict[str, str]:
        self.validate_operation_closure_package(package)
        if consumer == "241":
            # Real downstream public validator proves its current contract accepts the exact package.
            import importlib
            importlib.import_module("east_v5.agents.241.generator").BoundDataGenerator(self.repo_root).validate_operation_closure(package)
        elif consumer != "251":
            _fail("DOWNSTREAM_CONSUMER_REJECTED")
        return {"consumer": consumer, "operation_hash": package["envelope"]["content_hash"]}

    @staticmethod
    def retry_status(attempt_no: int) -> str:
        if attempt_no not in (1, 2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        return "blocked_manual" if attempt_no == 3 else "candidate"
