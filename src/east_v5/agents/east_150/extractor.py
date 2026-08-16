"""Contract boundary for 150-Codex question-SQL main generator."""
from __future__ import annotations

from datetime import datetime, timezone
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json

SCHEMAS = {
    "query_specification_package": "contracts/packages/query-specification-package.schema.json",
    "question_sql_pending_precheck": "contracts/packages/question-sql-pending-precheck-package.schema.json",
    "precheck_failed_feedback": "contracts/packages/precheck-failed-feedback-package.schema.json",
    "deepseek_review_result": "contracts/packages/deepseek-review-result.schema.json",
    "glm_review_result": "contracts/packages/glm-review-result.schema.json",
    "sql_regression_failed_feedback": "contracts/packages/sql-regression-failed-feedback.schema.json",
}
REVIEW_ERRORS = {"QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR", "QUESTION_FACT_OMISSION"}
MAPPED_SPEC_ITEMS = (
    "query_goal", "must_preserve_fact_refs", "main_object_and_grain", "query_entry", "related_objects_and_path",
    "filters_and_evidence", "return_fields", "aggregation_dedup_sort_time", "observability_boundary",
    "expected_result_shape", "sql_schema_scope", "minimum_positive_count", "minimum_negative_count",
    "condition_coverage", "code_value_coverage", "expected_row_group_count", "join_expansion_limit",
)


def _fail(code: str) -> None:
    raise ContractError(code)


_ROUTE_CONTEXT_TOKEN = object()


@dataclass(frozen=True)
class TrustedRouteContext:
    """Route evidence resolved from the immutable runtime artifact registry.

    Parent-reference names alone are untrusted caller data.  The runtime must
    resolve the relevant 010/110 envelopes from its registry first, then pass
    this context to 150.  Each feedback package is checked against those exact
    immutable references and its common lineage fields.
    """

    _token: object
    stage_110_ref: dict[str, Any]
    stage_110_lineage: tuple[str, str, str]
    stage_010_ref: dict[str, Any] | None = None
    stage_010_lineage: tuple[str, str, str] | None = None

    @classmethod
    def from_registry(
        cls, registry: Any, *, stage_110_ref: dict[str, Any], stage_010_ref: dict[str, Any] | None = None,
    ) -> "TrustedRouteContext":
        """Resolve route artifacts from ``ArtifactRegistry`` (or equivalent)."""
        def resolve(reference: dict[str, Any], producer: str) -> tuple[dict[str, Any], tuple[str, str, str]]:
            try:
                package = registry.resolve(reference)
                envelope, payload = package["envelope"], package["payload"]
                validate_envelope(Path(registry.repo_root), envelope, payload)
            except Exception as exc:
                raise ContractError("TRUSTED_ROUTE_CONTEXT_INVALID") from exc
            if envelope["producer_id"] != producer or artifact_ref(envelope) != reference:
                _fail("TRUSTED_ROUTE_CONTEXT_INVALID")
            return artifact_ref(envelope), (envelope["run_id"], envelope["qa_id"], envelope["trace_id"])

        trusted_110, lineage_110 = resolve(stage_110_ref, "110")
        if stage_010_ref is None:
            return cls(_ROUTE_CONTEXT_TOKEN, trusted_110, lineage_110)
        trusted_010, lineage_010 = resolve(stage_010_ref, "010")
        if lineage_010 != lineage_110:
            _fail("TRUSTED_ROUTE_CONTEXT_INVALID")
        return cls(_ROUTE_CONTEXT_TOKEN, trusted_110, lineage_110, trusted_010, lineage_010)

    def authorize(self, feedback_envelope: dict[str, Any], *, requires_010: bool) -> None:
        if self._token is not _ROUTE_CONTEXT_TOKEN:
            _fail("TRUSTED_ROUTE_CONTEXT_INVALID")
        lineage = (feedback_envelope["run_id"], feedback_envelope["qa_id"], feedback_envelope["trace_id"])
        refs = feedback_envelope["parent_artifact_refs"]
        if lineage != self.stage_110_lineage or self.stage_110_ref not in refs:
            _fail("TRUSTED_ROUTE_CONTEXT_REJECTED")
        if requires_010 and (self.stage_010_ref is None or self.stage_010_lineage != lineage or self.stage_010_ref not in refs):
            _fail("TRUSTED_ROUTE_CONTEXT_REJECTED")


