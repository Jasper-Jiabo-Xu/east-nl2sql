"""Independent, sanitized 260 consumer used only for the 252 contract test."""
from __future__ import annotations

import hashlib
from typing import Any

from jsonschema import Draft202012Validator, RefResolver, ValidationError

from east_v5.artifacts import content_hash, validate_envelope
from east_v5.governance import ContractError, canonical_bytes, load_json


class _Transaction:
    def __init__(self, calls: list[tuple[str, str, Any]]) -> None:
        self.calls = calls

    def __enter__(self) -> "_Transaction":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def read(self, table: str, values: Any) -> None:
        self.calls.append(("read", table, values))

    def check(self, table: str, values: Any) -> None:
        self.calls.append(("check", table, values))

    def insert(self, table: str, values: Any) -> None:
        self.calls.append(("insert", table, values))

    def update(self, table: str, values: Any) -> None:
        self.calls.append(("update", table, values))


class _Context:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def transaction(self) -> _Transaction:
        return _Transaction(self.calls)


def consume(package: dict[str, Any], repo_root) -> dict[str, Any]:
    schema = load_json(repo_root / "contracts/packages/frozen-orm-package.schema.json")
    common = load_json(repo_root / "contracts/common/common-envelope.schema.json")
    runtime = load_json(repo_root / "contracts/v5-runtime-packages.schema.json")
    try:
        Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store={schema["$id"]: schema, common["$id"]: common, runtime["$id"]: runtime})).validate(package)
        validate_envelope(repo_root, package["envelope"], package["payload"])
    except ValidationError as exc:
        raise ContractError("260_STUB_SCHEMA_REJECTED") from exc
    payload = package["payload"]
    plan = payload["validated_orm_plan"]
    expected_hash = hashlib.sha256(canonical_bytes({"orm_source_code": plan["orm_source_code"], "execution_contract": plan["execution_contract"], "operations": plan["operations"]})).hexdigest()
    if plan["code_hash"] != expected_hash or payload["validated_hash"] != plan["code_hash"]:
        raise ContractError("260_STUB_HASH_REJECTED")
    if payload["source_orm_plan_ref"] != {"artifact_id": package["envelope"]["parent_artifact_refs"][0]["artifact_id"], "version": package["envelope"]["parent_artifact_refs"][0]["version"], "content_hash": package["envelope"]["parent_artifact_refs"][0]["content_hash"]}:
        raise ContractError("260_STUB_LINEAGE_REJECTED")
    namespace: dict[str, Any] = {}
    exec(compile(plan["orm_source_code"], "<approved-260-sandbox>", "exec"), {"__builtins__": {}}, namespace)
    context = _Context()
    report = namespace["apply"](context, {})
    if context.calls or report != {"execution_mode": "empty", "executed_operation_ids": [], "write_count": 0}:
        raise ContractError("260_STUB_EMPTY_RUN_REJECTED")
    return {"decision": "pass", "validated_hash": payload["validated_hash"], "empty_write_count": 0, "binding_slot_count": len(plan["execution_contract"]["binding_slots"])}
