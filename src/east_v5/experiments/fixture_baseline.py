from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _normal_sql(baseline_id: str, qa_id: str) -> str:
    suffix = baseline_id.replace("-", "_").lower()
    return f"SELECT synthetic_value FROM public_s0_table WHERE qa_id = '{qa_id}' AND baseline_id = '{suffix}'"


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic S0 baseline used only for Stage 1 smoke tests.")
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--mode", choices=["normal", "illegal-sql", "transport-error", "budget-exceeded", "sleep"], default="normal")
    args = parser.parse_args()

    if args.mode == "transport-error":
        print("SYNTHETIC_TRANSPORT_ERROR", file=sys.stderr)
        return 7
    if args.mode == "sleep":
        time.sleep(5)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        for question in dataset["questions"]:
            qa_id = question["qa_id"]
            sql = "UPDATE public_s0_table SET synthetic_value = 1" if args.mode == "illegal-sql" else _normal_sql(args.baseline_id, qa_id)
            token_total = 999999 if args.mode == "budget-exceeded" else 32
            handle.write(json.dumps({"qa_id": qa_id, "sql": sql, "token_calls": 1, "token_total": token_total}, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
