"""Deterministic, read-only ORM validation and hash freezing for Agent 252.

Agent 252 validates the Agent 251 ``restricted_orm`` package without modifying it
and without executing it against any database.  On success it emits a
``frozen_orm`` package whose ``validated_hash`` is the unchanged 251 ``code_hash``;
on validation failure it emits an ``orm_validation_failed_feedback`` package
aggregating every failed rule and its operation/table/object location.

The five validation types map 1:1 to the frozen contract: ``static_ast``
(AST and forbidden capabilities), ``api_allowlist`` (allowed API), ``import_compile``
(import and compile), ``empty_dry_run`` (empty-data zero-write run) and
``object_detail_state`` (object-detail-state ordering and 230-closure consistency).
"""
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver, ValidationError

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.artifacts.schema import validate_common_envelope_schema
from east_v5.governance import ContractError, canonical_bytes, load_json

VALIDATOR_VERSION = "v5.orm-validator/v2"
ORM_RUNTIME_VERSION = "v5.restricted-orm-runtime/v1"

TRANSPORT_KEYS = {"envelope", "payload"}
RESTRICTED_ORM_PAYLOAD_KEYS = {"schema_version", "structure_closure_ref", "operation_closure_ref", "orm_source_code", "execution_contract", "operations", "orm_runtime_version", "code_hash"}
EXECUTION_CONTRACT_KEYS = {"entrypoint", "binding_slots", "empty_run_contract", "transaction_id", "allowed_api", "rollback_policy"}
OPERATION_KEYS = {"operation_id", "source_operation_step_id", "kind", "table_id", "data_placeholder_ref", "where_ref", "depends_on", "preconditions", "postconditions", "transaction_id"}
OP_TYPES = {"READ", "CHECK", "INSERT", "UPDATE"}
WRITE_TYPES = {"INSERT", "UPDATE"}
ALLOWED_API = ["context.transaction", "transaction.read", "transaction.check", "transaction.insert", "transaction.update", "transaction.rollback"]
ALLOWED_METHODS = {"read", "check", "insert", "update"}
FORBIDDEN_AST = (ast.Import, ast.ImportFrom, ast.ClassDef, ast.Lambda, ast.Global, ast.Nonlocal)
FORBIDDEN_NAMES = {"eval", "exec", "open", "__import__", "compile", "globals", "locals", "vars", "input", "breakpoint", "exit", "quit", "__builtins__"}
VALIDATION_TYPES = ("static_ast", "api_allowlist", "import_compile", "empty_dry_run", "object_detail_state")

EMPTY_REPORT = {"execution_mode": "empty", "executed_operation_ids": [], "write_count": 0}


def _fail(code: str) -> None:
    raise ContractError(code)


class _FakeTransaction:
    def __init__(self, calls: list[tuple[str, str | None, Any]]) -> None:
        self.calls = calls

    def __enter__(self) -> "_FakeTransaction":
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

    def rollback(self) -> None:
        self.calls.append(("rollback", None, None))


class _FakeContext:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, Any]] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self.calls)


def _placeholder(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "params"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and node.slice.value.startswith("slot_")
    )


def _mapping(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) and bool(node.keys) and all(
        isinstance(key, ast.Constant) and isinstance(key.value, str) and _placeholder(value)
        for key, value in zip(node.keys, node.values)
    )


def _call_method(statement: ast.stmt, tx_name: str) -> tuple[str, str]:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        _fail("AST_STATEMENT_FORBIDDEN")
    call = statement.value
    if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name) or call.func.value.id != tx_name:
        _fail("API_NOT_ALLOWED")
    method = call.func.attr
    if method not in ALLOWED_METHODS or call.keywords or len(call.args) != 2:
        _fail("API_NOT_ALLOWED")
    table, values = call.args
    if not isinstance(table, ast.Constant) or not isinstance(table.value, str) or not table.value:
        _fail("BUSINESS_DATA_OR_PLACEHOLDER_INVALID")
    if method in WRITE_TYPES:
        if not _mapping(values):
            _fail("BUSINESS_DATA_OR_PLACEHOLDER_INVALID")
    elif not isinstance(values, ast.Dict) or values.keys:
        _fail("BUSINESS_DATA_OR_PLACEHOLDER_INVALID")
    return method, table.value


