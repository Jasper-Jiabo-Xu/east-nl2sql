"""Strict, side-effect-free orchestration for V5 question-SQL stage 110.

This module only validates immutable packages and emits dispatch intents.  A
runtime adapter is responsible for invoking agents/persisting artifacts; 110
never calls a model, mutates a review, opens a database, or starts data work
until both reviews have passed.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from .query_binding import resolve_declaration, validate_declarations
from east_v5.governance import ContractError, load_json, sha256

ERROR_ROUTE = {
    "FACT_PACKAGE_ERROR": "120",
    "OBSERVABLE_MAPPING_ERROR": "130",
    "QUERY_SPEC_ERROR": "140",
    "QUESTION_SQL_ERROR": "150",
    "BUSINESS_EVENT_ERROR": "150",
    "QUESTION_FACT_OMISSION": "150",
}
ROUTE_PRIORITY = ("120", "130", "140", "150")
_TRANSPORT_KEYS = {"envelope", "payload"}
_REF_KEYS = {"artifact_id", "version", "content_hash"}


def _fail(code: str) -> None:
    raise ContractError(code)


class QuestionSqlStageScheduler:
    """110's fixed state machine: 010→120 and 160→{170,180}→{210|repair}."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _registry(self) -> Registry:
        paths = [
            self.repo_root / "contracts/common/common-envelope.schema.json",
            *sorted((self.repo_root / "contracts/packages").glob("*.schema.json")),
        ]
        return Registry().with_resources(
            [(load_json(path)["$id"], Resource.from_contents(load_json(path))) for path in paths]
        )

    def _validate(self, package: Any, schema: str, code: str) -> None:
        if not isinstance(package, dict) or set(package) != _TRANSPORT_KEYS:
            _fail(f"{code}:TRANSPORT")
        try:
            validate_envelope(self.repo_root, package["envelope"], package["payload"])
            Draft202012Validator(
                load_json(self.repo_root / "contracts/packages" / schema),
                registry=self._registry(), format_checker=FormatChecker(),
            ).validate(package)
        except (ValidationError, ContractError) as exc:
            raise ContractError(code) from exc

    @staticmethod
    def _ref(value: Any, code: str) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _REF_KEYS:
            _fail(code)
        return value

    def start_question_sql(self, source: dict[str, Any]) -> dict[str, Any]:
        """Forward exactly one 010 source package to 120; do not inspect content."""
        self._validate(source, "penalty-source-package.schema.json", "110_SOURCE_REJECTED")
        env = source["envelope"]
        if (env["artifact_type"], env["producer_id"], env["mode"]) != ("penalty_source_package", "010", "question_sql"):
            _fail("110_SOURCE_ROUTE_REJECTED")
        if env["status"] == "blocked_manual":
            _fail("110_SOURCE_BLOCKED")
        return {"target": "120", "kind": "penalty_fact_construction", "source_package_ref": artifact_ref(env)}

    def dispatch_dual_review(self, pending: dict[str, Any]) -> list[dict[str, Any]]:
        """Fan out one frozen 160 package without changing its identity."""
        review_170 = importlib.import_module("east_v5.agents.170.review").DeepSeekReviewAgent(self.repo_root)
        review_170.validate_dual_review(pending)
        env, body = pending["envelope"], pending["payload"]
        if env["status"] == "blocked_manual":
            _fail("110_PENDING_BLOCKED")
        if body["review_round"] != env["attempt_no"]:
            _fail("110_REVIEW_ROUND_MISMATCH")
        source_ref = artifact_ref(env)
        return [
            {"target": "170", "kind": "semantic_review", "reviewed_package_ref": source_ref, "package_hash": body["package_hash"], "review_round": body["review_round"]},
            {"target": "180", "kind": "semantic_review", "reviewed_package_ref": source_ref, "package_hash": body["package_hash"], "review_round": body["review_round"]},
        ]

    def _validate_reviews(self, pending: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        self.dispatch_dual_review(pending)
        if not isinstance(reviews, list) or len(reviews) != 2:
            _fail("110_REVIEW_SET_INCOMPLETE")
        review_170 = importlib.import_module("east_v5.agents.170.review").DeepSeekReviewAgent(self.repo_root)
        review_180 = importlib.import_module("east_v5.agents.east_180.reviewer").GLMReviewerAgent(self.repo_root)
        by_reviewer: dict[str, dict[str, Any]] = {}
        for package in reviews:
            if not isinstance(package, dict) or not isinstance(package.get("envelope"), dict):
                _fail("110_REVIEW_TRANSPORT_REJECTED")
            producer = package["envelope"].get("producer_id")
            try:
                if producer == "170":
                    review_170.validate_result(package)
                elif producer == "180":
                    review_180.validate_output(package)
                else:
                    _fail("110_REVIEW_PRODUCER_REJECTED")
            except ContractError as exc:
                raise ContractError("110_REVIEW_REJECTED") from exc
            report = package["payload"]["semantic_review_report"]
            reviewer_id = report["reviewer_id"]
            if reviewer_id != producer or reviewer_id in by_reviewer:
                _fail("110_REVIEWER_DUPLICATE_OR_MISMATCH")
            by_reviewer[reviewer_id] = package
        if set(by_reviewer) != {"170", "180"}:
            _fail("110_REVIEW_SET_INCOMPLETE")
        source_env, source_body = pending["envelope"], pending["payload"]
        source_ref = artifact_ref(source_env)
        for package in by_reviewer.values():
            env, body = package["envelope"], package["payload"]
            if body["reviewed_package_ref"] != source_ref or env["parent_artifact_refs"] != [source_ref]:
                _fail("110_REVIEW_SOURCE_REF_DRIFT")
            if (env["run_id"], env["qa_id"], env["trace_id"], env["attempt_no"], env["mode"]) != (
                source_env["run_id"], source_env["qa_id"], source_env["trace_id"], source_env["attempt_no"], source_env["mode"],
            ):
                _fail("110_REVIEW_CONTEXT_DRIFT")
            if source_body["review_round"] != source_env["attempt_no"]:
                _fail("110_REVIEW_ROUND_MISMATCH")
        return by_reviewer

    @staticmethod
    def _route(reports: list[dict[str, Any]]) -> str:
        errors = {item for report in reports for item in report["error_types"]}
        if not errors:
            _fail("110_REJECTION_WITHOUT_ERRORS")
        routes = {ERROR_ROUTE.get(item) for item in errors}
        if None in routes:
            _fail("110_UNKNOWN_ERROR_TYPE")
        return next(route for route in ROUTE_PRIORITY if route in routes)

    def collect_reviews(self, pending: dict[str, Any], reviews: list[dict[str, Any]], query_spec: dict[str, Any] | None = None, *, created_at: str | None = None) -> dict[str, Any]:
        """Join 170/180 deterministically; success alone can dispatch 210."""
        before = copy.deepcopy((pending, reviews))
        by_reviewer = self._validate_reviews(pending, reviews)
        reports = [by_reviewer["170"]["payload"]["semantic_review_report"], by_reviewer["180"]["payload"]["semantic_review_report"]]
        source_env, source_body = pending["envelope"], pending["payload"]
        if any(item["envelope"]["status"] == "blocked_manual" for item in by_reviewer.values()) or (source_env["attempt_no"] == 3 and any(report["decision"] == "no" for report in reports)):
            result: dict[str, Any] = {"target": "manual", "kind": "blocked_manual", "reason": "REVIEW_ATTEMPT_EXHAUSTED", "attempt_no": source_env["attempt_no"], "reviewed_package_ref": artifact_ref(source_env)}
        elif all(report["decision"] == "yes" for report in reports):
            approved = self._assemble_passed(pending, by_reviewer, created_at=created_at)
            if query_spec is None:
                _fail("110_QUERY_PARAMETER_BINDING_SOURCE_REQUIRED")
            binding = self.build_query_parameter_binding(approved, query_spec, created_at=created_at)
            result = {"target": "210", "kind": "question_sql_dual_review_passed", "approved_package": approved, "approved_package_ref": artifact_ref(approved["envelope"]), "query_parameter_binding": binding, "query_parameter_binding_ref": artifact_ref(binding["envelope"])}
        else:
            result = {"target": self._route(reports), "kind": "repair", "reason": "SEMANTIC_REVIEW_REJECTED", "attempt_no": source_env["attempt_no"], "reviewed_package_ref": artifact_ref(source_env), "reports": [by_reviewer["170"], by_reviewer["180"]], "package_hash": source_body["package_hash"]}
        if before != (pending, reviews):
            _fail("110_INPUT_MUTATED")
        return result

    def build_query_parameter_binding(self, approved: dict[str, Any], query_spec: dict[str, Any], *, created_at: str | None = None) -> dict[str, Any]:
        """110's sole production point for immutable query parameter values."""
        before = copy.deepcopy((approved, query_spec))
        self._validate(approved, "question-sql-dual-review-passed-package.schema.json", "110_BINDING_APPROVED_REJECTED")
        try:
            validate_envelope(self.repo_root, query_spec["envelope"], query_spec["payload"])
            Draft202012Validator(load_json(self.repo_root / "contracts/packages/query-specification-package.schema.json"), format_checker=FormatChecker()).validate(query_spec["payload"])
        except (KeyError, ValidationError, ContractError) as exc:
            raise ContractError("110_BINDING_QUERY_SPEC_REJECTED") from exc
        if (query_spec["envelope"].get("artifact_type"), query_spec["envelope"].get("producer_id"), query_spec["envelope"].get("mode")) != ("query_specification_package", "140", "question_sql"):
            _fail("110_BINDING_QUERY_SPEC_REJECTED")
        approved_env, approved_payload = approved["envelope"], approved["payload"]
        query_env = query_spec["envelope"]
        if approved_payload["query_specification_package"] != artifact_ref(query_env):
            _fail("110_BINDING_QUERY_SPEC_LINEAGE_REJECTED")
        if (approved_env["run_id"], approved_env["qa_id"], approved_env["trace_id"], approved_env["attempt_no"]) != (query_env["run_id"], query_env["qa_id"], query_env["trace_id"], query_env["attempt_no"]):
            _fail("110_BINDING_CONTEXT_DRIFT")
        candidate = approved_payload["candidate_content"]
        names = validate_declarations(candidate["sql_gold"], candidate["query_parameter_bindings"])
        parameters = [resolve_declaration(item, query_spec) for item in candidate["query_parameter_bindings"]]
        parameters.sort(key=lambda item: item["name"])
        if tuple(item["name"] for item in parameters) != names:
            _fail("110_BINDING_PARAMETER_SET_DRIFT")
        approved_ref, query_ref = artifact_ref(approved_env), artifact_ref(query_env)
        payload = {"schema_version": "v5.query-parameter-binding/v1", "source_question_sql_ref": approved_ref, "source_query_spec_ref": query_ref, "sql_hash": hashlib.sha256(candidate["sql_gold"].strip().encode("utf-8")).hexdigest(), "parameters": parameters, "binding_hash": "0" * 64}
        payload["binding_hash"] = sha256({key: value for key, value in payload.items() if key != "binding_hash"})
        envelope = {"artifact_id": f"110-query-parameter-binding-{approved_env['artifact_id']}", "artifact_type": "query_parameter_binding", "run_id": approved_env["run_id"], "qa_id": approved_env["qa_id"], "version": approved_env["version"], "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": approved_env["attempt_no"], "producer_id": "110", "parent_artifact_refs": [approved_ref, query_ref], "input_hashes": [approved_ref["content_hash"], query_ref["content_hash"]], "status": "validated", "mode": "event_data", "created_at": created_at or approved_env["created_at"], "trace_id": approved_env["trace_id"], "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self._validate(package, "query-parameter-binding-package.schema.json", "110_BINDING_OUTPUT_REJECTED")
        if before != (approved, query_spec): _fail("110_INPUT_MUTATED")
        return package

    def _assemble_passed(self, pending: dict[str, Any], reviews: dict[str, dict[str, Any]], *, created_at: str | None) -> dict[str, Any]:
        source_env, source = pending["envelope"], pending["payload"]
        for package in reviews.values():
            if package["payload"]["semantic_review_report"]["decision"] != "yes" or package["envelope"]["status"] != "candidate":
                _fail("110_APPROVAL_REQUIRES_DOUBLE_YES")
        parent_refs = [
            source["candidate_ref"],
            {"artifact_id": f"{source_env['artifact_id']}-precheck-report", "version": source_env["version"], "content_hash": source["precheck_report"]["report_hash"]},
            artifact_ref(reviews["170"]["envelope"]), artifact_ref(reviews["180"]["envelope"]),
        ]
        payload = {
            "schema_version": "v5.question-sql-dual-review-passed/v1", "candidate_ref": source["candidate_ref"],
            "candidate_content": source["candidate_content"], "query_specification_package": source["query_specification_package"],
            "penalty_fact_package": source["penalty_fact_package"], "observable_fact_package": source["observable_fact_package"],
            "constraint_evidence_summary": source["constraint_evidence_summary"], "precheck_report": {"decision": "pass", "report_hash": source["precheck_report"]["report_hash"]},
            "deepseek_review": {"decision": "pass", "issue_level": "none", "reason": "170 semantic review passed", "review_hash": reviews["170"]["envelope"]["content_hash"]},
            "glm_review": {"decision": "pass", "issue_level": "none", "reason": "180 semantic review passed", "review_hash": reviews["180"]["envelope"]["content_hash"]},
            "adjudication": {"decision": "pass", "report_hash": sha256({"pending": source["package_hash"], "reviews": [reviews["170"]["envelope"]["content_hash"], reviews["180"]["envelope"]["content_hash"]]} )},
            "review_round": source["review_round"], "package_hash": "0" * 64,
        }
        payload["package_hash"] = sha256({key: value for key, value in payload.items() if key != "package_hash"})
        envelope = {
            "artifact_id": f"{source_env['artifact_id']}-dual-review-passed", "artifact_type": "question_sql_dual_review_passed",
            "run_id": source_env["run_id"], "qa_id": source_env["qa_id"], "version": source_env["version"], "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
            "supersedes_ref": None, "attempt_no": source_env["attempt_no"], "producer_id": "110", "parent_artifact_refs": parent_refs,
            "input_hashes": [item["content_hash"] for item in parent_refs], "status": "validated", "mode": "event_data", "created_at": created_at or source_env["created_at"], "trace_id": source_env["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self._validate(package, "question-sql-dual-review-passed-package.schema.json", "110_APPROVED_OUTPUT_REJECTED")
        return package