class PendingPrecheckBuilder:
    """Builds 150 candidates and immutable retry routes; never implements 160."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validate(self, package: dict[str, Any], artifact_type: str) -> None:
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != artifact_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        try:
            Draft202012Validator(load_json(self.repo_root / SCHEMAS[artifact_type])).validate(payload)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{artifact_type}") from exc

    def validate_query_spec(self, package: dict[str, Any]) -> None:
        self._validate(package, "query_specification_package")

    def validate_pending_precheck(self, package: dict[str, Any]) -> None:
        self._validate(package, "question_sql_pending_precheck")

    def validate_precheck_feedback(self, package: dict[str, Any]) -> None:
        self._validate(package, "precheck_failed_feedback")

    @staticmethod
    def _validate_sql(sql: str, scope: dict[str, Any]) -> None:
        statement = sql.strip()
        if not statement:
            _fail("CANDIDATE_SQL_EMPTY")
        if not re.match(r"^(SELECT|WITH)\b", statement, flags=re.I):
            _fail("SQL_NOT_READ_ONLY")
        if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM)\b", statement, re.I):
            _fail("SQL_NOT_READ_ONLY")
        if ";" in statement[:-1] or statement.count(";") > 1:
            _fail("SQL_MULTIPLE_STATEMENTS")
        if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?(?:[A-Za-z_]\w*\.)?\*", statement, re.I):
            _fail("SQL_SELECT_STAR_FORBIDDEN")
        if re.search(r"\b(CURRENT_DATE|CURRENT_TIME|CURRENT_TIMESTAMP|DATE\s*\(\s*['\"]now|DATETIME\s*\(\s*['\"]now)", statement, re.I):
            _fail("SQL_DYNAMIC_TIME_FORBIDDEN")
        allowed = {item["table_id"]: set(item["allowed_fields"]) for item in scope["allowed_tables"]}
        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        if not allowed or any(not identifier.fullmatch(table) or not fields or any(not identifier.fullmatch(field) for field in fields) for table, fields in allowed.items()):
            _fail("SQL_SCOPE_INVALID")
        # SQLite's own compiler is the scope parser.  The in-memory schema has
        # exactly the frozen tables/columns, therefore EXPLAIN rejects unknown
        # aliases, unqualified ambiguity, CTE leakage, derived-table leakage,
        # and nested-subquery scope errors without executing candidate SQL.
        connection = sqlite3.connect(":memory:")
        try:
            for table, fields in allowed.items():
                quoted_table = '"' + table.replace('"', '""') + '"'
                columns = ", ".join('"' + field.replace('"', '""') + '"' for field in sorted(fields))
                connection.execute(f"CREATE TABLE {quoted_table} ({columns})")

            def authorizer(action: int, _arg1: str | None, _arg2: str | None, _database: str | None, _source: str | None) -> int:
                permitted = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
                return sqlite3.SQLITE_OK if action in permitted else sqlite3.SQLITE_DENY

            connection.set_authorizer(authorizer)
            parameters = re.findall(r"(?<!:)([:@$][A-Za-z_]\w*)", statement)
            if parameters:
                connection.execute("EXPLAIN " + statement, {parameter[1:]: None for parameter in parameters}).fetchall()
            else:
                connection.execute("EXPLAIN " + statement, tuple(None for _ in re.findall(r"\?", statement))).fetchall()
        except sqlite3.DatabaseError as exc:
            detail = str(exc).lower()
            if "ambiguous column name" in detail:
                _fail("SQL_UNQUALIFIED_FIELD_AMBIGUOUS")
            if "no such table" in detail:
                _fail("SQL_TABLE_OUT_OF_SCOPE")
            missing = re.search(r"no such column:\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)", detail)
            if missing:
                name = missing.group(1)
                if "." not in name:
                    _fail("SQL_UNQUALIFIED_FIELD_OUT_OF_SCOPE")
                qualifier = name.split(".", 1)[0]
                aliases = {name.lower() for name in set(allowed) | set(re.findall(r"\b(?:FROM|JOIN)\s+[A-Za-z_]\w*\s+(?:AS\s+)?([A-Za-z_]\w*)", statement, re.I)) | set(re.findall(r"\b(?:WITH|,)\s*([A-Za-z_]\w*)\s+AS\s*\(", statement, re.I))}
                _fail("SQL_FIELD_OUT_OF_SCOPE" if qualifier in aliases else "SQL_QUALIFIER_OUT_OF_SCOPE")
            _fail("SQL_PARSE_REJECTED")
        finally:
            connection.close()

    @staticmethod
    def _mapping(mapping: list[dict[str, str]], question: str, sql: str) -> list[dict[str, str]]:
        if not isinstance(mapping, list) or len(mapping) != len(MAPPED_SPEC_ITEMS) or any(not isinstance(item, dict) or set(item) != {"spec_item", "question_fragment", "sql_fragment"} for item in mapping):
            _fail("SPECIFICATION_MAPPING_INCOMPLETE")
        if {item["spec_item"] for item in mapping} != set(MAPPED_SPEC_ITEMS):
            _fail("SPECIFICATION_MAPPING_INCOMPLETE")
        if any(not isinstance(item["question_fragment"], str) or not isinstance(item["sql_fragment"], str) or not item["question_fragment"] or not item["sql_fragment"] or item["question_fragment"] not in question or item["sql_fragment"] not in sql for item in mapping):
            _fail("SPECIFICATION_MAPPING_FRAGMENT_INVALID")
        return mapping

    def build_pending_precheck(
        self, query_spec: dict[str, Any], *, run_id: str, qa_id: str, sql_gold: str,
        clear_question: str | None = None, sql_explanation: dict[str, str] | None = None,
        business_event_candidates: list[dict[str, Any]] | None = None, specification_mapping: list[dict[str, str]] | None = None, version: int = 1, attempt_no: int = 1,
        supersedes_ref: dict[str, Any] | None = None, parents: list[dict[str, Any]] | None = None,
        status: str = "candidate", created_at: str | None = None,
    ) -> dict[str, Any]:
        self.validate_query_spec(query_spec)
        spec_envelope, spec = query_spec["envelope"], query_spec["payload"]
        if (run_id, qa_id) != (spec_envelope["run_id"], spec_envelope["qa_id"]):
            _fail("RUN_OR_QA_MISMATCH")
        if attempt_no not in (1, 2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        if (version == 1) != (supersedes_ref is None):
            _fail("VERSION_OVERWRITE_ATTEMPTED")
        self._validate_sql(sql_gold, spec["sql_schema_scope"])
        if clear_question is None or sql_explanation is None or business_event_candidates is None or specification_mapping is None:
            _fail("GENERATED_FIELDS_REQUIRED")
        question = clear_question
        mapping = self._mapping(specification_mapping, question, sql_gold)
        payload = {
            "candidate_id": f"150-{run_id}-{qa_id}", "query_spec_ref": artifact_ref(spec_envelope),
            "penalty_fact_package_ref": spec["penalty_fact_package_ref"],
            "observable_fact_package_ref": spec["observable_fact_package_ref"],
            "clear_question": question, "sql_gold": sql_gold,
            "sql_explanation": sql_explanation,
            "business_event_candidates": business_event_candidates,
            "specification_mapping": mapping,
            "evidence_refs": [f"penalty:{spec['penalty_fact_package_ref']['artifact_id']}", f"observable:{spec['observable_fact_package_ref']['artifact_id']}"] + [item["evidence_ref"] for item in spec["filters_and_evidence"] if item.get("evidence_ref")],
            "sql_dialect": "sqlite",
        }
        parent_refs = list(parents or [artifact_ref(spec_envelope)])
        envelope = {
            "artifact_id": f"150-question-sql-{run_id}", "artifact_type": "question_sql_pending_precheck", "run_id": run_id, "qa_id": qa_id,
            "version": version, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": supersedes_ref,
            "attempt_no": attempt_no, "producer_id": "150", "parent_artifact_refs": parent_refs, "input_hashes": [item["content_hash"] for item in parent_refs],
            "status": status, "mode": "question_sql", "created_at": created_at or datetime.now(timezone.utc).isoformat(), "trace_id": spec_envelope["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self.validate_pending_precheck(package)
        return package

    def _retry(self, query_spec: dict[str, Any], feedback: dict[str, Any], previous: dict[str, Any], *, run_id: str, qa_id: str, attempt_no: int, sql_gold: str, created_at: str | None, **candidate: Any) -> dict[str, Any]:
        self.validate_pending_precheck(previous)
        previous_envelope = previous["envelope"]
        if attempt_no not in (2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        if attempt_no != previous_envelope["attempt_no"] + 1:
            _fail("ATTEMPT_LINEAGE_MISMATCH")
        if (run_id, qa_id) != (previous_envelope["run_id"], previous_envelope["qa_id"]):
            _fail("RUN_OR_QA_MISMATCH")
        for key in ("run_id", "qa_id", "trace_id"):
            if feedback["envelope"][key] != previous_envelope[key]:
                _fail("FEEDBACK_LINEAGE_MISMATCH")
        parents = [artifact_ref(query_spec["envelope"]), artifact_ref(feedback["envelope"]), artifact_ref(previous_envelope)]
        kwargs = dict(run_id=run_id, qa_id=qa_id, sql_gold=sql_gold, version=previous_envelope["version"] + 1, attempt_no=attempt_no, supersedes_ref=artifact_ref(previous_envelope), parents=parents, created_at=created_at, **candidate)
        try:
            return self.build_pending_precheck(query_spec, **kwargs)
        except ContractError:
            if attempt_no != 3:
                raise
            previous_payload = previous["payload"]
            return self.build_pending_precheck(query_spec, **{**kwargs, "sql_gold": previous_payload["sql_gold"], "clear_question": previous_payload["clear_question"], "sql_explanation": previous_payload["sql_explanation"], "business_event_candidates": previous_payload["business_event_candidates"], "specification_mapping": previous_payload["specification_mapping"], "status": "blocked_manual"})

    def handle_precheck_feedback(self, query_spec: dict[str, Any], feedback: dict[str, Any], previous: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.validate_precheck_feedback(feedback)
        if feedback["envelope"]["producer_id"] != "160":
            _fail("PRECHECK_PRODUCER_REJECTED")
        if feedback["payload"]["candidate_ref"] != artifact_ref(previous["envelope"]):
            _fail("FEEDBACK_REF_MISMATCH")
        return self._retry(query_spec, feedback, previous, **kwargs)

    def _validate_regression_package(self, package: dict[str, Any]) -> None:
        try:
            schema = load_json(self.repo_root / SCHEMAS["sql_regression_failed_feedback"])
            resources = []
            for path in [self.repo_root / "contracts/common/common-envelope.schema.json", *sorted((self.repo_root / "contracts/packages").glob("*.schema.json"))]:
                item = load_json(path)
                resources.append((item["$id"], Resource.from_contents(item)))
            Draft202012Validator(schema, registry=Registry().with_resources(resources)).validate(package)
        except Exception as exc:
            raise ContractError("REGRESSION_SCHEMA_REJECTED") from exc

    def handle_routed_feedback(
        self, query_spec: dict[str, Any], feedback: dict[str, Any], previous: dict[str, Any], *, route_context: TrustedRouteContext | None, **kwargs: Any,
    ) -> dict[str, Any]:
        artifact_type = feedback.get("envelope", {}).get("artifact_type")
        if artifact_type in {"deepseek_review_result", "glm_review_result"}:
            self._validate(feedback, artifact_type)
            report = feedback["payload"]["semantic_review_report"]
            expected_producer = "170" if artifact_type == "deepseek_review_result" else "180"
            if feedback["envelope"]["producer_id"] != expected_producer or report["reviewer_id"] != expected_producer:
                _fail("REVIEW_PRODUCER_REJECTED")
            if route_context is None:
                _fail("TRUSTED_ROUTE_CONTEXT_REQUIRED")
            route_context.authorize(feedback["envelope"], requires_010=False)
            if report["decision"] != "no" or report["route_suggestion"] != "150" or not REVIEW_ERRORS.intersection(report["error_types"]):
                _fail("REVIEW_ROUTE_REJECTED")
            if feedback["payload"]["reviewed_package_ref"] != artifact_ref(previous["envelope"]):
                _fail("REVIEW_REF_MISMATCH")
        elif artifact_type == "sql_regression_failed_feedback":
            if not isinstance(feedback, dict) or set(feedback) != {"envelope", "payload"}:
                _fail("TRANSPORT_PACKAGE_INVALID")
            validate_envelope(self.repo_root, feedback["envelope"], feedback["payload"])
            if feedback["envelope"]["producer_id"] != "260":
                _fail("REGRESSION_ROUTE_REJECTED")
            self._validate_regression_package(feedback)
            if route_context is None:
                _fail("TRUSTED_ROUTE_CONTEXT_REQUIRED")
            route_context.authorize(feedback["envelope"], requires_010=True)
            if feedback["payload"]["failure_details"]["error_code"] != "SQL_EXECUTION_ERROR" or feedback["payload"]["route_target"] != "110":
                _fail("REGRESSION_ROUTE_REJECTED")
        else:
            _fail("ROUTE_SOURCE_REJECTED")
        return self._retry(query_spec, feedback, previous, **kwargs)
