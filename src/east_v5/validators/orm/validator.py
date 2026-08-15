"""EAS-18: freeze a safe 251 package without executing it or changing inputs."""
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.artifacts.registry import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, canonical_bytes, load_json, verify_governed_manifest

VALIDATOR_VERSION = "v5.orm-validator/v1"
RULE_VERSION = "v5.orm-rules/v1"
MANIFEST_HASH = "8b28baa6263fa7935b76675abf57586c00a296615fcdd09dac80dcce64a1fae7"
CA_REF = {"artifact_id": "CA-MULTIFIELD-20260812-003", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.3.0", "content_hash": "cbcbd79e318f91522393403241b50919a656aa77d71c7f950f43725286b64d3d"}
TRG_REF = {"artifact_id": "EAS-TYPED-GRAPH-20260812-001", "artifact_type": "typed_reference_graph_ref", "asset_version": "TRG-V1.0.0", "content_hash": "a3480f669bd9e97db78a8fec96fac7e317b43b9ed6f222d5c920bc227eaf3b6a"}
RULES = {"allowed_api": ["context.transaction", "transaction.insert", "transaction.update", "transaction.delete", "transaction.rollback"], "placeholder_prefix": "slot_", "required_function": "apply", "requires_rollback": True}
RULE_HASH = hashlib.sha256(canonical_bytes(RULES)).hexdigest()


def _fail(code: str) -> None:
    raise ContractError(code)


def _exact(value: Any, keys: set[str], code: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(code)


def _schema(repo_root: Path, name: str, package: dict[str, Any]) -> None:
    try:
        Draft202012Validator(load_json(repo_root / "contracts" / "validators" / "orm" / name)).validate(package)
    except ValidationError as exc:
        _fail("SCHEMA_VALIDATION_FAILED:" + name.removesuffix(".schema.json"))


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _validate_asset_refs(payload: dict[str, Any]) -> None:
    if payload["constraint_asset_ref"] != CA_REF or payload["typed_reference_graph_ref"] != TRG_REF:
        _fail("ASSET_LINEAGE_DRIFT")
    if payload["governance_manifest_hash"] != MANIFEST_HASH:
        _fail("MANIFEST_LINEAGE_DRIFT")


def _validate_package(repo_root: Path, package: dict[str, Any], name: str, artifact_type: str, producer: str) -> None:
    _schema(repo_root, name, package)
    envelope, payload = package["envelope"], package["payload"]
    validate_envelope(repo_root, envelope, payload)
    if envelope["artifact_type"] != artifact_type or envelope["producer_id"] != producer or envelope["mode"] != "event_data":
        _fail("PACKAGE_ASSOCIATION_INVALID")
    if envelope["status"] == "blocked_manual":
        _fail("UPSTREAM_BLOCKED_MANUAL")
    if envelope["status"] not in {"candidate", "pending_validation"}:
        _fail("PACKAGE_STATUS_INVALID")
    if envelope["mode"] == "foundation":
        _fail("FOUNDATION_FORBIDDEN")
    _validate_asset_refs(payload)


def _placeholder(node: ast.AST) -> bool:
    return isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "params" and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) and node.slice.value.startswith("slot_")


def _mapping(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) and bool(node.keys) and all(isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(value, ast.AST) and _placeholder(value) for key, value in zip(node.keys, node.values))


def _call_method(statement: ast.stmt, tx_name: str) -> tuple[str, str]:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        _fail("AST_STATEMENT_FORBIDDEN")
    call = statement.value
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name) or call.func.value.id != tx_name:
        _fail("API_NOT_ALLOWED")
    method = call.func.attr
    if method not in {"insert", "update", "delete"} or call.keywords or len(call.args) != 2:
        _fail("API_NOT_ALLOWED")
    table, values = call.args
    if not isinstance(table, ast.Constant) or not isinstance(table.value, str) or not table.value or not _mapping(values):
        _fail("BUSINESS_DATA_OR_PLACEHOLDER_INVALID")
    return method, table.value


