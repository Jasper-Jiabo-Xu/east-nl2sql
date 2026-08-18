"""Independent, schema-only sanitized 210 consumer used by 260 contract tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import validate_envelope
from east_v5.governance import ContractError, load_json, sha256


def _registry(root: Path) -> Registry:
    paths = [root / "contracts/common/common-envelope.schema.json", root / "contracts/v5-runtime-packages.schema.json", *sorted((root / "contracts/packages").glob("*.schema.json"))]
    return Registry().with_resources([(load_json(path)["$id"], Resource.from_contents(load_json(path))) for path in paths])


def _sqlite_literal(typed: dict[str, Any]) -> str:
    """Validate the typed parameter before rendering the non-executable audit SQL."""
    value, standard_type, is_null = typed["value"], typed["standard_type"], typed["is_null"]
    if is_null:
        if standard_type != "NULL" or value is not None:
            raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
        return "NULL"
    if standard_type == "STRING" and isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if standard_type == "INTEGER" and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if standard_type == "DECIMAL" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if standard_type == "BOOLEAN" and isinstance(value, bool):
        return "1" if value else "0"
    raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")


def _render_audit_sql(sql: str, values: list[dict[str, Any]]) -> str:
    pieces = sql.split("?")
    if len(pieces) != len(values) + 1:
        raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
    return "".join(piece + (_sqlite_literal(values[index]) if index < len(values) else "") for index, piece in enumerate(pieces))


def consume(package: dict[str, Any], root: Path) -> dict[str, str]:
    mode, artifact_type = package.get("envelope", {}).get("mode"), package.get("envelope", {}).get("artifact_type")
    if artifact_type == "database_copy_regression" and mode == "event_data":
        schema = "regression-passed-data-orm.schema.json"
    elif artifact_type == "database_copy_regression" and mode == "foundation":
        schema = "foundation-regression-report.schema.json"
    elif artifact_type == "sql_regression_failed_feedback":
        schema = "sql-regression-failed-feedback.schema.json"
    else:
        raise ContractError("210_STUB_SCHEMA_REJECTED")
    try:
        validate_envelope(root, package["envelope"], package["payload"])
        Draft202012Validator(load_json(root / "contracts/packages" / schema), registry=_registry(root), format_checker=FormatChecker()).validate(package)
    except (ValidationError, ContractError) as exc:
        raise ContractError("210_STUB_SCHEMA_REJECTED") from exc
    if artifact_type == "database_copy_regression":
        refs = package["envelope"]["parent_artifact_refs"]
        if mode == "event_data" and (len(refs) != 6 or package["payload"]["query_spec_ref"] not in refs or package["payload"].get("event_query_context_ref") not in refs):
            raise ContractError("210_STUB_LINEAGE_REJECTED")
        if mode == "foundation" and (len(refs) != 4 or package["payload"]["foundation_task_ref"] not in refs or package["payload"]["structure_closure_ref"] not in refs):
            raise ContractError("210_STUB_LINEAGE_REJECTED")
        if mode == "foundation":
            payload = package["payload"]
            write_batch = payload["foundation_write_batch"]
            hashed = {key: write_batch[key] for key in ("transaction_groups", "sql_statements", "parameter_sets", "execution_order", "expected_write_counts")}
            if payload["foundation_write_batch_hash"] != sha256(hashed) or payload["report_hash"] != sha256({key: value for key, value in payload.items() if key != "report_hash"}):
                raise ContractError("210_STUB_HASH_REJECTED")
            for table, target in payload["target_count_validation"].items():
                delta = payload["database_state_delta"].get(table)
                if not delta or target["actual"] != delta["after"] or target["target"] != target["actual"] or not target["passed"]:
                    raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            if any(summary["key_range"]["minimum"] is None or summary["key_range"]["maximum"] is None for summary in payload["table_write_summary"].values()):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            if any(relation["relation_type"] == "temporary_or_existing" for relation in payload["referential_integrity_validation"]["relations"]):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            sql = write_batch["sql_statements"]
            params = write_batch["parameter_sets"]
            order = write_batch["execution_order"]
            expected = write_batch["expected_write_counts"]
            executed = payload["sandbox_execution_report"]["statements"]
            statement_ids = [item["statement_id"] for item in sql]
            # Every declared surface is one ordered, unique description of the
            # same frozen compiler batch; a valid hash alone is not evidence.
            if not statement_ids or len(set(statement_ids)) != len(statement_ids):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            if any([item["statement_id"] for item in surface] != statement_ids for surface in (params, order, expected, executed)):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            sql_by_id = {item["statement_id"]: item for item in sql}
            if any(item["source_record_id"] != sql_by_id[item["statement_id"]]["source_record_id"] for item in params):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            if any(item["table_id"] != sql_by_id[item["statement_id"]]["table_id"] for surface in (expected, executed) for item in surface):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            groups = write_batch["transaction_groups"]
            transaction_ids = [item["transaction_id"] for item in groups]
            executed_transaction_ids = [item["transaction_id"] for item in payload["sandbox_execution_report"]["transactions"]]
            if len(set(transaction_ids)) != len(transaction_ids) or executed_transaction_ids != transaction_ids:
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            if any(item["transaction_id"] not in transaction_ids for item in order):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            if any(group["statement_ids"] != [item["statement_id"] for item in order if item["transaction_id"] == group["transaction_id"]] for group in groups):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            actual_by_table: dict[str, int] = {}
            for item in executed:
                actual_by_table[item["table_id"]] = actual_by_table.get(item["table_id"], 0) + item["affected_rows"]
            delta_by_table = {table: item["delta"] for table, item in payload["database_state_delta"].items()}
            summary_by_table = {table: item["actual_count"] for table, item in payload["table_write_summary"].items()}
            if actual_by_table != delta_by_table or actual_by_table != summary_by_table:
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            if len(write_batch["rendered_sql_for_audit"]) != len(sql) or any(
                audit != _render_audit_sql(statement["sql"], parameter["values"])
                for audit, statement, parameter in zip(write_batch["rendered_sql_for_audit"], sql, params)
            ):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            distribution = payload["distribution_validation"]
            expected_tables = set(distribution["expected"])
            target_tables = set(payload["target_count_validation"])
            if not expected_tables or expected_tables != target_tables or expected_tables != set(distribution["baseline"]) or expected_tables != set(distribution["delta"]) or expected_tables != set(distribution["after"]) or expected_tables != set(distribution["allowed_tolerance"]):
                raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
            for table, expected in distribution["expected"].items():
                baseline, delta, after, tolerance = (distribution[key][table] for key in ("baseline", "delta", "after", "allowed_tolerance"))
                labels = set(expected)
                if not labels or labels != set(baseline) or labels != set(delta) or labels != set(after) or labels != set(tolerance):
                    raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
                # Foundation-task-package has no tolerance source.  Only a
                # future task-contract revision may relax this hard zero.
                if any(tolerance[label] != 0 for label in labels):
                    raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
                if any(after[label] != baseline[label] + delta[label] or abs(after[label] - expected[label]) > tolerance[label] for label in labels):
                    raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
                state = payload["database_state_delta"].get(table)
                target = payload["target_count_validation"][table]
                if not state or sum(expected.values()) != target["target"] or sum(after.values()) != target["actual"] or target["actual"] != state["after"]:
                    raise ContractError("210_STUB_EXECUTION_FACT_REJECTED")
        return {"decision": "accepted", "kind": "success"}
    if package["payload"]["route_target"] not in {"210", "manual", "241", "251", "010"}:
        raise ContractError("210_STUB_ROUTE_REJECTED")
    return {"decision": "accepted", "kind": "feedback"}
