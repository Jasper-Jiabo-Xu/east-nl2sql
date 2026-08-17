"""EAS-38：question-SQL 阶段端到端联调（120→130↔000→140→150→160→170/180→110→210）。

本测试从冻结脱敏处罚来源 Fixture 出发，驱动已验收 V5 Agent 的确定性组件，
覆盖正常路径、160 预审失败、六类 error type 失败路由、双审不一致/超时/非法输出、
170/180 独立同哈希、110 最上游路由、三次失败人工阻断，以及
「答案侧字段不进 benchmark 输入」「来源与可观察边界全程不丢失」「可一键复现且不改动输入」。
"""
from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.agents.east_120.extractor import FactExtractor
from east_v5.agents.east_130 import ObservableFactMapper
from east_v5.agents.east_140 import QuerySpecBuilder
from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.agents.east_180.reviewer import GLMReviewerAgent
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, load_json, sha256

PrecheckAgent = importlib.import_module("east_v5.agents.160.precheck").PrecheckAgent
DeepSeekReviewAgent = importlib.import_module("east_v5.agents.170.review").DeepSeekReviewAgent
QuestionSqlStageScheduler = importlib.import_module("east_v5.agents.110.scheduler").QuestionSqlStageScheduler
DataStageCoordinator = importlib.import_module("east_v5.agents.210.scheduler").DataStageCoordinator

TIME = "2026-08-17T00:00:00+00:00"
FIXTURES = ROOT / "fixtures" / "integration" / "qs"
RUN, QA, TRACE = "run-qs", "QA-QS", "trace-qs"

GOOD_SQL = "SELECT EAST_D001.F001, EAST_D001.F002 FROM EAST_D001 WHERE EAST_D001.F001 = :v"
QUESTION = "筛查某自然人的违规发放贷款记录"
ANSWER_FIELDS = ("clear_question", "sql_gold", "sql_explanation", "business_event_candidates", "specification_mapping")

# fact-001(subject)/fact-002(behavior)/fact-006(time) 映射到 000 冻结资产的 3 条记录；
# 其余 4 条事实显式以 NO_EAST_ASSET 标记不可观察（不伪造表/字段）。
FACT_MAP = [
    ("fact-001", 0, "以 EAST_D001.F001 筛查处罚主体 fact-001"),
    ("fact-002", 1, "以 EAST_D001.F002 筛查违规行为 fact-002"),
    ("fact-006", 2, "以 EAST_D001.F003 筛查处罚日期 fact-006"),
]


def wrap(kind, identity, payload, *, producer, attempt=1, parents=None, mode="question_sql",
         status="candidate", version=1):
    parents = parents or []
    env = {
        "artifact_id": identity, "artifact_type": kind, "run_id": RUN, "qa_id": QA, "version": version,
        "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
        "attempt_no": attempt, "producer_id": producer, "parent_artifact_refs": parents,
        "input_hashes": [p["content_hash"] for p in parents], "status": status, "mode": mode,
        "created_at": TIME, "trace_id": TRACE, "storage_locator": None,
    }
    env["content_hash"] = content_hash(env, payload)
    return {"envelope": env, "payload": payload}


class ScriptedGLM:
    def __init__(self, response: dict):
        self.response = json.dumps(response, ensure_ascii=False)

    def review(self, _request):
        return self.response


# ── 链上各段构建 ────────────────────────────────────────────────

def build_penalty():
    source = load_json(FIXTURES / "penalty-source-sanitized.json")
    extractor = FactExtractor(ROOT)
    extractor.validate_input(source)
    fact_payload = extractor.extract(source)
    extractor.validate_output(fact_payload, source)
    penalty = wrap("penalty_fact_package", "penalty-qs", fact_payload, producer="120")
    return penalty, source


def build_request(penalty, mapper):
    return mapper.plan_constraint_query(penalty, run_id=RUN, qa_id=QA, created_at=TIME)


def build_assets(request):
    payload = load_json(FIXTURES / "constraint-asset-approved.json")
    payload["request_id"] = request["payload"]["request_id"]
    return wrap("constraint_asset_package", f"asset-{request['payload']['request_id']}", payload,
                producer="000", attempt=request["envelope"]["attempt_no"],
                parents=[artifact_ref(request["envelope"])])


