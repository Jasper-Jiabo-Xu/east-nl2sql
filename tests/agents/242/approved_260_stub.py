"""Independent, sanitized 260 consumer used only for the 242 contract test.

It re-validates the ``verified_bound_data`` schema and envelope, recomputes the
frozen data hash from ``validated_data_package``, re-verifies the manifest-bound
constraint-universe proof, and independently re-verifies every query-receipt
chain (EAS-58 ``v5.constraint-asset-query-result/v2``: source binding, per-page
hash integrity, cursor chain closure, no duplicate record) so a self-certified,
truncated or zero-check ``validated`` can never be consumed.
"""
from __future__ import annotations

import importlib
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, validate_envelope
from east_v5.governance import ContractError, load_json, sha256

_resolver_mod = importlib.import_module("east_v5.agents.242.resolver")
load_approved_sources = _resolver_mod.load_approved_sources
universe_content_hash = _resolver_mod.universe_content_hash
EXPECTED_SOURCE_BY_METHOD = _resolver_mod.EXPECTED_SOURCE_BY_METHOD
SOURCE_VERSIONS = _resolver_mod.SOURCE_VERSIONS

METHOD_TO_VERSION = {service_method: version for (version, _artifact_type, service_method) in EXPECTED_SOURCE_BY_METHOD.values()}


def _validator(repo_root) -> Draft202012Validator:
    resources = []
    for relative in ("contracts/common/common-envelope.schema.json", "contracts/v5-runtime-packages.schema.json"):
        schema = load_json(repo_root / relative)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    for path in (repo_root / "contracts" / "packages").glob("*.schema.json"):
        schema = load_json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    schema = load_json(repo_root / "contracts" / "packages" / "verified-bound-data-package.schema.json")
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


def _verify_receipts(receipts: Any, approved: dict[str, dict[str, str]]) -> None:
    if not isinstance(receipts, list) or not receipts:
        raise ContractError("260_STUB_RECEIPTS_REJECTED")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ContractError("260_STUB_RECEIPTS_REJECTED")
        groups.setdefault((receipt["query_method"], receipt["table_code"]), []).append(receipt)

    methods = {method for (method, _table) in groups}
    if methods != set(METHOD_TO_VERSION):
        raise ContractError("260_STUB_SOURCE_MISSING")

    for (method, table_code), pages in groups.items():
        version = METHOD_TO_VERSION[method]
        if version not in SOURCE_VERSIONS:
            raise ContractError("260_STUB_SOURCE_VERSION_REJECTED")
        expected_cursor: str | None = None
        total: int | None = None
        seen: set[str] = set()
        count = 0
        for page in pages:
            if page.get("query_method") != method or page.get("table_code") != table_code:
                raise ContractError("260_STUB_RECEIPT_METHOD_REJECTED")
            if page["asset_version"] != version:
                raise ContractError("260_STUB_SOURCE_VERSION_REJECTED")
            if page["artifact_id"] != approved[version]["artifact_id"] or page["content_hash"] != approved[version]["content_hash"]:
                raise ContractError("260_STUB_SOURCE_DRIFT_REJECTED")
            source = {key: page[key] for key in ("artifact_type", "artifact_id", "asset_version", "content_hash")}
            if page["source_hash"] != sha256(source):
                raise ContractError("260_STUB_RECEIPT_DRIFT")
            if page["records_hash"] != sha256(page["records"]) or page["returned_count"] != len(page["records"]):
                raise ContractError("260_STUB_RECEIPT_DRIFT")
            if page["complete"] != (page["next_cursor"] is None):
                raise ContractError("260_STUB_RECEIPT_DRIFT")
            if page["receipt_hash"] != sha256({key: value for key, value in page.items() if key != "receipt_hash"}):
                raise ContractError("260_STUB_RECEIPT_DRIFT")
            if page["cursor"] != expected_cursor:
                raise ContractError("260_STUB_RECEIPT_CHAIN_GAP")
            if total is None:
                total = page["total"]
            elif page["total"] != total:
                raise ContractError("260_STUB_RECEIPT_INCOMPLETE")
            for record in page["records"]:
                fingerprint = sha256(record)
                if fingerprint in seen:
                    raise ContractError("260_STUB_RECEIPT_DUPLICATE")
                seen.add(fingerprint)
                count += 1
            expected_cursor = page["next_cursor"]
        if total is None or count != total:
            raise ContractError("260_STUB_RECEIPT_INCOMPLETE")
        if pages[-1]["complete"] is not True or pages[-1]["next_cursor"] is not None:
            raise ContractError("260_STUB_RECEIPT_INCOMPLETE")


def consume(package: dict[str, Any], repo_root, approved: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    approved = approved if approved is not None else load_approved_sources(repo_root)
    try:
        _validator(repo_root).validate(package)
        validate_envelope(repo_root, package["envelope"], package["payload"])
    except ValidationError as exc:
        raise ContractError("260_STUB_SCHEMA_REJECTED") from exc
    payload = package["payload"]
    if payload["schema_version"] != "v5.verified-bound-data/v1":
        raise ContractError("260_STUB_SCHEMA_VERSION_REJECTED")
    data = payload["validated_data_package"]
    if payload["validated_hash"] != sha256(data):
        raise ContractError("260_STUB_HASH_REJECTED")
    parent = package["envelope"]["parent_artifact_refs"][0]
    if payload["source_data_package_ref"] != artifact_ref(parent):
        raise ContractError("260_STUB_LINEAGE_REJECTED")
    if not isinstance(data["data_groups"], list) or not data["data_groups"]:
        raise ContractError("260_STUB_EMPTY_DATA_REJECTED")

    report = payload["data_validation_report"]
    universe = report["constraint_universe"]
    if universe["content_sha256"] != universe_content_hash(universe):
        raise ContractError("260_STUB_UNIVERSE_REJECTED")
    if universe["closure_ref"] != data["structure_closure_ref"]:
        raise ContractError("260_STUB_CLOSURE_LINEAGE_REJECTED")
    if universe["sources"] != approved:
        raise ContractError("260_STUB_SOURCE_DRIFT_REJECTED")
    if report["total_checks"] != len(universe["constraints"]):
        raise ContractError("260_STUB_CHECK_COUNT_REJECTED")
    if report["total_checks"] != sum(item["rule_count"] for item in report["module_results"]):
        raise ContractError("260_STUB_MODULE_COUNT_REJECTED")
    _verify_receipts(report["query_receipts"], approved)

    record_count = sum(len(group["records"]) for group in data["data_groups"])
    return {
        "decision": "pass",
        "validated_hash": payload["validated_hash"],
        "record_count": record_count,
        "total_checks": report["total_checks"],
        "receipt_count": len(report["query_receipts"]),
    }
