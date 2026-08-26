"""Deterministic bound-data generation and contract boundary for Agent 241.

Agent 241 is the only component that generates or modifies bound data.  It reads
a frozen structure closure plus either an operation closure (event mode) or a
foundation profile (foundation mode) and an optional read-only snapshot, then
produces a ``bound_data`` transport package for 242.  242/260 failure feedback
mints a *new* version with ``supersedes_ref`` and an incremented ``attempt_no``;
an earlier version is never overwritten.  The third attempt is terminal and must
be ``blocked_manual``.

Everything here is deterministic hard code.  The LLM proposes candidate values
only through ``proposed_data_groups``; this module validates record boundaries,
types, references, value provenance, target conditions and the package schema
before anything is wrapped.
"""
from __future__ import annotations

import copy
import importlib
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json
from east_v5.agents.foundation_contract import (
    FoundationInvocationVerifier,
    validate_context as validate_foundation_context,
    validate_traces as validate_foundation_traces,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
_closure_contract = importlib.import_module("east_v5.agents.220.closure")

TRANSPORT_KEYS = {"envelope", "payload"}
STRUCTURE_FIELDS = {"schema_version", "constraint_asset_version", "graph_version", "tables", "fields", "references"}
FOUNDATION_STRUCTURE_FIELDS = STRUCTURE_FIELDS | {"foundation_task_ref"}
OPERATION_FIELDS = {"schema_version", "mode", "operations", "consumers"}
CASE_ROLES = {"positive", "hard_negative", "background", "foundation"}
STANDARD_TYPES = {"STRING", "INTEGER", "NUMBER", "DECIMAL", "BOOLEAN", "DATE", "DATETIME", "CODE"}
PROVENANCE_TYPES = {
    "structure_closure_constraint", "distribution", "database_record", "hierarchy_asset", "foundation_task_package", "foundation_profile",
}
FIELD_PATH = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")

BOUND_DATA_SCHEMA = "contracts/packages/bound-data-package.schema.json"
VALIDATION_FEEDBACK_SCHEMA = "contracts/packages/data-validation-failed-feedback.schema.json"
REGRESSION_FEEDBACK_SCHEMA = "contracts/packages/sql-regression-failed-feedback.schema.json"
SNAPSHOT_SCHEMA = "contracts/packages/database-read-snapshot.schema.json"
FOUNDATION_PROFILE_SCHEMA = "contracts/packages/foundation-profile-package.schema.json"
FOUNDATION_TASK_SCHEMA = "contracts/packages/foundation-task-package.schema.json"
FOUNDATION_CONTEXT_SCHEMA = "contracts/packages/foundation-generation-context-package.schema.json"
MANIFEST_SCHEMA = "contracts/packages/bound-data-manifest.schema.json"
RUNTIME_SCHEMA = "contracts/v5-runtime-packages.schema.json"

_RECORD_KEYS = {"record_id", "record_type", "table_id", "field_values", "existing_record_refs", "temporary_record_refs", "value_provenance", "case_role", "target_condition_refs", "constraint_refs"}
_LEGACY_RECORD_KEYS = _RECORD_KEYS - {"record_type"}
_FIELD_VALUE_KEYS = {"field_id", "value", "standard_type", "is_null"}
_EXISTING_REF_KEYS = {"table_id", "record_key"}
_TEMP_REF_KEYS = {"record_id"}
_PROVENANCE_KEYS = {"source_type", "source_ref"}


def _fail(code: str) -> None:
    raise ContractError(code)


def _registry() -> Registry:
    resources = []
    for relative in ("contracts/common/common-envelope.schema.json", RUNTIME_SCHEMA):
        schema = load_json(REPO_ROOT / relative)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    for path in (REPO_ROOT / "contracts" / "packages").glob("*.schema.json"):
        schema = load_json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


class BoundDataGenerator:
    """Implements the three Excel tasks for Agent 241 without inventing data."""

    def __init__(self, repo_root: Path, *, foundation_invocation_verifier: FoundationInvocationVerifier | None = None):
        self.repo_root = repo_root.resolve()
        self.foundation_invocation_verifier = foundation_invocation_verifier

    # ------------------------------------------------------------------ schema

    def _package_validator(self, relative: str) -> Draft202012Validator:
        schema = load_json(self.repo_root / relative)
        return Draft202012Validator(schema, registry=_registry())

    def _validate_package_schema(self, package: dict[str, Any], relative: str, label: str) -> None:
        try:
            self._package_validator(relative).validate(package)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc

    def _validate_runtime_def(self, payload: dict[str, Any], def_key: str, label: str) -> None:
        runtime = load_json(self.repo_root / RUNTIME_SCHEMA)
        try:
            Draft202012Validator(runtime["$defs"][def_key], registry=_registry()).validate(payload)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc

    # ------------------------------------------------------------- transport

    def _validate_transport(self, package: dict[str, Any], expected_type: str, schema_path: str) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != expected_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        if not isinstance(payload, dict):
            _fail("PAYLOAD_NOT_OBJECT")
        self._validate_package_schema(package, schema_path, expected_type.upper())

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
        self._validate_runtime_def(payload, "structure_closure", "STRUCTURE_CLOSURE")

    def validate_operation_closure(self, package: dict[str, Any]) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("operation_closure", "230", "event_data"):
            _fail("OPERATION_CLOSURE_ENVELOPE_INVALID")
        if not isinstance(payload, dict) or set(payload) != OPERATION_FIELDS:
            _fail("UNKNOWN_FIELD:OPERATION_CLOSURE")
        self._validate_runtime_def(payload, "operation_closure", "OPERATION_CLOSURE")

    def validate_foundation_profile(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "foundation_profile", FOUNDATION_PROFILE_SCHEMA)

    def validate_foundation_task_package(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "foundation_task_package", FOUNDATION_TASK_SCHEMA)
        envelope, payload = package["envelope"], package["payload"]
        if envelope["producer_id"] != "210" or envelope["mode"] != "foundation":
            _fail("FOUNDATION_TASK_ENVELOPE_INVALID")
        if envelope["artifact_id"] != payload["foundation_task_id"]:
            _fail("FOUNDATION_TASK_IDENTITY_MISMATCH")

    def validate_foundation_generation_context(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "foundation_generation_context", FOUNDATION_CONTEXT_SCHEMA)
        if package["envelope"]["producer_id"] != "EAS-19" or package["envelope"]["mode"] != "foundation":
            _fail("FOUNDATION_GENERATION_CONTEXT_ENVELOPE_INVALID")

    def validate_snapshot(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "database_read_snapshot", SNAPSHOT_SCHEMA)

    def validate_validation_feedback(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "data_validation_failed_feedback", VALIDATION_FEEDBACK_SCHEMA)

    def validate_regression_feedback(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "sql_regression_failed_feedback", REGRESSION_FEEDBACK_SCHEMA)

    def validate_bound_data(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "bound_data", BOUND_DATA_SCHEMA)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _closure_index(closure: dict[str, Any]) -> tuple[set[str], set[str]]:
        tables = set(closure["tables"])
        fields = set(closure["fields"])
        for field in fields:
            if not FIELD_PATH.fullmatch(field):
                _fail("STRUCTURE_CLOSURE_FIELD_INVALID")
            tables.add(field.split(".", 1)[0])
        return tables, fields

    @staticmethod
    def _snapshot_keys(snapshot: dict[str, Any] | None) -> set[tuple[str, str]]:
        if snapshot is None:
            return set()
        keys = set()
        for record in snapshot["payload"]["object_state_records"]:
            record_keys = record["record_keys"]
            keys.add((record_keys["table_id"], record_keys["primary_key"]))
        return keys

    @staticmethod
    def _check_value_type(value: Any, standard_type: str) -> None:
        if standard_type in ("STRING", "CODE", "DATE", "DATETIME"):
            if not isinstance(value, str):
                _fail("VALUE_TYPE_MISMATCH")
        elif standard_type == "INTEGER":
            if not isinstance(value, int) or isinstance(value, bool):
                _fail("VALUE_TYPE_MISMATCH")
        elif standard_type in ("NUMBER", "DECIMAL"):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                _fail("VALUE_TYPE_MISMATCH")
        elif standard_type == "BOOLEAN":
            if not isinstance(value, bool):
                _fail("VALUE_TYPE_MISMATCH")
        else:
            _fail("STANDARD_TYPE_INVALID")

    def _validate_record(self, record: dict[str, Any], tables: set[str], fields: set[str], snapshot_keys: set[tuple[str, str]], mode: str, record_ids: set[str]) -> None:
        if not isinstance(record, dict) or set(record) not in (_RECORD_KEYS, _LEGACY_RECORD_KEYS):
            _fail("UNKNOWN_FIELD:RECORD")
        record_id = record["record_id"]
        if not isinstance(record_id, str) or not record_id:
            _fail("RECORD_ID_INVALID")
        if record_id in record_ids:
            _fail("RECORD_ID_DUPLICATE")
        record_ids.add(record_id)
        table = record["table_id"]
        if table not in tables:
            _fail("RECORD_TABLE_OUT_OF_CLOSURE")
        role = record["case_role"]
        if role not in CASE_ROLES:
            _fail("CASE_ROLE_INVALID")
        if mode == "foundation" and role != "foundation":
            _fail("EVENT_ROLE_IN_FOUNDATION_MODE")
        if mode != "foundation" and role == "foundation":
            _fail("FOUNDATION_ROLE_IN_EVENT_MODE")
        field_values = record["field_values"]
        if not isinstance(field_values, list) or not field_values:
            _fail("FIELD_VALUES_EMPTY")
        seen_fields: set[str] = set()
        for field_value in field_values:
            if not isinstance(field_value, dict) or set(field_value) != _FIELD_VALUE_KEYS:
                _fail("FIELD_VALUE_INVALID")
            field_id = field_value["field_id"]
            if not isinstance(field_id, str) or not field_id:
                _fail("FIELD_ID_INVALID")
            if f"{table}.{field_id}" not in fields:
                _fail("FIELD_OUT_OF_CLOSURE")
            if field_id in seen_fields:
                _fail("FIELD_VALUE_DUPLICATE")
            seen_fields.add(field_id)
            if field_value["standard_type"] not in STANDARD_TYPES:
                _fail("STANDARD_TYPE_INVALID")
            if field_value["is_null"] is True:
                if field_value["value"] is not None:
                    _fail("NULL_VALUE_MISMATCH")
            else:
                self._check_value_type(field_value["value"], field_value["standard_type"])
        for ref in record["existing_record_refs"]:
            if not isinstance(ref, dict) or set(ref) != _EXISTING_REF_KEYS:
                _fail("EXISTING_RECORD_REF_INVALID")
            key = (ref["table_id"], ref["record_key"])
            if snapshot_keys and key not in snapshot_keys:
                _fail("EXISTING_RECORD_ORPHAN")
        for ref in record["temporary_record_refs"]:
            if not isinstance(ref, dict) or set(ref) != _TEMP_REF_KEYS or not isinstance(ref["record_id"], str) or not ref["record_id"]:
                _fail("TEMPORARY_RECORD_REF_INVALID")
        provenance = record["value_provenance"]
        if not isinstance(provenance, list) or not provenance:
            _fail("VALUE_PROVENANCE_EMPTY")
        for entry in provenance:
            if not isinstance(entry, dict) or set(entry) != _PROVENANCE_KEYS or entry["source_type"] not in PROVENANCE_TYPES or not isinstance(entry["source_ref"], str) or not entry["source_ref"]:
                _fail("VALUE_PROVENANCE_INVALID")
        if not isinstance(record["target_condition_refs"], list) or not all(isinstance(item, str) for item in record["target_condition_refs"]):
            _fail("TARGET_CONDITION_REFS_INVALID")
        if not isinstance(record["constraint_refs"], list):
            _fail("CONSTRAINT_REFS_INVALID")

    def _validate_summary(self, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
        table_counts: dict[str, int] = {}
        positive = hard_negative = background = foundation = 0
        for record in records:
            table_counts[record["table_id"]] = table_counts.get(record["table_id"], 0) + 1
            role = record["case_role"]
            if role == "positive": positive += 1
            elif role == "hard_negative": hard_negative += 1
            elif role == "background": background += 1
            else: foundation += 1
        expected = {
            "table_record_counts": table_counts,
            "positive_count": positive,
            "hard_negative_count": hard_negative,
            "background_count": background,
            "foundation_count": foundation,
            "object_count": len(table_counts),
        }
        if summary != expected:
            _fail("GROUP_SUMMARY_MISMATCH")

    def _validate_data_groups(self, data_groups: Any, closure: dict[str, Any], snapshot: dict[str, Any] | None, mode: str) -> None:
        if not isinstance(data_groups, list) or not data_groups:
            _fail("DATA_GROUPS_EMPTY")
        tables, fields = self._closure_index(closure)
        snapshot_keys = self._snapshot_keys(snapshot)
        seen_groups: set[str] = set()
        for group in data_groups:
            if not isinstance(group, dict):
                _fail("DATA_GROUP_INVALID")
            group_id = group.get("data_group_id")
            if not isinstance(group_id, str) or not group_id:
                _fail("DATA_GROUP_ID_INVALID")
            if group_id in seen_groups:
                _fail("DATA_GROUP_ID_DUPLICATE")
            seen_groups.add(group_id)
            records = group.get("records")
            if not isinstance(records, list) or not records:
                _fail("DATA_GROUP_RECORDS_EMPTY")
            record_ids: set[str] = set()
            for record in records:
                self._validate_record(record, tables, fields, snapshot_keys, mode, record_ids)
            for record in records:
                for ref in record["temporary_record_refs"]:
                    if ref["record_id"] not in record_ids:
                        _fail("TEMPORARY_RECORD_ORPHAN")
                    if ref["record_id"] == record["record_id"]:
                        _fail("TEMPORARY_RECORD_SELF_REFERENCE")
            links = group.get("record_links")
            if not isinstance(links, list):
                _fail("RECORD_LINKS_INVALID")
            for link in links:
                for key in ("source_record_id", "target_record_id"):
                    if link[key] not in record_ids:
                        _fail("RECORD_LINK_ORPHAN")
                if link["source_record_id"] == link["target_record_id"]:
                    _fail("RECORD_LINK_SELF_REFERENCE")
            self._validate_summary(group["group_summary"], records)

    # ------------------------------------------------------------- generation

    @staticmethod
    def _sanitized_value(field_id: str) -> str:
        return f"脱敏值-{field_id}"

    def _deterministic_data_groups(self, closure: dict[str, Any], mode: str) -> list[dict[str, Any]]:
        tables = list(closure["tables"])
        if not tables:
            _fail("STRUCTURE_CLOSURE_EMPTY")
        records: list[dict[str, Any]] = []
        for index, table in enumerate(tables):
            field_ids = sorted({field.split(".", 1)[1] for field in closure["fields"] if field.startswith(table + ".")})
            role = "foundation" if mode == "foundation" else ("positive" if index == 0 else "background")
            records.append({
                "record_id": f"rec-{table}",
                "table_id": table,
                "field_values": [
                    {"field_id": field_id, "value": self._sanitized_value(field_id), "standard_type": "STRING", "is_null": False}
                    for field_id in field_ids
                ],
                "existing_record_refs": [],
                "temporary_record_refs": [],
                "value_provenance": [
                    {"source_type": "foundation_task_package" if mode == "foundation" else "structure_closure_constraint", "source_ref": "CA-V0.3.0"},
                ],
                "case_role": role,
                "target_condition_refs": [],
                "constraint_refs": [],
            })
        record_links: list[dict[str, Any]] = []
        for ref in closure["references"]:
            data = ref.get("data") if isinstance(ref, dict) else None
            if not isinstance(data, dict):
                continue
            source, target = data.get("from"), data.get("to")
            if FIELD_PATH.fullmatch(str(source)) and FIELD_PATH.fullmatch(str(target)):
                source_table, source_field = source.split(".", 1)
                target_table, target_field = target.split(".", 1)
                record_links.append({
                    "source_record_id": f"rec-{source_table}",
                    "target_record_id": f"rec-{target_table}",
                    "relation_type": ref.get("type", "cross_table"),
                    "source_field_id": source_field,
                    "target_field_id": target_field,
                    "constraint_refs": [],
                })
        summary = self._summarize(records)
        return [{"data_group_id": f"group-{mode}", "records": records, "record_links": record_links, "group_summary": summary}]

    @staticmethod
    def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
        table_counts: dict[str, int] = {}
        positive = hard_negative = background = foundation = 0
        for record in records:
            table_counts[record["table_id"]] = table_counts.get(record["table_id"], 0) + 1
            role = record["case_role"]
            if role == "positive": positive += 1
            elif role == "hard_negative": hard_negative += 1
            elif role == "background": background += 1
            else: foundation += 1
        return {
            "table_record_counts": table_counts,
            "positive_count": positive,
            "hard_negative_count": hard_negative,
            "background_count": background,
            "foundation_count": foundation,
            "object_count": len(table_counts),
        }

    def _wrap(
        self, payload: dict[str, Any], *, artifact_id: str, run_id: str, qa_id: str | None, mode: str,
        parents: list[dict[str, Any]], version: int = 1, attempt_no: int = 1,
        supersedes_ref: dict[str, Any] | None = None, status: str = "candidate",
        trace_id: str = "241-trace", created_at: str | None = None,
    ) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "artifact_id": artifact_id, "artifact_type": "bound_data", "run_id": run_id,
            "qa_id": qa_id, "version": version, "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64, "supersedes_ref": supersedes_ref, "attempt_no": attempt_no,
            "producer_id": "241", "parent_artifact_refs": parents,
            "input_hashes": [item["content_hash"] for item in parents], "status": status,
            "mode": mode, "created_at": created_at or self._now(), "trace_id": trace_id,
            "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self.validate_bound_data(package)
        return package

    def build_bound_data(
        self, structure_closure: dict[str, Any], *, operation_closure: dict[str, Any] | None = None,
        foundation_profile: dict[str, Any] | None = None, foundation_task_package: dict[str, Any] | None = None, snapshot: dict[str, Any] | None = None,
        foundation_generation_context: dict[str, Any] | None = None, selection_traces: list[dict[str, Any]] | None = None,
        generation_receipt: dict[str, Any] | None = None,
        proposed_data_groups: list[dict[str, Any]] | None = None,
        version: int = 1, attempt_no: int = 1, status: str = "candidate",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Task 1: generate a schema-valid bound_data package from frozen inputs."""
        self.validate_structure_closure(structure_closure)
        closure = structure_closure["payload"]
        mode = structure_closure["envelope"]["mode"]
        if mode == "event_data":
            if operation_closure is None:
                _fail("OPERATION_CLOSURE_REQUIRED")
            self.validate_operation_closure(operation_closure)
            if foundation_profile is not None or foundation_task_package is not None:
                _fail("FOUNDATION_PROFILE_IN_EVENT_MODE")
        else:
            if operation_closure is not None:
                _fail("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
            self.validate_foundation_task_package(foundation_task_package)
            task_ref = artifact_ref(foundation_task_package["envelope"])
            if closure["foundation_task_ref"] != task_ref:
                _fail("FOUNDATION_TASK_REF_DRIFT")
            # 241 consumes the closed scope but never repairs it: every frozen
            # 210 write field must already be present in the 220 package.
            _closure_contract.validate_foundation_closure_task_scope(foundation_task_package, structure_closure)
            self.validate_foundation_generation_context(foundation_generation_context) if foundation_generation_context is not None else _fail("FOUNDATION_GENERATION_CONTEXT_REQUIRED")
            if foundation_profile is not None:
                self.validate_foundation_profile(foundation_profile)
                expected_profile = {
                    "schema_version": "v5.foundation-profile/v1", "foundation_task_ref": task_ref,
                    "base_database_version": foundation_task_package["payload"]["target_database_version"],
                    "target_classes": foundation_task_package["payload"]["target_object_types"],
                    "target_counts": foundation_task_package["payload"]["target_counts"],
                    "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0",
                }
                if foundation_profile["payload"] != expected_profile:
                    _fail("FOUNDATION_PROFILE_PROJECTION_DRIFT")
        if snapshot is not None:
            self.validate_snapshot(snapshot)

        source = structure_closure["envelope"]
        run_id = source["run_id"]
        qa_id = source["qa_id"]
        trace_id = source["trace_id"]

        if mode == "foundation" and proposed_data_groups is None:
            _fail("FOUNDATION_241_PROPOSED_DATA_GROUPS_REQUIRED")
        data_groups = list(proposed_data_groups) if proposed_data_groups is not None else self._deterministic_data_groups(closure, mode)
        self._validate_data_groups(data_groups, closure, snapshot, mode)
        if mode == "foundation":
            validate_foundation_context(foundation_generation_context, foundation_task_package, structure_closure, snapshot)
            validate_foundation_traces(
                data_groups, selection_traces, foundation_generation_context, generation_receipt,
                task=foundation_task_package, run_id=run_id, qa_id=qa_id, trace_id=trace_id,
                attempt_no=attempt_no, invocation_verifier=self.foundation_invocation_verifier,
            )

        parents = [artifact_ref(source)]
        if mode == "event_data":
            parents.append(artifact_ref(operation_closure["envelope"]))
        else:
            parents.append(artifact_ref(foundation_task_package["envelope"]))
            parents.append(artifact_ref(foundation_generation_context["envelope"]))
            if foundation_profile is not None:
                parents.append(artifact_ref(foundation_profile["envelope"]))
        if snapshot is not None:
            parents.append(artifact_ref(snapshot["envelope"]))

        payload: dict[str, Any] = {
            "schema_version": "v5.bound-data/v1",
            "data_package_id": f"bound-data-{run_id}",
            "structure_closure_ref": artifact_ref(source),
            "operation_closure_ref": artifact_ref(operation_closure["envelope"]) if mode == "event_data" else None,
            "database_snapshot_ref": artifact_ref(snapshot["envelope"]) if snapshot is not None else None,
            "foundation_task_ref": artifact_ref(foundation_task_package["envelope"]) if mode == "foundation" else None,
            "foundation_generation_context_ref": artifact_ref(foundation_generation_context["envelope"]) if mode == "foundation" else None,
            "selection_traces": copy.deepcopy(selection_traces) if mode == "foundation" else [],
            "generation_receipt": copy.deepcopy(generation_receipt) if mode == "foundation" else None,
            "data_groups": data_groups,
        }
        return self._wrap(
            payload, artifact_id=f"bound-data-{run_id}", run_id=run_id, qa_id=qa_id, mode=mode,
            parents=parents, version=version, attempt_no=attempt_no, status=status,
            trace_id=trace_id, created_at=created_at,
        )

    # ------------------------------------------------------------ feedback

    def _feedback_lineage(self, previous: dict[str, Any], feedback: dict[str, Any], attempt_no: int | None, code: str) -> tuple[int, str]:
        previous_envelope = previous["envelope"]
        feedback_envelope = feedback["envelope"]
        for key in ("run_id", "qa_id", "trace_id"):
            if feedback_envelope[key] != previous_envelope[key]:
                _fail(code)
        next_attempt = attempt_no if attempt_no is not None else previous_envelope["attempt_no"] + 1
        if next_attempt not in (1, 2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        if next_attempt != previous_envelope["attempt_no"] + 1:
            _fail("ATTEMPT_LINEAGE_MISMATCH")
        terminal = next_attempt == 3
        return next_attempt, "blocked_manual" if terminal else "candidate"

    def _remap(
        self, previous: dict[str, Any], structure_closure: dict[str, Any], snapshot: dict[str, Any] | None,
        proposed_data_groups: list[dict[str, Any]], next_attempt: int, status: str, created_at: str | None, *,
        foundation_task_package: dict[str, Any] | None = None,
        foundation_generation_context: dict[str, Any] | None = None,
        selection_traces: list[dict[str, Any]] | None = None,
        generation_receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous_envelope = previous["envelope"]
        self.validate_structure_closure(structure_closure)
        if previous["payload"]["structure_closure_ref"] != artifact_ref(structure_closure["envelope"]):
            _fail("STRUCTURE_CLOSURE_REF_MISMATCH")
        closure = structure_closure["payload"]
        if snapshot is not None:
            self.validate_snapshot(snapshot)
        self._validate_data_groups(proposed_data_groups, closure, snapshot, previous_envelope["mode"])
        payload = copy.deepcopy(previous["payload"])
        if previous_envelope["mode"] == "foundation":
            if foundation_task_package is None or foundation_generation_context is None or selection_traces is None or generation_receipt is None:
                _fail("FOUNDATION_RETRY_EVIDENCE_REQUIRED")
            self.validate_foundation_task_package(foundation_task_package)
            self.validate_foundation_generation_context(foundation_generation_context)
            if payload["foundation_task_ref"] != artifact_ref(foundation_task_package["envelope"]):
                _fail("FOUNDATION_RETRY_TASK_REF_DRIFT")
            if payload["foundation_generation_context_ref"] != artifact_ref(foundation_generation_context["envelope"]):
                _fail("FOUNDATION_RETRY_CONTEXT_REF_DRIFT")
            if snapshot is None:
                _fail("FOUNDATION_SNAPSHOT_REQUIRED")
            if payload["database_snapshot_ref"] != artifact_ref(snapshot["envelope"]):
                _fail("FOUNDATION_RETRY_SNAPSHOT_REF_DRIFT")
            validate_foundation_context(foundation_generation_context, foundation_task_package, structure_closure, snapshot)
            validate_foundation_traces(
                proposed_data_groups, selection_traces, foundation_generation_context, generation_receipt,
                task=foundation_task_package, run_id=previous_envelope["run_id"], qa_id=previous_envelope["qa_id"],
                trace_id=previous_envelope["trace_id"], attempt_no=next_attempt,
                invocation_verifier=self.foundation_invocation_verifier,
            )
        payload["data_groups"] = copy.deepcopy(proposed_data_groups)
        if previous_envelope["mode"] == "foundation":
            payload["selection_traces"] = copy.deepcopy(selection_traces)
            payload["generation_receipt"] = copy.deepcopy(generation_receipt)
        if snapshot is not None:
            payload["database_snapshot_ref"] = artifact_ref(snapshot["envelope"])
        return self._wrap(
            payload, artifact_id=previous_envelope["artifact_id"], run_id=previous_envelope["run_id"],
            qa_id=previous_envelope["qa_id"], mode=previous_envelope["mode"],
            parents=previous_envelope["parent_artifact_refs"], version=previous_envelope["version"] + 1,
            attempt_no=next_attempt, supersedes_ref=artifact_ref(previous_envelope), status=status,
            trace_id=previous_envelope["trace_id"], created_at=created_at,
        )

    def apply_validation_feedback(
        self, previous: dict[str, Any], feedback: dict[str, Any], structure_closure: dict[str, Any], *,
        snapshot: dict[str, Any] | None = None, proposed_data_groups: list[dict[str, Any]] | None = None,
        foundation_task_package: dict[str, Any] | None = None, foundation_generation_context: dict[str, Any] | None = None,
        selection_traces: list[dict[str, Any]] | None = None, generation_receipt: dict[str, Any] | None = None,
        attempt_no: int | None = None, created_at: str | None = None,
    ) -> dict[str, Any]:
        """Task 2: consume 242 validation failure, fix the data, mint a new version."""
        self.validate_bound_data(previous)
        self.validate_validation_feedback(feedback)
        previous_envelope = previous["envelope"]
        if feedback["payload"]["data_package_ref"] != artifact_ref(previous_envelope):
            _fail("FEEDBACK_PACKAGE_REF_MISMATCH")
        next_attempt, status = self._feedback_lineage(previous, feedback, attempt_no, "FEEDBACK_LINEAGE_MISMATCH")
        if proposed_data_groups is None:
            _fail("PROPOSED_DATA_GROUPS_REQUIRED")
        return self._remap(
            previous, structure_closure, snapshot, proposed_data_groups, next_attempt, status, created_at,
            foundation_task_package=foundation_task_package, foundation_generation_context=foundation_generation_context,
            selection_traces=selection_traces, generation_receipt=generation_receipt,
        )

    def apply_regression_feedback(
        self, previous: dict[str, Any], feedback: dict[str, Any], structure_closure: dict[str, Any], *,
        snapshot: dict[str, Any] | None = None, proposed_data_groups: list[dict[str, Any]] | None = None,
        foundation_task_package: dict[str, Any] | None = None, foundation_generation_context: dict[str, Any] | None = None,
        selection_traces: list[dict[str, Any]] | None = None, generation_receipt: dict[str, Any] | None = None,
        attempt_no: int | None = None, created_at: str | None = None,
    ) -> dict[str, Any]:
        """Task 3: consume 260 regression failure routed back to 241, fix, re-mint."""
        self.validate_bound_data(previous)
        self.validate_regression_feedback(feedback)
        previous_envelope = previous["envelope"]
        payload = feedback["payload"]
        if payload["route_target"] != "241":
            _fail("REGRESSION_NOT_ROUTED_TO_241")
        if payload["mode"] != ("foundation" if previous_envelope["mode"] == "foundation" else "event_data"):
            _fail("REGRESSION_MODE_MISMATCH")
        if payload["retry_count"] not in (1, 2, 3):
            _fail("REGRESSION_RETRY_COUNT_INVALID")
        if artifact_ref(previous_envelope) not in payload["input_data_refs"]:
            _fail("REGRESSION_DATA_REF_MISSING")
        next_attempt, status = self._feedback_lineage(previous, feedback, attempt_no, "REGRESSION_LINEAGE_MISMATCH")
        if proposed_data_groups is None:
            _fail("PROPOSED_DATA_GROUPS_REQUIRED")
        return self._remap(
            previous, structure_closure, snapshot, proposed_data_groups, next_attempt, status, created_at,
            foundation_task_package=foundation_task_package, foundation_generation_context=foundation_generation_context,
            selection_traces=selection_traces, generation_receipt=generation_receipt,
        )

    # -------------------------------------------------------------- manifest

    def _validate_manifest(self, manifest: dict[str, Any], bound_data: dict[str, Any], issue_key: str) -> None:
        self._validate_package_schema(manifest, MANIFEST_SCHEMA, "BOUND_DATA_MANIFEST")
        envelope = bound_data["envelope"]
        if manifest["artifact_ref"] != artifact_ref(envelope):
            _fail("MANIFEST_ARTIFACT_REF_MISMATCH")
        if manifest["input_artifact_refs"] != envelope["parent_artifact_refs"]:
            _fail("MANIFEST_INPUT_REFS_MISMATCH")
        for key in ("run_id", "qa_id", "trace_id", "attempt_no", "status"):
            if manifest[key] != envelope[key]:
                _fail("MANIFEST_LINEAGE_MISMATCH")
        if manifest["issue_key"] != issue_key:
            _fail("MANIFEST_ISSUE_KEY_MISMATCH")
        locator = PurePosixPath(manifest["runtime_locator"])
        expected = PurePosixPath("vnext") / "03_构建过程层" / "issues" / issue_key / envelope["run_id"] / str(envelope["attempt_no"]) / "manifest.json"
        if locator.is_absolute() or ".." in locator.parts or locator != expected:
            _fail("MANIFEST_RUNTIME_BOUNDARY_VIOLATION")

    def build_manifest(self, bound_data: dict[str, Any], *, issue_key: str) -> dict[str, Any]:
        self.validate_bound_data(bound_data)
        envelope = bound_data["envelope"]
        manifest = {
            "manifest_schema_version": "bound-data-manifest/v1", "issue_key": issue_key,
            "artifact_ref": artifact_ref(envelope), "input_artifact_refs": envelope["parent_artifact_refs"],
            "run_id": envelope["run_id"], "qa_id": envelope["qa_id"], "trace_id": envelope["trace_id"],
            "attempt_no": envelope["attempt_no"], "status": envelope["status"],
            "runtime_locator": f"vnext/03_构建过程层/issues/{issue_key}/{envelope['run_id']}/{envelope['attempt_no']}/manifest.json",
        }
        self._validate_manifest(manifest, bound_data, issue_key)
        return manifest


def build_foundation_bound_data_from_runtime(bootstrap: Any, assembly: Any, structure_closure: dict[str, Any], **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Production 241 start: acquire the sealed launch before generation."""
    launch = bootstrap.foundation_repo_launcher().launch()
    package = assembly.generator(Path(__file__).resolve().parents[4]).build_bound_data(structure_closure, **kwargs)
    return package, launch
