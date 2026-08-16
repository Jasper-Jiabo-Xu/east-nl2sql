"""Contract boundary for 150-Codex question-SQL main generator."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

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
        if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?\*", statement, re.I):
            _fail("SQL_SELECT_STAR_FORBIDDEN")
        if re.search(r"\b(CURRENT_DATE|CURRENT_TIME|CURRENT_TIMESTAMP|DATE\s*\(\s*['\"]now|DATETIME\s*\(\s*['\"]now)", statement, re.I):
            _fail("SQL_DYNAMIC_TIME_FORBIDDEN")
        allowed = {item["table_id"]: set(item["allowed_fields"]) for item in scope["allowed_tables"]}
        ctes = set(re.findall(r"\b(?:WITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", statement, re.I))
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", statement, re.I))
        if not tables or not (tables - ctes) <= set(allowed):
            _fail("SQL_TABLE_OUT_OF_SCOPE")
        aliases = {table: table for table in allowed}
        aliases.update({alias: table for table, alias in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)\s+(?:AS\s+)?([A-Za-z_]\w*)", statement, re.I) if table in allowed})
        # A CTE or derived table can only expose explicitly selected columns.
        # It never opens arbitrary base-table fields to its outer scope.
        def output_columns(body: str) -> set[str]:
            selected = re.search(r"\bSELECT\s+(.*?)\s+\bFROM\b", body, re.I | re.S)
            if not selected:
                return set()
            exposed: set[str] = set()
            for expression in selected.group(1).split(","):
                expression = expression.strip()
                alias = re.search(r"\bAS\s+([A-Za-z_]\w*)\s*$", expression, re.I)
                qualified = re.search(r"\b[A-Za-z_]\w*\.([A-Za-z_]\w*)\s*$", expression)
                bare = re.fullmatch(r"[A-Za-z_]\w*", expression)
                if alias:
                    exposed.add(alias.group(1))
                elif qualified:
                    exposed.add(qualified.group(1))
                elif bare:
                    exposed.add(expression)
            return exposed

        cte_columns: dict[str, set[str]] = {}
        for name, body in re.findall(r"(?:\bWITH|,)\s*([A-Za-z_]\w*)\s+AS\s*\((.*?)\)\s*(?:,|SELECT)", statement, re.I | re.S):
            cte_columns[name] = output_columns(body)
        for body, alias in re.findall(r"\b(?:FROM|JOIN)\s*\(\s*(SELECT.*?)\)\s+(?:AS\s+)?([A-Za-z_]\w*)", statement, re.I | re.S):
            cte_columns[alias] = output_columns(body)
        aliases.update({name: name for name in cte_columns})
        for table, field in re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)", statement):
            if table not in aliases:
                _fail("SQL_QUALIFIER_OUT_OF_SCOPE")
            if aliases[table] in allowed and field not in allowed[aliases[table]]:
                _fail("SQL_FIELD_OUT_OF_SCOPE")
            if aliases[table] not in allowed and (table not in cte_columns or field not in cte_columns[table]):
                _fail("SQL_FIELD_OUT_OF_SCOPE")
        # SQLite permits unqualified columns, but 150 may only use one that is
        # present in the approved scope.  Strip literals and qualified tokens
        # before inspecting identifiers so aliases/parameters do not count.
        scrubbed = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", statement)
        scrubbed = re.sub(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*\b", " ", scrubbed)
        scrubbed = re.sub(r"\bAS\s+[A-Za-z_]\w*\b", " ", scrubbed, flags=re.I)
        keywords = {"SELECT", "FROM", "WHERE", "JOIN", "ON", "AS", "WITH", "GROUP", "BY", "ORDER", "LIMIT", "DISTINCT", "COUNT", "SUM", "AVG", "MIN", "MAX", "IN", "AND", "OR", "NOT", "NULL", "ASC", "DESC", "HAVING", "CASE", "WHEN", "THEN", "ELSE", "END"}
        visible_sources = [allowed[table] for table in tables - ctes if table in allowed]
        visible_sources.extend(cte_columns[table] for table in tables & ctes if table in cte_columns)
        known = set().union(*visible_sources) if visible_sources else set()
        ignored = keywords | set(allowed) | ctes | set(aliases) | {"v"}
        for token in re.findall(r"\b[A-Za-z_]\w*\b", scrubbed):
            if token.upper() in keywords or token in ignored:
                continue
            matches = sum(token in fields for fields in visible_sources)
            if matches == 0:
                _fail("SQL_UNQUALIFIED_FIELD_OUT_OF_SCOPE")
            if matches > 1:
                _fail("SQL_UNQUALIFIED_FIELD_AMBIGUOUS")

    @staticmethod
    def _mapping(mapping: list[dict[str, str]]) -> list[dict[str, str]]:
        if len(mapping) != len(MAPPED_SPEC_ITEMS) or {item["spec_item"] for item in mapping} != set(MAPPED_SPEC_ITEMS):
            _fail("SPECIFICATION_MAPPING_INCOMPLETE")
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
        mapping = self._mapping(specification_mapping)
        for item in mapping:
            if not item.get("question_fragment") or not item.get("sql_fragment") or item["sql_fragment"] not in sql_gold:
                _fail("SPECIFICATION_MAPPING_FRAGMENT_INVALID")
        payload = {
            "candidate_id": f"150-{run_id}-{qa_id}", "query_spec_ref": artifact_ref(spec_envelope),
            "penalty_fact_package_ref": spec["penalty_fact_package_ref"],
            "observable_fact_package_ref": spec["observable_fact_package_ref"],
            "clear_question": question, "sql_gold": sql_gold,
            "sql_explanation": sql_explanation,
            "business_event_candidates": business_event_candidates,
            "specification_mapping": mapping,
            "evidence_refs": [f"penalty:{spec['penalty_fact_package_ref']['artifact_id']}", f"observable:{spec['observable_fact_package_ref']['artifact_id']}"],
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

    @staticmethod
    def _has_110_route(envelope: dict[str, Any]) -> bool:
        return any(ref["artifact_id"].startswith("110-") for ref in envelope["parent_artifact_refs"])

    @staticmethod
    def _has_010_to_110_route(envelope: dict[str, Any]) -> bool:
        ids = {ref["artifact_id"].split("-", 1)[0] for ref in envelope["parent_artifact_refs"]}
        return {"010", "110"} <= ids

    @staticmethod
    def _validate_regression_payload(payload: dict[str, Any]) -> None:
        required = {"schema_version", "mode", "input_data_refs", "input_orm_ref", "sandbox_snapshot_id", "failure_details", "route_target", "retry_count"}
        if set(payload) != required:
            _fail("REGRESSION_SCHEMA_REJECTED")
        details = payload["failure_details"]
        detail_required = {"error_code", "error_stage", "error_location", "expected_values", "actual_values", "sql_error_detail", "regression_metrics"}
        if not isinstance(details, dict) or set(details) != detail_required:
            _fail("REGRESSION_SCHEMA_REJECTED")
        if payload["schema_version"] != "v5.sql-regression-failed-feedback/v1" or payload["retry_count"] not in (1, 2, 3):
            _fail("REGRESSION_SCHEMA_REJECTED")

    def handle_routed_feedback(self, query_spec: dict[str, Any], feedback: dict[str, Any], previous: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        artifact_type = feedback.get("envelope", {}).get("artifact_type")
        if artifact_type in {"deepseek_review_result", "glm_review_result"}:
            self._validate(feedback, artifact_type)
            report = feedback["payload"]["semantic_review_report"]
            expected_producer = "170" if artifact_type == "deepseek_review_result" else "180"
            if feedback["envelope"]["producer_id"] != expected_producer or report["reviewer_id"] != expected_producer:
                _fail("REVIEW_PRODUCER_REJECTED")
            if not self._has_110_route(feedback["envelope"]):
                _fail("REVIEW_BYPASS_ROUTE_REJECTED")
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
            self._validate_regression_payload(feedback["payload"])
            if not self._has_010_to_110_route(feedback["envelope"]):
                _fail("REGRESSION_BYPASS_ROUTE_REJECTED")
            if feedback["payload"]["failure_details"]["error_code"] != "SQL_EXECUTION_ERROR" or feedback["payload"]["route_target"] != "110":
                _fail("REGRESSION_ROUTE_REJECTED")
        else:
            _fail("ROUTE_SOURCE_REJECTED")
        return self._retry(query_spec, feedback, previous, **kwargs)