def build_mapping_candidates(asset):
    records = asset["payload"]["matched_records"]
    return {
        "asset_package_ref": artifact_ref(asset["envelope"]),
        "candidates": [
            {"penalty_fact_id": fid, "asset_record_index": index,
             "source_ref": records[index]["source_refs"][0], "proxy_expression": proxy}
            for fid, index, proxy in FACT_MAP
        ],
    }


def build_observable(penalty, assets, mapper):
    return mapper.build_observable_facts(
        penalty, assets, run_id=RUN, qa_id=QA,
        mapping_candidates=build_mapping_candidates(assets), created_at=TIME,
    )


def llm_fields():
    return {
        "query_goal": "脱敏风险筛查",
        "main_object_and_grain": {"main_object": "某自然人", "grain": "一条EAST业务记录"},
        "query_entry": {"entry_table": "EAST_D001", "entry_conditions": [{"field_id": "F001", "operator": "=", "value": "某自然人"}]},
        "related_objects_and_path": [],
        "filters_and_evidence": [{"field_id": "F001", "operator": "=", "value": "某自然人", "evidence_ref": "constraint_asset:CA-V0.3.0#record-0"}],
        "return_fields": [{"field_id": "F001", "display_name": "处罚主体", "source_table": "EAST_D001"}],
        "aggregation_dedup_sort_time": {"group_by_fields": ["EAST_D001.F001"], "distinct_required": False, "order_by": [{"field_id": "F001", "direction": "ASC"}], "time_window": {"field_id": "F003", "window_type": "fixed"}},
        "observability_boundary": {"answerable": ["脱敏风险筛查"], "unanswerable": ["具体处罚金额"]},
        "expected_result_shape": {"row_grain": "一条EAST业务记录", "column_set": ["F001", "F002"], "aggregation_shape": "group_by"},
        "sql_schema_scope": {"allowed_tables": [{"table_id": "EAST_D001", "allowed_fields": ["F001", "F002", "F003"]}]},
        "minimum_positive_count": 1,
        "minimum_negative_count": 1,
        "condition_coverage": [{"predicate": "F001 = 某自然人", "positive_types": ["命中"], "negative_types": ["未命中"]}],
        "code_value_coverage": [{"field_id": "F001", "target_code_values": ["A", "B"]}],
        "expected_row_group_count": {"minimum": 1, "target": 10, "tolerance_range": {"low": 1, "high": 100}},
        "join_expansion_limit": {"max_multiplier": 2.0, "max_result_rows": 1000},
    }


def candidate_fields(sql, question=QUESTION):
    return {
        "sql_gold": sql,
        "clear_question": question,
        "sql_explanation": {"select": "处罚主体与违规行为字段", "from_join": "EAST_D001", "where": "处罚主体等于指定值", "aggregation": "无", "sort": "固定排序", "business_meaning": "风险筛查"},
        "business_event_candidates": [{"event_name": "筛查", "objective": "风险筛查", "objects": ["某自然人"], "state_changes": ["识别"]}],
        "specification_mapping": [{"spec_item": item, "question_fragment": question, "sql_fragment": sql} for item in MAPPED_SPEC_ITEMS],
    }


def build_pending(spec):
    return PendingPrecheckBuilder(ROOT).build_pending_precheck(
        spec, run_id=RUN, qa_id=QA, created_at=TIME, **candidate_fields(GOOD_SQL),
    )


def build_dual(spec, pending):
    checker = PrecheckAgent(ROOT)
    result = checker.precheck(pending, spec, checked_at=TIME)
    assert result["decision"] == "pass", result
    return checker.build_dual_review(pending, spec, result, created_at=TIME)


def build_full_chain():
    penalty, source = build_penalty()
    mapper = ObservableFactMapper(ROOT)
    request = build_request(penalty, mapper)
    assets = build_assets(request)
    observable = build_observable(penalty, assets, mapper)
    spec = QuerySpecBuilder(ROOT).build_query_spec(
        penalty, observable, run_id=RUN, qa_id=QA, created_at=TIME, **llm_fields(),
    )
    pending = build_pending(spec)
    dual = build_dual(spec, pending)
    return {
        "penalty": penalty, "source": source, "request": request, "assets": assets,
        "observable": observable, "spec": spec, "pending": pending, "dual": dual,
    }


# ── 审核报告构造 ────────────────────────────────────────────────

def report_170_yes():
    return {"reviewer_id": "170", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [], "route_suggestion": "150"}


