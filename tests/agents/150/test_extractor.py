from __future__ import annotations
import copy
import unittest
from pathlib import Path
from east_v5.agents.east_150 import PendingPrecheckBuilder, MAPPED_SPEC_ITEMS
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError
ROOT, TIME = Path(__file__).resolve().parents[3], "2026-08-16T00:00:00+00:00"

def wrap(kind, identity, payload, *, producer, attempt=1, parents=None, mode="question_sql"):
    parents=parents or []; env={"artifact_id":identity,"artifact_type":kind,"run_id":"run150","qa_id":"QA150","version":1,"schema_version":"COMMON-ENVELOPE/v1","content_hash":"0"*64,"supersedes_ref":None,"attempt_no":attempt,"producer_id":producer,"parent_artifact_refs":parents,"input_hashes":[x["content_hash"] for x in parents],"status":"candidate","mode":mode,"created_at":TIME,"trace_id":"trace150","storage_locator":None}; env["content_hash"]=content_hash(env,payload); return {"envelope":env,"payload":payload}
def hop(stage="110"): return {"artifact_id":f"{stage}-route-150","version":1,"content_hash":"1"*64}
def spec():
    p={"query_spec_id":"qspec-150","penalty_fact_package_ref":{"artifact_id":"penalty","version":1,"content_hash":"a"*64},"observable_fact_package_ref":{"artifact_id":"observable","version":1,"content_hash":"b"*64},"query_goal":"脱敏风险筛查","must_preserve_fact_refs":["fact-1"],"main_object_and_grain":{"main_object":"机构","grain":"记录"},"query_entry":{"entry_table":"T1","entry_conditions":[]},"related_objects_and_path":[],"filters_and_evidence":[],"return_fields":[{"field_id":"F1","display_name":"字段1","source_table":"T1"}],"aggregation_dedup_sort_time":{"group_by_fields":[]},"observability_boundary":{"answerable":["风险"],"unanswerable":[]},"expected_result_shape":{"row_grain":"记录","column_set":["F1"],"aggregation_shape":"none"},"sql_schema_scope":{"allowed_tables":[{"table_id":"T1","allowed_fields":["F1","F2","F3"]},{"table_id":"T2","allowed_fields":["F2"]}]},"minimum_positive_count":1,"minimum_negative_count":1,"condition_coverage":[],"code_value_coverage":[],"expected_row_group_count":{"minimum":1,"target":1,"tolerance_range":{"low":1,"high":2}},"join_expansion_limit":{"max_multiplier":2,"max_result_rows":10},"query_specification_package_schema_version":"query-specification-v1"};return wrap("query_specification_package","qspec150",p,producer="140")
def candidate(sql, suffix=""):
    q="筛查机构"+suffix; return {"sql_gold":sql,"clear_question":q,"sql_explanation":{"select":"机构字段","from_join":"限定关联","where":"固定条件","aggregation":"无","sort":"固定排序","business_meaning":"风险筛查"},"business_event_candidates":[{"event_name":"筛查"+suffix,"objective":"风险筛查","objects":["机构"],"state_changes":["识别"]}],"specification_mapping":[{"spec_item":item,"question_fragment":q,"sql_fragment":sql} for item in MAPPED_SPEC_ITEMS]}
def precheck(old, producer="160"):
    p={"candidate_ref":artifact_ref(old["envelope"]),"precheck_decision":"fail","failed_items":[{"failed_rule_ids":["RULE"],"error_locations":["sql_gold"],"expected_values":"合法","actual_values":"错误","error_details":"修复"}]};return wrap("precheck_failed_feedback","feedback150",p,producer=producer,attempt=old["envelope"]["attempt_no"])

