"""Fixed-code, transactional formal-release boundary for EAST V5.

The caller supplies already-frozen packages from the artifact registry.  This
module does no model invocation, creates no business data, and never accepts
ad-hoc SQL: event writes are the verified 260 operation report and Foundation
writes are the fixed 260 INSERT compiler batch.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, canonical_bytes, load_json, sha256

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_TRANSPORT_KEYS = {"envelope", "payload"}


class FormalReleaseError(ContractError):
    """A release was rejected before writing or was atomically rolled back."""


def _fail(code: str) -> None:
    raise FormalReleaseError(code)


class FormalReleaseCommitter:
    """010's only database writer, limited to an externally-owned connection."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _registry(self) -> Registry:
        paths = [self.repo_root / "contracts/common/common-envelope.schema.json", self.repo_root / "contracts/v5-runtime-packages.schema.json", *sorted((self.repo_root / "contracts/packages").glob("*.schema.json"))]
        return Registry().with_resources([(load_json(path)["$id"], Resource.from_contents(load_json(path))) for path in paths])

    def _validate(self, package: Any, schema: str, code: str) -> None:
        if not isinstance(package, dict) or set(package) != _TRANSPORT_KEYS:
            _fail(f"{code}:TRANSPORT")
        try:
            validate_envelope(self.repo_root, package["envelope"], package["payload"])
            Draft202012Validator(load_json(self.repo_root / "contracts/packages" / schema), registry=self._registry(), format_checker=FormatChecker()).validate(package)
        except (ContractError, ValidationError) as exc:
            raise FormalReleaseError(code) from exc

    @staticmethod
    def _same_ref(actual: dict[str, Any], expected: dict[str, Any], code: str) -> None:
        if actual != expected:
            _fail(code)

    @staticmethod
    def _same_context(*packages: dict[str, Any]) -> None:
        context = {(p["envelope"]["run_id"], p["envelope"]["qa_id"], p["envelope"]["trace_id"], p["envelope"]["attempt_no"]) for p in packages}
        if len(context) != 1:
            _fail("010_CONTEXT_DRIFT")

    @staticmethod
    def _identifier(value: Any, code: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            _fail(code)
        return value

    @staticmethod
    def _next_version(value: str) -> str:
        match = re.search(r"(\d+)$", value)
        if not match:
            _fail("010_DATABASE_VERSION_NOT_INCREMENTABLE")
        return f"{value[:match.start()]}{int(match.group(1)) + 1}"

    def _validate_event(self, candidate: dict[str, Any], approved: dict[str, Any], regression: dict[str, Any]) -> list[dict[str, Any]]:
        self._validate(approved, "question-sql-dual-review-passed-package.schema.json", "010_APPROVED_QUESTION_SQL_REJECTED")
        self._validate(regression, "regression-passed-data-orm.schema.json", "010_EVENT_REGRESSION_REJECTED")
        self._same_context(candidate, approved, regression)
        body, report = candidate["payload"], regression["payload"]
        self._same_ref(body["approved_question_sql_ref"], artifact_ref(approved["envelope"]), "010_EVENT_APPROVAL_REF_DRIFT")
        self._same_ref(body["event_regression_passed_ref"], artifact_ref(regression["envelope"]), "010_EVENT_REGRESSION_REF_DRIFT")
        if candidate["envelope"]["parent_artifact_refs"] != [artifact_ref(approved["envelope"]), artifact_ref(regression["envelope"])]:
            _fail("010_EVENT_PARENT_LINEAGE_DRIFT")
        hashes = body["package_hashes"]
        if hashes["question_sql"] != approved["envelope"]["content_hash"] or hashes["regression"] != regression["envelope"]["content_hash"]:
            _fail("010_EVENT_PACKAGE_HASH_DRIFT")
        if len(report["data_package_refs"]) != 1 or hashes["data"] != report["data_package_refs"][0]["content_hash"] or hashes["orm"] != report["orm_plan_ref"]["content_hash"] or hashes["query_binding"] != report["query_parameter_binding_hash"]:
            _fail("010_EVENT_PACKAGE_HASH_DRIFT")
        if report["question_sql_ref"] != artifact_ref(approved["envelope"]):
            _fail("010_EVENT_REGRESSION_LINEAGE_DRIFT")
        if report["query_parameter_binding_ref"] not in regression["envelope"]["parent_artifact_refs"]:
            _fail("010_EVENT_REGRESSION_LINEAGE_DRIFT")
        return self._event_operations(report["sandbox_execution_report"]["operations"], body["expected_write_summary"])

    def _event_operations(self, operations: list[dict[str, Any]], expected: dict[str, Any]) -> list[dict[str, Any]]:
        writes = [operation for operation in operations if operation["operation"] in {"insert", "update"}]
        if not writes or set(expected) != {"orm_execution"} or expected["orm_execution"].get("insert_or_update") != len(writes):
            _fail("010_EVENT_WRITE_SUMMARY_DRIFT")
        for operation in writes:
            self._identifier(operation["table_id"], "010_EVENT_TABLE_INVALID")
            values = operation.get("values")
            if not isinstance(values, dict) or not values:
                _fail("010_EVENT_VALUES_INVALID")
            for field in values:
                self._identifier(field, "010_EVENT_FIELD_INVALID")
        return writes

    def start_question_sql(self, penalty_source: dict[str, Any]) -> dict[str, Any]:
        """The only user entry for a penalty task: fixed forwarding to 110."""
        if not isinstance(penalty_source, dict) or set(penalty_source) != _TRANSPORT_KEYS:
            _fail("010_SOURCE_REJECTED:TRANSPORT")
        try:
            validate_envelope(self.repo_root, penalty_source["envelope"], penalty_source["payload"])
            # This is the frozen payload schema used directly by 120; it is
            # intentionally wrapped here rather than changed into a new
            # envelope schema, preserving its existing downstream contract.
            source_validator = __import__("east_v5.agents.east_120.extractor", fromlist=["validate_source_package"]).validate_source_package
            source_validator(self.repo_root, penalty_source["payload"])
        except ContractError as exc:
            raise FormalReleaseError("010_SOURCE_REJECTED") from exc
        env = penalty_source["envelope"]
        if (env["artifact_type"], env["producer_id"], env["mode"], env["status"]) != ("penalty_source_package", "010", "question_sql", "candidate"):
            _fail("010_SOURCE_ROUTE_REJECTED")
        return {"target": "110", "kind": "question_sql_stage", "source_package_ref": artifact_ref(env)}

    def build_penalty_source_package(self, source_payload: dict[str, Any], *, run_id: str, trace_id: str, created_at: str, attempt_no: int = 1) -> dict[str, Any]:
        """Wrap a fixed-source-builder payload without deriving or repairing facts."""
        source_validator = __import__("east_v5.agents.east_120.extractor", fromlist=["validate_source_package"]).validate_source_package
        try:
            source_validator(self.repo_root, source_payload)
        except ContractError as exc:
            raise FormalReleaseError("010_SOURCE_PAYLOAD_REJECTED") from exc
        envelope = {"artifact_id": f"010-source-{source_payload['source_document_id']}", "artifact_type": "penalty_source_package", "run_id": run_id, "qa_id": f"QA-{source_payload['source_document_id']}", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt_no, "producer_id": "010", "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "question_sql", "created_at": created_at, "trace_id": trace_id, "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, source_payload)
        package = {"envelope": envelope, "payload": source_payload}
        self.start_question_sql(package)
        return package

    @staticmethod
    def start_foundation(request: dict[str, Any]) -> dict[str, Any]:
        """Fixed hand-off; 210 alone constructs and validates the task package."""
        allowed = {"foundation_task_request", "current_database_version", "run_id", "trace_id", "created_at", "parent_artifact_refs"}
        if not isinstance(request, dict) or set(request) != allowed or not isinstance(request["foundation_task_request"], dict):
            _fail("010_FOUNDATION_REQUEST_REJECTED")
        if not all(isinstance(request[key], str) and request[key] for key in ("current_database_version", "run_id", "trace_id", "created_at")) or not isinstance(request["parent_artifact_refs"], list):
            _fail("010_FOUNDATION_REQUEST_REJECTED")
        return {"target": "210", "kind": "foundation", **request}

    def route_sql_regression_failure(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Cross-stage SQL execution failure has exactly one legal destination: 110."""
        self._validate(feedback, "sql-regression-failed-feedback.schema.json", "010_SQL_FEEDBACK_REJECTED")
        body = feedback["payload"]
        if body["failure_details"]["error_code"] not in {"SQL_EXECUTION_ERROR", "QUERY_PARAMETER_BINDING_ERROR"} or body["route_target"] != "010":
            _fail("010_SQL_FEEDBACK_ROUTE_REJECTED")
        kind = "query_parameter_binding_repair" if body["failure_details"]["error_code"] == "QUERY_PARAMETER_BINDING_ERROR" else "sql_gold_repair"
        return {"target": "110", "kind": kind, "feedback_ref": artifact_ref(feedback["envelope"]), "attempt_no": body["retry_count"]}

    @staticmethod
    def status_report(stage_statuses: list[dict[str, Any]]) -> dict[str, Any]:
        """Expose state and manual-review items without changing any package."""
        if not isinstance(stage_statuses, list) or any(not isinstance(item, dict) or set(item) != {"agent", "status", "attempt_no", "blocked_reason"} for item in stage_statuses):
            _fail("010_STATUS_REPORT_REJECTED")
        return {"stages": sorted(stage_statuses, key=lambda item: item["agent"]), "manual_review": [item for item in stage_statuses if item["status"] == "blocked_manual"]}

    def _validate_foundation(self, candidate: dict[str, Any], task: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
        self._validate(task, "foundation-task-package.schema.json", "010_FOUNDATION_TASK_REJECTED")
        self._validate(report, "foundation-regression-report.schema.json", "010_FOUNDATION_REGRESSION_REJECTED")
        self._same_context(candidate, task, report)
        body, payload = candidate["payload"], report["payload"]
        self._same_ref(body["foundation_regression_report_ref"], artifact_ref(report["envelope"]), "010_FOUNDATION_REGRESSION_REF_DRIFT")
        self._same_ref(payload["foundation_task_ref"], artifact_ref(task["envelope"]), "010_FOUNDATION_TASK_REF_DRIFT")
        if candidate["envelope"]["parent_artifact_refs"] != [artifact_ref(task["envelope"]), artifact_ref(report["envelope"])]:
            _fail("010_FOUNDATION_PARENT_LINEAGE_DRIFT")
        hashes = body["package_hashes"]
        if (hashes["foundation_task"] != task["envelope"]["content_hash"] or hashes["regression_report"] != report["envelope"]["content_hash"] or hashes["data"] != payload["validated_data_package_refs"][0]["content_hash"] or hashes["write_batch"] != payload["foundation_write_batch_hash"]):
            _fail("010_FOUNDATION_PACKAGE_HASH_DRIFT")
        if body["target_database_version"] != payload["target_database_version"]:
            _fail("010_FOUNDATION_DATABASE_VERSION_DRIFT")
        batch = payload["foundation_write_batch"]
        if sha256({key: batch[key] for key in ("transaction_groups", "sql_statements", "parameter_sets", "execution_order", "expected_write_counts")}) != payload["foundation_write_batch_hash"]:
            _fail("010_FOUNDATION_WRITE_BATCH_HASH_DRIFT")
        return self._foundation_operations(batch, body["expected_write_summary"])

    def _foundation_operations(self, batch: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
        params = {item["statement_id"]: item for item in batch["parameter_sets"]}
        counts = {item["statement_id"]: item for item in batch["expected_write_counts"]}
        operations: list[dict[str, Any]] = []
        observed: dict[str, int] = defaultdict(int)
        for statement in batch["sql_statements"]:
            sid, table, sql = statement["statement_id"], statement["table_id"], statement["sql"]
            self._identifier(table, "010_FOUNDATION_TABLE_INVALID")
            if not isinstance(sql, str) or not sql.startswith(f'INSERT INTO "{table}"') or sid not in params or sid not in counts or counts[sid]["table_id"] != table or counts[sid]["expected_rowcount"] != 1:
                _fail("010_FOUNDATION_BATCH_INVALID")
            values = [item["value"] for item in params[sid]["values"]]
            if sql.count("?") != len(values):
                _fail("010_FOUNDATION_PARAMETER_DRIFT")
            operations.append({"operation": "insert", "table_id": table, "sql": sql, "parameters": values})
            observed[table] += 1
        target = {table: item["insert"] for table, item in expected.items()}
        if dict(observed) != target or any(item.get("update") != 0 for item in expected.values()):
            _fail("010_FOUNDATION_WRITE_SUMMARY_DRIFT")
        return operations

    @staticmethod
    def _require_formal_tables(connection: sqlite3.Connection) -> None:
        required = {"formal_release_state", "formal_release_ledger", "question_dataset"}
        actual = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not required <= actual:
            _fail("010_FORMAL_STORE_SCHEMA_MISSING")

    def _existing_receipt(self, connection: sqlite3.Connection, candidate: dict[str, Any]) -> dict[str, Any] | None:
        row = connection.execute("SELECT candidate_hash, receipt_json FROM formal_release_ledger WHERE idempotency_key = ?", (candidate["payload"]["idempotency_key"],)).fetchone()
        if row is None:
            return None
        if row[0] != candidate["envelope"]["content_hash"]:
            _fail("010_IDEMPOTENCY_KEY_CONFLICT")
        receipt = json.loads(row[1])
        self._validate(receipt, "formal-release-receipt.schema.json", "010_STORED_RECEIPT_INVALID")
        return receipt

    def _write_operations(self, connection: sqlite3.Connection, operations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        keys: dict[str, list[str]] = defaultdict(list)
        for op in operations:
            table = op["table_id"]
            if "sql" in op:
                cursor = connection.execute(op["sql"], op["parameters"])
            elif op["operation"] == "insert":
                fields = list(op["values"])
                quoted = ", ".join(f'"{field}"' for field in fields)
                cursor = connection.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({", ".join("?" for _ in fields)})', [op["values"][field] for field in fields])
            else:
                fields = list(op["values"])
                if "id" not in op["values"]:
                    _fail("010_EVENT_UPDATE_ID_REQUIRED")
                changes = [field for field in fields if field != "id"]
                if not changes:
                    _fail("010_EVENT_UPDATE_EMPTY")
                assignments = ", ".join('"' + field + '" = ?' for field in changes)
                cursor = connection.execute(f'UPDATE "{table}" SET {assignments} WHERE "id" = ?', [op["values"][field] for field in changes] + [op["values"]["id"]])
            if cursor.rowcount != 1:
                _fail("010_FIXED_WRITE_ROWCOUNT_MISMATCH")
            item = summary.setdefault(table, {"insert": 0, "update": 0})
            item[op["operation"]] += 1
            keys[table].append(sha256(op.get("values", op.get("parameters", []))))
        return {table: {**item, "primary_key_digest": sha256(sorted(keys[table]))} for table, item in summary.items()}

    def commit(self, candidate: dict[str, Any], connection: sqlite3.Connection, *, approved_question_sql: dict[str, Any] | None = None, event_regression: dict[str, Any] | None = None, foundation_task: dict[str, Any] | None = None, foundation_regression: dict[str, Any] | None = None, committed_at: str = "2026-08-18T00:00:00+00:00") -> dict[str, Any]:
        """Atomically promote one fully authenticated 210 candidate, or raise."""
        self._validate(candidate, "release-candidate-package.schema.json", "010_CANDIDATE_REJECTED")
        body, mode = candidate["payload"], candidate["envelope"]["mode"]
        if (body["release_mode"], mode) not in {("event_data", "event_data"), ("foundation", "foundation")}:
            _fail("010_CANDIDATE_MODE_DRIFT")
        self._require_formal_tables(connection)
        existing = self._existing_receipt(connection, candidate)
        if existing is not None:
            return existing
        if mode == "event_data":
            if approved_question_sql is None or event_regression is None or foundation_task is not None or foundation_regression is not None:
                _fail("010_EVENT_MATERIAL_MISSING")
            operations = self._validate_event(candidate, approved_question_sql, event_regression)
        else:
            if foundation_task is None or foundation_regression is None or approved_question_sql is not None or event_regression is not None:
                _fail("010_FOUNDATION_MATERIAL_MISSING")
            operations = self._validate_foundation(candidate, foundation_task, foundation_regression)
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT database_version, question_dataset_version FROM formal_release_state WHERE state_id = 1").fetchone()
            if state is None or state[0] != body["target_database_version"] or (mode == "event_data" and state[1] != body["target_question_dataset_version"]):
                _fail("010_TARGET_VERSION_CONFLICT")
            writes = self._write_operations(connection, operations)
            database_after = self._next_version(state[0])
            question_id = None
            question_after = state[1]
            if mode == "event_data":
                question_id = f"question-sql:{body['release_candidate_id']}"
                connection.execute("INSERT INTO question_dataset (question_sql_record_id, qa_id, question_sql_hash, release_candidate_id) VALUES (?, ?, ?, ?)", (question_id, candidate["envelope"]["qa_id"], approved_question_sql["envelope"]["content_hash"], body["release_candidate_id"]))
                question_after = self._next_version(state[1])
            connection.execute("UPDATE formal_release_state SET database_version = ?, question_dataset_version = ? WHERE state_id = 1", (database_after, question_after))
            payload = {"release_id": f"release:{body['release_candidate_id']}", "release_candidate_ref": artifact_ref(candidate["envelope"]), "commit_status": "committed", "database_version_before": state[0], "database_version_after": database_after, "written_rows_by_table": writes, "question_sql_record_id": question_id, "idempotency_key": body["idempotency_key"], "committed_package_hash": candidate["envelope"]["content_hash"], "manifest_location": f"release-manifests/{body['release_candidate_id']}.json", "trace_location": f"release-traces/{candidate['envelope']['trace_id']}.json", "committed_at": committed_at, "failure_detail": None}
            envelope = {"artifact_id": f"010-receipt-{body['release_candidate_id']}", "artifact_type": "release_receipt", "run_id": candidate["envelope"]["run_id"], "qa_id": candidate["envelope"]["qa_id"], "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": candidate["envelope"]["attempt_no"], "producer_id": "010", "parent_artifact_refs": [artifact_ref(candidate["envelope"])], "input_hashes": [candidate["envelope"]["content_hash"]], "status": "approved", "mode": mode, "created_at": committed_at, "trace_id": candidate["envelope"]["trace_id"], "storage_locator": None}
            envelope["content_hash"] = content_hash(envelope, payload)
            receipt = {"envelope": envelope, "payload": payload}
            self._validate(receipt, "formal-release-receipt.schema.json", "010_RECEIPT_REJECTED")
            connection.execute("INSERT INTO formal_release_ledger (idempotency_key, candidate_hash, receipt_json) VALUES (?, ?, ?)", (body["idempotency_key"], candidate["envelope"]["content_hash"], canonical_bytes(receipt).decode("utf-8")))
            connection.commit()
            return receipt
        except FormalReleaseError:
            connection.rollback()
            raise
        except (sqlite3.Error, ValueError, TypeError) as exc:
            connection.rollback()
            raise FormalReleaseError("010_FORMAL_RELEASE_ROLLED_BACK") from exc
