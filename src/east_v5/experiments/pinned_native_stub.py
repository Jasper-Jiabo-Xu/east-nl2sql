"""Local-only pinned-worktree stub; never selected by a native manifest."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--adapter-id", required=True); p.add_argument("--dataset", required=True); p.add_argument("--output-jsonl", required=True); p.add_argument("--provider-config", required=True); p.add_argument("--mode", default="success")
    a = p.parse_args(); config = json.loads(Path(a.provider_config).read_text())
    if set(config) != {"provider", "model_id", "base_url", "endpoint_kind", "api_key_env", "reasoning_enabled", "temperature", "max_tokens", "timeout_seconds", "retry_count"}: return 73
    if a.mode == "transport-error":
        print("NATIVE_TRANSPORT_ERROR", file=sys.stderr)
        return 71
    if a.mode == "model-unavailable":
        print("NATIVE_MODEL_UNAVAILABLE", file=sys.stderr)
        return 72
    if a.mode == "echo-secret":
        print(os.environ.get("DEEPSEEK_API_KEY", ""), file=sys.stderr)
        return 71
    if a.mode == "sleep":
        time.sleep(3)
        return 0
    rows=[]
    for q in json.loads(Path(a.dataset).read_text())["questions"]:
        sql = "SELECT value FROM synthetic WHERE qa_id = '" + q["qa_id"] + "'"
        if a.mode == "illegal-sql":
            sql = "DELETE FROM synthetic"
        if a.adapter_id == "deepeye_sql_native_v1": body={"deepeye_result":{"final_sql":sql,"usage":{"calls":1,"total_tokens":11}}}
        elif a.adapter_id == "datagallery_text2sql_native_v1": body={"dataagent_result":{"generated_sql":sql,"metrics":{"llm_calls":1,"token_count":12}}}
        elif a.adapter_id == "joydataagent_sql_native_v1": body={"joydata_answer":{"sql_text":sql,"model_cost":{"requests":1,"tokens":13}}}
        elif a.adapter_id == "databao_agent_native_v1": body={"databao_thread":{"sql":sql,"telemetry":{"llm_calls":1,"tokens":14}}}
        elif a.adapter_id == "reforce_native_v1": body={"reforce_result":{"selected_sql":sql,"refinement":{"generation_calls":1,"token_total":15}}}
        else: body={"autolink_result":{"final_query":sql,"exploration":{"model_calls":1,"tokens":16}}}
        if a.mode == "budget-exceeded":
            for value in body.values():
                for metric_name in ("usage", "metrics", "model_cost", "telemetry", "refinement", "exploration"):
                    if isinstance(value.get(metric_name), dict):
                        metrics = value[metric_name]
                        for token_name in ("total_tokens", "token_count", "tokens", "token_total"):
                            if token_name in metrics:
                                metrics[token_name] = 100000
        if a.mode == "invalid-response":
            rows.append({"qa_id": q["qa_id"], "unexpected": body})
        elif a.mode == "budget-exceeded":
            rows.append({"qa_id": q["qa_id"], **body, "budget_exceeded": True})
        else:
            rows.append({"qa_id":q["qa_id"],**body})
    Path(a.output_jsonl).write_text("".join(json.dumps(x)+"\n" for x in rows)); return 0
if __name__ == "__main__": raise SystemExit(main())