def _validate_ast(code: str, operations: list[dict[str, Any]]) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        _fail("AST_INVALID")
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        _fail("AST_TOP_LEVEL_FORBIDDEN")
    function = tree.body[0]
    if function.name != "apply" or function.decorator_list or function.returns or [arg.arg for arg in function.args.args] != ["context", "params"] or function.args.vararg or function.args.kwarg or function.args.defaults or function.args.kw_defaults:
        _fail("AST_FUNCTION_SIGNATURE_INVALID")
    if len(function.body) != 1 or not isinstance(function.body[0], ast.With):
        _fail("TRANSACTION_REQUIRED")
    block = function.body[0]
    if len(block.items) != 1 or not isinstance(block.items[0].context_expr, ast.Call):
        _fail("TRANSACTION_REQUIRED")
    opening = block.items[0]
    call = opening.context_expr
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name) or call.func.value.id != "context" or call.func.attr != "transaction" or call.args or call.keywords or not isinstance(opening.optional_vars, ast.Name):
        _fail("TRANSACTION_REQUIRED")
    tx_name = opening.optional_vars.id
    if not block.body:
        _fail("ROLLBACK_REQUIRED")
    rollback = block.body[-1]
    if not isinstance(rollback, ast.Expr) or not isinstance(rollback.value, ast.Call) or rollback.value.args or rollback.value.keywords or not isinstance(rollback.value.func, ast.Attribute) or not isinstance(rollback.value.func.value, ast.Name) or rollback.value.func.value.id != tx_name or rollback.value.func.attr != "rollback":
        _fail("ROLLBACK_REQUIRED")
    if len(block.body) != len(operations) + 1:
        _fail("OPERATION_ORDER_MISMATCH")
    observed = [_call_method(stmt, tx_name) for stmt in block.body[:-1]]
    expected = [(item["method"], item["table"]) for item in operations]
    if observed != expected:
        _fail("OPERATION_ORDER_MISMATCH")


def freeze_orm(repo_root: Path, restricted_orm: dict[str, Any], operation_closure: dict[str, Any]) -> dict[str, Any]:
    """Validate inputs read-only and return the reproducible 252 frozen package."""
    before = copy.deepcopy((restricted_orm, operation_closure))
    _validate_package(repo_root, restricted_orm, "restricted-orm.schema.json", "restricted_orm", "251")
    _validate_package(repo_root, operation_closure, "operation-closure.schema.json", "operation_closure", "230")
    manifest = verify_governed_manifest(repo_root)
    if manifest["content_sha256"] != MANIFEST_HASH:
        _fail("MANIFEST_LINEAGE_DRIFT")
    orm_payload, closure_payload = restricted_orm["payload"], operation_closure["payload"]
    if restricted_orm["envelope"]["run_id"] != operation_closure["envelope"]["run_id"] or restricted_orm["envelope"]["attempt_no"] != operation_closure["envelope"]["attempt_no"]:
        _fail("ATTEMPT_ISOLATION_VIOLATION")
    closure_reference = artifact_ref(operation_closure["envelope"])
    if orm_payload["operation_closure_ref"] != closure_reference:
        _fail("OPERATION_CLOSURE_REFERENCE_MISMATCH")
    operations = closure_payload["operations"]
    if orm_payload["operation_ids"] != [item["operation_id"] for item in operations] or len(set(orm_payload["operation_ids"])) != len(operations):
        _fail("OPERATION_ORDER_MISMATCH")
    if orm_payload["code_sha256"] != _code_hash(orm_payload["code"]):
        _fail("CODE_HASH_DRIFT")
    _validate_ast(orm_payload["code"], operations)
    if before != (restricted_orm, operation_closure):
        _fail("INPUT_MUTATED")
    source = restricted_orm["envelope"]
    envelope = {**source, "artifact_id": source["artifact_id"] + ":frozen", "artifact_type": "frozen_orm", "producer_id": "252", "parent_artifact_refs": [artifact_ref(source), closure_reference], "input_hashes": [source["content_hash"], closure_reference["content_hash"]], "content_hash": ""}
    if len(envelope["artifact_id"]) > 128:
        _fail("ARTIFACT_ID_INVALID")
    payload: dict[str, Any] = {"schema_version": "v5.frozen-orm/v1", "code_sha256": orm_payload["code_sha256"], "validator_version": VALIDATOR_VERSION, "rule_version": RULE_VERSION, "rule_hash": RULE_HASH, "validation_evidence": {"ast": "PASS", "allowed_api": RULES["allowed_api"], "dry_run": "NO_DATA_EXECUTION", "input_immutable": True, "operation_ids": list(orm_payload["operation_ids"]), "rollback": "PASS", "manifest_content_sha256": MANIFEST_HASH, "constraint_asset_ref": CA_REF, "typed_reference_graph_ref": TRG_REF}, "verdict": "PASS", "failures": []}
    envelope["content_hash"] = content_hash(envelope, payload)
    output = {"envelope": envelope, "payload": payload}
    _schema(repo_root, "frozen-orm.schema.json", output)
    validate_envelope(repo_root, envelope, payload)
    return output
