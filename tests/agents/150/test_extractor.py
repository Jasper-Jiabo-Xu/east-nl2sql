from __future__ import annotations
import unittest
from pathlib import Path
from east_v5.agents.east_150 import PendingPrecheckBuilder
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT, TIME = Path(__file__).resolve().parents[3], "2026-08-16T00:00:00+00:00"

def wrap(kind, identity, payload, *, producer, run="run150", qa="QA150", attempt=1, version=1, parents=None, mode="question_sql"):
    parents = parents or []
    env = {"artifact_id":identity,"artifact_type":kind,"run_id":run,"qa_id":qa,"version":version,"schema_version":"COMMON-ENVELOPE/v1","content_hash":"0"*64,"supersedes_ref":None,"attempt_no":attempt,"producer_id":producer,"parent_artifact_refs":parents,"input_hashes":[x["content_hash"] for x in parents],"status":"candidate","mode":mode,"created_at":TIME,"trace_id":"trace150","storage_locator":None}
    env["content_hash"] = content_hash(env, payload)
    return {"envelope":env,"payload":payload}

def spec():
    p={"query_spec_id":"qspec-150","penalty_fact_package_ref":{"artifact_id":"penalty","version":1,"content_hash":"a"*64},"observable_fact_package_ref":{"artifact_id":"observable","version":1,"content_hash":"b"*64},"query_goal":"脱敏风险筛查","must_preserve_fact_refs":["fact-1"],"main_object_and_grain":{"main_object":"机构","grain":"记录"},"query_entry":{"entry_table":"T1","entry_conditions":[]},"related_objects_and_path":[],"filters_and_evidence":[],"return_fields":[{"field_id":"F1","display_name":"字段1","source_table":"T1"}],"aggregation_dedup_sort_time":{"group_by_fields":[]},"observability_boundary":{"answerable":["风险"],"unanswerable":[]},"expected_result_shape":{"row_grain":"记录","column_set":["F1"],"aggregation_shape":"none"},"sql_schema_scope":{"allowed_tables":[{"table_id":"T1","allowed_fields":["F1","F2","F3"]},{"table_id":"T2","allowed_fields":["F2"]}]},"minimum_positive_count":1,"minimum_negative_count":1,"condition_coverage":[],"code_value_coverage":[],"expected_row_group_count":{"minimum":1,"target":1,"tolerance_range":{"low":1,"high":2}},"join_expansion_limit":{"max_multiplier":2,"max_result_rows":10},"query_specification_package_schema_version":"query-specification-v1"}
    return wrap("query_specification_package","qspec150",p,producer="140")

def feedback(previous):
    p={"candidate_ref":artifact_ref(previous["envelope"]),"precheck_decision":"fail","failed_items":[{"failed_rule_ids":["RULE-1"],"error_locations":["sql_gold"],"expected_values":"合法字段","actual_values":"错误字段","error_details":"脱敏失败"}]}
    return wrap("precheck_failed_feedback","feedback150",p,producer="160",attempt=previous["envelope"]["attempt_no"])