def report_170_no(error_type, route):
    return {"reviewer_id": "170", "decision": "no", "error_types": [error_type],
            "error_details": [{"object": "candidate", "location": "x", "reason": "synthetic", "suggestion": "repair"}],
            "evidence_refs": [{"source": "fixture", "ref": "fact-001"}], "route_suggestion": route}


def report_180_yes():
    return {"reviewer_id": "180", "decision": "yes", "error_types": [], "error_details": [],
            "evidence_refs": [{"kind": "fixture", "ref": "x", "description": "脱敏"}], "route_suggestion": "150"}


def report_180_no(error_types, route=None):
    route_map = {"FACT_PACKAGE_ERROR": "120", "OBSERVABLE_MAPPING_ERROR": "130", "QUERY_SPEC_ERROR": "140",
                 "QUESTION_SQL_ERROR": "150", "BUSINESS_EVENT_ERROR": "150", "QUESTION_FACT_OMISSION": "120"}
    if route is None:
        route = next(r for r in ("120", "130", "140", "150") if r in {route_map[t] for t in error_types})
    details = [{"error_type": t, "object": "candidate", "location": "x", "reason": "synthetic", "suggestion": "repair"} for t in error_types]
    return {"reviewer_id": "180", "decision": "no", "error_types": list(error_types), "error_details": details,
            "evidence_refs": [{"kind": "fixture", "ref": "x", "description": "脱敏"}], "route_suggestion": route}


def review_170(pending, report):
    return DeepSeekReviewAgent(ROOT).review(pending, report, created_at=TIME)


def review_180(pending, report):
    return GLMReviewerAgent(ROOT, ScriptedGLM(report)).review(pending, created_at=TIME)


def forge_invalid(package, sql):
    forged = copy.deepcopy(package)
    forged["payload"]["sql_gold"] = sql
    for item in forged["payload"]["specification_mapping"]:
        item["sql_fragment"] = sql
    forged["envelope"]["content_hash"] = content_hash(forged["envelope"], forged["payload"])
    return forged


def forge_round3(dual):
    forged = copy.deepcopy(dual)
    forged["envelope"]["attempt_no"] = 3
    forged["payload"]["review_round"] = 3
    forged["payload"]["package_hash"] = sha256({k: v for k, v in forged["payload"].items() if k != "package_hash"})
    forged["envelope"]["content_hash"] = content_hash(forged["envelope"], forged["payload"])
    return forged


