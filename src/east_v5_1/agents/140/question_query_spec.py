"""V5.1 Agent 140 adapter for an approved question and 000 constraints.

This module deliberately does not reuse the V5 140 implementation: its input
semantics are different.  Its output, however, is the unchanged canonical V5
``query_specification_package`` so that 150 can consume it without a shim.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.agents.east_150.extractor import PendingPrecheckBuilder
from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json


QUESTION_SCHEMA = "contracts/v5_1/question-input.schema.json"
ADAPTER_SCHEMA = "contracts/v5_1/query-spec-from-question.schema.json"
CONSTRAINT_SCHEMA = "contracts/packages/constraint-asset-package.schema.json"
CANONICAL_SCHEMA = "contracts/packages/query-specification-package.schema.json"
TRANSPORT_KEYS = {"envelope", "payload"}
MAX_ATTEMPTS = 3


def _fail(code: str) -> None:
    raise ContractError(code)


def _validate_schema(root: Path, relative: str, value: Any, label: str) -> None:
    try:
        Draft202012Validator(load_json(root / relative)).validate(value)
    except ValidationError as exc:
        raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc


def _stable_id(run_id: str, qa_id: str) -> str:
    return f"qspec-{int(hashlib.sha256(f'{run_id}|{qa_id}'.encode()).hexdigest()[:8], 16) % 1000:03d}"


class QuestionQuerySpecBuilder:
    """Fail-closed V5.1 140 contract adapter.

    ``question_input`` is a V5.1 transport package with artifact type
    ``question_input``.  It intentionally has no V5 catalog entry, so its
    common-envelope shape and hash are checked locally; 000's package remains
    validated by the real V5 envelope and payload contract.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validate_question_transport(self, package: dict[str, Any]) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("QUESTION_TRANSPORT_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        if not isinstance(envelope, dict) or not isinstance(payload, dict):
            _fail("QUESTION_TRANSPORT_INVALID")
        required = {
            "artifact_id", "artifact_type", "run_id", "qa_id", "version", "schema_version", "content_hash",
            "supersedes_ref", "attempt_no", "producer_id", "parent_artifact_refs", "input_hashes", "status",
            "mode", "created_at", "trace_id", "storage_locator",
        }
        if set(envelope) != required:
            _fail("QUESTION_ENVELOPE_INVALID")
        if envelope["artifact_type"] != "question_input":
            _fail("QUESTION_ARTIFACT_TYPE_MISMATCH")
        if envelope["schema_version"] != "COMMON-ENVELOPE/v1" or envelope["mode"] != "question_sql":
            _fail("QUESTION_ENVELOPE_INVALID")
        if not isinstance(envelope["attempt_no"], int) or envelope["attempt_no"] not in range(1, MAX_ATTEMPTS + 1):
            _fail("ATTEMPT_OUT_OF_RANGE")
        if not isinstance(envelope["version"], int) or isinstance(envelope["version"], bool) or envelope["version"] < 1:
            _fail("VERSION_INVALID")
        if not all(isinstance(envelope.get(key), str) and envelope[key] for key in ("artifact_id", "run_id", "qa_id", "producer_id", "trace_id", "created_at")):
            _fail("QUESTION_ENVELOPE_INVALID")
        if envelope["supersedes_ref"] is not None or envelope["parent_artifact_refs"] or envelope["input_hashes"]:
            _fail("QUESTION_PARENT_LINEAGE_INVALID")
        if envelope["content_hash"] != content_hash(envelope, payload):
            _fail("CONTENT_HASH_DRIFT")
        _validate_schema(self.repo_root, QUESTION_SCHEMA, payload, "QUESTION_INPUT")
        if payload["question_id"] != envelope["qa_id"]:
            _fail("QUESTION_QA_ID_MISMATCH")
        if payload["quality_assessment"]["has_ambiguity"] or payload["quality_assessment"]["requires_human_correction"]:
            _fail("QUESTION_AMBIGUITY_UNRESOLVED")

    def _validate_constraint(self, package: dict[str, Any]) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != "constraint_asset_package" or envelope["producer_id"] != "000":
            _fail("CONSTRAINT_ASSET_TYPE_MISMATCH")
        _validate_schema(self.repo_root, CONSTRAINT_SCHEMA, payload, "CONSTRAINT_ASSET_PACKAGE")

    @staticmethod
    def _asset_fields(constraints: dict[str, Any]) -> tuple[dict[str, set[str]], set[str]]:
        tables: dict[str, set[str]] = {}
        evidence: set[str] = set()
        for index, record in enumerate(constraints["payload"]["matched_records"]):
            data = record.get("data", {})
            table = data.get("table_id")
            field = data.get("field_id")
            refs = record.get("source_refs", [])
            if isinstance(table, str) and table and isinstance(field, str) and field and refs:
                tables.setdefault(table, set()).add(field)
                evidence.add(f"constraint_asset:{constraints['payload']['asset_version']}#record-{index}")
        return tables, evidence

    @staticmethod
    def _expected_fields(question: dict[str, Any]) -> dict[str, set[str]]:
        expected_tables = question["payload"]["answer_contract"]["expected_tables"]
        result = {table: set() for table in expected_tables}
        for value in question["payload"]["answer_contract"]["expected_fields"]:
            if "." in value:
                table, field = value.rsplit(".", 1)
                if table not in result:
                    _fail("QUESTION_FIELD_TABLE_MISMATCH")
            elif len(result) == 1:
                table, field = next(iter(result)), value
            else:
                _fail("QUESTION_FIELD_AMBIGUOUS")
            if not field:
                _fail("QUESTION_FIELD_MISSING")
            result[table].add(field)
        if not all(result.values()):
            _fail("QUESTION_FIELD_MISSING")
        return result

    @staticmethod
    def _must_preserve(question: dict[str, Any]) -> list[str]:
        payload = question["payload"]
        refs = [f"question:{payload['question_id']}:table:{table}" for table in payload["answer_contract"]["expected_tables"]]
        refs.extend(f"question:{payload['question_id']}:field:{field}" for field in payload["answer_contract"]["expected_fields"])
        for condition in payload["parsed_intent"]["predicate_conditions"]:
            digest = hashlib.sha256(json.dumps(condition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
            refs.append(f"question:{payload['question_id']}:predicate:{digest}")
        return sorted(dict.fromkeys(refs))

    @staticmethod
    def _validate_candidate_scope(candidate: dict[str, Any], expected: dict[str, set[str]], asset: dict[str, set[str]], evidence: set[str]) -> None:
        scope = candidate.get("sql_schema_scope", {}).get("allowed_tables")
        if not isinstance(scope, list):
            _fail("SQL_SCOPE_INVALID")
        observed: dict[str, set[str]] = {}
        for item in scope:
            if not isinstance(item, dict) or not isinstance(item.get("table_id"), str) or not isinstance(item.get("allowed_fields"), list):
                _fail("SQL_SCOPE_INVALID")
            observed[item["table_id"]] = set(item["allowed_fields"])
        if set(observed) != set(expected):
            _fail("SQL_SCOPE_TABLE_MISMATCH")
        for table, fields in observed.items():
            if not fields or not fields.issubset(asset.get(table, set())):
                _fail("SQL_SCOPE_FIELD_OUT_OF_ASSET")
            if not expected[table].issubset(fields):
                _fail("RETURN_FIELD_NOT_COVERED")
        returned = {(item.get("source_table"), item.get("field_id")) for item in candidate.get("return_fields", []) if isinstance(item, dict)}
        required = {(table, field) for table, fields in expected.items() for field in fields}
        if not required.issubset(returned):
            _fail("RETURN_FIELD_NOT_COVERED")
        if any(table not in observed or field not in observed[table] for table, field in returned):
            _fail("RETURN_FIELD_OUT_OF_SCOPE")
        entry = candidate.get("query_entry", {})
        if entry.get("entry_table") not in observed:
            _fail("QUERY_ENTRY_OUT_OF_SCOPE")
        for condition in entry.get("entry_conditions", []):
            if condition.get("field_id") not in observed[entry["entry_table"]]:
                _fail("QUERY_ENTRY_FIELD_OUT_OF_SCOPE")
        for filter_item in candidate.get("filters_and_evidence", []):
            if filter_item.get("evidence_ref") not in evidence:
                _fail("FILTER_EVIDENCE_MISSING")
            if not any(filter_item.get("field_id") in fields for fields in observed.values()):
                _fail("FILTER_FIELD_OUT_OF_SCOPE")

    def build_query_spec_from_question(
        self, question_input: dict[str, Any], constraint_asset_package: dict[str, Any], *, run_id: str, qa_id: str,
        candidate: dict[str, Any], version: int = 1, attempt_no: int = 1,
        supersedes_ref: dict[str, Any] | None = None, status: str = "candidate", created_at: str | None = None,
    ) -> dict[str, Any]:
        """Seal caller-supplied candidate fields after deterministic validation."""
        protected = {
            "query_spec_id", "penalty_fact_package_ref", "observable_fact_package_ref",
            "must_preserve_fact_refs", "query_specification_package_schema_version",
        }
        if not isinstance(candidate, dict) or protected.intersection(candidate):
            _fail("CANDIDATE_PROTECTED_FIELD_FORBIDDEN")
        self._validate_question_transport(question_input)
        self._validate_constraint(constraint_asset_package)
        question_envelope, asset_envelope = question_input["envelope"], constraint_asset_package["envelope"]
        for key in ("run_id", "qa_id", "trace_id"):
            if question_envelope[key] != asset_envelope[key] or question_envelope[key] != {"run_id": run_id, "qa_id": qa_id, "trace_id": question_envelope["trace_id"]}[key]:
                _fail("INPUT_LINEAGE_MISMATCH")
        if question_envelope["attempt_no"] != asset_envelope["attempt_no"] or attempt_no != question_envelope["attempt_no"]:
            _fail("ATTEMPT_LINEAGE_MISMATCH")
        if question_envelope["version"] != asset_envelope["version"]:
            _fail("VERSION_LINEAGE_MISMATCH")
        if attempt_no not in range(1, MAX_ATTEMPTS + 1):
            _fail("MAX_ATTEMPTS_EXCEEDED")
        if (version == 1) != (supersedes_ref is None):
            _fail("VERSION_OVERWRITE_ATTEMPTED")
        expected = self._expected_fields(question_input)
        asset, evidence = self._asset_fields(constraint_asset_package)
        if constraint_asset_package["payload"]["unmatched_items"] or not asset:
            _fail("CONSTRAINT_ASSET_UNMATCHED")
        if any(not fields.issubset(asset.get(table, set())) for table, fields in expected.items()):
            _fail("CONSTRAINT_ASSET_NOT_COVERED")
        self._validate_candidate_scope(candidate, expected, asset, evidence)
        qref, aref = artifact_ref(question_envelope), artifact_ref(asset_envelope)
        payload = {
            **candidate,
            "query_spec_id": _stable_id(run_id, qa_id),
            "penalty_fact_package_ref": qref,
            "observable_fact_package_ref": aref,
            "must_preserve_fact_refs": self._must_preserve(question_input),
            "query_specification_package_schema_version": "query-specification-v1",
        }
        context = {"question_input_ref": qref, "constraint_asset_package_ref": aref, "canonical_payload": payload,
                   "compatibility_ref_mapping": {"penalty_fact_package_ref": qref, "observable_fact_package_ref": aref}}
        _validate_schema(self.repo_root, ADAPTER_SCHEMA, context, "QUERY_SPEC_FROM_QUESTION")
        _validate_schema(self.repo_root, CANONICAL_SCHEMA, payload, "QUERY_SPECIFICATION_PACKAGE")
        parents = [qref, aref]
        envelope = {"artifact_id": f"140-qspec-{run_id}", "artifact_type": "query_specification_package", "run_id": run_id,
                    "qa_id": qa_id, "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
                    "supersedes_ref": supersedes_ref, "attempt_no": attempt_no, "producer_id": "140",
                    "parent_artifact_refs": parents, "input_hashes": [ref["content_hash"] for ref in parents], "status": status,
                    "mode": "question_sql", "created_at": created_at or question_envelope["created_at"], "trace_id": question_envelope["trace_id"], "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        PendingPrecheckBuilder(self.repo_root).validate_query_spec(package)
        return package
