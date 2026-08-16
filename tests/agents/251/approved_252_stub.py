"""Independent, sanitized 252 consumer used only for the 251 contract test."""
from __future__ import annotations

import ast
import hashlib
from typing import Any

from jsonschema import Draft202012Validator, RefResolver, ValidationError

from east_v5.artifacts import content_hash, validate_envelope
from east_v5.governance import ContractError, canonical_bytes, load_json


class _Transaction:
    def __init__(self, calls: list[tuple[str, str, dict[str, Any]]]): self.calls = calls
    def __enter__(self): return self
    def __exit__(self, *_: object) -> bool: return False
    def read(self, table: str, values: dict[str, Any]) -> None: self.calls.append(("read", table, values))
    def check(self, table: str, values: dict[str, Any]) -> None: self.calls.append(("check", table, values))
    def insert(self, table: str, values: dict[str, Any]) -> None: self.calls.append(("insert", table, values))
    def update(self, table: str, values: dict[str, Any]) -> None: self.calls.append(("update", table, values))


class _Context:
    def __init__(self): self.calls: list[tuple[str, str, dict[str, Any]]] = []
    def transaction(self) -> _Transaction: return _Transaction(self.calls)


def consume(package: dict[str, Any], repo_root) -> dict[str, Any]:
    schema = load_json(repo_root / "contracts/packages/restricted-orm-package.schema.json")
    common = load_json(repo_root / "contracts/common/common-envelope.schema.json")
    runtime = load_json(repo_root / "contracts/v5-runtime-packages.schema.json")
    try:
        Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store={schema["$id"]: schema, common["$id"]: common, runtime["$id"]: runtime})).validate(package)
        validate_envelope(repo_root, package["envelope"], package["payload"])
    except ValidationError as exc:
        raise ContractError("252_STUB_SCHEMA_REJECTED") from exc
    payload = package["payload"]
    expected_hash = hashlib.sha256(canonical_bytes({"orm_source_code": payload["orm_source_code"], "execution_contract": payload["execution_contract"], "operations": payload["operations"]})).hexdigest()
    if payload["code_hash"] != expected_hash or package["envelope"]["content_hash"] != content_hash(package["envelope"], payload):
        raise ContractError("252_STUB_HASH_REJECTED")
    tree = ast.parse(payload["orm_source_code"], mode="exec")
    forbidden = (ast.Import, ast.ImportFrom, ast.Lambda, ast.ClassDef, ast.Global, ast.Nonlocal)
    if any(isinstance(node, forbidden) for node in ast.walk(tree)):
        raise ContractError("252_STUB_AST_REJECTED")
    allowed = {"transaction", "read", "check", "insert", "update"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr not in allowed:
            raise ContractError("252_STUB_API_REJECTED")
        if isinstance(node, ast.Name) and node.id in {"eval", "exec", "open", "__import__"}:
            raise ContractError("252_STUB_AST_REJECTED")
    namespace: dict[str, Any] = {}
    exec(compile(payload["orm_source_code"], "<approved-252-sandbox>", "exec"), {"__builtins__": {}}, namespace)
    context = _Context()
    report = namespace["apply"](context, {})
    if context.calls or report != {"execution_mode": "empty", "executed_operation_ids": [], "write_count": 0}:
        raise ContractError("252_STUB_EMPTY_RUN_REJECTED")
    return {"decision": "pass", "code_hash": payload["code_hash"], "empty_write_count": 0}