class QuestionSqlE2ETests(unittest.TestCase):
    def setUp(self):
        self.scheduler = QuestionSqlStageScheduler(ROOT)

    # ── 正常路径 ──────────────────────────────────────────────────

    def test_end_to_end_happy_path_reaches_210_and_220(self):
        chain = build_full_chain()
        dual = chain["dual"]
        r170 = review_170(dual, report_170_yes())
        r180 = review_180(dual, report_180_yes())
        result = self.scheduler.collect_reviews(dual, [r180, r170], created_at=TIME)
        self.assertEqual((result["target"], result["kind"]), ("210", "question_sql_dual_review_passed"))
        approved = result["approved_package"]
        self.assertEqual((approved["envelope"]["producer_id"], approved["envelope"]["status"]), ("110", "validated"))
        # 210 消费并通过 220 调度，不误启动 data 阶段
        coordinator = DataStageCoordinator(ROOT)
        self.assertEqual(coordinator.begin_event(approved)["dispatches"][0]["target"], "220")
        # 来源与可观察边界全程不丢失
        spec = chain["spec"]["payload"]
        self.assertEqual(approved["payload"]["penalty_fact_package"], spec["penalty_fact_package_ref"])
        self.assertEqual(approved["payload"]["observable_fact_package"], spec["observable_fact_package_ref"])

    def test_source_and_observability_boundary_preserved(self):
        chain = build_full_chain()
        for fact in chain["observable"]["payload"]["observable_facts"]:
            self.assertIn("不直接认定", fact["risk_screening_boundary"])
        spec = chain["spec"]["payload"]
        self.assertEqual(spec["penalty_fact_package_ref"], artifact_ref(chain["penalty"]["envelope"]))
        self.assertEqual(spec["observable_fact_package_ref"], artifact_ref(chain["observable"]["envelope"]))
        self.assertEqual(spec["observability_boundary"]["answerable"], ["脱敏风险筛查"])
        self.assertEqual(spec["observability_boundary"]["unanswerable"], ["具体处罚金额"])

    # ── 160 预审失败 → 150 修复 ───────────────────────────────────

    def test_160_precheck_failure_feedback_and_150_repair(self):
        chain = build_full_chain()
        spec, builder, checker = chain["spec"], PendingPrecheckBuilder(ROOT), PrecheckAgent(ROOT)
        bad = forge_invalid(chain["pending"], "SELECT EAST_D001.F9 FROM EAST_D001")
        result = checker.precheck(bad, spec, checked_at=TIME)
        self.assertEqual(result["decision"], "fail")
        self.assertIn("PC-SQL-005", {item["failed_rule_ids"][0] for item in result["failed_items"]})
        feedback = checker.build_feedback(bad, result, created_at=TIME)
        self.assertEqual(feedback["envelope"]["producer_id"], "160")
        self.assertEqual(feedback["payload"]["candidate_ref"], artifact_ref(bad["envelope"]))
        repaired = builder.handle_precheck_feedback(
            spec, feedback, bad, run_id=RUN, qa_id=QA, attempt_no=2, created_at=TIME,
            **candidate_fields(GOOD_SQL, QUESTION + "-修复"),
        )
        self.assertEqual(repaired["envelope"]["attempt_no"], 2)
        self.assertEqual(repaired["envelope"]["supersedes_ref"], artifact_ref(bad["envelope"]))
        self.assertEqual(checker.precheck(repaired, spec, checked_at=TIME)["decision"], "pass")

    # ── 六类 error type 失败 Fixture 路由 ─────────────────────────

    def test_each_error_type_failure_fixture_routes_correctly(self):
        dual = build_full_chain()["dual"]
        fixture = load_json(FIXTURES / "error-type-failure-reports.json")
        self.assertEqual(fixture["reviewer"], "170")
        for entry in fixture["error_reports"]:
            with self.subTest(error_type=entry["error_type"]):
                r170 = review_170(dual, entry["report"])
                r180 = review_180(dual, report_180_yes())
                result = self.scheduler.collect_reviews(dual, [r170, r180], created_at=TIME)
                self.assertEqual((result["target"], result["kind"]), (entry["route"], "repair"))
                self.assertNotIn("approved_package", result)

    # ── 110 最上游路由 ────────────────────────────────────────────

    def test_most_upstream_route_across_reviewers(self):
        dual = build_full_chain()["dual"]
        r170 = review_170(dual, report_170_no("OBSERVABLE_MAPPING_ERROR", "130"))
        r180 = review_180(dual, report_180_no(["QUESTION_SQL_ERROR"], "150"))
        self.assertEqual(self.scheduler.collect_reviews(dual, [r170, r180], created_at=TIME)["target"], "130")

    def test_180_mixed_errors_route_to_most_upstream(self):
        dual = build_full_chain()["dual"]
        r170 = review_170(dual, report_170_yes())
        r180 = review_180(dual, report_180_no(["FACT_PACKAGE_ERROR", "QUESTION_SQL_ERROR"], "120"))
        self.assertEqual(self.scheduler.collect_reviews(dual, [r170, r180], created_at=TIME)["target"], "120")

    # ── 170/180 独立且输入同一冻结哈希 ────────────────────────────

    def test_170_180_independent_and_same_frozen_hash(self):
        chain = build_full_chain()
        dual = chain["dual"]
        dispatches = self.scheduler.dispatch_dual_review(dual)
        self.assertEqual(dispatches[0]["package_hash"], dispatches[1]["package_hash"])
        self.assertEqual(dispatches[0]["reviewed_package_ref"], dispatches[1]["reviewed_package_ref"])
        self.assertEqual(dispatches[0]["package_hash"], dual["payload"]["package_hash"])
        r170 = review_170(dual, report_170_yes())
        r180 = review_180(dual, report_180_yes())
        self.assertEqual(r170["payload"]["reviewed_package_ref"], artifact_ref(dual["envelope"]))
        self.assertEqual(r180["payload"]["reviewed_package_ref"], artifact_ref(dual["envelope"]))
        self.assertEqual(r170["envelope"]["input_hashes"], [dual["envelope"]["content_hash"]])
        self.assertEqual(r180["envelope"]["input_hashes"], [dual["envelope"]["content_hash"]])
        # 180 的模型请求不携带 170 结论，彼此隔离
        request = GLMReviewerAgent._request(dual)
        self.assertNotIn("deepseek", json.dumps(request, ensure_ascii=False))
        self.assertNotIn("review_result", request["frozen_package"])

    # ── 双审超时 / 非法输出 ──────────────────────────────────────

    def test_180_timeout_blocks_manual_on_third_attempt(self):
        round3 = forge_round3(build_full_chain()["dual"])

        class TimeoutClient:
            def review(self, _request):
                raise TimeoutError("model timeout")

        agent = GLMReviewerAgent(ROOT, TimeoutClient())
        blocked = agent.review(round3, created_at=TIME)
        self.assertEqual(blocked["envelope"]["status"], "blocked_manual")
        self.assertEqual(blocked["envelope"]["producer_id"], "180")
        self.assertEqual(blocked["payload"]["semantic_review_report"]["decision"], "no")

    def test_180_illegal_output_raises_retry_exhausted_before_third_attempt(self):
        dual = build_full_chain()["dual"]

        class GarbageClient:
            def review(self, _request):
                return "not-json{{{"

        agent = GLMReviewerAgent(ROOT, GarbageClient())
        with self.assertRaisesRegex(ContractError, "MODEL_RETRY_EXHAUSTED"):
            agent.review(dual, created_at=TIME)

    def test_170_model_failure_blocks_manual_after_three(self):
        dual = build_full_chain()["dual"]
        agent = DeepSeekReviewAgent(ROOT)
        blocked = agent.run(dual, lambda _p: (_ for _ in ()).throw(RuntimeError("model unavailable")), created_at=TIME)
        self.assertEqual(blocked["decision"], "blocked_manual")
        self.assertEqual(blocked["attempts"], 3)

    # ── 三次失败 / 任一审核 blocked_manual → 人工阻断 ─────────────

    def test_third_round_no_routes_to_manual(self):
        round3 = forge_round3(build_full_chain()["dual"])
        r170 = review_170(round3, report_170_no("QUESTION_SQL_ERROR", "150"))
        r180 = review_180(round3, report_180_yes())
        result = self.scheduler.collect_reviews(round3, [r170, r180], created_at=TIME)
        self.assertEqual((result["target"], result["kind"]), ("manual", "blocked_manual"))

    def test_reviewer_blocked_manual_routes_to_manual(self):
        dual = build_full_chain()["dual"]
        r170 = review_170(dual, report_170_no("QUESTION_SQL_ERROR", "150"))
        r170["envelope"]["status"] = "blocked_manual"
        r170["envelope"]["content_hash"] = content_hash(r170["envelope"], r170["payload"])
        r180 = review_180(dual, report_180_yes())
        self.assertEqual(self.scheduler.collect_reviews(dual, [r170, r180], created_at=TIME)["target"], "manual")

    # ── 答案侧字段不进 benchmark 输入 ─────────────────────────────

    def test_answer_fields_not_in_benchmark_inputs(self):
        chain = build_full_chain()
        for key in ANSWER_FIELDS:
            self.assertNotIn(key, chain["source"])
            self.assertNotIn(key, chain["assets"]["payload"])
            self.assertNotIn(key, chain["spec"]["payload"])
        self.assertIn("clear_question", chain["pending"]["payload"])
        self.assertIn("sql_gold", chain["pending"]["payload"])

    # ── 可一键复现且不改动已验证输入 ─────────────────────────────

    def test_reproducible_and_no_input_mutation(self):
        first = build_full_chain()
        second = build_full_chain()
        self.assertEqual(first["spec"]["envelope"]["content_hash"], second["spec"]["envelope"]["content_hash"])
        self.assertEqual(first["dual"]["envelope"]["content_hash"], second["dual"]["envelope"]["content_hash"])
        self.assertEqual(first["dual"]["payload"]["package_hash"], second["dual"]["payload"]["package_hash"])
        source_snapshot = copy.deepcopy(first["source"])
        penalty_snapshot = copy.deepcopy(first["penalty"])
        dual_before = copy.deepcopy(first["dual"])
        self.scheduler.collect_reviews(first["dual"], [review_180(first["dual"], report_180_yes()), review_170(first["dual"], report_170_yes())], created_at=TIME)
        self.assertEqual(first["source"], source_snapshot)
        self.assertEqual(first["penalty"], penalty_snapshot)
        self.assertEqual(first["dual"], dual_before)
        FactExtractor(ROOT).validate_input(first["source"])


if __name__ == "__main__":
    unittest.main()
