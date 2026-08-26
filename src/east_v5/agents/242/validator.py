"""Deterministic, read-only data validation and hash freezing for Agent 242.

Agent 242 is the only component that *validates* bound data.  It never
generates or modifies data, never emits INSERT/ORM, and never writes the formal
store.  It consumes the 241 ``bound_data`` package plus the referenced
``structure_closure`` and validates the data against the **authoritative
applicable-constraint universe** enumerated from the frozen CA/TRG resolver —
never the candidate's self-reported ``constraint_refs``.

The universe proof is manifest-bound: it must carry the exact structure closure
artifact ref and the approved CA-V0.2.0 / CA-V0.3.0 / TRG-V1.0.0 artifact IDs
and content hashes (see :mod:`east_v5.agents.242.resolver`).  A self-consistent
checksum is not accepted; wrong sources, cross-closure replay, a duplicate
constraint, a forged empty universe, rule-body drift on resolve, or a missing
rule all fail closed.

Identity is deterministic: ``validated_at`` defaults to the source
``created_at`` and the immutable report carries no wall-clock timing, so the
same input always reproduces the same ``artifact_id + version + content_hash``.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json, sha256
from east_v5.agents.foundation_contract import (
    FoundationInvocationVerifier,
    validate_context as validate_foundation_context,
    validate_traces as validate_foundation_traces,
)
from east_v5.validators import (
    REGISTRY_SCHEMA_VERSION,
    Snapshot,
    build_registry,
    dispatch_validator,
    split_endpoint,
    verify_registry,
)
from east_v5.validators.cross_table import validate_cross_table_rule
from east_v5.validators.field import validate_field, validate_field_column
from east_v5.validators.table import validate_table_rule

from .resolver import verify_universe

VERIFIED_BOUND_DATA_SCHEMA_VERSION = "v5.verified-bound-data/v1"
BOUND_DATA_SCHEMA_VERSION = "v5.bound-data/v1"
FEEDBACK_SCHEMA_VERSION = "v5.data-validation-failed-feedback/v1"
REPORT_SCHEMA_VERSION = "v5.data-validation-report/v1"

FIELD_VALIDATOR = "east_v5.validators.field"
TABLE_VALIDATOR = "east_v5.validators.table"
CROSS_TABLE_VALIDATOR = "east_v5.validators.cross_table"

_COLUMN_KINDS = frozenset({"UNIQUE", "PRIMARY_KEY"})

TRANSPORT_KEYS = {"envelope", "payload"}
BOUND_DATA_PAYLOAD_KEYS = {"schema_version", "data_package_id", "structure_closure_ref", "operation_closure_ref", "database_snapshot_ref", "foundation_task_ref", "foundation_generation_context_ref", "selection_traces", "generation_receipt", "data_groups"}
# Event-data packages produced before EAS-114 have neither Foundation-only
# fields nor a task reference; preserving this shape is the promised zero
# semantic change for the event path.
LEGACY_BOUND_DATA_PAYLOAD_KEYS = {"schema_version", "data_package_id", "structure_closure_ref", "operation_closure_ref", "database_snapshot_ref", "data_groups"}
PRE_EAS114_FOUNDATION_PAYLOAD_KEYS = LEGACY_BOUND_DATA_PAYLOAD_KEYS | {"foundation_task_ref"}
STRUCTURE_FIELDS = {"schema_version", "constraint_asset_version", "graph_version", "tables", "fields", "references"}
FOUNDATION_STRUCTURE_FIELDS = STRUCTURE_FIELDS | {"foundation_task_ref"}

BOUND_DATA_SCHEMA = "contracts/packages/bound-data-package.schema.json"
VERIFIED_SCHEMA = "contracts/packages/verified-bound-data-package.schema.json"
FEEDBACK_SCHEMA = "contracts/packages/data-validation-failed-feedback.schema.json"


def _fail(code: str) -> None:
    raise ContractError(code)


class RuleResolver(Protocol):
    """A resolver whose enumerated universe must pass ``verify_universe``.

    The type alone is not trusted: every enumeration is re-validated against the
    resolver's own approved sources and the structure closure before any rule is
    applied.
    """

    receipts: list[dict[str, Any]]
    sources: dict[str, dict[str, str]]

    def enumerate(self, structure_closure: dict[str, Any]) -> dict[str, Any]: ...

    def resolve(self, constraint_id: str, scope: str) -> tuple[dict[str, Any], dict[str, Any]]: ...


class DataValidator:
    """Read-only 241 -> 242 validation and data-hash freezing."""

    def __init__(self, repo_root: Path, *, foundation_invocation_verifier: FoundationInvocationVerifier | None = None):
        self.repo_root = repo_root.resolve()
        self.foundation_invocation_verifier = foundation_invocation_verifier

    # ------------------------------------------------------------------ schema

    def _registry(self) -> Registry:
        resources = []
        for relative in ("contracts/common/common-envelope.schema.json", "contracts/v5-runtime-packages.schema.json"):
            schema = load_json(self.repo_root / relative)
            resources.append((schema["$id"], Resource.from_contents(schema)))
        for path in (self.repo_root / "contracts" / "packages").glob("*.schema.json"):
            schema = load_json(path)
            resources.append((schema["$id"], Resource.from_contents(schema)))
        return Registry().with_resources(resources)

    def _validator(self, relative: str) -> Draft202012Validator:
        schema = load_json(self.repo_root / relative)
        return Draft202012Validator(schema, registry=self._registry())

    def _validate_schema(self, package: dict[str, Any], relative: str, label: str) -> None:
        try:
            self._validator(relative).validate(package)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc

    # ------------------------------------------------------------ input gates

    def validate_structure_closure(self, package: dict[str, Any]) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"]) != ("structure_closure", "220"):
            _fail("STRUCTURE_CLOSURE_ENVELOPE_INVALID")
        expected = FOUNDATION_STRUCTURE_FIELDS if envelope["mode"] == "foundation" else STRUCTURE_FIELDS
        if not isinstance(payload, dict) or set(payload) != expected:
            _fail("UNKNOWN_FIELD:STRUCTURE_CLOSURE")

    def validate_bound_data(self, package: dict[str, Any], structure_closure: dict[str, Any], *, foundation_task_package: dict[str, Any] | None = None, database_snapshot: dict[str, Any] | None = None, foundation_generation_context: dict[str, Any] | None = None) -> None:
        """Hard contract validation; raises ContractError on the first rejection.

        These rejections mean the package is not a well-formed bound data
        candidate at all (bad transport/envelope/schema/lineage/mode), so no
        feedback package can reference it.
        """
        before = copy.deepcopy((package, structure_closure))
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(payload, dict) or set(payload) not in (BOUND_DATA_PAYLOAD_KEYS, LEGACY_BOUND_DATA_PAYLOAD_KEYS, PRE_EAS114_FOUNDATION_PAYLOAD_KEYS):
            _fail("UNKNOWN_FIELD:BOUND_DATA")
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"]) != ("bound_data", "241"):
            _fail("BOUND_DATA_ENVELOPE_INVALID")
        if envelope["status"] == "blocked_manual":
            _fail("UPSTREAM_BLOCKED_MANUAL")
        if payload["schema_version"] != BOUND_DATA_SCHEMA_VERSION:
            _fail("SCHEMA_VERSION_UNSUPPORTED")
        self.validate_structure_closure(structure_closure)
        if payload["structure_closure_ref"] != artifact_ref(structure_closure["envelope"]):
            _fail("STRUCTURE_CLOSURE_REFERENCE_MISMATCH")
        if envelope["mode"] == "foundation" and payload["operation_closure_ref"] is not None:
            _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
        if envelope["mode"] == "foundation":
            if payload.get("foundation_task_ref") is None:
                _fail("FOUNDATION_TASK_REF_REQUIRED")
            if payload.get("foundation_task_ref") != structure_closure["payload"].get("foundation_task_ref"):
                _fail("FOUNDATION_TASK_REF_DRIFT")
            if payload.get("foundation_generation_context_ref") is None or not payload.get("selection_traces") or payload.get("generation_receipt") is None:
                _fail("FOUNDATION_SELECTION_CONTRACT_REQUIRED")
            if foundation_task_package is None or database_snapshot is None or foundation_generation_context is None:
                _fail("FOUNDATION_CONTEXT_INPUTS_REQUIRED")
            validate_foundation_context(foundation_generation_context, foundation_task_package, structure_closure, database_snapshot)
            if payload["foundation_generation_context_ref"] != artifact_ref(foundation_generation_context["envelope"]):
                _fail("FOUNDATION_CONTEXT_REFERENCE_MISMATCH")
            validate_foundation_traces(
                payload["data_groups"], payload["selection_traces"], foundation_generation_context, payload["generation_receipt"],
                task=foundation_task_package, run_id=envelope["run_id"], qa_id=envelope["qa_id"], trace_id=envelope["trace_id"],
                attempt_no=envelope["attempt_no"], invocation_verifier=self.foundation_invocation_verifier,
            )
        elif payload.get("foundation_task_ref") is not None:
            _fail("FOUNDATION_TASK_REF_FORBIDDEN")
        if envelope["mode"] == "event_data" and payload["operation_closure_ref"] is None:
            _fail("OPERATION_CLOSURE_REQUIRED")
        self._validate_schema(package, BOUND_DATA_SCHEMA, "BOUND_DATA")
        if before != (package, structure_closure):
            _fail("INPUT_MUTATED")

    # ------------------------------------------------------------- snapshot

    @staticmethod
    def _snapshot(data_groups: Any) -> tuple[Snapshot, dict[tuple[str, int], tuple[str, str]]]:
        """Build the frozen multi-table view plus a record location index.

        ``locations[(table_code, row_index)] -> (data_group_id, record_id)`` is
        used to map every validator violation back to its originating record.
        """
        tables: dict[str, list[dict[str, Any]]] = {}
        locations: dict[tuple[str, int], tuple[str, str]] = {}
        for group in data_groups:
            for record in group["records"]:
                table_id = record["table_id"]
                row: dict[str, Any] = {}
                for field_value in record["field_values"]:
                    row[field_value["field_id"]] = None if field_value["is_null"] else field_value["value"]
                index = len(tables.setdefault(table_id, []))
                tables[table_id].append(row)
                locations[(table_id, index)] = (group["data_group_id"], record["record_id"])
        return Snapshot(tables), locations

    # ------------------------------------------------------------ validation

    def _dispatch_field(self, rule: dict[str, Any], snapshot: Snapshot) -> list[dict[str, Any]]:
        kind = rule.get("rule_kind")
        endpoint = rule.get("endpoint")
        if endpoint is None:
            _fail("FIELD_RULE_ENDPOINT_REQUIRED")
        table_code, field_code = split_endpoint(endpoint)
        if kind in _COLUMN_KINDS:
            return validate_field_column(rule, snapshot.table(table_code).column(field_code))
        table = snapshot.table(table_code)
        violations: list[dict[str, Any]] = []
        for record_index in range(len(table)):
            for violation in validate_field(rule, table.value(field_code, record_index)):
                violation["location"]["record_index"] = record_index
                violations.append(violation)
        return violations

    def _run_checks(
        self, bound_data: dict[str, Any], structure_closure: dict[str, Any], resolver: RuleResolver,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, dict[str, Any], list[dict[str, Any]]]:
        """Enumerate the manifest-bound universe, run every rule, aggregate all
        violations and per-module stats.  Raises ContractError when the universe
        is unavailable, unprovable, or a rule drifts from its enumeration."""
        registry = verify_registry(build_registry())
        universe = verify_universe(resolver.enumerate(structure_closure), structure_closure, resolver.sources)

        snapshot, locations = self._snapshot(bound_data["payload"]["data_groups"])
        rule_counts = {FIELD_VALIDATOR: 0, TABLE_VALIDATOR: 0, CROSS_TABLE_VALIDATOR: 0}
        located: list[dict[str, Any]] = []

        for ref in universe["constraints"]:
            constraint_id, scope = ref["constraint_id"], ref["scope"]
            rule, content = resolver.resolve(constraint_id, scope)
            if not isinstance(rule, dict) or not rule:
                _fail("UNKNOWN_CONSTRAINT")
            if sha256(content) != ref["canonical_rule_hash"]:
                _fail("RULE_CONTENT_HASH_DRIFT")

            if scope == "field":
                validator_id = dispatch_validator(rule["rule_kind"])
                rule_counts[validator_id] += 1
                for violation in self._dispatch_field(rule, snapshot):
                    located.append(self._locate(violation, validator_id, "field", snapshot, locations))
            elif scope == "within_table":
                kind = rule.get("kind")
                validator_id = dispatch_validator(kind, "INTRA_TABLE")
                rule_counts[validator_id] += 1
                for violation in validate_table_rule(rule, snapshot.table(_table_of(rule))):
                    located.append(self._locate(violation, validator_id, "table", snapshot, locations))
            else:  # cross_table
                kind = rule.get("kind")
                validator_id = dispatch_validator(kind, "CROSS_TABLE")
                rule_counts[validator_id] += 1
                for violation in validate_cross_table_rule(rule, snapshot):
                    located.append(self._locate(violation, validator_id, "cross_table", snapshot, locations))

        violations_by_layer = {FIELD_VALIDATOR: [], TABLE_VALIDATOR: [], CROSS_TABLE_VALIDATOR: []}
        for item in located:
            violations_by_layer[item["validator_id"]].append(item["violation"])

        module_results: list[dict[str, Any]] = []
        for validator_id, layer in ((FIELD_VALIDATOR, "field"), (TABLE_VALIDATOR, "table"), (CROSS_TABLE_VALIDATOR, "cross_table")):
            count = len(violations_by_layer[validator_id])
            module_results.append({
                "validator_id": validator_id,
                "layer": layer,
                "rule_count": rule_counts[validator_id],
                "violation_count": count,
                "result": "fail" if count else "pass",
            })

        return located, module_results, registry["schema_version"], universe, list(resolver.receipts)

    @staticmethod
    def _locate(
        violation: dict[str, Any], validator_id: str, layer: str,
        snapshot: Snapshot, locations: dict[tuple[str, int], tuple[str, str]],
    ) -> dict[str, Any]:
        location = violation["location"]
        table_code = location["table_code"]
        record_index = location["record_index"]
        if table_code is None or record_index is None:
            _fail("VIOLATION_LOCATION_MISSING")
        if (table_code, record_index) not in locations:
            _fail("VIOLATION_LOCATION_UNRESOLVABLE")
        data_group_id, record_id = locations[(table_code, record_index)]
        return {
            "validator_id": validator_id,
            "layer": layer,
            "violation": violation,
            "data_group_id": data_group_id,
            "record_id": record_id,
            "table_id": table_code,
            "field_id": location["field_code"],
        }

    # -------------------------------------------------------------- outputs

    def freeze_bound_data(
        self, bound_data: dict[str, Any], structure_closure: dict[str, Any], resolver: RuleResolver, *,
        validated_at: str | None = None, foundation_task_package: dict[str, Any] | None = None,
        database_snapshot: dict[str, Any] | None = None, foundation_generation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate read-only and return the reproducible verified package.

        ``validated_at`` defaults to the source ``created_at`` so the same input
        always reproduces the same immutable package.
        """
        self.validate_bound_data(bound_data, structure_closure, foundation_task_package=foundation_task_package, database_snapshot=database_snapshot, foundation_generation_context=foundation_generation_context)
        before = copy.deepcopy((bound_data, structure_closure))
        located, module_results, registry_version, universe, receipts = self._run_checks(bound_data, structure_closure, resolver)
        if located:
            _fail("VALIDATION_REJECTED")
        source = bound_data["envelope"]
        payload: dict[str, Any] = {
            "schema_version": VERIFIED_BOUND_DATA_SCHEMA_VERSION,
            "validated_data_package": copy.deepcopy(bound_data["payload"]),
            "source_data_package_ref": artifact_ref(source),
            "data_validation_report": {
                "schema_version": REPORT_SCHEMA_VERSION,
                "constraint_universe": universe,
                "query_receipts": receipts,
                "module_results": module_results,
                "total_checks": sum(item["rule_count"] for item in module_results),
                "passed_checks": sum(item["rule_count"] for item in module_results if item["result"] == "pass"),
                "violation_count": 0,
            },
            "validator_registry_version": registry_version,
            "validated_hash": sha256(bound_data["payload"]),
            "validated_at": validated_at or source["created_at"],
        }
        envelope: dict[str, Any] = {
            "artifact_id": f"{source['artifact_id']}:verified-bound-data", "artifact_type": "verified_bound_data",
            "run_id": source["run_id"], "qa_id": source["qa_id"], "version": 1,
            "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
            "attempt_no": source["attempt_no"], "producer_id": "242",
            "parent_artifact_refs": [artifact_ref(source)], "input_hashes": [source["content_hash"]],
            "status": "validated", "mode": source["mode"], "created_at": source["created_at"],
            "trace_id": source["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        output = {"envelope": envelope, "payload": payload}
        self._validate_schema(output, VERIFIED_SCHEMA, "VERIFIED_BOUND_DATA")
        validate_envelope(self.repo_root, envelope, payload)
        if before != (bound_data, structure_closure):
            _fail("INPUT_MUTATED")
        return output

    def build_validation_feedback(
        self, bound_data: dict[str, Any], structure_closure: dict[str, Any], resolver: RuleResolver, *, foundation_task_package: dict[str, Any] | None = None, database_snapshot: dict[str, Any] | None = None, foundation_generation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Aggregate every validation failure and emit data_validation_failed_feedback."""
        self.validate_bound_data(bound_data, structure_closure, foundation_task_package=foundation_task_package, database_snapshot=database_snapshot, foundation_generation_context=foundation_generation_context)
        located, _, registry_version, _, _ = self._run_checks(bound_data, structure_closure, resolver)
        if not located:
            _fail("VALIDATION_NOT_FAILED")

        by_constraint: dict[tuple[str, str], dict[str, Any]] = {}
        for item in located:
            violation = item["violation"]
            key = (item["validator_id"], violation["constraint_id"])
            entry = by_constraint.setdefault(key, {
                "failed_module_ids": [item["validator_id"]],
                "constraint_ids": [violation["constraint_id"]],
                "record_field_locations": [],
                "expected_values": [f"constraint {violation['constraint_id']} ({violation['rule_kind']}) must hold"],
                "actual_values": [],
                "error_details": violation["message"],
            })
            entry["record_field_locations"].append({
                "data_group_id": item["data_group_id"],
                "record_id": item["record_id"],
                "table_id": item["table_id"],
                "field_id": item["field_id"],
            })
            entry["actual_values"].append(violation["message"])

        failed_items = list(by_constraint.values())
        source = bound_data["envelope"]
        payload: dict[str, Any] = {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "data_package_ref": artifact_ref(source),
            "decision": "fail",
            "validator_registry_version": registry_version,
            "failed_items": failed_items,
        }
        envelope: dict[str, Any] = {
            "artifact_id": f"{source['artifact_id']}:data-validation-feedback", "artifact_type": "data_validation_failed_feedback",
            "run_id": source["run_id"], "qa_id": source["qa_id"], "version": 1,
            "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
            "attempt_no": source["attempt_no"], "producer_id": "242",
            "parent_artifact_refs": [artifact_ref(source)], "input_hashes": [source["content_hash"]],
            "status": "rejected", "mode": source["mode"], "created_at": source["created_at"],
            "trace_id": source["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        output = {"envelope": envelope, "payload": payload}
        self._validate_schema(output, FEEDBACK_SCHEMA, "DATA_VALIDATION_FEEDBACK")
        validate_envelope(self.repo_root, envelope, payload)
        return output


def freeze_foundation_bound_data_from_runtime(bootstrap: Any, assembly: Any, launch_receipt: dict[str, Any], bound_data: dict[str, Any], structure_closure: dict[str, Any], resolver: RuleResolver, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Production 242 entrance: consume the sealed 241 edge before validation."""
    accepted = bootstrap.foundation_242_launcher().verify_downstream(launch_receipt)
    frozen = assembly.validator(Path(__file__).resolve().parents[4]).freeze_bound_data(bound_data, structure_closure, resolver, **kwargs)
    return frozen, accepted


def _table_of(rule: dict[str, Any]) -> str:
    """The single table an INTRA_TABLE rule operates on (from its endpoints)."""
    fields = rule.get("fields") or []
    if not fields:
        _fail("EXPRESSION_INVALID")
    table_codes = {split_endpoint(field)[0] for field in fields}
    if len(table_codes) != 1:
        _fail("EXPRESSION_INVALID")
    return table_codes.pop()
