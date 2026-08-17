"""Independent, schema-only sanitized 210 consumer used by 260 contract tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import validate_envelope
from east_v5.governance import ContractError, load_json, sha256


def _registry(root: Path) -> Registry:
    paths = [root / "contracts/common/common-envelope.schema.json", root / "contracts/v5-runtime-packages.schema.json", *sorted((root / "contracts/packages").glob("*.schema.json"))]
    return Registry().with_resources([(load_json(path)["$id"], Resource.from_contents(load_json(path))) for path in paths])


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
        Draft202012Validator(load_json(root / "contracts/packages" / schema), registry=_registry(root)).validate(package)
    except (ValidationError, ContractError) as exc:
        raise ContractError("210_STUB_SCHEMA_REJECTED") from exc
    if artifact_type == "database_copy_regression":
        refs = package["envelope"]["parent_artifact_refs"]
        if mode == "event_data" and (len(refs) != 5 or package["payload"]["query_spec_ref"] not in refs):
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
        return {"decision": "accepted", "kind": "success"}
    if package["payload"]["route_target"] not in {"210", "manual", "241", "251"}:
        raise ContractError("210_STUB_ROUTE_REJECTED")
    return {"decision": "accepted", "kind": "feedback"}
