"""Local, desensitized minimum run for Agent 150's contract boundary."""
from __future__ import annotations
import json
from pathlib import Path
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.agents.east_150.extractor import PendingPrecheckBuilder
from east_v5.governance import ContractError
TIME="2026-08-16T00:00:00+00:00"
def _wrap(kind,identity,payload,producer,attempt=1):
 env={"artifact_id":identity,"artifact_type":kind,"run_id":"eas24-probe","qa_id":"QA-EAS24","version":1,"schema_version":"COMMON-ENVELOPE/v1","content_hash":"0"*64,"supersedes_ref":None,"attempt_no":attempt,"producer_id":producer,"parent_artifact_refs":[],"input_hashes":[],"status":"candidate","mode":"question_sql","created_at":TIME,"trace_id":"eas24-probe","storage_locator":None};env["content_hash"]=content_hash(env,payload);return {"envelope":env,"payload":payload}
def _spec():
 p={"query_spec_id":"qspec-024","penalty_fact_package_ref":{"artifact_id":"penalty","version":1,"content_hash":"a"*64},"observable_fact_package_ref":{"artifact_id":"observable","version":1,"content_hash":"b"*64},"query_goal":"脱敏风险筛查","must_preserve_fact_refs":["fact-1"],"main_object_and_grain":{"main_object":"机构","grain":"记录"},"query_entry":{"entry_table":"T1","entry_conditions":[]},"related_objects_and_path":[],"filters_and_evidence":[],"return_fields":[{"field_id":"F1","display_name":"字段1","source_table":"T1"}],"aggregation_dedup_sort_time":{"group_by_fields":[]},"observability_boundary":{"answerable":["风险"],"unanswerable":[]},"expected_result_shape":{"row_grain":"记录","column_set":["F1"],"aggregation_shape":"none"},"sql_schema_scope":{"allowed_tables":[{"table_id":"T1","allowed_fields":["F1","F2"]}]},"minimum_positive_count":1,"minimum_negative_count":1,"condition_coverage":[],"code_value_coverage":[],"expected_row_group_count":{"minimum":1,"target":1,"tolerance_range":{"low":1,"high":1}},"join_expansion_limit":{"max_multiplier":1,"max_result_rows":5},"query_specification_package_schema_version":"query-specification-v1"};return _wrap("query_specification_package","qspec024",p,"140")
def run_sanitized_probe(root:Path):
 builder,query=PendingPrecheckBuilder(root),_spec();first=builder.build_pending_precheck(query,run_id="eas24-probe",qa_id="QA-EAS24",sql_gold="SELECT T1.F1 FROM T1",created_at=TIME);feedback=_wrap("precheck_failed_feedback","feedback024",{"candidate_ref":artifact_ref(first["envelope"]),"precheck_decision":"fail","failed_items":[{"failed_rule_ids":["F1"],"error_locations":["sql_gold"],"expected_values":"field","actual_values":"field","error_details":"retry"}]},"160");second=builder.handle_precheck_feedback(query,feedback,first,run_id="eas24-probe",qa_id="QA-EAS24",attempt_no=2,sql_gold="SELECT T1.F1 FROM T1",created_at=TIME);rejected=False
 try: builder.build_pending_precheck(query,run_id="eas24-probe",qa_id="QA-EAS24",sql_gold="DELETE FROM T1",created_at=TIME)
 except ContractError: rejected=True
 return {"summary":{"candidate_valid":second["envelope"]["status"]=="candidate","input_rejected":rejected,"stub_160_consumed":bool(second["payload"]["candidate_id"]),"content_hash":second["envelope"]["content_hash"]}}
if __name__=="__main__": print(json.dumps(run_sanitized_probe(Path(__file__).resolve().parents[4])["summary"],ensure_ascii=False,sort_keys=True))
