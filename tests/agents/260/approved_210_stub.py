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
        return {"decision": "accepted", "kind": "success"}
    if package["payload"]["route_target"] not in {"210", "manual", "241", "251"}:
        raise ContractError("210_STUB_ROUTE_REJECTED")
    return {"decision": "accepted", "kind": "feedback"}