class Tests(unittest.TestCase):
 def setUp(self): self.b,self.s,self.sql=PendingPrecheckBuilder(ROOT),spec(),"SELECT T1.F1, T1.F2 FROM T1 WHERE T1.F1=:v"
 def build(self, **kw): return self.b.build_pending_precheck(self.s,run_id="run150",qa_id="QA150",created_at=TIME,**{**candidate(self.sql),**kw})
 def repair(self, old, fb, **kw): return self.b.handle_precheck_feedback(self.s,fb,old,run_id="run150",qa_id="QA150",attempt_no=2,created_at=TIME,**{**candidate(self.sql,"-修复"),**kw})
 def test_requires_all_generated_fields(self):
    with self.assertRaisesRegex(ContractError,"GENERATED_FIELDS_REQUIRED"): self.b.build_pending_precheck(self.s,run_id="run150",qa_id="QA150",sql_gold=self.sql,created_at=TIME)
 def test_all_fields_change_on_semantic_repair(self):
    old=self.build(); new=self.repair(old,precheck(old)); self.assertNotEqual(new["payload"]["clear_question"],old["payload"]["clear_question"]); self.assertNotEqual(new["payload"]["business_event_candidates"],old["payload"]["business_event_candidates"]); self.assertEqual(len(new["payload"]["specification_mapping"]),17)
 def test_mapping_missing_or_invalid_fragment_rejected(self):
    bad=candidate(self.sql); bad["specification_mapping"]=bad["specification_mapping"][:-1]
    with self.assertRaisesRegex(ContractError,"SPECIFICATION_MAPPING_INCOMPLETE"): self.build(**bad)
    bad=candidate(self.sql); bad["specification_mapping"].append(copy.deepcopy(bad["specification_mapping"][0]))
    with self.assertRaisesRegex(ContractError,"SPECIFICATION_MAPPING_INCOMPLETE"): self.build(**bad)
    bad=candidate(self.sql); bad["specification_mapping"][0]["sql_fragment"]="missing"
    with self.assertRaisesRegex(ContractError,"SPECIFICATION_MAPPING_FRAGMENT_INVALID"): self.build(**bad)
 def test_alias_unqualified_and_cte_scope_rejected(self):
    cases=[("SELECT t.F9 FROM T1 AS t","SQL_FIELD_OUT_OF_SCOPE"),("SELECT F9 FROM T1","SQL_UNQUALIFIED_FIELD_OUT_OF_SCOPE"),("SELECT F2 FROM T1 JOIN T2 ON T1.F2=T2.F2","SQL_UNQUALIFIED_FIELD_AMBIGUOUS"),("WITH x AS (SELECT T1.F1 AS X1 FROM T1) SELECT x.F1 FROM x","SQL_FIELD_OUT_OF_SCOPE"),("SELECT z.F1 FROM T1","SQL_QUALIFIER_OUT_OF_SCOPE")]
    for sql,code in cases:
      with self.subTest(sql=sql),self.assertRaisesRegex(ContractError,code): self.build(**candidate(sql))
 def test_sql_structures_allowed(self):
    for sql in ["SELECT t.F1 FROM T1 AS t JOIN T2 AS u ON t.F2=u.F2","SELECT F1, COUNT(F2) FROM T1 GROUP BY F1","SELECT F1 FROM T1 WHERE F1 IN (SELECT F1 FROM T1)","WITH x AS (SELECT T1.F1 FROM T1) SELECT x.F1 FROM x","SELECT y.X1 FROM (SELECT T1.F1 AS X1 FROM T1) AS y","SELECT F1 FROM T1 ORDER BY F1 LIMIT 5"]:
      with self.subTest(sql=sql): self.build(**candidate(sql))
 def test_160_producer_and_hash_rejected(self):
    old=self.build()
    with self.assertRaisesRegex(ContractError,"PRECHECK_PRODUCER_REJECTED"): self.repair(old,precheck(old,"170"))
    bad=copy.deepcopy(old);bad["payload"]["clear_question"]="drift"
    with self.assertRaisesRegex(ContractError,"CONTENT_HASH_DRIFT"):self.b.validate_pending_precheck(bad)
 def test_invalid_third_attempt_becomes_manual_block(self):
    old=self.build(); second=self.repair(old,precheck(old))
    third=self.b.handle_precheck_feedback(self.s,precheck(second),second,run_id="run150",qa_id="QA150",attempt_no=3,created_at=TIME,**candidate("SELECT F9 FROM T1","-人工"))
    self.assertEqual(third["envelope"]["status"],"blocked_manual")
 def review(self,old,kind="deepseek_review_result",producer=None,parents=None):
    who="170" if kind=="deepseek_review_result" else "180";p={"reviewed_package_ref":artifact_ref(old["envelope"]),"semantic_review_report":{"reviewer_id":who,"decision":"no","error_types":["QUESTION_SQL_ERROR","BUSINESS_EVENT_ERROR","QUESTION_FACT_OMISSION"],"error_details":[{}],"evidence_refs":[],"route_suggestion":"150"}};return wrap(kind,"review"+who,p,producer=producer or who,attempt=old["envelope"]["attempt_no"],parents=[hop()] if parents is None else parents)
 def test_170_180_full_route_matrix(self):
    old=self.build()
    for kind in ("deepseek_review_result","glm_review_result"):
      result=self.b.handle_routed_feedback(self.s,self.review(old,kind),old,run_id="run150",qa_id="QA150",attempt_no=2,created_at=TIME,**candidate(self.sql,"-审修"));self.assertEqual(result["envelope"]["attempt_no"],2)
    with self.assertRaisesRegex(ContractError,"REVIEW_PRODUCER_REJECTED"):self.b.handle_routed_feedback(self.s,self.review(old,producer="180"),old,run_id="run150",qa_id="QA150",attempt_no=2,created_at=TIME,**candidate(self.sql))
    with self.assertRaisesRegex(ContractError,"REVIEW_BYPASS_ROUTE_REJECTED"):self.b.handle_routed_feedback(self.s,self.review(old,parents=[]),old,run_id="run150",qa_id="QA150",attempt_no=2,created_at=TIME,**candidate(self.sql))
 def regression(self,old, payload=None, parents=None):
    p=payload or {"schema_version":"v5.sql-regression-failed-feedback/v1","mode":"event_data","input_data_refs":[],"input_orm_ref":None,"sandbox_snapshot_id":"copy","failure_details":{"error_code":"SQL_EXECUTION_ERROR","error_stage":"sql_execution","error_location":"sql","expected_values":[],"actual_values":[],"sql_error_detail":{"sql_text":self.sql,"error_code":"SQLITE","error_message":"bad"},"regression_metrics":{}},"route_target":"110","retry_count":1};return wrap("sql_regression_failed_feedback","reg150",p,producer="260",mode="event_data",parents=[hop("010"),hop()] if parents is None else parents)
 def test_260_full_payload_and_route_matrix(self):
    old=self.build();self.assertEqual(self.b.handle_routed_feedback(self.s,self.regression(old),old,run_id="run150",qa_id="QA150",attempt_no=2,created_at=TIME,**candidate(self.sql,"-260"))["envelope"]["attempt_no"],2)
    p=self.regression(old)["payload"];p.pop("retry_count")
    with self.assertRaisesRegex(ContractError,"REGRESSION_SCHEMA_REJECTED"):self.b.handle_routed_feedback(self.s,self.regression(old,p),old,run_id="run150",qa_id="QA150",attempt_no=2,created_at=TIME,**candidate(self.sql))
    with self.assertRaisesRegex(ContractError,"REGRESSION_BYPASS_ROUTE_REJECTED"):self.b.handle_routed_feedback(self.s,self.regression(old,parents=[]),old,run_id="run150",qa_id="QA150",attempt_no=2,created_at=TIME,**candidate(self.sql))
if __name__=="__main__":unittest.main()