class OrmValidator:
    """Read-only 251 → 252 validation and code-hash freezing."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validator(self, name: str) -> Draft202012Validator:
        schema = load_json(self.repo_root / "contracts" / "packages" / name)

        def absolutize(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: (schema["$id"] + item if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/") else absolutize(item)) for key, item in value.items()}
            if isinstance(value, list):
                return [absolutize(item) for item in value]
            return value

        schema = absolutize(schema)
        common = load_json(self.repo_root / "contracts" / "common" / "common-envelope.schema.json")
        runtime = load_json(self.repo_root / "contracts" / "v5-runtime-packages.schema.json")
        return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store={schema["$id"]: schema, common["$id"]: common, runtime["$id"]: runtime}))

    @staticmethod
    def _code_hash(code: str, execution_contract: dict[str, Any], operations: list[dict[str, Any]]) -> str:
        return hashlib.sha256(canonical_bytes({"orm_source_code": code, "execution_contract": execution_contract, "operations": operations})).hexdigest()

    def validate_restricted_orm(self, package: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any]) -> None:
        """Hard contract validation; raises ContractError on the first rejection.

        These rejections mean the package is not a well-formed restricted ORM plan
        at all (bad transport, envelope, schema, lineage, mode or a self-inconsistent
        code hash), so no feedback package can reference it.
        """
        before = copy.deepcopy((package, structure_closure, operation_closure))
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(payload, dict) or set(payload) != RESTRICTED_ORM_PAYLOAD_KEYS:
            _fail("UNKNOWN_FIELD:RESTRICTED_ORM")
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("restricted_orm", "251", "event_data"):
            _fail("FOUNDATION_ORM_FORBIDDEN" if envelope.get("mode") == "foundation" else "RESTRICTED_ORM_ENVELOPE_INVALID")
        if envelope["status"] == "blocked_manual":
            _fail("UPSTREAM_BLOCKED_MANUAL")
        if payload["structure_closure_ref"] != artifact_ref(structure_closure["envelope"]):
            _fail("STRUCTURE_CLOSURE_REFERENCE_MISMATCH")
        if payload["operation_closure_ref"] != artifact_ref(operation_closure["envelope"]):
            _fail("OPERATION_CLOSURE_REFERENCE_MISMATCH")
        if not isinstance(payload["operations"], list) or not payload["operations"]:
            _fail("OPERATIONS_EMPTY")
        if any(not isinstance(item, dict) or set(item) != OPERATION_KEYS for item in payload["operations"]):
            _fail("UNKNOWN_FIELD:OPERATION")
        if not isinstance(payload["execution_contract"], dict) or set(payload["execution_contract"]) != EXECUTION_CONTRACT_KEYS:
            _fail("UNKNOWN_FIELD:EXECUTION_CONTRACT")
        try:
            self._validator("restricted-orm-package.schema.json").validate(package)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:RESTRICTED_ORM") from exc
        expected_hash = self._code_hash(payload["orm_source_code"], payload["execution_contract"], payload["operations"])
        if payload["code_hash"] != expected_hash:
            _fail("CODE_HASH_DRIFT")
        if before != (package, structure_closure, operation_closure):
            _fail("INPUT_MUTATED")

    # --- five validation types (aggregate all failures) ---

    def _static_ast(self, payload: dict[str, Any], tree: ast.AST | None, parse_error: str | None, all_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        failures: list[tuple[str, dict[str, Any]]] = []
        if parse_error is not None:
            failures.append(("static_ast", self._item(["AST-001"], all_ids, ["compilable restricted ORM"], [f"SyntaxError: {parse_error}"], "Python AST 无法解析")))
            return failures
        assert tree is not None
        function = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                function = node
        if function is None or len(tree.body) != 1 or function.name != "apply":
            failures.append(("static_ast", self._item(["AST-002"], all_ids, ["单一顶层 apply 函数"], ["顶层非单一 apply 函数"], "AST 顶层结构不合法")))
        else:
            args = function.args
            if (function.decorator_list or function.returns or [arg.arg for arg in args.args] != ["context", "params"] or args.vararg or args.kwarg or args.defaults or args.kw_defaults):
                failures.append(("static_ast", self._item(["AST-003"], all_ids, ["apply(context, params) 无装饰器/默认值"], ["apply 签名漂移"], "AST 函数签名不合法")))
        forbidden = [type(node).__name__ for node in ast.walk(tree) if isinstance(node, FORBIDDEN_AST)]
        if forbidden:
            failures.append(("static_ast", self._item(["AST-004"], all_ids, ["无 import/class/lambda/global/nonlocal"], [sorted(set(forbidden))], "AST 包含禁用构造")))
        forbidden_names = sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES})
        if forbidden_names:
            failures.append(("static_ast", self._item(["AST-005"], all_ids, ["无动态执行/文件/进程内建名"], [forbidden_names], "AST 引用禁用内建名")))
        has_transaction = False
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and node.items and isinstance(node.items[0].context_expr, ast.Call):
                call = node.items[0].context_expr
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "context" and call.func.attr == "transaction":
                    has_transaction = True
        if not has_transaction:
            failures.append(("static_ast", self._item(["AST-006"], all_ids, ["with context.transaction() 事务上下文"], ["缺少事务上下文"], "AST 缺少事务上下文")))
        return failures

    def _api_allowlist(self, payload: dict[str, Any], tree: ast.AST | None, all_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        failures: list[tuple[str, dict[str, Any]]] = []
        contract = payload["execution_contract"]
        if contract["allowed_api"] != ALLOWED_API:
            failures.append(("api_allowlist", self._item(["API-001"], all_ids, [ALLOWED_API], [contract["allowed_api"]], "execution_contract.allowed_api 白名单漂移")))
        if payload["orm_runtime_version"] != ORM_RUNTIME_VERSION:
            failures.append(("api_allowlist", self._item(["API-002"], all_ids, [ORM_RUNTIME_VERSION], [payload["orm_runtime_version"]], "ORM 运行时版本漂移")))
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr not in ALLOWED_METHODS | {"transaction"}:
                    failures.append(("api_allowlist", self._item(["API-003"], all_ids, [f"transaction.{m}" for m in sorted(ALLOWED_METHODS)], [f"transaction.{node.func.attr}"], "源代码调用未批准 API")))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in {"apply"}:
                    failures.append(("api_allowlist", self._item(["API-004"], all_ids, ["仅 transaction.* / context.transaction"], [node.func.id], "源代码调用未批准函数")))
        return failures

    def _import_compile(self, payload: dict[str, Any], all_ids: list[str]) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any] | None]:
        namespace: dict[str, Any] = {}
        try:
            code = compile(payload["orm_source_code"], "<generated_orm>", "exec")
            exec(code, {"__builtins__": {}}, namespace)
            if not callable(namespace.get("apply")):
                raise ValueError("apply not callable")
        except Exception as exc:  # noqa: BLE001 - any compile/exec failure is a single rejection
            return [("import_compile", self._item(["IMP-001"], all_ids, ["可导入/编译并定义 apply"], [f"{type(exc).__name__}: {exc}"], "导入/编译失败"))], None
        return [], namespace

    def _empty_dry_run(self, payload: dict[str, Any], namespace: dict[str, Any] | None, all_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        failures: list[tuple[str, dict[str, Any]]] = []
        if namespace is None:
            return failures
        context = _FakeContext()
        try:
            report = namespace["apply"](context, {})
        except Exception as exc:  # noqa: BLE001 - a dry run raising is itself the rejection
            failures.append(("empty_dry_run", self._item(["DRY-001"], all_ids, [EMPTY_REPORT], [f"{type(exc).__name__}: {exc}"], "空数据空跑异常")))
            return failures
        if report != EMPTY_REPORT:
            failures.append(("empty_dry_run", self._item(["DRY-002"], all_ids, [EMPTY_REPORT], [report], "空数据空跑返回结构非法")))
        if context.calls:
            failures.append(("empty_dry_run", self._item(["DRY-003"], all_ids, [{"transaction_calls": 0}], [{"transaction_calls": len(context.calls)}], "空数据空跑产生数据库副作用")))
        return failures

    def _object_detail_state(self, payload: dict[str, Any], operation_closure: dict[str, Any], all_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
        failures: list[tuple[str, dict[str, Any]]] = []
        operations = payload["operations"]
        closure_ops = operation_closure["payload"]["operations"]
        if len(operations) != len(closure_ops):
            failures.append(("object_detail_state", self._item(["ODS-001"], all_ids, [f"{len(closure_ops)} 个操作"], [f"{len(operations)} 个操作"], "与 230 操作闭包操作数量不一致")))
            return failures
        observed_ids: set[str] = set()
        for index, (orm_op, closure_op) in enumerate(zip(operations, closure_ops), 1):
            expected_id = f"orm-op-{index:03d}"
            if orm_op["operation_id"] != expected_id:
                failures.append(("object_detail_state", self._item(["ODS-002"], [orm_op["operation_id"]], [expected_id], [orm_op["operation_id"]], "操作序号漂移")))
            if orm_op["source_operation_step_id"] != closure_op["operation_step_id"]:
                failures.append(("object_detail_state", self._item(["ODS-003"], [orm_op["operation_id"]], [closure_op["operation_step_id"]], [orm_op["source_operation_step_id"]], "与操作闭包步骤标识不一致")))
            if orm_op["kind"] != closure_op["operation_type"] or orm_op["table_id"] != closure_op["object_refs"][0]["table_id"]:
                failures.append(("object_detail_state", self._item(["ODS-004"], [orm_op["operation_id"]], [f"{closure_op['operation_type']} {closure_op['object_refs'][0]['table_id']}"], [f"{orm_op['kind']} {orm_op['table_id']}"], "与操作闭包操作类型/表不一致")))
            if orm_op["transaction_id"] != closure_op["transaction_boundary"]["transaction_id"]:
                failures.append(("object_detail_state", self._item(["ODS-005"], [orm_op["operation_id"]], [closure_op["transaction_boundary"]["transaction_id"]], [orm_op["transaction_id"]], "事务标识与操作闭包不一致")))
            if orm_op["operation_id"] in observed_ids:
                failures.append(("object_detail_state", self._item(["ODS-006"], [orm_op["operation_id"]], ["唯一 operation_id"], [orm_op["operation_id"]], "操作标识重复")))
            observed_ids.add(orm_op["operation_id"])
            for dep in orm_op["depends_on"]:
                if dep not in observed_ids:
                    failures.append(("object_detail_state", self._item(["ODS-007"], [orm_op["operation_id"]], ["仅依赖前序操作"], [dep], "依赖前向引用或环")))
        if failures:
            return failures
        update_indices = [index for index, op in enumerate(operations) if op["kind"] == "UPDATE"]
        insert_ids = {op["operation_id"] for op in operations if op["kind"] == "INSERT"}
        for index in update_indices:
            op = operations[index]
            table_insert = [item for item in op["depends_on"] if item in insert_ids]
            if not table_insert:
                failures.append(("object_detail_state", self._item(["ODS-008"], [op["operation_id"]], ["UPDATE 依赖本表 INSERT"], [op["depends_on"]], "对象-明细-状态顺序：UPDATE 未依赖 INSERT")))
        # UPDATE state steps must form the trailing "commit" phase: every UPDATE must come after every INSERT.
        last_insert = max((i for i, op in enumerate(operations) if op["kind"] == "INSERT"), default=-1)
        for index in update_indices:
            if index <= last_insert:
                failures.append(("object_detail_state", self._item(["ODS-009"], [operations[index]["operation_id"]], ["UPDATE(状态) 位于全部 INSERT 之后"], [operations[index]["operation_id"]], "对象-明细-状态顺序逆序")))
        return failures

    @staticmethod
    def _item(rule_ids: list[str], operation_locations: list[str], expected: list[Any], actual: list[Any], detail: str) -> dict[str, Any]:
        return {"failed_rule_ids": rule_ids, "operation_locations": operation_locations, "expected_values": expected, "actual_values": actual, "error_details": detail}

    def _run_checks(self, package: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any]) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any], dict[str, Any]]:
        payload = package["payload"]
        operations = payload["operations"]
        all_ids = [op["operation_id"] for op in operations]
        tree: ast.AST | None = None
        parse_error: str | None = None
        try:
            tree = ast.parse(payload["orm_source_code"], mode="exec")
        except SyntaxError as exc:
            parse_error = exc.msg

        failures: list[tuple[str, dict[str, Any]]] = []
        failures.extend(self._static_ast(payload, tree, parse_error, all_ids))
        failures.extend(self._api_allowlist(payload, tree, all_ids))
        import_failures, namespace = self._import_compile(payload, all_ids)
        failures.extend(import_failures)
        failures.extend(self._empty_dry_run(payload, namespace, all_ids))
        failures.extend(self._object_detail_state(payload, operation_closure, all_ids))

        rule_refs = sorted({rule_ref for op in operation_closure["payload"]["operations"] for rule_ref in op.get("object_detail_state_rule_refs", [])})
        safety: dict[str, Any] = {
            "static_ast": {"status": "PASS"},
            "api_allowlist": {"status": "PASS", "allowed_api": list(ALLOWED_API)},
            "import_compile": {"status": "PASS"},
            "transaction": {"status": "PASS", "transaction_id": payload["execution_contract"]["transaction_id"]},
            "code_hash": {"status": "PASS", "code_hash": payload["code_hash"]},
        }
        sequence: dict[str, Any] = {
            "empty_dry_run": {"status": "PASS", "execution_mode": "empty", "write_count": 0, "database_side_effect": False},
            "object_detail_state": {"status": "PASS", "operation_count": len(operations), "rule_refs": rule_refs},
        }
        return failures, safety, sequence

    # --- outputs ---

    def freeze_orm(self, restricted_orm: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any], *, validated_at: str | None = None) -> dict[str, Any]:
        """Validate read-only and return the reproducible 252 frozen package."""
        self.validate_restricted_orm(restricted_orm, structure_closure, operation_closure)
        before = copy.deepcopy((restricted_orm, structure_closure, operation_closure))
        failures, safety, sequence = self._run_checks(restricted_orm, structure_closure, operation_closure)
        if failures:
            _fail("ORM_VALIDATION_REJECTED")
        source = restricted_orm["envelope"]
        payload: dict[str, Any] = {
            "schema_version": "v5.frozen-orm/v2",
            "validated_orm_plan": {"orm_source_code": restricted_orm["payload"]["orm_source_code"], "execution_contract": copy.deepcopy(restricted_orm["payload"]["execution_contract"]), "operations": copy.deepcopy(restricted_orm["payload"]["operations"]), "code_hash": restricted_orm["payload"]["code_hash"]},
            "source_orm_plan_ref": artifact_ref(source),
            "safety_precheck_report": safety,
            "sequence_validation_report": sequence,
            "validator_module_version": VALIDATOR_VERSION,
            "validated_hash": restricted_orm["payload"]["code_hash"],
            "validated_at": validated_at or source["created_at"],
        }
        envelope: dict[str, Any] = {
            "artifact_id": f"{source['artifact_id']}:frozen-orm", "artifact_type": "frozen_orm", "run_id": source["run_id"], "qa_id": source["qa_id"],
            "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
            "attempt_no": source["attempt_no"], "producer_id": "252", "parent_artifact_refs": [artifact_ref(source)], "input_hashes": [source["content_hash"]],
            "status": "validated", "mode": "event_data", "created_at": source["created_at"], "trace_id": source["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        output = {"envelope": envelope, "payload": payload}
        try:
            self._validator("frozen-orm-package.schema.json").validate(output)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:FROZEN_ORM") from exc
        validate_envelope(self.repo_root, envelope, payload)
        if before != (restricted_orm, structure_closure, operation_closure):
            _fail("INPUT_MUTATED")
        return output

    def build_validation_feedback(self, restricted_orm: dict[str, Any], structure_closure: dict[str, Any], operation_closure: dict[str, Any]) -> dict[str, Any]:
        """Aggregate every validation failure and emit orm_validation_failed_feedback."""
        self.validate_restricted_orm(restricted_orm, structure_closure, operation_closure)
        failures, _, _ = self._run_checks(restricted_orm, structure_closure, operation_closure)
        if not failures:
            _fail("ORM_VALIDATION_NOT_FAILED")
        validation_types = sorted({item[0] for item in failures})
        failed_items = [item[1] for item in failures]
        source = restricted_orm["envelope"]
        payload: dict[str, Any] = {"schema_version": "v5.orm-validation-failed-feedback/v1", "orm_plan_ref": artifact_ref(source), "decision": "fail", "validation_types": validation_types, "failed_items": failed_items}
        envelope: dict[str, Any] = {
            "artifact_id": f"{source['artifact_id']}:orm-validation-feedback", "artifact_type": "orm_validation_failed_feedback", "run_id": source["run_id"], "qa_id": source["qa_id"],
            "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
            "attempt_no": source["attempt_no"], "producer_id": "252", "parent_artifact_refs": [artifact_ref(source)], "input_hashes": [source["content_hash"]],
            "status": "rejected", "mode": "event_data", "created_at": source["created_at"], "trace_id": source["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        output = {"envelope": envelope, "payload": payload}
        validate_common_envelope_schema(self.repo_root, envelope)
        if envelope["content_hash"] != content_hash(envelope, payload):
            _fail("CONTENT_HASH_DRIFT")
        try:
            self._validator("orm-validation-failed-feedback-package.schema.json").validate(output)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:ORM_VALIDATION_FEEDBACK") from exc
        return output
