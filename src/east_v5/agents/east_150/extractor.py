"""Deterministic contract boundary and retry orchestration for Agent 150.

Agent 150 consumes QUERY-SPECIFICATION-PACKAGE (140) to produce
QUESTION-SQL-PENDING-PRECHECK-PACKAGE for downstream agent 160.

When 160 returns PRECHECK-FAILED-FEEDBACK, 150 retries (up to 3 attempts).
After 3 failed attempts, 150 outputs a ``blocked_manual`` package.

Transported packages are always ``{"envelope": ..., "payload": ...}``.
The envelope is validated first and the payload schema second; all hard-code
validators run before the output package is sealed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json

TRANSPORT_KEYS = {"envelope", "payload"}
QUERY_SPEC_SCHEMA = "contracts/packages/query-specification-package.schema.json"
PENDING_PRECHECK_SCHEMA = "contracts/packages/question-sql-pending-precheck-package.schema.json"
PRECHECK_FEEDBACK_SCHEMA = "contracts/packages/precheck-failed-feedback-package.schema.json"
MAX_ATTEMPTS = 3


def _fail(code: str) -> None:
    raise ContractError(code)


def _schema_validate(repo_root: Path, relative: str, payload: dict[str, Any], label: str) -> None:
    try:
        Draft202012Validator(load_json(repo_root / relative)).validate(payload)
    except ValidationError as exc:
        raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc


def _same_ref(actual: Any, expected: dict[str, Any], code: str) -> None:
    if actual != expected:
        _fail(code)


def _same_lineage(envelope: dict[str, Any], reference: dict[str, Any], code: str) -> None:
    for key in ("run_id", "qa_id", "trace_id"):
        if envelope[key] != reference[key]:
            _fail(code)


def _stable_pending_precheck_id(run_id: str, qa_id: str) -> str:
    """Return a deterministic package-local logical identity.

    The frozen v1 schema reserves a three-digit suffix.  Deriving it solely
    from the stable task identity, rather than the attempt/version, preserves
    the ID across every reconstruction of the same pending-precheck package.
    """
    digest = hashlib.sha256(f"{run_id}|{qa_id}|150".encode("utf-8")).hexdigest()
    return f"ppre-{int(digest[:8], 16) % 1000:03d}"


class PendingPrecheckBuilder:
    """Implements the two tasks for Agent 150 without inventing assets.

    Task 1: consume QUERY-SPECIFICATION-PACKAGE, produce QUESTION-SQL-PENDING-PRECHECK-PACKAGE.
    Task 2: consume PRECHECK-FAILED-FEEDBACK from 160, retry or block.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validate_transport(
        self, package: dict[str, Any], expected_type: str, schema_path: str | None = None,
    ) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != expected_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        if not isinstance(payload, dict):
            _fail("PAYLOAD_NOT_OBJECT")
        if schema_path is not None:
            _schema_validate(self.repo_root, schema_path, payload, expected_type.upper())
        else:
            _fail("PACKAGE_SCHEMA_UNREGISTERED")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _wrap(
        self,
        artifact_type: str,
        artifact_id: str,
        payload: dict[str, Any],
        *,
        run_id: str,
        qa_id: str,
        version: int = 1,
        attempt_no: int = 1,
        parents: list[dict[str, Any]] | None = None,
        supersedes_ref: dict[str, Any] | None = None,
        status: str = "candidate",
        trace_id: str = "150-trace",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        parent_refs = list(parents or [])
        envelope: dict[str, Any] = {
            "artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": run_id,
            "qa_id": qa_id, "version": version, "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64, "supersedes_ref": supersedes_ref,
            "attempt_no": attempt_no, "producer_id": "150", "parent_artifact_refs": parent_refs,
            "input_hashes": [item["content_hash"] for item in parent_refs], "status": status,
            "mode": "question_sql", "created_at": created_at or self._now(),
            "trace_id": trace_id, "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self._validate_transport(package, artifact_type, PENDING_PRECHECK_SCHEMA)
        return package

    # ── Input validation ──────────────────────────────────────────

    def validate_query_spec(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "query_specification_package", QUERY_SPEC_SCHEMA)

    def validate_precheck_feedback(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "precheck_failed_feedback", PRECHECK_FEEDBACK_SCHEMA)

    def validate_pending_precheck(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "question_sql_pending_precheck", PENDING_PRECHECK_SCHEMA)

    # ── Hard-code validators ───────────────────────────────────────

    @staticmethod
    def _validate_ref_integrity(
        spec_ref: dict[str, Any], spec_envelope: dict[str, Any],
    ) -> None:
        """Check: output ref must match actual input package identity."""
        _same_ref(spec_ref, artifact_ref(spec_envelope), "REF_INTEGRITY_VIOLATION")

    @staticmethod
    def _validate_allowed_tables_consistency(
        allowed_tables_ref: list[str], sql_scope: dict[str, Any],
    ) -> None:
        """Check: allowed_tables_ref must match sql_schema_scope table list."""
        scoped_tables = [t["table_id"] for t in sql_scope.get("allowed_tables", [])]
        if set(allowed_tables_ref) != set(scoped_tables):
            _fail("ALLOWED_TABLES_INCONSISTENT")

    @staticmethod
    def _validate_entry_table_in_scope(
        entry_table: str, sql_scope: dict[str, Any],
    ) -> None:
        """Check: entry_table must appear in sql_schema_scope."""
        scoped_tables = {t["table_id"] for t in sql_scope.get("allowed_tables", [])}
        if entry_table not in scoped_tables:
            _fail("ENTRY_TABLE_NOT_IN_SCOPE")

    @staticmethod
    def _validate_sql_nonempty(candidate_sql: str) -> None:
        """Check: candidate SQL must not be empty."""
        if not candidate_sql or not candidate_sql.strip():
            _fail("CANDIDATE_SQL_EMPTY")

    @staticmethod
    def _validate_version_immutability(
        version: int, supersedes_ref: dict[str, Any] | None,
    ) -> None:
        """Check: version 1 must have no supersedes_ref; version > 1 must have one."""
        if version == 1 and supersedes_ref is not None:
            _fail("VERSION_OVERWRITE_ATTEMPTED")
        if version > 1 and supersedes_ref is None:
            _fail("VERSION_OVERWRITE_ATTEMPTED")

    # ── Task 1: Build pending-precheck package ─────────────────────

    def build_pending_precheck(
        self,
        query_spec: dict[str, Any],
        *,
        run_id: str,
        qa_id: str,
        version: int = 1,
        attempt_no: int = 1,
        supersedes_ref: dict[str, Any] | None = None,
        status: str = "candidate",
        # LLM-extracted fields
        candidate_sql: str,
        sql_parameters: list[dict[str, Any]],
        # Optional overrides for precheck expectations
        expected_checks: list[dict[str, Any]] | None = None,
        max_result_rows_hint: int | None = None,
        created_at: str | None = None,
        parent_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Task 1: derive a pending-precheck package from query specification."""
        self.validate_query_spec(query_spec)
        spec_payload = query_spec["payload"]
        spec_envelope = query_spec["envelope"]

        # Lineage consistency
        _same_lineage(spec_envelope, spec_envelope, "INPUT_LINEAGE_MISMATCH")
        if spec_envelope["run_id"] != run_id or spec_envelope["qa_id"] != qa_id:
            _fail("RUN_OR_QA_MISMATCH")

        # Hard-code validators
        self._validate_version_immutability(version, supersedes_ref)
        self._validate_sql_nonempty(candidate_sql)

        # Derive fields from query specification
        query_goal = spec_payload["query_goal"]
        entry_table = spec_payload["query_entry"]["entry_table"]
        sql_scope = spec_payload["sql_schema_scope"]
        allowed_tables_ref = [t["table_id"] for t in sql_scope["allowed_tables"]]

        self._validate_entry_table_in_scope(entry_table, sql_scope)
        self._validate_allowed_tables_consistency(allowed_tables_ref, sql_scope)

        # Derive precheck expectations from query spec if not overridden
        if expected_checks is None:
            expected_checks = self._derive_expected_checks(spec_payload)
        if max_result_rows_hint is None:
            max_result_rows_hint = spec_payload["join_expansion_limit"]["max_result_rows"]

        # Build payload
        payload: dict[str, Any] = {
            "pending_precheck_id": _stable_pending_precheck_id(run_id, qa_id),
            "query_specification_package_ref": artifact_ref(spec_envelope),
            "candidate_sql": candidate_sql,
            "sql_parameters": sql_parameters,
            "precheck_expectations": {
                "expected_checks": expected_checks,
                "max_result_rows_hint": max_result_rows_hint,
            },
            "query_goal": query_goal,
            "entry_table": entry_table,
            "allowed_tables_ref": allowed_tables_ref,
            "question_sql_pending_precheck_package_schema_version": "question-sql-pending-precheck-v1",
        }

        # Attempt boundary
        if attempt_no > MAX_ATTEMPTS:
            _fail("ATTEMPT_OUT_OF_RANGE")

        parents = parent_refs if parent_refs is not None else [
            artifact_ref(spec_envelope),
        ]
        return self._wrap(
            "question_sql_pending_precheck", f"150-ppre-{run_id}", payload,
            run_id=run_id, qa_id=qa_id, version=version, attempt_no=attempt_no,
            parents=parents, supersedes_ref=supersedes_ref, status=status,
            trace_id=spec_envelope["trace_id"], created_at=created_at,
        )

    @staticmethod
    def _derive_expected_checks(spec_payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Derive default precheck expectations from query specification payload."""
        checks: list[dict[str, Any]] = [
            {"check_id": "chk-syntax", "check_type": "syntax", "description": "SQL语法合法性检查"},
            {"check_id": "chk-scope", "check_type": "scope", "description": "SQL表字段范围检查"},
            {"check_id": "chk-param-type", "check_type": "parameter_type", "description": "参数类型一致性检查"},
            {"check_id": "chk-row-limit", "check_type": "result_row_limit", "description": "结果行数上限检查"},
            {"check_id": "chk-safety", "check_type": "safety_gate", "description": "安全门禁检查"},
        ]
        return checks

    # ── Task 2: Precheck feedback retry ────────────────────────────

    def _validate_remap_output(
        self,
        output: dict[str, Any],
        previous: dict[str, Any],
        feedback: dict[str, Any],
        query_spec: dict[str, Any],
        attempt_no: int,
    ) -> None:
        self.validate_pending_precheck(output)
        envelope = output["envelope"]
        prior = previous["envelope"]
        _same_lineage(envelope, prior, "REMAP_OUTPUT_LINEAGE_MISMATCH")
        if envelope["version"] != prior["version"] + 1:
            _fail("REMAP_OUTPUT_VERSION_MISMATCH")
        _same_ref(envelope["supersedes_ref"], artifact_ref(prior), "REMAP_OUTPUT_SUPERSEDES_MISMATCH")
        if envelope["attempt_no"] != attempt_no:
            _fail("REMAP_OUTPUT_ATTEMPT_MISMATCH")
        expected_parents = [
            artifact_ref(query_spec["envelope"]),
            artifact_ref(feedback["envelope"]),
            artifact_ref(prior),
        ]
        if envelope["parent_artifact_refs"] != expected_parents:
            _fail("REMAP_OUTPUT_PARENT_LINEAGE_MISMATCH")
        if envelope["input_hashes"] != [ref["content_hash"] for ref in expected_parents]:
            _fail("REMAP_OUTPUT_INPUT_HASH_ORDER_MISMATCH")
        if output["payload"]["pending_precheck_id"] != previous["payload"]["pending_precheck_id"]:
            _fail("REMAP_OUTPUT_PENDING_PRECHECK_ID_MISMATCH")

    def handle_precheck_feedback(
        self,
        query_spec: dict[str, Any],
        feedback: dict[str, Any],
        previous_ppre: dict[str, Any],
        *,
        run_id: str,
        qa_id: str,
        attempt_no: int,
        # LLM-extracted fields for the revised candidate
        candidate_sql: str,
        sql_parameters: list[dict[str, Any]],
        expected_checks: list[dict[str, Any]] | None = None,
        max_result_rows_hint: int | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Task 2: consume 160 precheck feedback, retry or block."""
        self.validate_query_spec(query_spec)
        self.validate_precheck_feedback(feedback)
        self.validate_pending_precheck(previous_ppre)

        previous_envelope = previous_ppre["envelope"]
        spec_envelope = query_spec["envelope"]

        _same_lineage(spec_envelope, previous_envelope, "SPEC_LINEAGE_MISMATCH")
        _same_lineage(feedback["envelope"], previous_envelope, "FEEDBACK_LINEAGE_MISMATCH")

        # Feedback must reference the previous pending-precheck package
        feedback_payload = feedback["payload"]
        _same_ref(
            feedback_payload["pending_precheck_package_ref"],
            artifact_ref(previous_envelope),
            "FEEDBACK_REF_MISMATCH",
        )

        if attempt_no not in (2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        if attempt_no != previous_envelope["attempt_no"] + 1:
            _fail("ATTEMPT_LINEAGE_MISMATCH")
        if run_id != previous_envelope["run_id"] or qa_id != previous_envelope["qa_id"]:
            _fail("RUN_OR_QA_MISMATCH")

        parents = [
            artifact_ref(spec_envelope),
            artifact_ref(feedback["envelope"]),
            artifact_ref(previous_envelope),
        ]
        build_kwargs = dict(
            run_id=run_id,
            qa_id=qa_id,
            version=previous_envelope["version"] + 1,
            attempt_no=attempt_no,
            supersedes_ref=artifact_ref(previous_envelope),
            status="candidate",
            candidate_sql=candidate_sql,
            sql_parameters=sql_parameters,
            expected_checks=expected_checks,
            max_result_rows_hint=max_result_rows_hint,
            created_at=created_at,
        )
        # A valid third reconstruction remains a candidate.  Only a failed
        # reconstruction on that final attempt becomes a lawful manual block.
        try:
            new_ppre = self.build_pending_precheck(
                query_spec, **build_kwargs, parent_refs=parents,
            )
        except ContractError:
            if attempt_no != MAX_ATTEMPTS:
                raise
            blocked_payload = dict(previous_ppre["payload"])
            new_ppre = self._wrap(
                "question_sql_pending_precheck", f"150-ppre-{run_id}", blocked_payload,
                run_id=run_id, qa_id=qa_id, version=previous_envelope["version"] + 1,
                attempt_no=attempt_no, parents=parents,
                supersedes_ref=artifact_ref(previous_envelope),
                status="blocked_manual", trace_id=spec_envelope["trace_id"],
                created_at=created_at,
            )
        self._validate_remap_output(new_ppre, previous_ppre, feedback, query_spec, attempt_no)
        return new_ppre
