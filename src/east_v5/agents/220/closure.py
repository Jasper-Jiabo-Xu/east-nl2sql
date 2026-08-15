"""220: deterministic, read-only structure closure construction."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from east_v5.governance import ContractError


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fail(code: str) -> None:
    raise ContractError(code)


def build_closure(profile: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    """Expand a 210 Foundation profile or event seed using 000 result records.

    This function never executes SQL or writes a database; `assets` are already
    returned by 000 and only their frozen structural fields are consumed.
    """
    if profile.get("schema_version") != "v5.foundation-profile/v1":
        _fail("SCHEMA_VERSION_UNSUPPORTED:FOUNDATION_PROFILE")
    if profile.get("constraint_asset_version") != "CA-V0.3.0" or profile.get("graph_version") != "TRG-V1.0.0":
        _fail("ASSET_VERSION_DRIFT")
    tables, fields, refs = set(), set(), []
    for target in profile.get("target_classes", []):
        if not isinstance(target, str) or not target:
            _fail("FOUNDATION_PROFILE_INVALID")
        if target.startswith("EVENT_OWNED:"):
            _fail("FOUNDATION_EVENT_OWNED_REJECTED")
        tables.add(target)
    for package in assets:
        if package.get("asset_version") not in {"CA-V0.3.0", "TRG-V1.0.0"}:
            _fail("ASSET_VERSION_DRIFT")
        for record in package.get("matched_records", []):
            data = record.get("data", {})
            if data.get("record_class") == "EVENT_OWNED":
                _fail("FOUNDATION_EVENT_OWNED_REJECTED")
            table = data.get("table_id") or data.get("table_code")
            field = data.get("field_id")
            if table: tables.add(table)
            if table and field: fields.add(f"{table}.{field}")
            if record.get("record_type") in {"cross_table", "hierarchy_reference"}:
                refs.append({"type": record["record_type"], "data": data})
    result = {"schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "tables": sorted(tables), "fields": sorted(fields), "references": sorted(refs, key=lambda x: _hash(x))}
    validate_closure(result)
    return result


def validate_closure(value: dict[str, Any]) -> None:
    required = {"schema_version", "constraint_asset_version", "graph_version", "tables", "fields", "references"}
    if set(value) != required:
        _fail("UNKNOWN_FIELD:STRUCTURE_CLOSURE")
    if value["schema_version"] != "v5.structure-closure/v1" or value["constraint_asset_version"] != "CA-V0.3.0" or value["graph_version"] != "TRG-V1.0.0":
        _fail("SCHEMA_VERSION_UNSUPPORTED:STRUCTURE_CLOSURE")
    if not all(isinstance(x, list) for x in (value["tables"], value["fields"], value["references"])):
        _fail("SCHEMA_VALIDATION_FAILED:STRUCTURE_CLOSURE")
