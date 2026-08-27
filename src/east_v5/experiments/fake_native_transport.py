"""Test-only fake transport for six *different* upstream response envelopes.

It is intentionally opt-in through ``transport_mode=fake_transport`` and is
never a substitute for a frozen upstream checkout in a real run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _row(adapter: str, qa_id: str, sql: str, tokens: int = 0) -> dict[str, object]:
    if adapter == "deepeye_sql_native_v1":
        return {"qa_id": qa_id, "final_sql": sql, "usage": {"calls": 1, "total_tokens": tokens or 11}}
    if adapter == "datagallery_text2sql_native_v1":
        return {"qa_id": qa_id, "dataagent_result": {"generated_sql": sql, "inference_stats": {"llm_calls": 1, "token_count": 12}}}
    if adapter == "joydataagent_sql_native_v1":
        return {"qa_id": qa_id, "joydata_answer": {"sql_text": sql, "model_cost": {"requests": 1, "tokens": 13}}}
    if adapter == "databao_agent_native_v1":
        return {"qa_id": qa_id, "databao_thread": {"sql": sql, "telemetry": {"llm_calls": 1, "tokens": 14}}}
    if adapter == "reforce_native_v1":
        return {"qa_id": qa_id, "reforce_result": {"selected_sql": sql, "refinement": {"generation_calls": 1, "token_total": 15}}}
    if adapter == "autolink_native_v1":
        return {"qa_id": qa_id, "autolink_result": {"final_query": sql, "exploration": {"model_calls": 1, "tokens": 16}}}
    raise ValueError("unknown adapter")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--mode", choices=["success", "transport-error", "model-unavailable", "invalid-response", "echo-secret", "illegal-sql", "budget-exceeded", "sleep"], default="success")
    args = parser.parse_args()
    if args.mode == "transport-error":
        print("NATIVE_TRANSPORT_ERROR", file=sys.stderr); return 71
    if args.mode == "model-unavailable":
        print("NATIVE_MODEL_UNAVAILABLE", file=sys.stderr); return 72
    if args.mode == "echo-secret":
        print("secret=" + __import__("os").environ.get("DEEPSEEK_API_KEY", ""), file=sys.stderr); return 71
    if args.mode == "sleep":
        time.sleep(5)
    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    rows = []
    for question in data["questions"]:
        sql = "UPDATE public_s0_table SET synthetic_value = 1" if args.mode == "illegal-sql" else "SELECT synthetic_value FROM public_s0_table WHERE qa_id = '" + question["qa_id"] + "'"
        row = _row(args.adapter_id, question["qa_id"], sql, 999999 if args.mode == "budget-exceeded" else 0)
        if args.mode == "invalid-response": row = {"qa_id": question["qa_id"], "invalid": True}
        rows.append(row)
    Path(args.output_jsonl).write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
