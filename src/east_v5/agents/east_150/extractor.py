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
    "query_goal", "must_preserve_fact_refs", "query_entry", "related_objects_and_path",
    "filters_and_evidence", "return_fields", "aggregation_dedup_sort_time",
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
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", statement, re.I))
        ctes = set(re.findall(r"\bWITH\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\b", statement, re.I))
        if not tables or not (tables - ctes) <= set(allowed):
            _fail("SQL_TABLE_OUT_OF_SCOPE")
        for table, field in re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)", statement):
            if table in allowed and field not in allowed[table]:
                _fail("SQL_FIELD_OUT_OF_SCOPE")

    @staticmethod
    def _mapping(question: str, sql: str) -> list[dict[str, str]]:
        mapping = [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS]
        if {item["spec_item"] for item in mapping} != set(MAPPED_SPEC_ITEMS):
            _fail("SPECIFICATION_MAPPING_INCOMPLETE")
        return mapping

    def build_pending_precheck(
        self, query_spec: dict[str, Any], *, run_id: str, qa_id: str, sql_gold: str,
        clear_question: str | None = None, version: int = 1, attempt_no: int = 1,
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
        question = clear_question or spec["query_goal"]
        payload = {
            "candidate_id": f"150-{run_id}-{qa_id}", "query_spec_ref": artifact_ref(spec_envelope),
            "penalty_fact_package_ref": spec["penalty_fact_package_ref"],
            "observable_fact_package_ref": spec["observable_fact_package_ref"],
            "clear_question": question, "sql_gold": sql_gold,
            "sql_explanation": {"select": "返回查询规格要求字段", "from_join": "按规格关联路径连接", "where": "按规格筛选与固定时间窗口", "aggregation": "按规格聚合去重", "sort": "按规格排序", "business_meaning": spec["query_goal"]},
            "business_event_candidates": [{"event_name": "query-specification-event", "objective": spec["query_goal"], "objects": [spec["main_object_and_grain"]["main_object"]], "state_changes": []}],
            "specification_mapping": self._mapping(question, sql_gold),
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

    def _retry(self, query_spec: dict[str, Any], feedback: dict[str, Any], previous: dict[str, Any], *, run_id: str, qa_id: str, attempt_no: int, sql_gold: str, created_at: str | None) -> dict[str, Any]:
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
        kwargs = dict(run_id=run_id, qa_id=qa_id, sql_gold=sql_gold, version=previous_envelope["version"] + 1, attempt_no=attempt_no, supersedes_ref=artifact_ref(previous_envelope), parents=parents, created_at=created_at)
        try:
            return self.build_pending_precheck(query_spec, **kwargs)
        except ContractError:
            if attempt_no != 3:
                raise
            return self.build_pending_precheck(query_spec, **{**kwargs, "sql_gold": previous["payload"]["sql_gold"], "status": "blocked_manual"})

    def handle_precheck_feedback(self, query_spec: dict[str, Any], feedback: dict[str, Any], previous: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.validate_precheck_feedback(feedback)
        if feedback["payload"]["candidate_ref"] != artifact_ref(previous["envelope"]):
            _fail("FEEDBACK_REF_MISMATCH")
        return self._retry(query_spec, feedback, previous, **kwargs)

    def handle_routed_feedback(self, query_spec: dict[str, Any], feedback: dict[str, Any], previous: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        artifact_type = feedback.get("envelope", {}).get("artifact_type")
        if artifact_type in {"deepseek_review_result", "glm_review_result"}:
            self._validate(feedback, artifact_type)
            report = feedback["payload"]["semantic_review_report"]
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
            if feedback["payload"]["failure_details"]["error_code"] != "SQL_EXECUTION_ERROR" or feedback["payload"]["route_target"] != "110":
                _fail("REGRESSION_ROUTE_REJECTED")
        else:
            _fail("ROUTE_SOURCE_REJECTED")
        return self._retry(query_spec, feedback, previous, **kwargs)
