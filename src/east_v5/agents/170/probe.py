"""脱敏 EAS-26 探针：真实 160 → 170 合同闭环（含 110/150 下游 Stub 消费）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import copy
import importlib

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.artifacts import content_hash
from east_v5.governance import ContractError, sha256

from .review import ERROR_TYPE_ROUTE, ERROR_TYPES, DeepSeekReviewAgent

# ``160`` 是数字起始的包名，无法写成直接 import 语句；与 160 测试同样经 importlib 引入。
PrecheckAgent = importlib.import_module("east_v5.agents.160.precheck").PrecheckAgent

TIME = "2026-08-17T00:00:00+00:00"


def _wrap(kind: str, identity: str, payload: dict[str, Any], producer: str, *, attempt: int = 1, mode: str = "question_sql", parents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parent_refs = list(parents or [])
    envelope = {
        "artifact_id": identity, "artifact_type": kind, "run_id": "eas26-probe", "qa_id": "QA-EAS26",
        "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": attempt, "producer_id": producer,
        "parent_artifact_refs": parent_refs, "input_hashes": [ref["content_hash"] for ref in parent_refs],
        "status": "candidate", "mode": mode, "created_at": TIME, "trace_id": "eas26-probe", "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _spec() -> dict[str, Any]:
    payload = {
        "query_spec_id": "qspec-026", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "a" * 64},
        "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "b" * 64},
        "query_goal": "脱敏风险筛查", "must_preserve_fact_refs": ["fact-1"], "main_object_and_grain": {"main_object": "机构", "grain": "记录"},
        "query_entry": {"entry_table": "T1", "entry_conditions": []}, "related_objects_and_path": [], "filters_and_evidence": [],
        "return_fields": [{"field_id": "F1", "display_name": "字段1", "source_table": "T1"}],
        "aggregation_dedup_sort_time": {"group_by_fields": []}, "observability_boundary": {"answerable": ["风险"], "unanswerable": []},
        "expected_result_shape": {"row_grain": "记录", "column_set": ["F1"], "aggregation_shape": "none"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "T1", "allowed_fields": ["F1", "F2"]}]},
        "minimum_positive_count": 1, "minimum_negative_count": 1, "condition_coverage": [], "code_value_coverage": [],
        "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 1, "high": 1}},
        "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 5}, "query_specification_package_schema_version": "query-specification-v1",
    }
    return _wrap("query_specification_package", "qspec026", payload, "140")


def _candidate(sql: str, suffix: str = "") -> dict[str, Any]:
    question = f"脱敏机构风险筛查{suffix}"
    return {
        "sql_gold": sql, "clear_question": question,
        "sql_explanation": {"select": f"字段{suffix}", "from_join": f"T1{suffix}", "where": f"固定{suffix}", "aggregation": f"无{suffix}", "sort": f"固定{suffix}", "business_meaning": f"筛查{suffix}"},
        "business_event_candidates": [{"event_name": f"筛查{suffix}", "objective": f"筛查{suffix}", "objects": ["机构"], "state_changes": [suffix] if suffix else []}],
        "specification_mapping": [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS],
    }


def _report(decision: str, error_types: tuple[str, ...] = (), route: str = "150") -> dict[str, Any]:
    if decision == "yes":
        return {"reviewer_id": "170", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [], "route_suggestion": route}
    return {
        "reviewer_id": "170", "decision": decision, "error_types": list(error_types),
        "error_details": [{"object": item, "location": "candidate", "reason": "synthetic", "suggestion": "repair"} for item in error_types],
        "evidence_refs": [{"source": "fixture", "ref": "fact-1"}],
        "route_suggestion": route,
    }


def _forge_round3(dual: dict[str, Any]) -> dict[str, Any]:
    """将 round-1 冻结双审核包重写为合法 round-3 包（重算 package_hash 与信封哈希）。"""
    forged = copy.deepcopy(dual)
    forged["envelope"]["attempt_no"] = 3
    forged["payload"]["review_round"] = 3
    forged["payload"]["package_hash"] = sha256({key: value for key, value in forged["payload"].items() if key != "package_hash"})
    forged["envelope"]["content_hash"] = content_hash(forged["envelope"], forged["payload"])
    return forged


def consume_110_stub(root: Path, package: dict[str, Any]) -> dict[str, str]:
    """独立下游消费者（110/150）：校验 170 结果包可由下游消费。"""
    PendingPrecheckBuilder(root)._validate(package, "deepseek_review_result")
    envelope, payload = package["envelope"], package["payload"]
    report = payload["semantic_review_report"]
    if envelope["producer_id"] != "170" or report["reviewer_id"] != "170":
        raise ContractError("REVIEW_PRODUCER_REJECTED")
    if payload["reviewed_package_ref"] != envelope["parent_artifact_refs"][0]:
        raise ContractError("REVIEW_REF_MISMATCH")
    return {"consumer": "110", "decision": report["decision"], "reviewer_id": report["reviewer_id"]}


def run_sanitized_probe(root: Path) -> dict[str, Any]:
    builder_150, agent_160, reviewer_170 = PendingPrecheckBuilder(root), PrecheckAgent(root), DeepSeekReviewAgent(root)
    query = _spec()
    valid = builder_150.build_pending_precheck(query, run_id="eas26-probe", qa_id="QA-EAS26", created_at=TIME, **_candidate("SELECT T1.F1 FROM T1"))
    result = agent_160.precheck(valid, query, checked_at=TIME)
    dual = agent_160.build_dual_review(valid, query, result, created_at=TIME)
    dual_again = agent_160.build_dual_review(valid, query, result, created_at=TIME)

    passed = reviewer_170.review(dual, _report("yes"), created_at=TIME)
    consumed_pass = consume_110_stub(root, passed)

    error_matrix: dict[str, Any] = {}
    for error_type in ERROR_TYPES:
        route = ERROR_TYPE_ROUTE[error_type]
        reviewed = reviewer_170.review(dual, _report("no", (error_type,), route), created_at=TIME)
        consume_110_stub(root, reviewed)
        error_matrix[error_type] = {
            "decision": reviewed["payload"]["semantic_review_report"]["decision"],
            "route_suggestion": reviewed["payload"]["semantic_review_report"]["route_suggestion"],
            "status": reviewed["envelope"]["status"],
        }

    multi = reviewer_170.review(dual, _report("no", ("QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR"), "150"), created_at=TIME)
    consume_110_stub(root, multi)

    # 三轮阻断：round 3 仍 no → 信封 status=blocked_manual。
    round3 = _forge_round3(dual)
    reviewer_170.validate_dual_review(round3)
    blocked = reviewer_170.review(round3, _report("no", ("QUESTION_SQL_ERROR",), "150"), created_at=TIME)

    # 模型失败：LLM 连续三次抛异常 → 合法阻断（decision=blocked_manual）。
    def _failing_llm(_payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model unavailable")

    model_blocked = reviewer_170.run(dual, _failing_llm, created_at=TIME)

    return {
        "summary": {
            "dual_review_identical": dual["envelope"]["content_hash"] == dual_again["envelope"]["content_hash"] and dual["payload"]["package_hash"] == dual_again["payload"]["package_hash"],
            "pass_reviewed": consumed_pass["decision"] == "yes",
            "six_error_types_covered": set(error_matrix) == set(ERROR_TYPES),
            "every_error_routes_consistently": all(item["route_suggestion"] == ERROR_TYPE_ROUTE[k] for k, item in error_matrix.items()),
            "every_error_status_candidate": all(item["status"] == "candidate" for item in error_matrix.values()),
            "multi_error_non_conflicting": multi["payload"]["semantic_review_report"]["error_types"] == ["QUESTION_SQL_ERROR", "BUSINESS_EVENT_ERROR"],
            "round3_blocked_manual": blocked["envelope"]["status"] == "blocked_manual",
            "model_failure_blocked_manual": model_blocked["decision"] == "blocked_manual" and model_blocked["attempts"] == 3,
        }
    }


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe(Path(__file__).resolve().parents[4])["summary"], ensure_ascii=False, sort_keys=True))
