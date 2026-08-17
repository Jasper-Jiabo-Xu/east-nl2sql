"""Strict Foundation database-copy regression gate for Agent 260."""
from __future__ import annotations

import hashlib
import importlib
import sqlite3
import copy
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.foundation.compiler import compile_insert_batch
from east_v5.governance import ContractError, canonical_bytes, load_json, sha256

_closure = importlib.import_module("east_v5.agents.220.closure")

_PLAN_KEYS = {
    "task_ref", "structure_closure_ref", "verified_data_ref", "verified_validated_hash",
    "snapshot_ref", "snapshot_hash", "record_counts", "input_sha256",
    "writes_formal_store", "plan_sha256",
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _registry(repo_root: Path) -> Registry:
    resources = []
    for relative in ("contracts/common/common-envelope.schema.json", "contracts/v5-runtime-packages.schema.json"):
        schema = load_json(repo_root / relative)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    for path in (repo_root / "contracts" / "packages").glob("*.schema.json"):
        schema = load_json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _validate_schema(repo_root: Path, package: dict[str, Any], relative: str, label: str) -> None:
    try:
        Draft202012Validator(load_json(repo_root / relative), registry=_registry(repo_root)).validate(package)
    except ValidationError as exc:
        raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc


def _records(verified: dict[str, Any]) -> list[dict[str, Any]]:
    return [record for group in verified["payload"]["validated_data_package"]["data_groups"] for record in group["records"]]


def _distribution(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    actual: dict[str, dict[str, int]] = {}
    for record in records:
        labels = [item.removeprefix("distribution:") for item in record["target_condition_refs"] if item.startswith("distribution:")]
        if len(labels) > 1:
            _fail("FOUNDATION_DISTRIBUTION_AMBIGUOUS")
        label = labels[0] if labels else "default"
        actual.setdefault(record["table_id"], {})[label] = actual.setdefault(record["table_id"], {}).get(label, 0) + 1
    return actual


def _hierarchy_refs(closure: dict[str, Any]) -> set[tuple[str, int, str]]:
    found = set()
    for reference in closure["payload"]["references"]:
        ref = reference.get("artifact_ref") if isinstance(reference, dict) and reference.get("type") == "hierarchy_asset" else None
        if isinstance(ref, dict) and set(ref) == {"artifact_id", "version", "content_hash"}:
            found.add((ref["artifact_id"], ref["version"], ref["content_hash"]))
    return found


def _validate_references(data: dict[str, Any], snapshot: dict[str, Any]) -> None:
    records = [record for group in data["data_groups"] for record in group["records"]]
    ids = {record["record_id"] for record in records}
    if len(ids) != len(records):
        _fail("FOUNDATION_RECORD_ID_DUPLICATE")
    snapshot_keys = {(item["record_keys"]["table_id"], item["record_keys"]["primary_key"]) for item in snapshot["object_state_records"]}
    for group in data["data_groups"]:
        for record in group["records"]:
            if any(ref["record_id"] not in ids for ref in record["temporary_record_refs"]):
                _fail("FOUNDATION_REFERENTIAL_INTEGRITY_FAILED")
            if any((ref["table_id"], ref["record_key"]) not in snapshot_keys for ref in record["existing_record_refs"]):
                _fail("FOUNDATION_REFERENTIAL_INTEGRITY_FAILED")
        for link in group["record_links"]:
            if link["source_record_id"] not in ids or link["target_record_id"] not in ids:
                _fail("FOUNDATION_REFERENTIAL_INTEGRITY_FAILED")


def _plan_hash(plan: dict[str, Any]) -> str:
    return sha256({key: value for key, value in plan.items() if key != "plan_sha256"})


def _validate_execution_plan(plan: dict[str, Any]) -> None:
    if set(plan) != _PLAN_KEYS:
        _fail("REGRESSION_PLAN_FIELDS_INVALID")
    if plan["writes_formal_store"] is not False or not isinstance(plan["record_counts"], dict):
        _fail("REGRESSION_PLAN_INVALID")
    expected_input = sha256({"task": plan["task_ref"], "closure": plan["structure_closure_ref"], "verified": plan["verified_data_ref"], "snapshot": plan["snapshot_ref"]})
    if plan["input_sha256"] != expected_input:
        _fail("REGRESSION_PLAN_INPUT_DRIFT")
    if plan["plan_sha256"] != _plan_hash(plan):
        _fail("REGRESSION_PLAN_HASH_DRIFT")


def _validate_execution_package(repo_root: Path, plan: dict[str, Any], verified_bound_data: dict[str, Any]) -> None:
    """Re-authenticate the package at the write boundary, rather than trusting plan construction."""
    _validate_schema(repo_root, verified_bound_data, "contracts/packages/verified-bound-data-package.schema.json", "VERIFIED_BOUND_DATA")
    envelope, payload = verified_bound_data["envelope"], verified_bound_data["payload"]
    validate_envelope(repo_root, envelope, payload)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"], envelope["status"]) != ("verified_bound_data", "242", "foundation", "validated"):
        _fail("VERIFIED_BOUND_DATA_ENVELOPE_INVALID")
    data = payload["validated_data_package"]
    if artifact_ref(envelope) != plan["verified_data_ref"]:
        _fail("EXECUTION_VERIFIED_REF_DRIFT")
    if payload["validated_hash"] != plan["verified_validated_hash"] or payload["validated_hash"] != sha256(data):
        _fail("EXECUTION_VERIFIED_HASH_DRIFT")
    if data["foundation_task_ref"] != plan["task_ref"]:
        _fail("EXECUTION_TASK_REF_DRIFT")
    if data["structure_closure_ref"] != plan["structure_closure_ref"]:
        _fail("EXECUTION_CLOSURE_REF_DRIFT")
    if data["database_snapshot_ref"] != plan["snapshot_ref"] or plan["snapshot_hash"] == "":
        _fail("EXECUTION_SNAPSHOT_REF_DRIFT")
    if data["operation_closure_ref"] is not None:
        _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
    if payload["source_data_package_ref"] not in envelope["parent_artifact_refs"]:
        _fail("SOURCE_DATA_LINEAGE_MISSING")


def _database_files(connection: sqlite3.Connection) -> set[Path]:
    """Return physical database files; private :memory: databases deliberately have no file identity."""
    return {Path(row[2]).resolve() for row in connection.execute("PRAGMA database_list") if row[2]}


def _assert_copy_formal_isolated(copy_connection: sqlite3.Connection, formal_connection: sqlite3.Connection) -> None:
    if copy_connection is formal_connection or _database_files(copy_connection) & _database_files(formal_connection):
        _fail("DATABASE_COPY_FORMAL_NOT_ISOLATED")


def validate_foundation_regression_inputs(repo_root: Path, task_package: dict[str, Any], structure_closure: dict[str, Any], verified_bound_data: dict[str, Any], database_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate complete schema-valid inputs.  No profile or inferred intent is accepted."""
    task_envelope, task = _closure.validate_foundation_task_package(task_package)
    _closure.validate_structure_closure_package(structure_closure)
    _validate_schema(repo_root, verified_bound_data, "contracts/packages/verified-bound-data-package.schema.json", "VERIFIED_BOUND_DATA")
    _validate_schema(repo_root, database_snapshot, "contracts/packages/database-read-snapshot.schema.json", "DATABASE_READ_SNAPSHOT")
    envelope, payload = verified_bound_data["envelope"], verified_bound_data["payload"]
    snapshot_envelope, snapshot = database_snapshot["envelope"], database_snapshot["payload"]
    validate_envelope(repo_root, envelope, payload)
    validate_envelope(repo_root, snapshot_envelope, snapshot)
    if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"], envelope["status"]) != ("verified_bound_data", "242", "foundation", "validated"):
        _fail("VERIFIED_BOUND_DATA_ENVELOPE_INVALID")
    if (snapshot_envelope["artifact_type"], snapshot_envelope["producer_id"], snapshot_envelope["mode"]) != ("database_read_snapshot", "EAS-19", "foundation"):
        _fail("DATABASE_SNAPSHOT_INVALID")
    task_ref = artifact_ref(task_envelope)
    if structure_closure["envelope"]["mode"] != "foundation" or structure_closure["payload"].get("foundation_task_ref") != task_ref:
        _fail("FOUNDATION_TASK_REF_DRIFT")
    data = payload["validated_data_package"]
    if data["foundation_task_ref"] != task_ref:
        _fail("FOUNDATION_TASK_REF_DRIFT")
    if data["structure_closure_ref"] != artifact_ref(structure_closure["envelope"]):
        _fail("STRUCTURE_CLOSURE_REFERENCE_MISMATCH")
    if data["operation_closure_ref"] is not None:
        _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
    if payload["validated_hash"] != sha256(data):
        _fail("VERIFIED_DATA_HASH_DRIFT")
    if payload["source_data_package_ref"] not in envelope["parent_artifact_refs"]:
        _fail("SOURCE_DATA_LINEAGE_MISSING")
    if snapshot["base_database_version"] != task["target_database_version"]:
        _fail("DATABASE_VERSION_DRIFT")
    if data["database_snapshot_ref"] != artifact_ref(snapshot_envelope):
        _fail("DATABASE_SNAPSHOT_REF_DRIFT")
    if snapshot["snapshot_hash"] != sha256({key: value for key, value in snapshot.items() if key != "snapshot_hash"}):
        _fail("DATABASE_SNAPSHOT_HASH_DRIFT")
    records = _records(verified_bound_data)
    _validate_references(data, snapshot)
    actual_counts: dict[str, int] = {}
    for record in records:
        table = record["table_id"]
        if table not in task["target_table_field_scope"] or record["case_role"] != "foundation":
            _fail("FOUNDATION_SCOPE_OUT_OF_BOUNDS")
        if record.get("record_type") in task["prohibited_record_types"]:
            _fail("FOUNDATION_PROHIBITED_TYPE_HIT")
        if not {value["field_id"] for value in record["field_values"]} <= set(task["target_table_field_scope"][table]):
            _fail("FOUNDATION_SCOPE_OUT_OF_BOUNDS")
        actual_counts[table] = actual_counts.get(table, 0) + 1
    if actual_counts != task["target_counts"]:
        _fail("FOUNDATION_TARGET_COUNT_MISMATCH")
    if _distribution(records) != task["distribution_targets"]:
        _fail("FOUNDATION_DISTRIBUTION_MISMATCH")
    expected_hierarchy = {(ref["artifact_id"], ref["version"], ref["content_hash"]) for ref in task["hierarchy_asset_refs"]}
    if not expected_hierarchy <= _hierarchy_refs(structure_closure):
        _fail("FOUNDATION_HIERARCHY_REFERENCE_INVALID")
    plan = {
        "task_ref": task_ref,
        "structure_closure_ref": artifact_ref(structure_closure["envelope"]),
        "verified_data_ref": artifact_ref(envelope),
        "verified_validated_hash": payload["validated_hash"],
        "snapshot_ref": artifact_ref(snapshot_envelope),
        "snapshot_hash": snapshot["snapshot_hash"],
        "record_counts": actual_counts,
        "input_sha256": sha256({"task": task_ref, "closure": artifact_ref(structure_closure["envelope"]), "verified": artifact_ref(envelope), "snapshot": artifact_ref(snapshot_envelope)}),
        "writes_formal_store": False,
    }
    plan["plan_sha256"] = _plan_hash(plan)
    return plan


def run_database_copy_regression(repo_root: Path, plan: dict[str, Any], verified_bound_data: dict[str, Any], copy_connection: sqlite3.Connection, formal_connection: sqlite3.Connection, event_owned_tables: set[str]) -> dict[str, Any]:
    """Execute only the authenticated plan on a physically separate copy in one atomic transaction."""
    _validate_execution_plan(plan)
    _validate_execution_package(repo_root, plan, verified_bound_data)
    _assert_copy_formal_isolated(copy_connection, formal_connection)
    formal_before = "\n".join(formal_connection.iterdump()).encode()
    source_records = _records(verified_bound_data)
    ids = {record["record_id"]: f"r{index}" for index, record in enumerate(source_records, start=1)}
    records = [{"record_id": ids[record["record_id"]], "table": record["table_id"], "values": {value["field_id"]: None if value["is_null"] else value["value"] for value in record["field_values"]}, "depends_on": [ids[item["record_id"]] for item in record["temporary_record_refs"]]} for record in source_records]
    batch = compile_insert_batch({"schema_version": "v5.foundation-verified-data/v1", "mode": "foundation", "base_database_version": "copy-bound", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "records": records}, event_owned_tables)
    before = {table: copy_connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in plan["record_counts"]}
    committed = False
    try:
        copy_connection.execute("BEGIN IMMEDIATE")
        for operation in batch["operations"]:
            copy_connection.execute(operation["sql"], operation["parameters"])
        delta = {table: copy_connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] - before[table] for table in before}
        if delta != plan["record_counts"]:
            _fail("DATABASE_DELTA_MISMATCH")
        formal_after = "\n".join(formal_connection.iterdump()).encode()
        if hashlib.sha256(formal_before).digest() != hashlib.sha256(formal_after).digest():
            _fail("FORMAL_STORE_MUTATED")
        copy_connection.commit()
        committed = True
    except Exception:
        copy_connection.rollback()
        raise
    if not committed:
        _fail("DATABASE_COPY_COMMIT_FAILED")
    return {"batch": batch, "database_delta": delta, "rollback_verified": False, "formal_store_sha256": hashlib.sha256(formal_after).hexdigest()}


# Event-data support intentionally lives beside (not inside) the Foundation
# execution path above.  Foundation has a distinct frozen task contract and
# deterministic compiler; event data alone can execute the already validated
# restricted ORM on a disposable database copy.
class _EventTransaction:
    def __init__(self, connection: sqlite3.Connection, report: list[dict[str, Any]]):
        self.connection, self.report = connection, report

    def __enter__(self):
        self.connection.execute("BEGIN")
        return self

    def __exit__(self, typ, *_):
        (self.connection.commit if typ is None else self.connection.rollback)()
        return False

    def read(self, table: str, values: dict[str, Any]) -> None:
        self.connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchall()
        self.report.append({"operation": "read", "table_id": table, "values": values})

    def check(self, table: str, values: dict[str, Any]) -> None:
        self.read(table, values); self.report[-1]["operation"] = "check"

    def insert(self, table: str, values: dict[str, Any]) -> None:
        fields = list(values)
        self.connection.execute(f'INSERT INTO "{table}" ({", ".join(chr(34) + f + chr(34) for f in fields)}) VALUES ({", ".join("?" for _ in fields)})', [values[f] for f in fields])
        self.report.append({"operation": "insert", "table_id": table, "values": values, "rowcount": 1})

    def update(self, table: str, values: dict[str, Any]) -> None:
        fields = list(values)
        where = " AND ".join(f'"{field}" = ?' for field in fields)
        if self.connection.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}', [values[f] for f in fields]).fetchone()[0] != 1:
            _fail("ORM_PLAN_ERROR:UPDATE_TARGET_AMBIGUOUS")
        self.connection.execute(f'UPDATE "{table}" SET ' + ", ".join(f'"{field}" = ?' for field in fields) + f' WHERE {where}', [*values.values(), *values.values()])
        self.report.append({"operation": "update", "table_id": table, "values": values, "rowcount": 1})


class _EventContext:
    def __init__(self, connection: sqlite3.Connection, report: list[dict[str, Any]]): self.connection, self.report = connection, report
    def transaction(self) -> _EventTransaction: return _EventTransaction(self.connection, self.report)


class DatabaseCopyRegression:
    """EAS-35 event-data gate; it never accepts a projected 110 payload."""

    def __init__(self, repo_root: Path): self.repo_root = repo_root.resolve()

    def _validate(self, package: dict[str, Any], relative: str, code: str) -> None:
        _validate_schema(self.repo_root, package, f"contracts/packages/{relative}", code)

    @staticmethod
    def _transport(package: dict[str, Any], artifact_type: str, producer: str, mode: str) -> None:
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope = package["envelope"]
        if (envelope.get("artifact_type"), envelope.get("producer_id"), envelope.get("mode")) != (artifact_type, producer, mode):
            _fail("INPUT_ENVELOPE_INVALID")
        if envelope.get("status") == "blocked_manual": _fail("UPSTREAM_BLOCKED_MANUAL")

    def validate_event_inputs(self, data: dict[str, Any], orm: dict[str, Any], snapshot: dict[str, Any], review: dict[str, Any], query_spec: dict[str, Any]) -> None:
        before = copy.deepcopy((data, orm, snapshot, review, query_spec))
        for package, kind, producer in ((data, "verified_bound_data", "242"), (orm, "frozen_orm", "252"), (snapshot, "database_read_snapshot", "EAS-19"), (review, "question_sql_dual_review_passed", "110"), (query_spec, "query_specification_package", "140")):
            self._transport(package, kind, producer, "event_data")
            if package is review:
                # 110 passed output is introduced by this frozen contract and
                # is intentionally not a legacy catalog entry.
                from east_v5.artifacts.schema import validate_common_envelope_schema
                validate_common_envelope_schema(self.repo_root, package["envelope"])
                if package["envelope"]["content_hash"] != content_hash(package["envelope"], package["payload"]): _fail("CONTENT_HASH_DRIFT")
            else:
                validate_envelope(self.repo_root, package["envelope"], package["payload"])
        self._validate(data, "verified-bound-data-package.schema.json", "SCHEMA_VALIDATION_FAILED:VERIFIED_BOUND_DATA")
        self._validate(orm, "frozen-orm-package.schema.json", "SCHEMA_VALIDATION_FAILED:FROZEN_ORM")
        self._validate(snapshot, "database-read-snapshot.schema.json", "SCHEMA_VALIDATION_FAILED:DATABASE_SNAPSHOT")
        self._validate(review, "question-sql-dual-review-passed-package.schema.json", "SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED")
        self._validate(query_spec["payload"], "query-specification-package.schema.json", "SCHEMA_VALIDATION_FAILED:QUERY_SPEC")
        r = review["payload"]
        if r["query_specification_package"] != artifact_ref(query_spec["envelope"]): _fail("QUERY_SPEC_REFERENCE_MISMATCH")
        if r["package_hash"] != sha256({key: value for key, value in r.items() if key != "package_hash"}): _fail("QUESTION_SQL_PACKAGE_HASH_DRIFT")
        if r["query_specification_package"] not in review["envelope"]["parent_artifact_refs"]: _fail("QUESTION_SQL_PARENT_REFERENCE_MISSING")
        plan = orm["payload"]["validated_orm_plan"]
        expected = hashlib.sha256(canonical_bytes({"orm_source_code": plan["orm_source_code"], "execution_contract": plan["execution_contract"], "operations": plan["operations"]})).hexdigest()
        if orm["payload"].get("validated_hash") != expected or plan.get("code_hash") != expected: _fail("ORM_HASH_DRIFT")
        if data["payload"]["validated_data_package"].get("database_snapshot_ref") not in (None, artifact_ref(snapshot["envelope"])): _fail("DATABASE_SNAPSHOT_REFERENCE_MISMATCH")
        if before != (data, orm, snapshot, review, query_spec): _fail("INPUT_MUTATED")

    @staticmethod
    def _rows(data: dict[str, Any]) -> list[dict[str, Any]]:
        return [record for group in data["payload"]["validated_data_package"]["data_groups"] for record in group["records"]]

    @classmethod
    def _bind(cls, data: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        available: dict[tuple[str, str], list[Any]] = {}
        for record in cls._rows(data):
            if record["case_role"] != "positive":
                continue
            for value in record["field_values"]:
                available.setdefault((record["table_id"], value["field_id"]), []).append(None if value["is_null"] else value["value"])
        bound = {}
        for slot in plan["execution_contract"]["binding_slots"]:
            pairs = [(slot["target_table_id"], field) for field in slot["field_ids"]]
            values = [available.get(pair, []) for pair in pairs]
            if any(not value for value in values): _fail("DATA_VALUE_ERROR:BINDING_SLOT_MISSING")
            if len(pairs) != 1 or any(len(value) != 1 for value in values): _fail("ORM_PLAN_ERROR:BINDING_SLOT_AMBIGUOUS")
            bound[slot["slot_id"]] = values[0][0]
        return bound

    @staticmethod
    def _metrics(rows: list[tuple[Any, ...]], spec: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
        flat = [value for row in rows for value in row]
        role_values = {role: {value["value"] for record in records if record["case_role"] == role for value in record["field_values"] if not value["is_null"]} for role in {record["case_role"] for record in records}}
        positive_values = role_values.get("positive", set())
        negative_values = set().union(*(values for role, values in role_values.items() if role in {"negative", "hard_negative"})) if role_values else set()
        positive_hits = sum(1 for value in positive_values if value in flat)
        negative_hits = sum(1 for value in negative_values if value in flat)
        coverage = spec["condition_coverage"]
        conditions_ok = all(all(role_values.get(role, set()) and any(value in flat for value in role_values[role]) for role in item["positive_types"]) and all(not any(value in flat for value in role_values.get(role, set())) for role in item["negative_types"]) for item in coverage)
        code_ok = all(all(value in flat for value in item["target_code_values"]) for item in spec["code_value_coverage"])
        aggregation = spec["aggregation_dedup_sort_time"]
        group_fields = aggregation["group_by_fields"]
        return_fields = [item["field_id"] for item in spec["return_fields"]]
        indexes = [return_fields.index(field) for field in group_fields if field in return_fields]
        group_keys = [tuple(row[index] for index in indexes) for row in rows] if indexes else [tuple(row) for row in rows]
        distinct_count = len({tuple(row) for row in rows})
        distinct_ok = not aggregation.get("distinct_required", False) or distinct_count == len(rows)
        groups_ok = len(group_keys) == len(set(group_keys))
        count = spec["expected_row_group_count"]
        lower, upper = count["target"] + count["tolerance_range"]["low"], count["target"] + count["tolerance_range"]["high"]
        rows_ok = len(rows) >= count["minimum"] and lower <= len(rows) <= upper
        baseline = max(1, len(set(group_keys)))
        multiplier = len(rows) / baseline
        limit = spec["join_expansion_limit"]
        join_ok = multiplier <= limit["max_multiplier"] and len(rows) <= limit["max_result_rows"]
        pos_ok = positive_hits >= spec["minimum_positive_count"] and negative_hits == 0 and conditions_ok and code_ok
        return {"positive_negative_metrics": {"positive_hits": positive_hits, "negative_hits": negative_hits, "negative_excluded": negative_hits == 0, "condition_coverage_passed": conditions_ok, "code_value_coverage_passed": code_ok, "passed": pos_ok}, "density_group_metrics": {"row_count": len(rows), "distinct_count": distinct_count, "group_count": len(set(group_keys)), "target": count["target"], "tolerance_range": count["tolerance_range"], "distinct_required": aggregation.get("distinct_required", False), "passed": rows_ok and distinct_ok and groups_ok}, "join_expansion_metrics": {"row_count": len(rows), "baseline_grain_count": baseline, "actual_multiplier": multiplier, "max_multiplier": limit["max_multiplier"], "max_result_rows": limit["max_result_rows"], "passed": join_ok}}

    @contextmanager
    def _sandbox(self, formal_database: Path) -> Iterator[sqlite3.Connection]:
        before = formal_database.read_bytes()
        with tempfile.TemporaryDirectory(prefix="east-v5-260-") as directory:
            copy_path = Path(directory) / "copy.sqlite"; shutil.copy2(formal_database, copy_path)
            connection = sqlite3.connect(copy_path)
            try: yield connection
            finally: connection.close()
        if formal_database.read_bytes() != before: _fail("FORMAL_DATABASE_MUTATED")

    def feedback(self, data: dict[str, Any], orm: dict[str, Any] | None, snapshot: dict[str, Any], error_code: str, stage: str, detail: str, attempt_no: int, parents: list[dict[str, Any]]) -> dict[str, Any]:
        error_code, route = ("MANUAL_REVIEW_REQUIRED", "manual") if attempt_no == 3 else (error_code, {"DATA_VALUE_ERROR": "241", "ORM_PLAN_ERROR": "251", "FOUNDATION_REQUIRED": "210"}.get(error_code, "210"))
        payload = {"schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data", "input_data_refs": [artifact_ref(data["envelope"])], "input_orm_ref": artifact_ref(orm["envelope"]) if orm else None, "sandbox_snapshot_id": snapshot["payload"]["snapshot_id"], "failure_details": {"error_code": error_code, "error_stage": stage, "error_location": "database_copy", "expected_values": [], "actual_values": [detail], "sql_error_detail": None, "regression_metrics": {}}, "route_target": route, "retry_count": attempt_no}
        return self._wrap("sql_regression_failed_feedback", payload, data["envelope"], parents, attempt_no, "rejected")

    def run_event(self, data: dict[str, Any], orm: dict[str, Any], snapshot: dict[str, Any], review: dict[str, Any], query_spec: dict[str, Any], formal_database: Path, *, attempt_no: int = 1) -> dict[str, Any]:
        self.validate_event_inputs(data, orm, snapshot, review, query_spec)
        if attempt_no not in (1, 2, 3): _fail("ATTEMPT_OUT_OF_RANGE")
        parents = [artifact_ref(item["envelope"]) for item in (data, orm, snapshot, review, query_spec)]
        try:
            params = self._bind(data, orm["payload"]["validated_orm_plan"])
            with self._sandbox(formal_database) as connection:
                operations: list[dict[str, Any]] = []; namespace: dict[str, Any] = {}
                exec(compile(orm["payload"]["validated_orm_plan"]["orm_source_code"], "<frozen_orm>", "exec"), {"__builtins__": {}}, namespace)
                execution = namespace["apply"](_EventContext(connection, operations), params)
                sql_gold = review["payload"]["candidate_content"]["sql_gold"]
                rows = connection.execute(sql_gold).fetchall()
                metrics = self._metrics(rows, query_spec["payload"], self._rows(data))
                if not all(item["passed"] for item in metrics.values()): _fail("DATA_VALUE_ERROR:REGRESSION_GATE")
        except sqlite3.Error as exc:
            return self.feedback(data, orm, snapshot, "SQL_EXECUTION_ERROR", "sql_execution", str(exc), attempt_no, parents)
        except ContractError as exc:
            category = "ORM_PLAN_ERROR" if "ORM_PLAN" in str(exc) else ("FOUNDATION_REQUIRED" if "FOUNDATION_REQUIRED" in str(exc) else "DATA_VALUE_ERROR")
            return self.feedback(data, orm, snapshot, category, "binding" if "BINDING" in str(exc) else "regression_gate", str(exc), attempt_no, parents)
        payload = {"schema_version": "v5.regression-passed-data-orm/v1", "regression_package_id": f"regression-{data['envelope']['artifact_id']}", "mode": "event_data", "data_package_refs": [artifact_ref(data["envelope"])], "orm_plan_ref": artifact_ref(orm["envelope"]), "question_sql_ref": artifact_ref(review["envelope"]), "query_spec_ref": artifact_ref(query_spec["envelope"]), "execution_instances": {"params": params, "operations": execution["executed_operation_ids"]}, "sandbox_snapshot_id": snapshot["payload"]["snapshot_id"], "sandbox_execution_report": {"operations": operations, "write_count": execution["write_count"], "rolled_back": False}, "sql_regression_report": {"sql_gold": review["payload"]["candidate_content"]["sql_gold"], "row_count": len(rows), **metrics}, "executable_package_hash": sha256({"orm": orm["payload"]["validated_hash"], "data": data["payload"]["validated_hash"], "review": review["payload"]["package_hash"], "query_spec": query_spec["envelope"]["content_hash"], "params": params}), "regression_status": "passed", "regressed_at": data["payload"]["validated_at"]}
        return self._wrap("database_copy_regression", payload, data["envelope"], parents, attempt_no, "validated")

    @staticmethod
    def _wrap(artifact_type: str, payload: dict[str, Any], source: dict[str, Any], parents: list[dict[str, Any]], attempt_no: int, status: str) -> dict[str, Any]:
        envelope = {"artifact_id": f"260-{artifact_type}-{source['artifact_id']}", "artifact_type": artifact_type, "run_id": source["run_id"], "qa_id": source["qa_id"], "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "", "supersedes_ref": None, "attempt_no": attempt_no, "producer_id": "260", "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": status, "mode": "event_data", "created_at": source["created_at"], "trace_id": source["trace_id"], "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}
