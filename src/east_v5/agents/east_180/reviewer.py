"""180 的 GLM 审核运行边界。

这里没有默认审核结论，也不接受调用方传入的 decision、错误类型或路由。
语义输入仅来自 ``GLMReviewClient`` 的完整 JSON；本模块负责校验、冻结，
并将不可解析的模型结果限定到三次重试与人工阻断。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json, sha256

REVIEWER_ID = "180"
MAX_MODEL_ATTEMPTS = 3
ERROR_TYPES = frozenset({
    "FACT_PACKAGE_ERROR", "OBSERVABLE_MAPPING_ERROR", "QUERY_SPEC_ERROR",
    "QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR", "QUESTION_FACT_OMISSION",
})
ROUTE_SUGGESTIONS = frozenset({"120", "130", "140", "150"})
ERROR_ROUTE = {
    "FACT_PACKAGE_ERROR": "120", "QUESTION_FACT_OMISSION": "120",
    "OBSERVABLE_MAPPING_ERROR": "130", "QUERY_SPEC_ERROR": "140",
    "QUESTION_SQL_ERROR": "150", "BUSINESS_EVENT_ERROR": "150",
}
# A complete review can legitimately contain defects owned by several repair
# layers.  110 still takes only one next step, so choose the most-upstream
# affected layer deterministically; never discard the lower-layer findings.
ROUTE_PRIORITY = ("120", "130", "140", "150")
REPORT_KEYS = {"reviewer_id", "decision", "error_types", "error_details", "evidence_refs", "route_suggestion"}
DETAIL_KEYS = {"error_type", "object", "location", "reason", "suggestion"}
EVIDENCE_KEYS = {"kind", "ref", "description"}
SCHEMAS = {
    "question_sql_pending_dual_review": "contracts/packages/question-sql-pending-dual-review-package.schema.json",
    "glm_review_result": "contracts/packages/glm-review-result.schema.json",
}


class GLMReviewClient(Protocol):
    """最小模型边界；实现必须返回 GLM 原始 JSON 文本。"""

    def review(self, request: dict[str, Any]) -> str: ...


def _fail(code: str) -> None:
    raise ContractError(code)


def _compute_package_hash(payload: dict[str, Any]) -> str:
    return sha256({key: value for key, value in payload.items() if key != "package_hash"})


def _valid_ref(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == {"artifact_id", "version", "content_hash"}
        and isinstance(value["artifact_id"], str) and bool(value["artifact_id"])
        and isinstance(value["version"], int) and not isinstance(value["version"], bool) and value["version"] >= 1
        and isinstance(value["content_hash"], str) and re.fullmatch(r"[0-9a-f]{64}", value["content_hash"]) is not None
    )


class GLMReviewerAgent:
    """从真实 GLM 结构化结果生成不可变 180 审核包。"""

    def __init__(self, repo_root: Path, model_client: GLMReviewClient | None = None):
        self.repo_root = repo_root.resolve()
        self.model_client = model_client

    def _schema_registry(self) -> Registry:
        resources = []
        for path in [self.repo_root / "contracts/common/common-envelope.schema.json", *sorted((self.repo_root / "contracts/packages").glob("*.schema.json"))]:
            item = load_json(path)
            resources.append((item["$id"], Resource.from_contents(item)))
        return Registry().with_resources(resources)

    def _validate_package(self, package: dict[str, Any], artifact_type: str, producer: str) -> None:
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != artifact_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        if envelope["producer_id"] != producer:
            _fail("DUAL_REVIEW_PRODUCER_REJECTED")
        try:
            Draft202012Validator(load_json(self.repo_root / SCHEMAS[artifact_type]), registry=self._schema_registry()).validate(payload)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{artifact_type}") from exc

    def validate_input(self, package: dict[str, Any]) -> None:
        self._validate_package(package, "question_sql_pending_dual_review", "160")
        envelope, payload = package["envelope"], package["payload"]
        if payload["package_hash"] != _compute_package_hash(payload):
            _fail("PACKAGE_HASH_DRIFT")
        if payload["review_round"] != envelope["attempt_no"]:
            _fail("REVIEW_ROUND_MISMATCH")
        if payload["precheck_report"]["decision"] != "pass":
            _fail("PRECHECK_REPORT_NOT_PASS")

    @staticmethod
    def _validate_report(report: Any, *, blocked_manual: bool) -> None:
        if not isinstance(report, dict) or set(report) != REPORT_KEYS:
            _fail("MODEL_REPORT_UNKNOWN_FIELD")
        if report["reviewer_id"] != REVIEWER_ID:
            _fail("REVIEWER_ID_MISMATCH")
        if report["decision"] not in {"yes", "no"}:
            _fail("DECISION_INVALID")
        if not isinstance(report["error_types"], list) or any(item not in ERROR_TYPES for item in report["error_types"]):
            _fail("ERROR_TYPE_INVALID")
        if len(set(report["error_types"])) != len(report["error_types"]):
            _fail("ERROR_TYPE_DUPLICATE")
        if report["route_suggestion"] not in ROUTE_SUGGESTIONS:
            _fail("ROUTE_SUGGESTION_INVALID")
        if not isinstance(report["error_details"], list) or not isinstance(report["evidence_refs"], list):
            _fail("MODEL_REPORT_COLLECTION_INVALID")
        for evidence in report["evidence_refs"]:
            if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KEYS or not all(isinstance(evidence[key], str) and evidence[key] for key in EVIDENCE_KEYS):
                _fail("EVIDENCE_REF_INVALID")

        if blocked_manual:
            if report["decision"] != "no" or report["error_types"] or len(report["error_details"]) != 1 or not report["evidence_refs"]:
                _fail("BLOCKED_MANUAL_REPORT_INVALID")
            detail = report["error_details"][0]
            if not isinstance(detail, dict) or detail.get("code") != "MODEL_RETRY_EXHAUSTED":
                _fail("BLOCKED_MANUAL_DETAIL_INVALID")
            return
        if report["decision"] == "yes":
            if report["error_types"] or report["error_details"]:
                _fail("INCONSISTENT_PASS_WITH_ERRORS")
            if not report["evidence_refs"]:
                _fail("PASS_REQUIRES_EVIDENCE")
            return
        if not report["error_types"]:
            _fail("INCONSISTENT_FAIL_WITHOUT_ERRORS")
        if len(report["error_details"]) < len(report["error_types"]) or not report["evidence_refs"]:
            _fail("FAIL_REQUIRES_DETAILS_AND_EVIDENCE")
        detail_types: set[str] = set()
        for detail in report["error_details"]:
            if not isinstance(detail, dict) or set(detail) != DETAIL_KEYS or detail["error_type"] not in report["error_types"]:
                _fail("ERROR_DETAIL_INVALID")
            if not all(isinstance(detail[key], str) and detail[key] for key in DETAIL_KEYS - {"error_type"}):
                _fail("ERROR_DETAIL_INVALID")
            detail_types.add(detail["error_type"])
        if detail_types != set(report["error_types"]):
            _fail("ERROR_DETAIL_COVERAGE_INVALID")
        routes = {ERROR_ROUTE[item] for item in report["error_types"]}
        expected_route = next(route for route in ROUTE_PRIORITY if route in routes)
        if report["route_suggestion"] != expected_route:
            _fail("ERROR_ROUTE_MAPPING_INVALID")

    def validate_output(self, package: dict[str, Any]) -> None:
        self._validate_package(package, "glm_review_result", REVIEWER_ID)
        envelope, payload = package["envelope"], package["payload"]
        blocked = envelope["status"] == "blocked_manual"
        if envelope["status"] not in {"candidate", "blocked_manual"}:
            _fail("OUTPUT_STATUS_INVALID")
        if blocked and envelope["attempt_no"] != MAX_MODEL_ATTEMPTS:
            _fail("BLOCKED_MANUAL_ATTEMPT_INVALID")
        if not _valid_ref(payload["reviewed_package_ref"]):
            _fail("REVIEWED_PACKAGE_REF_INVALID")
        if envelope["parent_artifact_refs"] != [payload["reviewed_package_ref"]] or envelope["input_hashes"] != [payload["reviewed_package_ref"]["content_hash"]]:
            _fail("LINEAGE_MISMATCH")
        self._validate_report(payload["semantic_review_report"], blocked_manual=blocked)

    @staticmethod
    def _request(package: dict[str, Any]) -> dict[str, Any]:
        return {
            "reviewer_id": REVIEWER_ID, "artifact_type": "question_sql_pending_dual_review",
            "package_hash": package["payload"]["package_hash"], "frozen_package": package["payload"],
            "required_output_keys": sorted(REPORT_KEYS), "error_route_map": ERROR_ROUTE,
            "route_priority": list(ROUTE_PRIORITY),
        }

    @staticmethod
    def _decode_model_report(raw: str) -> dict[str, Any]:
        if not isinstance(raw, str):
            _fail("MODEL_RESPONSE_NOT_TEXT")
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ContractError("MODEL_RESPONSE_INVALID_JSON") from exc
        GLMReviewerAgent._validate_report(parsed, blocked_manual=False)
        return parsed

    def _build_result(self, package: dict[str, Any], report: dict[str, Any], *, status: str, created_at: str | None) -> dict[str, Any]:
        input_envelope = package["envelope"]
        payload = {"reviewed_package_ref": artifact_ref(input_envelope), "semantic_review_report": report}
        envelope = {
            "artifact_id": f"{input_envelope['artifact_id']}-v{input_envelope['version']}-glm-review", "artifact_type": "glm_review_result",
            "run_id": input_envelope["run_id"], "qa_id": input_envelope["qa_id"], "version": 1,
            "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
            "attempt_no": input_envelope["attempt_no"], "producer_id": REVIEWER_ID, "parent_artifact_refs": [artifact_ref(input_envelope)],
            "input_hashes": [input_envelope["content_hash"]], "status": status, "mode": input_envelope["mode"],
            "created_at": created_at or datetime.now(timezone.utc).isoformat(), "trace_id": input_envelope["trace_id"], "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        result = {"envelope": envelope, "payload": payload}
        self.validate_output(result)
        return result

    def _blocked_manual(self, package: dict[str, Any], last_error: str, created_at: str | None) -> dict[str, Any]:
        envelope = package["envelope"]
        report = {
            "reviewer_id": REVIEWER_ID, "decision": "no", "error_types": [],
            "error_details": [{"code": "MODEL_RETRY_EXHAUSTED", "attempts": MAX_MODEL_ATTEMPTS, "reason": last_error, "object": "glm_review", "location": "structured_response", "suggestion": "人工审核冻结审核包"}],
            "evidence_refs": [{"kind": "frozen_package", "ref": envelope["content_hash"], "description": "三次模型调用均未产生可验证结构化结果"}],
            "route_suggestion": "150",
        }
        return self._build_result(package, report, status="blocked_manual", created_at=created_at)

    def review(self, package: dict[str, Any], *, created_at: str | None = None) -> dict[str, Any]:
        """调用 GLM 最多三次；调用方不能提供或覆盖审核结论。"""
        self.validate_input(package)
        if self.model_client is None:
            _fail("MODEL_CLIENT_REQUIRED")
        last_error = "MODEL_RESPONSE_UNAVAILABLE"
        for _ in range(MAX_MODEL_ATTEMPTS):
            try:
                report = self._decode_model_report(self.model_client.review(self._request(package)))
                return self._build_result(package, report, status="candidate", created_at=created_at)
            except ContractError as exc:
                last_error = str(exc)
            except Exception as exc:
                last_error = f"MODEL_TRANSPORT_ERROR:{type(exc).__name__}"
        if package["envelope"]["attempt_no"] == MAX_MODEL_ATTEMPTS:
            return self._blocked_manual(package, last_error, created_at)
        _fail(f"MODEL_RETRY_EXHAUSTED:{last_error}")


def consume_110_stub(repo_root: Path, result_package: dict[str, Any]) -> dict[str, str]:
    """110 只消费通过 180 硬校验的包；阻断包不可被误路由。"""
    agent = GLMReviewerAgent(repo_root)
    agent.validate_output(result_package)
    envelope, report = result_package["envelope"], result_package["payload"]["semantic_review_report"]
    if envelope["status"] == "blocked_manual":
        return {"consumer": "110", "reviewer_id": REVIEWER_ID, "decision": "blocked_manual", "route_suggestion": "manual", "error_types": "[]"}
    return {"consumer": "110", "reviewer_id": report["reviewer_id"], "decision": report["decision"], "route_suggestion": report["route_suggestion"], "error_types": json.dumps(report["error_types"], sort_keys=True)}