class Agent150Tests(unittest.TestCase):
    def setUp(self): self.builder,self.spec,self.sql=PendingPrecheckBuilder(ROOT),spec(),"SELECT T1.F1, T1.F2 FROM T1 WHERE T1.F1 = :v"
    def build(self,**kw): return self.builder.build_pending_precheck(self.spec,run_id="run150",qa_id="QA150",created_at=TIME,**{"sql_gold":self.sql,**kw})
    def retry(self,old,sql=None,attempt=2): return self.builder.handle_precheck_feedback(self.spec,feedback(old),old,run_id="run150",qa_id="QA150",attempt_no=attempt,sql_gold=self.sql if sql is None else sql,created_at=TIME)

    def test_frozen_fields_and_160_stub(self):
        p=self.build(); self.builder.validate_pending_precheck(p)
        self.assertEqual(set(p["payload"]),{"candidate_id","query_spec_ref","penalty_fact_package_ref","observable_fact_package_ref","clear_question","sql_gold","sql_explanation","business_event_candidates","specification_mapping","evidence_refs","sql_dialect"})
        self.assertEqual(p["payload"]["query_spec_ref"],artifact_ref(self.spec["envelope"])); self.assertEqual(len(p["payload"]["specification_mapping"]),7)

    def test_feedback_schema_and_ref_rejection(self):
        first=self.build(); good=feedback(first); self.builder.validate_precheck_feedback(good)
        bad=feedback(first); bad["payload"]["candidate_ref"]["content_hash"]="c"*64; bad["envelope"]["content_hash"]=content_hash(bad["envelope"],bad["payload"])
        with self.assertRaisesRegex(ContractError,"FEEDBACK_REF_MISMATCH"): self.builder.handle_precheck_feedback(self.spec,bad,first,run_id="run150",qa_id="QA150",attempt_no=2,sql_gold=self.sql,created_at=TIME)

    def test_retry_lineage_third_and_blocked(self):
        first=self.build(); second=self.retry(first); third=self.retry(second,attempt=3); blocked=self.retry(second,sql="",attempt=3)
        self.assertEqual(second["envelope"]["parent_artifact_refs"],[artifact_ref(self.spec["envelope"]),artifact_ref(feedback(first)["envelope"]),artifact_ref(first["envelope"])])
        self.assertEqual(third["envelope"]["status"],"candidate"); self.assertEqual(blocked["envelope"]["status"],"blocked_manual")

    def test_hash_and_version_guards(self):
        bad=self.build(); bad["payload"]["clear_question"]="篡改"
        with self.assertRaisesRegex(ContractError,"CONTENT_HASH_DRIFT"): self.builder.validate_pending_precheck(bad)
        with self.assertRaisesRegex(ContractError,"VERSION_OVERWRITE_ATTEMPTED"): self.build(version=2)

    def test_sql_rejections(self):
        cases=[("DELETE FROM T1","SQL_NOT_READ_ONLY"),("SELECT T1.F1 FROM T1; SELECT T1.F1 FROM T1","SQL_MULTIPLE_STATEMENTS"),("SELECT * FROM T1","SQL_SELECT_STAR_FORBIDDEN"),("SELECT T1.F1 FROM T1 WHERE T1.F3 < CURRENT_TIMESTAMP","SQL_DYNAMIC_TIME_FORBIDDEN"),("SELECT T9.F1 FROM T9","SQL_TABLE_OUT_OF_SCOPE"),("SELECT T1.F9 FROM T1","SQL_FIELD_OUT_OF_SCOPE")]
        for sql,code in cases:
            with self.subTest(code=code),self.assertRaisesRegex(ContractError,code): self.build(sql_gold=sql)

    def test_sql_structures(self):
        cases=["SELECT T1.F1 FROM T1 JOIN T2 ON T1.F2=T2.F2","SELECT T1.F1, COUNT(T1.F2) FROM T1 GROUP BY T1.F1","SELECT T1.F1 FROM T1 WHERE T1.F1 IN (SELECT T1.F1 FROM T1)","WITH x AS (SELECT T1.F1 FROM T1) SELECT x.F1 FROM x","SELECT T1.F1 FROM T1 ORDER BY T1.F1 LIMIT 5"]
        for sql in cases:
            with self.subTest(sql=sql): self.builder._validate_sql(sql,self.spec["payload"]["sql_schema_scope"])

    def review(self,old,kind="deepseek_review_result"):
        reviewer="170" if kind=="deepseek_review_result" else "180"; p={"reviewed_package_ref":artifact_ref(old["envelope"]),"semantic_review_report":{"reviewer_id":reviewer,"decision":"no","error_types":["QUESTION_SQL_ERROR","BUSINESS_EVENT_ERROR","QUESTION_FACT_OMISSION"],"error_details":[{"reason":"fix"}],"evidence_refs":[],"route_suggestion":"150"}}
        return wrap(kind,"review"+reviewer,p,producer=reviewer,attempt=old["envelope"]["attempt_no"])

    def test_170_180_routes_and_rejection(self):
        first=self.build()
        for kind in ("deepseek_review_result","glm_review_result"):
            self.assertEqual(self.builder.handle_routed_feedback(self.spec,self.review(first,kind),first,run_id="run150",qa_id="QA150",attempt_no=2,sql_gold=self.sql,created_at=TIME)["envelope"]["attempt_no"],2)
        bad=self.review(first); bad["payload"]["semantic_review_report"]["route_suggestion"]="140"; bad["envelope"]["content_hash"]=content_hash(bad["envelope"],bad["payload"])
        with self.assertRaisesRegex(ContractError,"REVIEW_ROUTE_REJECTED"): self.builder.handle_routed_feedback(self.spec,bad,first,run_id="run150",qa_id="QA150",attempt_no=2,sql_gold=self.sql,created_at=TIME)

    def test_260_route(self):
        first=self.build(); p={"schema_version":"v5.sql-regression-failed-feedback/v1","mode":"event_data","input_data_refs":[],"input_orm_ref":None,"sandbox_snapshot_id":"copy","failure_details":{"error_code":"SQL_EXECUTION_ERROR","error_stage":"sql_execution","error_location":"sql_gold","expected_values":[],"actual_values":[],"sql_error_detail":{"sql_text":self.sql,"error_code":"SQLITE","error_message":"bad"},"regression_metrics":{}},"route_target":"110","retry_count":1}; route=wrap("sql_regression_failed_feedback","reg150",p,producer="260",mode="event_data")
        self.assertEqual(self.builder.handle_routed_feedback(self.spec,route,first,run_id="run150",qa_id="QA150",attempt_no=2,sql_gold=self.sql,created_at=TIME)["envelope"]["attempt_no"],2)
        route["payload"]["failure_details"]["error_code"]="DATA_VALUE_ERROR"; route["envelope"]["content_hash"]=content_hash(route["envelope"],route["payload"])
        with self.assertRaisesRegex(ContractError,"REGRESSION_ROUTE_REJECTED"): self.builder.handle_routed_feedback(self.spec,route,first,run_id="run150",qa_id="QA150",attempt_no=2,sql_gold=self.sql,created_at=TIME)

if __name__=="__main__": unittest.main()
