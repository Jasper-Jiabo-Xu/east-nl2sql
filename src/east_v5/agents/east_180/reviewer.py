"""180: GLM semantic reviewer of frozen dual-review packages.

180 independently reviews the same frozen ``question_sql_pending_dual_review``
package that 170 reviews.  It returns a ``glm_review_result`` with
``reviewer_id=180``, a yes/no decision, structured error types, evidence, and
a route suggestion pointing at the most relevant repair agent (120/130/140/150).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json, sha256

REVIEWER_ID = "180"

# Six fixed error types per the frozen contract.
ERROR_TYPES = frozenset({
    "FACT_PACKAGE_ERROR",
    "OBSERVABLE_MAPPING_ERROR",
    "QUERY_SPEC_ERROR",
    "QUESTION_SQL_ERROR",
    "BUSINESS_EVENT_ERROR",
    "QUESTION_FACT_OMISSION",
})

# Valid route suggestion targets (repair agents).
ROUTE_SUGGESTIONS = frozenset({"120", "130", "140", "150"})

SCHEMAS = {
    "question_sql_pending_dual_review": "contracts/packages/question-sql-pending-dual-review-package.schema.json",
    "glm_review_result": "contracts/packages/glm-review-result.schema.json",
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _is_ref(value: Any) -> bool:
    """Check if a value is a valid artifact reference (artifact_id+version+content_hash)."""
    if not isinstance(value, dict) or set(value) != {"artifact_id", "version", "content_hash"}:
        return False
    if not isinstance(value["artifact_id"], str) or not value["artifact_id"]:
        return False
    if not isinstance(value["version"], int) or isinstance(value["version"], bool) or value["version"] < 1:
        return False
    return isinstance(value["content_hash"], str) and re.fullmatch(r"[0-9a-f]{64}", value["content_hash"]) is not None


def _validate_error_types(error_types: list[str]) -> None:
    """Validate that all error types are from the fixed six-type enumeration."""
    if not isinstance(error_types, list):
        _fail("ERROR_TYPES_NOT_LIST")
    for et in error_types:
        if et not in ERROR_TYPES:
            _fail(f"ERROR_TYPE_INVALID:{et}")


def _validate_route_suggestion(route_suggestion: str) -> None:
    """Validate that route_suggestion is one of the four repair agent IDs."""
    if route_suggestion not in ROUTE_SUGGESTIONS:
        _fail(f"ROUTE_SUGGESTION_INVALID:{route_suggestion}")


def _compute_package_hash(payload: dict[str, Any]) -> str:
    """Re-compute the package_hash from payload excluding the package_hash field itself."""
    return sha256({key: value for key, value in payload.items() if key != "package_hash"})


class GLMReviewerAgent:
    """180-GLM审核员: independent semantic review of a frozen dual-review package.

    This agent validates the input package, performs semantic review (delegated
    to a GLM model in production, but deterministic in this implementation for
    contract testing), and produces a ``glm_review_result`` output package.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _schema_registry(self) -> Registry:
        """Build a JSON Schema registry with all package schemas."""
        resources = []
        for path in [
            self.repo_root / "contracts/common/common-envelope.schema.json",
            *sorted((self.repo_root / "contracts/packages").glob("*.schema.json")),
        ]:
            item = load_json(path)
            resources.append((item["$id"], Resource.from_contents(item)))
        return Registry().with_resources(resources)

    def _validate_package(self, package: dict[str, Any], artifact_type: str, expected_producer: str) -> None:
        """Validate transport structure, envelope, and payload schema."""
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != artifact_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        if envelope["producer_id"] != expected_producer:
            _fail("DUAL_REVIEW_PRODUCER_REJECTED")
        try:
            schema = load_json(self.repo_root / SCHEMAS[artifact_type])
            Draft202012Validator(schema, registry=self._schema_registry()).validate(payload)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{artifact_type}") from exc

    def validate_input(self, package: dict[str, Any]) -> None:
        """Validate a question_sql_pending_dual_review input package."""
        self._validate_package(package, "question_sql_pending_dual_review", "160")
        payload = package["payload"]
        # Verify package_hash integrity
        if payload["package_hash"] != _compute_package_hash(payload):
            _fail("PACKAGE_HASH_DRIFT")
        # Verify review_round matches attempt_no
        if payload["review_round"] != package["envelope"]["attempt_no"]:
            _fail("REVIEW_ROUND_MISMATCH")
        # Verify precheck_report decision is pass
        if payload["precheck_report"]["decision"] != "pass":
            _fail("PRECHECK_REPORT_NOT_PASS")

    def validate_output(self, package: dict[str, Any]) -> None:
        """Validate a glm_review_result output package."""
        self._validate_package(package, "glm_review_result", REVIEWER_ID)
        payload = package["payload"]
        report = payload["semantic_review_report"]
        # Verify reviewer_id is fixed at 180
        if report["reviewer_id"] != REVIEWER_ID:
            _fail("REVIEWER_ID_MISMATCH")
        # Verify error types are from the fixed enumeration
        _validate_error_types(report["error_types"])
        # Verify route_suggestion is valid
        _validate_route_suggestion(report["route_suggestion"])
        # Verify decision consistency: yes → empty error_types; no → non-empty error_types
        if report["decision"] == "yes" and report["error_types"]:
            _fail("INCONSISTENT_PASS_WITH_ERRORS")
        if report["decision"] == "no" and not report["error_types"]:
            _fail("INCONSISTENT_FAIL_WITHOUT_ERRORS")

    def review(
        self,
        package: dict[str, Any],
        *,
        decision: str = "yes",
        error_types: list[str] | None = None,
        error_details: list[dict[str, Any]] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        route_suggestion: str = "150",
        created_at: str | None = None,
        artifact_id: str | None = None,
        version: int | None = None,
        supersedes_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform semantic review and produce a glm_review_result package.

        In production, the GLM model determines decision/error_types/etc.
        This implementation accepts them as parameters for contract testing.
        """
        self.validate_input(package)
        envelope, payload = package["envelope"], package["payload"]

        # Apply hard-coded constraints
        if decision not in ("yes", "no"):
            _fail("DECISION_INVALID")
        if decision == "yes":
            error_types = []
            error_details = []
        else:
            if not error_types:
                _fail("FAIL_REQUIRES_ERROR_TYPES")
            _validate_error_types(error_types)
            if error_details is None:
                error_details = []
        _validate_route_suggestion(route_suggestion)

        if evidence_refs is None:
            evidence_refs = [{"source": "180-hardcode", "ref": "input_package_hash", "value": payload["package_hash"]}]

        report = {
            "reviewer_id": REVIEWER_ID,
            "decision": decision,
            "error_types": error_types,
            "error_details": error_details,
            "evidence_refs": evidence_refs,
            "route_suggestion": route_suggestion,
        }

        result_payload = {
            "reviewed_package_ref": artifact_ref(envelope),
            "semantic_review_report": report,
        }

        result_envelope = {
            "artifact_id": artifact_id or f"{envelope['artifact_id']}-glm-review",
            "artifact_type": "glm_review_result",
            "run_id": envelope["run_id"],
            "qa_id": envelope["qa_id"],
            "version": version if version is not None else envelope["version"],
            "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64,
            "supersedes_ref": supersedes_ref,
            "attempt_no": envelope["attempt_no"],
            "producer_id": REVIEWER_ID,
            "parent_artifact_refs": [artifact_ref(envelope)],
            "input_hashes": [envelope["content_hash"]],
            "status": "candidate",
            "mode": envelope["mode"],
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "trace_id": envelope["trace_id"],
            "storage_locator": None,
        }
        result_envelope["content_hash"] = content_hash(result_envelope, result_payload)
        result = {"envelope": result_envelope, "payload": result_payload}
        self.validate_output(result)
        return result


def consume_110_stub(repo_root: Path, result_package: dict[str, Any]) -> dict[str, str]:
    """Independent downstream consumer (110) of a glm_review_result package.

    110 reads the review result to route: pass → next stage; fail → repair agent.
    """
    agent = GLMReviewerAgent(repo_root)
    agent.validate_output(result_package)
    payload = result_package["payload"]
    report = payload["semantic_review_report"]
    return {
        "consumer": "110",
        "reviewer_id": report["reviewer_id"],
        "decision": report["decision"],
        "route_suggestion": report["route_suggestion"],
        "error_types": json.dumps(report["error_types"], sort_keys=True),
    }
