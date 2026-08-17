"""Independent, schema-only sanitized 210 consumer used by 260 contract tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.governance import ContractError, load_json


def _registry(root: Path) -> Registry:
    paths = [root / "contracts/common/common-envelope.schema.json", root / "contracts/v5-runtime-packages.schema.json", *sorted((root / "contracts/packages").glob("*.schema.json"))]
    return Registry().with_resources([(load_json(path)["$id"], Resource.from_contents(load_json(path))) for path in paths])


def consume(package: dict[str, Any], root: Path) -> dict[str, str]:
    mode, artifact_type = package.get("envelope", {}).get("mode"), package.get("envelope", {}).get("artifact_type")
    schema = "regression-passed-data-orm.schema.json" if mode == "event_data" and artifact_type == "database_copy_regression" else "sql-regression-failed-feedback.schema.json"
    try:
        Draft202012Validator(load_json(root / "contracts/packages" / schema), registry=_registry(root)).validate(package)
    except ValidationError as exc:
        raise ContractError("210_STUB_SCHEMA_REJECTED") from exc
    if artifact_type == "database_copy_regression":
        refs = package["envelope"]["parent_artifact_refs"]
        if len(refs) != 5 or package["payload"]["query_spec_ref"] not in refs:
            raise ContractError("210_STUB_LINEAGE_REJECTED")
        return {"decision": "accepted", "kind": "success"}
    if package["payload"]["route_target"] not in {"210", "manual", "241", "251"}:
        raise ContractError("210_STUB_ROUTE_REJECTED")
    return {"decision": "accepted", "kind": "feedback"}
