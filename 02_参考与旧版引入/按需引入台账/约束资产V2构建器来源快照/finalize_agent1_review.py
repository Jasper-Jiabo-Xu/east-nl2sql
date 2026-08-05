#!/usr/bin/env python3
"""Create one auditable Agent1 review ledger from a full batch plus repair batches."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .pipeline import now, write_json


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def apply_final_human_scope_decisions(record: dict[str, Any]) -> dict[str, Any]:
    """Record confirmed review outcomes without treating excluded items as assets."""
    result = dict(record)
    code = result["data_element_code"]
    if code == "001032":
        result["hard_format_constraints"] = {
            **result["hard_format_constraints"],
            "format_parse_status": "PARSED_BY_APPROVED_OVERRIDE",
            "data_type": "INTEGER",
            "string_length_exact": None,
            "string_length_max": None,
            "integer_max_digits": 20,
            "decimal_max_fraction_digits": None,
            "parser_policy_ref": "SRC_POLICY:人工确认格式规则!001032",
            "override_reason": "人工确认：数量无E列明确约束，按最大20位整数处理。",
        }
        result["review_reasons"] = [reason for reason in result["review_reasons"] if reason != "HARD_FORMAT_UNSUPPORTED"]
        result["human_decision"] = "数量无明确E列约束；按最大20位整数处理。"
    if code == "010075":
        result["scope_status"] = "EXCLUDED_NO_CORRESPONDING_FIELD"
        result["human_decision"] = "数据结构中无对应字段；排除出字段约束资产，不发布。"
        result["review_reasons"] = []
    else:
        result.setdefault("scope_status", "IN_SCOPE")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-review", type=Path, required=True)
    parser.add_argument("--repair-review", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = load(args.base_review)
    records = {item["data_element_code"]: item for item in base["records"]}
    repairs: dict[str, dict[str, Any]] = {}
    for repair_path in args.repair_review:
        repair = load(repair_path)
        for record in repair["records"]:
            if record["run_status"] != "VALIDATED":
                raise RuntimeError(f"修复批次仍有未校验项: {record['data_element_code']}")
            repaired = dict(record)
            repaired["repair_provenance"] = str(repair_path)
            repairs[record["data_element_code"]] = repaired
    for code, repaired in repairs.items():
        if code not in records:
            raise RuntimeError(f"修复项不在全量批次中: {code}")
        records[code] = repaired
    ordered = [apply_final_human_scope_decisions(records[code]) for code in sorted(records)]
    failures = [record["data_element_code"] for record in ordered if record["run_status"] != "VALIDATED"]
    if failures:
        raise RuntimeError(f"完整审查清单仍存在合同失败: {failures}")
    review = {
        "created_at": now(),
        "contract_version": base["contract_version"],
        "publication_status": "review_only_not_released",
        "task_count": len(ordered),
        "base_review": str(args.base_review),
        "repair_reviews": [str(path) for path in args.repair_review],
        "repaired_data_element_codes": sorted(repairs),
        "run_status_counts": dict(sorted(Counter(item["run_status"] for item in ordered).items())),
        "extraction_status_counts": dict(sorted(Counter(item["extraction_status"] for item in ordered).items())),
        "excluded_data_element_codes": [item["data_element_code"] for item in ordered if item.get("scope_status") == "EXCLUDED_NO_CORRESPONDING_FIELD"],
        "manual_review_codes": [item["data_element_code"] for item in ordered if item.get("scope_status") != "EXCLUDED_NO_CORRESPONDING_FIELD" and item["review_reasons"]],
        "other_review_codes": [item["data_element_code"] for item in ordered if any(constraint["constraint_item_type"] == "OTHER" for constraint in item["semantic_constraints"])],
        "records": ordered,
    }
    write_json(args.output, review)
    print(json.dumps({key: value for key, value in review.items() if key != "records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
