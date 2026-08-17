"""170: DeepSeek 审核员 agent —— 对 160 冻结双审核包做独立语义审核。

170 消费 160 冻结的 ``question_sql_pending_dual_review``（170 与 180 拿到完全相同的
``package_hash``），输出 ``deepseek_review_result``。LLM 独立输出 yes/no、六类固定
error type、错误详情、证据引用与 route suggestion；硬代码校验输入 package hash、
输出 Schema、固定 error type、``reviewer_id=170`` 与引用完整。170 不读取 180 的结论，
不修改候选或上游包，不执行数据阶段。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json, sha256

REVIEWER_ID = "170"
ERROR_TYPES = (
    "FACT_PACKAGE_ERROR",
    "OBSERVABLE_MAPPING_ERROR",
    "QUERY_SPEC_ERROR",
    "QUESTION_SQL_ERROR",
    "BUSINESS_EVENT_ERROR",
    "QUESTION_FACT_OMISSION",
)
ROUTE_SUGGESTIONS = ("120", "130", "140", "150")
# 一次拒绝必须路由到能修复全部报错类型的唯一生产者；跨生产者路由视为冲突硬拒绝。
ERROR_TYPE_ROUTE = {
    "FACT_PACKAGE_ERROR": "120",
    "OBSERVABLE_MAPPING_ERROR": "130",
    "QUERY_SPEC_ERROR": "140",
    "QUESTION_SQL_ERROR": "150",
    "BUSINESS_EVENT_ERROR": "150",
    "QUESTION_FACT_OMISSION": "150",
}
SCHEMAS = {
    "question_sql_pending_dual_review": "contracts/packages/question-sql-pending-dual-review-package.schema.json",
    "deepseek_review_result": "contracts/packages/deepseek-review-result.schema.json",
}
REPORT_KEYS = {"reviewer_id", "decision", "error_types", "error_details", "evidence_refs", "route_suggestion"}
MAX_ATTEMPTS = 3


def _fail(code: str) -> None:
    raise ContractError(code)


class DeepSeekReviewAgent:
    """170 独立语义审核员：LLM 只产出语义报告，硬代码负责合同与引用校验。"""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _registry(self) -> Registry:
        resources = []
        for path in [
            self.repo_root / "contracts/common/common-envelope.schema.json",
            *sorted((self.repo_root / "contracts/packages").glob("*.schema.json")),
        ]:
            item = load_json(path)
            resources.append((item["$id"], Resource.from_contents(item)))
        return Registry().with_resources(resources)

    def _validate_payload_schema(self, artifact_type: str, payload: dict[str, Any]) -> None:
        try:
            Draft202012Validator(
                load_json(self.repo_root / SCHEMAS[artifact_type]),
                registry=self._registry(),
            ).validate(payload)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{artifact_type}") from exc

    def validate_dual_review(self, package: dict[str, Any]) -> None:
        """校验 160 冻结的待双审核包：信封、Schema、package_hash、轮次与血缘。"""
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != "question_sql_pending_dual_review":
            _fail("ARTIFACT_TYPE_MISMATCH")
        if envelope["producer_id"] != "160":
            _fail("DUAL_REVIEW_PRODUCER_REJECTED")
        self._validate_payload_schema("question_sql_pending_dual_review", payload)
        if payload["package_hash"] != sha256({key: value for key, value in payload.items() if key != "package_hash"}):
            _fail("PACKAGE_HASH_DRIFT")
        if payload["review_round"] != envelope["attempt_no"]:
            _fail("REVIEW_ROUND_MISMATCH")
        if payload["precheck_report"]["decision"] != "pass":
            _fail("PRECHECK_REPORT_NOT_PASS")

    def _validate_semantic_report(self, report: dict[str, Any]) -> None:
        """硬代码校验 LLM 语义报告：字段、固定枚举、证据充分性与路由一致性。"""
        if not isinstance(report, dict) or set(report) != REPORT_KEYS:
            _fail("UNKNOWN_FIELD:semantic_review_report")
        if report["reviewer_id"] != REVIEWER_ID:
            _fail("REVIEWER_ID_REJECTED")
        decision = report["decision"]
        if decision not in ("yes", "no"):
            _fail("DECISION_INVALID")
        error_types = report["error_types"]
        if not isinstance(error_types, list):
            _fail("ERROR_TYPES_INVALID")
        if len(error_types) != len(set(error_types)):
            _fail("ERROR_TYPES_DUPLICATE")
        for item in error_types:
            if item not in ERROR_TYPES:
                _fail("ERROR_TYPE_UNKNOWN")
        if decision == "yes":
            if error_types:
                _fail("PASS_WITH_ERRORS")
        elif not error_types:
            _fail("REJECT_WITHOUT_ERRORS")
        if report["route_suggestion"] not in ROUTE_SUGGESTIONS:
            _fail("ROUTE_SUGGESTION_INVALID")
        if decision == "no" and {ERROR_TYPE_ROUTE[item] for item in error_types} != {report["route_suggestion"]}:
            _fail("ROUTE_SUGGESTION_INCONSISTENT")
        error_details = report["error_details"]
        if not isinstance(error_details, list):
            _fail("ERROR_DETAILS_INVALID")
        if decision == "no" and not error_details:
            _fail("REJECT_WITHOUT_DETAILS")
        evidence_refs = report["evidence_refs"]
        if not isinstance(evidence_refs, list):
            _fail("EVIDENCE_REFS_INVALID")
        if decision == "no" and not evidence_refs:
            _fail("EVIDENCE_INSUFFICIENT")

    def build_result(
        self, package: dict[str, Any], report: dict[str, Any], *,
        created_at: str | None = None, artifact_id: str | None = None, version: int | None = None,
    ) -> dict[str, Any]:
        """由通过校验的语义报告组装 ``deepseek_review_result`` 信封+载荷。"""
        envelope, payload = package["envelope"], package["payload"]
        review_round = payload["review_round"]
        status = "blocked_manual" if report["decision"] == "no" and review_round == MAX_ATTEMPTS else "candidate"
        result_payload = {
            "reviewed_package_ref": artifact_ref(envelope),
            "semantic_review_report": dict(report),
        }
        result_envelope = {
            "artifact_id": artifact_id or f"{envelope['artifact_id']}-deepseek-review",
            "artifact_type": "deepseek_review_result",
            "run_id": envelope["run_id"],
            "qa_id": envelope["qa_id"],
            "version": version if version is not None else 1,
            "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64,
            "supersedes_ref": None,
            "attempt_no": envelope["attempt_no"],
            "producer_id": REVIEWER_ID,
            "parent_artifact_refs": [artifact_ref(envelope)],
            "input_hashes": [envelope["content_hash"]],
            "status": status,
            "mode": envelope["mode"],
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "trace_id": envelope["trace_id"],
            "storage_locator": None,
        }
        result_envelope["content_hash"] = content_hash(result_envelope, result_payload)
        return {"envelope": result_envelope, "payload": result_payload}

    def validate_result(self, package: dict[str, Any]) -> None:
        """校验 170 输出包：信封、artifact_type、producer、Schema 与语义报告。"""
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != "deepseek_review_result":
            _fail("ARTIFACT_TYPE_MISMATCH")
        if envelope["producer_id"] != REVIEWER_ID:
            _fail("REVIEW_PRODUCER_REJECTED")
        self._validate_payload_schema("deepseek_review_result", payload)
        self._validate_semantic_report(payload["semantic_review_report"])

    def review(
        self, package: dict[str, Any], report: dict[str, Any], *,
        created_at: str | None = None, artifact_id: str | None = None, version: int | None = None,
    ) -> dict[str, Any]:
        """纯审核：校验输入与语义报告，组装并自校验输出包。"""
        self.validate_dual_review(package)
        self._validate_semantic_report(report)
        result = self.build_result(package, report, created_at=created_at, artifact_id=artifact_id, version=version)
        if result["payload"]["reviewed_package_ref"] != artifact_ref(package["envelope"]):
            _fail("REVIEW_REF_MISMATCH")
        self.validate_result(result)
        return result

    def run(
        self, package: dict[str, Any], llm: Callable[[dict[str, Any]], dict[str, Any]], *,
        created_at: str | None = None, artifact_id: str | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> dict[str, Any]:
        """运行期入口：调用 LLM 产出语义报告，模型失败或非法输出最多重试三次。

        三次仍无法取得合法语义报告时返回 ``decision=blocked_manual`` 的合法阻断，
        而不是抛出异常或伪造一个合法审核包。
        """
        self.validate_dual_review(package)
        last: BaseException | None = None
        for _ in range(max_attempts):
            try:
                report = llm(package["payload"])
                self._validate_semantic_report(report)
                return self.review(package, report, created_at=created_at, artifact_id=artifact_id)
            except BaseException as exc:  # 模型失败或非法输出均可重试
                last = exc
                continue
        return {
            "decision": "blocked_manual",
            "reviewed_package_ref": artifact_ref(package["envelope"]),
            "attempts": max_attempts,
            "reason": "LLM_REVIEW_FAILED",
            "last_error": type(last).__name__ if last is not None else None,
        }
