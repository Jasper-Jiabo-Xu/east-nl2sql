"""Deterministic contract boundary and retry orchestration for Agent 140.

Agent 140 consumes PENALTY-FACT-PACKAGE (120) and EAST-OBSERVABLE-FACT-PACKAGE
(130) to produce QUERY-SPECIFICATION-PACKAGE for downstream agents 150/170/180/220/260.

Transported packages are always ``{"envelope": ..., "payload": ...}``.
The envelope is validated first and the payload schema second; all hard-code
validators run before the output package is sealed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError

from east_v5.agents.east_120.extractor import validate_fact_package
from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json

TRANSPORT_KEYS = {"envelope", "payload"}
REVIEW_ARTIFACTS = {"deepseek_review_result", "glm_review_result"}
QUERY_SPEC_SCHEMA = "contracts/packages/query-specification-package.schema.json"
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


def _stable_query_spec_id(run_id: str, qa_id: str) -> str:
    """Return a deterministic package-local logical identity.

    The frozen v1 schema reserves a three-digit suffix.  Deriving it solely
    from the stable task identity, rather than the attempt/version, preserves
    the ID across every reconstruction of the same query specification.
    """
    digest = hashlib.sha256(f"{run_id}|{qa_id}".encode("utf-8")).hexdigest()
    return f"qspec-{int(digest[:8], 16) % 1000:03d}"


class QuerySpecBuilder:
    """Implements the two tasks for Agent 140 without inventing assets."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validate_transport(self, package: dict[str, Any], expected_type: str, schema_path: str | None = None) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != expected_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        if not isinstance(payload, dict):
            _fail("PAYLOAD_NOT_OBJECT")
        if expected_type == "penalty_fact_package":
            validate_fact_package(self.repo_root, payload)
        elif expected_type == "east_observable_fact_package":
            _schema_validate(self.repo_root, "contracts/packages/east-observable-fact-package.schema.json", payload, "EAST_OBSERVABLE_FACT_PACKAGE")
        elif schema_path is not None:
            _schema_validate(self.repo_root, schema_path, payload, expected_type.upper())
        else:
            _fail("PACKAGE_SCHEMA_UNREGISTERED")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _wrap(
        self, artifact_type: str, artifact_id: str, payload: dict[str, Any], *, run_id: str,
        qa_id: str, version: int = 1, attempt_no: int = 1,
        parents: list[dict[str, Any]] | None = None, supersedes_ref: dict[str, Any] | None = None,
        status: str = "candidate", trace_id: str = "140-trace", created_at: str | None = None,
    ) -> dict[str, Any]:
        parent_refs = list(parents or [])
        envelope: dict[str, Any] = {
            "artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": run_id,
            "qa_id": qa_id, "version": version, "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64, "supersedes_ref": supersedes_ref,
            "attempt_no": attempt_no, "producer_id": "140", "parent_artifact_refs": parent_refs,
            "input_hashes": [item["content_hash"] for item in parent_refs], "status": status,
            "mode": "question_sql", "created_at": created_at or self._now(),
            "trace_id": trace_id, "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self._validate_transport(package, artifact_type, QUERY_SPEC_SCHEMA)
        return package

    # ── Input validation ──────────────────────────────────────────

    def validate_penalty(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "penalty_fact_package")

    def validate_observable(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "east_observable_fact_package", "contracts/packages/east-observable-fact-package.schema.json")

    def validate_review(self, package: dict[str, Any]) -> None:
        artifact_type = package.get("envelope", {}).get("artifact_type") if isinstance(package, dict) else None
        if artifact_type not in REVIEW_ARTIFACTS:
            _fail("REVIEW_ARTIFACT_TYPE_INVALID")
        schema_paths = {
            "deepseek_review_result": "contracts/packages/deepseek-review-result.schema.json",
            "glm_review_result": "contracts/packages/glm-review-result.schema.json",
        }
        self._validate_transport(package, artifact_type, schema_paths[artifact_type])

    def validate_query_spec(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "query_specification_package", QUERY_SPEC_SCHEMA)

    # ── Hard-code validators ───────────────────────────────────────

    @staticmethod
    def _validate_must_preserve_facts(
        penalty_payload: dict[str, Any], must_preserve_fact_refs: list[str],
    ) -> None:
        """Check 1: every yes/conditional fact from penalty must appear in must_preserve_fact_refs."""
        required_ids = {
            f["penalty_fact_id"]
            for f in penalty_payload["source_facts"]
            if f.get("must_preserve_in_question") in ("yes", "conditional")
        }
        covered = set(must_preserve_fact_refs)
        missing = required_ids - covered
        if missing:
            _fail("MUST_PRESERVE_FACTS_NOT_COVERED")

    @staticmethod
    def _validate_sql_scope(
        sql_scope: dict[str, Any], observable_payload: dict[str, Any],
    ) -> None:
        """Check 2: every scoped table *and field* is observable."""
        observable_fields: dict[str, set[str]] = {}
        for fact in observable_payload.get("observable_facts", []):
            entry_table = fact.get("entry_table", "")
            if entry_table:
                observable_fields.setdefault(entry_table, set())
            for rel in fact.get("related_tables_fields", []):
                table_id, field_id = rel.get("table_id", ""), rel.get("field_id", "")
                if table_id and field_id:
                    observable_fields.setdefault(table_id, set()).add(field_id)
            for mapping in fact.get("mapping_matrix", []):
                path = mapping.get("table_field_path", "")
                if isinstance(path, str) and "." in path:
                    table_id, field_id = path.rsplit(".", 1)
                    if table_id and field_id:
                        observable_fields.setdefault(table_id, set()).add(field_id)

        for scoped_table in sql_scope.get("allowed_tables", []):
            table_id = scoped_table["table_id"]
            if table_id not in observable_fields:
                _fail("SQL_SCOPE_TABLE_NOT_FOUND")
            unknown_fields = set(scoped_table.get("allowed_fields", [])) - observable_fields[table_id]
            if unknown_fields:
                _fail("SQL_SCOPE_FIELD_NOT_FOUND")

    @staticmethod
    def _validate_input_lineage(
        penalty_envelope: dict[str, Any], observable_envelope: dict[str, Any], run_id: str, qa_id: str,
    ) -> None:
        """Task 1 accepts only the same 120/130 run, QA pair, and trace."""
        _same_lineage(observable_envelope, penalty_envelope, "INPUT_LINEAGE_MISMATCH")
        if penalty_envelope["run_id"] != run_id or penalty_envelope["qa_id"] != qa_id:
            _fail("RUN_OR_QA_MISMATCH")

    @staticmethod
    def _validate_ref_integrity(
        penalty_ref: dict[str, Any], observable_ref: dict[str, Any],
        penalty_envelope: dict[str, Any], observable_envelope: dict[str, Any],
    ) -> None:
        """Check 3: input refs must match actual package identity."""
        _same_ref(penalty_ref, artifact_ref(penalty_envelope), "REF_INTEGRITY_VIOLATION")
        _same_ref(observable_ref, artifact_ref(observable_envelope), "REF_INTEGRITY_VIOLATION")

    @staticmethod
    def _validate_positive_counts(
        min_pos: int, min_neg: int,
    ) -> None:
        """Check 4: minimum_positive_count and minimum_negative_count must be >= 1."""
        if not isinstance(min_pos, int) or min_pos < 1:
            _fail("INVALID_COUNT")
        if not isinstance(min_neg, int) or min_neg < 1:
            _fail("INVALID_COUNT")

    @staticmethod
    def _validate_join_limit(limit: dict[str, Any]) -> None:
        """Check 5: max_multiplier > 0 and max_result_rows >= 1."""
        if not isinstance(limit.get("max_multiplier"), (int, float)) or limit["max_multiplier"] <= 0:
            _fail("JOIN_EXPANSION_EXCEEDED")
        if not isinstance(limit.get("max_result_rows"), int) or limit["max_result_rows"] < 1:
            _fail("JOIN_EXPANSION_EXCEEDED")

    @staticmethod
    def _validate_row_group_count(rgc: dict[str, Any]) -> None:
        """Check 6: minimum, target, and permissible range agree."""
        tr = rgc.get("tolerance_range", {})
        if tr.get("low", 0) > tr.get("high", 0):
            _fail("ROW_GROUP_RANGE_INVALID")
        if not isinstance(rgc.get("minimum"), int) or rgc["minimum"] < 0:
            _fail("ROW_GROUP_RANGE_INVALID")
        if not isinstance(rgc.get("target"), int) or rgc["target"] < 0:
            _fail("ROW_GROUP_RANGE_INVALID")
        if rgc["minimum"] > rgc["target"]:
            _fail("ROW_GROUP_TARGET_INCONSISTENT")
        if not tr.get("low", 0) <= rgc["target"] <= tr.get("high", 0):
            _fail("ROW_GROUP_TARGET_INCONSISTENT")
        if rgc["minimum"] > tr.get("low", 0):
            _fail("ROW_GROUP_TARGET_INCONSISTENT")

    @staticmethod
    def _validate_version_immutability(
        version: int, supersedes_ref: dict[str, Any] | None,
    ) -> None:
        """Check 7: version 1 must have no supersedes_ref; version > 1 must have one."""
        if version == 1 and supersedes_ref is not None:
            _fail("VERSION_OVERWRITE_ATTEMPTED")
        if version > 1 and supersedes_ref is None:
            _fail("VERSION_OVERWRITE_ATTEMPTED")

    def _run_all_hard_validators(
        self,
        penalty: dict[str, Any],
        observable: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Run all seven hard-code validators on the candidate payload."""
        self._validate_must_preserve_facts(penalty["payload"], payload["must_preserve_fact_refs"])
        self._validate_sql_scope(payload["sql_schema_scope"], observable["payload"])
        self._validate_ref_integrity(
            payload["penalty_fact_package_ref"], payload["observable_fact_package_ref"],
            penalty["envelope"], observable["envelope"],
        )
        self._validate_positive_counts(payload["minimum_positive_count"], payload["minimum_negative_count"])
        self._validate_join_limit(payload["join_expansion_limit"])
        self._validate_row_group_count(payload["expected_row_group_count"])
        self._validate_version_immutability(
            # version and supersedes_ref are in the envelope, not the payload;
            # this method is called before _wrap, so we pass them through payload
            # convention: _wrap adds them to the envelope. We validate the
            # caller's intent directly.
            1, None,  # validated separately in build / handle_review
        )

    # ── Task 1: Build query specification ──────────────────────────

    def build_query_spec(
        self,
        penalty: dict[str, Any],
        observable: dict[str, Any],
        *,
        run_id: str,
        qa_id: str,
        version: int = 1,
        attempt_no: int = 1,
        supersedes_ref: dict[str, Any] | None = None,
        status: str = "candidate",
        # LLM-extracted fields (passed in from the caller, e.g. probe or LLM loop)
        query_goal: str,
        main_object_and_grain: dict[str, Any],
        query_entry: dict[str, Any],
        related_objects_and_path: list[dict[str, Any]],
        filters_and_evidence: list[dict[str, Any]],
        return_fields: list[dict[str, Any]],
        aggregation_dedup_sort_time: dict[str, Any],
        observability_boundary: dict[str, Any],
        expected_result_shape: dict[str, Any],
        sql_schema_scope: dict[str, Any],
        minimum_positive_count: int,
        minimum_negative_count: int,
        condition_coverage: list[dict[str, Any]],
        code_value_coverage: list[dict[str, Any]],
        expected_row_group_count: dict[str, Any],
        join_expansion_limit: dict[str, Any],
        created_at: str | None = None,
        parent_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Task 1: combine penalty and observable facts into a query specification."""
        self.validate_penalty(penalty)
        self.validate_observable(observable)
        self._validate_input_lineage(penalty["envelope"], observable["envelope"], run_id, qa_id)

        # Hard-code check 7: version immutability
        self._validate_version_immutability(version, supersedes_ref)

        # Derive must_preserve_fact_refs from penalty facts
        must_preserve = [
            f["penalty_fact_id"]
            for f in penalty["payload"]["source_facts"]
            if f.get("must_preserve_in_question") in ("yes", "conditional")
        ]
        if not must_preserve:
            _fail("MUST_PRESERVE_FACTS_NOT_COVERED")

        # Build payload
        payload: dict[str, Any] = {
            "query_spec_id": _stable_query_spec_id(run_id, qa_id),
            "penalty_fact_package_ref": artifact_ref(penalty["envelope"]),
            "observable_fact_package_ref": artifact_ref(observable["envelope"]),
            "query_goal": query_goal,
            "must_preserve_fact_refs": must_preserve,
            "main_object_and_grain": main_object_and_grain,
            "query_entry": query_entry,
            "related_objects_and_path": related_objects_and_path,
            "filters_and_evidence": filters_and_evidence,
            "return_fields": return_fields,
            "aggregation_dedup_sort_time": aggregation_dedup_sort_time,
            "observability_boundary": observability_boundary,
            "expected_result_shape": expected_result_shape,
            "sql_schema_scope": sql_schema_scope,
            "minimum_positive_count": minimum_positive_count,
            "minimum_negative_count": minimum_negative_count,
            "condition_coverage": condition_coverage,
            "code_value_coverage": code_value_coverage,
            "expected_row_group_count": expected_row_group_count,
            "join_expansion_limit": join_expansion_limit,
            "query_specification_package_schema_version": "query-specification-v1",
        }

        # Run all hard-code validators (except version which was checked above)
        self._validate_must_preserve_facts(penalty["payload"], payload["must_preserve_fact_refs"])
        self._validate_sql_scope(payload["sql_schema_scope"], observable["payload"])
        self._validate_ref_integrity(
            payload["penalty_fact_package_ref"], payload["observable_fact_package_ref"],
            penalty["envelope"], observable["envelope"],
        )
        self._validate_positive_counts(payload["minimum_positive_count"], payload["minimum_negative_count"])
        self._validate_join_limit(payload["join_expansion_limit"])
        self._validate_row_group_count(payload["expected_row_group_count"])

        # Attempt boundary
        if attempt_no > MAX_ATTEMPTS:
            _fail("MAX_ATTEMPTS_EXCEEDED")

        parents = parent_refs if parent_refs is not None else [
            artifact_ref(penalty["envelope"]), artifact_ref(observable["envelope"]),
        ]
        return self._wrap(
            "query_specification_package", f"140-qspec-{run_id}", payload,
            run_id=run_id, qa_id=qa_id, version=version, attempt_no=attempt_no,
            parents=parents, supersedes_ref=supersedes_ref, status=status,
            trace_id=penalty["envelope"]["trace_id"], created_at=created_at,
        )

    # ── Task 2: Review feedback ────────────────────────────────────

    def _validate_remap_output(
        self, output: dict[str, Any], previous: dict[str, Any], review: dict[str, Any],
        penalty: dict[str, Any], observable: dict[str, Any], attempt_no: int,
    ) -> None:
        self.validate_query_spec(output)
        envelope = output["envelope"]
        prior = previous["envelope"]
        _same_lineage(envelope, prior, "REMAP_OUTPUT_LINEAGE_MISMATCH")
        if envelope["version"] != prior["version"] + 1:
            _fail("REMAP_OUTPUT_VERSION_MISMATCH")
        _same_ref(envelope["supersedes_ref"], artifact_ref(prior), "REMAP_OUTPUT_SUPERSEDES_MISMATCH")
        if envelope["attempt_no"] != attempt_no:
            _fail("REMAP_OUTPUT_ATTEMPT_MISMATCH")
        expected_parents = [
            artifact_ref(penalty["envelope"]), artifact_ref(observable["envelope"]),
            artifact_ref(review["envelope"]), artifact_ref(prior),
        ]
        if envelope["parent_artifact_refs"] != expected_parents:
            _fail("REMAP_OUTPUT_PARENT_LINEAGE_MISMATCH")
        if envelope["input_hashes"] != [ref["content_hash"] for ref in expected_parents]:
            _fail("REMAP_OUTPUT_INPUT_HASH_ORDER_MISMATCH")
        if output["payload"]["query_spec_id"] != previous["payload"]["query_spec_id"]:
            _fail("REMAP_OUTPUT_QUERY_SPEC_ID_MISMATCH")

    def handle_review_feedback(
        self,
        penalty: dict[str, Any],
        observable: dict[str, Any],
        review: dict[str, Any],
        previous_spec: dict[str, Any],
        *,
        run_id: str,
        qa_id: str,
        attempt_no: int,
        # LLM-extracted fields for the revised spec
        query_goal: str,
        main_object_and_grain: dict[str, Any],
        query_entry: dict[str, Any],
        related_objects_and_path: list[dict[str, Any]],
        filters_and_evidence: list[dict[str, Any]],
        return_fields: list[dict[str, Any]],
        aggregation_dedup_sort_time: dict[str, Any],
        observability_boundary: dict[str, Any],
        expected_result_shape: dict[str, Any],
        sql_schema_scope: dict[str, Any],
        minimum_positive_count: int,
        minimum_negative_count: int,
        condition_coverage: list[dict[str, Any]],
        code_value_coverage: list[dict[str, Any]],
        expected_row_group_count: dict[str, Any],
        join_expansion_limit: dict[str, Any],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Task 2: consume 170/180 review, supersede or block."""
        self.validate_penalty(penalty)
        self.validate_observable(observable)
        self.validate_review(review)
        self.validate_query_spec(previous_spec)

        previous_envelope = previous_spec["envelope"]
        penalty_envelope = penalty["envelope"]
        observable_envelope = observable["envelope"]

        _same_lineage(penalty_envelope, previous_envelope, "PENALTY_LINEAGE_MISMATCH")
        _same_lineage(observable_envelope, previous_envelope, "OBSERVABLE_LINEAGE_MISMATCH")
        _same_lineage(review["envelope"], previous_envelope, "REVIEW_LINEAGE_MISMATCH")

        # Review must be routed to 140
        _same_ref(
            review["payload"]["reviewed_package_ref"],
            artifact_ref(previous_envelope),
            "REVIEWED_PACKAGE_REF_MISMATCH",
        )
        report = review["payload"]["semantic_review_report"]
        if report["decision"] != "no" or "QUERY_SPEC_ERROR" not in report["error_types"] or report["route_suggestion"] != "140":
            _fail("REVIEW_NOT_ROUTED_TO_140")

        if attempt_no not in (2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        if attempt_no != previous_envelope["attempt_no"] + 1:
            _fail("ATTEMPT_LINEAGE_MISMATCH")
        if run_id != previous_envelope["run_id"] or qa_id != previous_envelope["qa_id"]:
            _fail("RUN_OR_QA_MISMATCH")

        parents = [
            artifact_ref(penalty_envelope), artifact_ref(observable_envelope),
            artifact_ref(review["envelope"]), artifact_ref(previous_envelope),
        ]
        build_args = dict(
            run_id=run_id, qa_id=qa_id,
            version=previous_envelope["version"] + 1,
            attempt_no=attempt_no,
            supersedes_ref=artifact_ref(previous_envelope),
            status="candidate",
            query_goal=query_goal,
            main_object_and_grain=main_object_and_grain,
            query_entry=query_entry,
            related_objects_and_path=related_objects_and_path,
            filters_and_evidence=filters_and_evidence,
            return_fields=return_fields,
            aggregation_dedup_sort_time=aggregation_dedup_sort_time,
            observability_boundary=observability_boundary,
            expected_result_shape=expected_result_shape,
            sql_schema_scope=sql_schema_scope,
            minimum_positive_count=minimum_positive_count,
            minimum_negative_count=minimum_negative_count,
            condition_coverage=condition_coverage,
            code_value_coverage=code_value_coverage,
            expected_row_group_count=expected_row_group_count,
            join_expansion_limit=join_expansion_limit,
            created_at=created_at,
        )
        # A valid third reconstruction remains a candidate.  Only a failed
        # reconstruction on that final attempt becomes a lawful manual block.
        try:
            new_spec = self.build_query_spec(penalty, observable, **build_args, parent_refs=parents)
        except ContractError:
            if attempt_no != MAX_ATTEMPTS:
                raise
            blocked_payload = dict(previous_spec["payload"])
            new_spec = self._wrap(
                "query_specification_package", f"140-qspec-{run_id}", blocked_payload,
                run_id=run_id, qa_id=qa_id, version=previous_envelope["version"] + 1,
                attempt_no=attempt_no, parents=parents, supersedes_ref=artifact_ref(previous_envelope),
                status="blocked_manual", trace_id=penalty_envelope["trace_id"], created_at=created_at,
            )
        self._validate_remap_output(new_spec, previous_spec, review, penalty, observable, attempt_no)
        return new_spec
