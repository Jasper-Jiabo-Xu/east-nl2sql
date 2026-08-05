#!/usr/bin/env python3
"""Export the final Agent1 OTHER queue as a deliberately minimal Excel review sheet."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .pipeline import write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows-json", type=Path, required=True)
    args = parser.parse_args()

    review = json.loads(args.review.read_text(encoding="utf-8"))
    rows = [["数据元编码", "数据元名称", "数据元说明"]]
    for record in review["records"]:
        if any(item["constraint_item_type"] == "OTHER" for item in record["semantic_constraints"]):
            rows.append([record["data_element_code"], record["data_element_name"], record["description"]])
    write_json(args.rows_json, rows)
    subprocess.run([
        "uvx", "--with", "click", "agent-xlsx", "write", str(args.output), "A1", "--from-json", str(args.rows_json),
    ], check=True)
    print(json.dumps({"output": str(args.output), "row_count_including_header": len(rows), "data_row_count": len(rows) - 1}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
