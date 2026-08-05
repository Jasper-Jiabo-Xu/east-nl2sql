#!/usr/bin/env python3
"""Compile the human-approved, EAST-scoped Agent1 core constraint library.

This builder intentionally has no LLM call.  It consumes the immutable Agent1
review, the user's Excel review annotations exported through ``agent-xlsx``, and
the Attachment-3 self-operated-business sheet exported through ``agent-xlsx``.
It materialises two query libraries:

* ``constraint_asset_core.sqlite`` -- all 301 data elements and their effective
  constraints, source references, review overlay and external-code-table links;
* ``local_code_value_library_core.sqlite`` -- one physical table per local code
  set.  Attachment-4 / national tables are referenced, never copied.

The Excel workbook generated at the end is a human review view only.  It is not
read by agents and cannot become an authority over the two SQLite databases.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
WORKING = ROOT / "kb/working/20260727_000_goal_mode_v1"
PHASE = WORKING / "构建过程层/04_字段多字段与对象明细状态提取-V2"
DEFAULT_REVIEW = PHASE / "agent1_runs/20260805_agent1_v3_3_complete_review_after_human_decisions/agent1_complete_review.json"
DEFAULT_HUMAN_CSV = PHASE / "core_east_asset_v1/Agent1_OTHER人工审查表_人工批示原始快照.csv"
DEFAULT_SELF_CSV = PHASE / "core_east_asset_v1/自营报送范围_原始快照.csv"
REFERENCE_LIBRARY = PHASE / "code_value_libraries/20260805_一表一码表_层级补全_v4/code_reference_library_v3.sqlite"
DEFAULT_OUTPUT = PHASE / "core_east_asset_v1/20260805_人工审批后核心库"

CORE_VERSION = "constraint-assets-core-east-v1-20260805"
SOURCE_LAYER = "EAST_CORE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def local_table_name(code_set_id: str) -> str:
    return "local_" + re.sub(r"[^a-z0-9]+", "_", code_set_id.lower()).strip("_")[:52]


def cell_ref_from_review_row(row_no: int) -> str:
    return f"SRC_HUMAN_REVIEW:Agent1_OTHER人工审查表.xlsx!E{row_no}"


def read_human_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0][:3] != ["数据元编码", "数据元名称", "数据元说明"]:
        raise RuntimeError("人工审查 CSV 表头不符合预期")
    result: dict[str, dict[str, str]] = {}
    for row_no, row in enumerate(rows[1:], start=2):
        if len(row) < 4 or not row[0].strip():
            continue
        result[row[0].strip()] = {
            "name": row[1].strip(), "annotation": row[3].strip(),
            "source_ref": cell_ref_from_review_row(row_no),
        }
    return result


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [[item.strip() for item in row] for row in csv.reader(handle)]


def literal(value: str, source_ref: str, evidence: str | None = None) -> dict[str, Any]:
    return {
        "value_kind": "LITERAL", "literal_value": value, "value_code": None,
        "value_label": None, "source_refs": [source_ref],
        "evidence_quote": evidence or value,
    }


def code_label(code: str, label: str, source_ref: str, evidence: str | None = None) -> dict[str, Any]:
    return {
        "value_kind": "CODE_LABEL", "literal_value": None, "value_code": code,
        "value_label": label, "source_refs": [source_ref],
        "evidence_quote": evidence or f"{code}（{label}）",
    }


def normalise_other(value: str | None) -> str | None:
    """Apply the confirmed core rule only to open placeholder spellings."""
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned in {"其他-", "其他-XX", "其他－XX", "其他-银行自定义", "银行自定义"}:
        return "其他"
    return cleaned


def apply_confirmed_overrides(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return auditable operations while editing an effective copy only."""
    code = record["data_element_code"]
    operations: list[dict[str, Any]] = []
    for value_set in record.get("code_value_sets", []):
        before = value_set.get("extension_policy")
        # The approved generator domain is closed.  EAST's “其他-XX/银行自定义”
        # wording is represented by literal “其他”, never an unconstrained domain.
        value_set["extension_policy"] = "CLOSED"
        for value in value_set.get("values", []):
            if value.get("value_kind") == "LITERAL":
                old = value.get("literal_value")
                new = normalise_other(old)
                if old != new:
                    value["literal_value"] = new
                    operations.append({"operation_type": "NORMALIZE_OTHER_LITERAL", "before": old, "after": new})
        if before != "CLOSED":
            operations.append({"operation_type": "CLOSE_CODE_SET", "set_name": value_set["set_name"], "before": before, "after": "CLOSED"})

    remove_other = code in {"001011", "005004", "005038"} or bool(record.get("code_value_sets"))
    if remove_other:
        prior = record.get("semantic_constraints", [])
        kept = []
        for item in prior:
            text = str(item.get("value", {}).get("other_text", ""))
            if item.get("constraint_item_type") == "OTHER" and (
                code in {"001011", "005004", "005038"}
                or "其他-" in text or "银行自定义" in text or "无法以枚举" in text
            ):
                operations.append({"operation_type": "REMOVE_SUPERSEDED_OTHER", "before": text})
            else:
                kept.append(item)
        record["semantic_constraints"] = kept

    if code == "002002":
        source = "SRC_HUMAN_REVIEW:Agent1_OTHER人工审查表.xlsx!E19"
        record["code_value_sets"].append({
            "set_name": "其他客户证件类别", "scope_kind": "CONDITIONAL",
            "condition": {"dimension_name": "客户类型", "dimension_value": "其他客户"},
            "extension_policy": "CLOSED", "evidence_quote": "其他：其他客户。",
            "source_refs": [source], "values": [literal("其他客户", source, "其他客户")],
            "provenance_type": "HUMAN_DECISION",
        })
        operations.append({"operation_type": "ADD_CONDITIONAL_CODE_SET", "set_name": "其他客户证件类别"})
    return operations


def manual_code_values() -> dict[str, list[str]]:
    """Approved simple literal tables absent from the Agent1 response.

    Values are deliberately transcribed from column E, not inferred from business
    knowledge.  Items marked “暂定/需要外部知识” are intentionally absent.
    """
    return {
        "005042": ["第一顺位", "第二顺位", "第三顺位", "第四顺位", "第五顺位", "第六顺位", "第七顺位"],
        "010010": ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "SD", "D", "未评级"],
    }


def add_manual_simple_sets(records: dict[str, dict[str, Any]], human: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    values = manual_code_values()
    for code, items in values.items():
        source = human[code]["source_ref"]
        if code == "010010":
            # Shared below with 010071, so do not create two duplicate tables.
            continue
        records[code]["code_value_sets"].append({
            "set_name": f"{records[code]['data_element_name']}人工码表", "scope_kind": "GLOBAL", "condition": None,
            "extension_policy": "CLOSED", "evidence_quote": human[code]["annotation"], "source_refs": [source],
            "values": [literal(normalise_other(item) or item, source, item) for item in items],
            "provenance_type": "HUMAN_DECISION",
        })
        operations.append({"data_element_code": code, "operation_type": "ADD_MANUAL_LITERAL_CODE_SET"})
    return operations


def parse_statistical_subjects(text: str, source_ref: str) -> tuple[list[dict[str, Any]], bool]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    currency = group = ""
    incomplete = False
    for raw in text.splitlines():
        line = raw.strip().replace("：", ":")
        if not line or line.startswith("码表"):
            continue
        if "……" in line or "至" in line:
            incomplete = True
            continue
        if line.endswith(":"):
            heading = line[:-1]
            if heading in {"人民币口径", "外汇口径"}:
                currency, group = heading, ""
                key = currency
                if key not in seen:
                    nodes.append({"node_key": key, "parent_node_key": None, "level": 1, "code": None, "label": heading, "selectable": 0, "source_ref": source_ref})
                    seen.add(key)
            else:
                group = heading
                key = f"{currency}/{group}"
                if key not in seen:
                    nodes.append({"node_key": key, "parent_node_key": currency or None, "level": 2, "code": None, "label": group, "selectable": 0, "source_ref": source_ref})
                    seen.add(key)
            continue
        matched = re.match(r"^(.*)（([0-9A-Z]+)）$", line)
        if not matched:
            continue
        label, code = matched.group(1).strip(), matched.group(2)
        key = f"{currency}/{group}/{code}"
        if key not in seen:
            nodes.append({"node_key": key, "parent_node_key": f"{currency}/{group}", "level": 3, "code": code, "label": label, "selectable": 1, "source_ref": source_ref})
            seen.add(key)
    return nodes, incomplete


def parse_risk_hierarchy(text: str, source_ref: str) -> tuple[list[dict[str, Any]], bool]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    subject = category = ""
    incomplete = False
    for raw in text.splitlines():
        line = raw.strip().replace("：", ":")
        if not line or line.startswith("码表"):
            continue
        if "……" in line or "至 I08" in line:
            incomplete = True
            continue
        if line in {"集团:", "法人:"}:
            subject, category = line[:-1], ""
            if subject not in seen:
                nodes.append({"node_key": subject, "parent_node_key": None, "level": 1, "code": None, "label": subject, "selectable": 0, "source_ref": source_ref})
                seen.add(subject)
            continue
        category_match = re.match(r"^([A-Z])\s+(.+?)(?::)?$", line)
        if category_match and "（" not in line:
            code, label = category_match.groups()
            if not subject:
                subject = "全部适用"
                if subject not in seen:
                    nodes.append({"node_key": subject, "parent_node_key": None, "level": 1, "code": None, "label": subject, "selectable": 0, "source_ref": source_ref})
                    seen.add(subject)
            category = code
            key = f"{subject}/{code}"
            if key not in seen:
                nodes.append({"node_key": key, "parent_node_key": subject or None, "level": 2, "code": code, "label": label.rstrip(":"), "selectable": 0, "source_ref": source_ref})
                seen.add(key)
            continue
        leaf = re.match(r"^([A-Z](?:\d{2})?)（(.+)）$", line)
        if leaf:
            code, label = leaf.groups()
            key = f"{subject}/{category}/{code}"
            if key not in seen:
                nodes.append({"node_key": key, "parent_node_key": f"{subject}/{category}" if category else subject or None, "level": 3, "code": code, "label": label, "selectable": 1, "source_ref": source_ref})
                seen.add(key)
    return nodes, incomplete


def parse_self_business_scope(rows: list[list[str]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    major = middle = ""
    # agent-xlsx exports the title row as CSV header and leaves the original
    # business-level header as data row 1; original Excel row 4 is CSV row 2.
    for row_no, row in enumerate(rows[2:], start=4):
        values = (row + ["", "", ""])[:3]
        if values[0]:
            major = normalise_other(values[0]) or values[0]
            middle = ""
            key = major
            if key not in seen:
                nodes.append({"node_key": key, "parent_node_key": None, "level": 1, "code": None, "label": major, "selectable": 1, "source_ref": f"SRC_ATT3:自营报送范围!A{row_no}:C{row_no}"})
                seen.add(key)
        if values[1]:
            middle = normalise_other(values[1]) or values[1]
            key = f"{major}/{middle}"
            if key not in seen:
                nodes.append({"node_key": key, "parent_node_key": major or None, "level": 2, "code": None, "label": middle, "selectable": 1, "source_ref": f"SRC_ATT3:自营报送范围!A{row_no}:C{row_no}"})
                seen.add(key)
        if values[2]:
            small = normalise_other(values[2]) or values[2]
            key = f"{major}/{middle}/{small}"
            if key not in seen:
                nodes.append({"node_key": key, "parent_node_key": f"{major}/{middle}" if middle else major or None, "level": 3, "code": None, "label": small, "selectable": 1, "source_ref": f"SRC_ATT3:自营报送范围!A{row_no}:C{row_no}"})
                seen.add(key)
    return nodes


def create_constraint_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE asset_version (
      asset_version TEXT PRIMARY KEY, source_layer TEXT NOT NULL, publication_status TEXT NOT NULL,
      created_at TEXT NOT NULL, base_review_sha256 TEXT NOT NULL, human_review_sha256 TEXT NOT NULL,
      reference_library_path TEXT NOT NULL, reference_library_sha256 TEXT NOT NULL, notes TEXT NOT NULL
    );
    CREATE TABLE data_element (
      data_element_code TEXT PRIMARY KEY, data_element_name TEXT NOT NULL, description TEXT NOT NULL,
      extraction_status TEXT NOT NULL, scope_status TEXT NOT NULL, core_status TEXT NOT NULL,
      format_status TEXT NOT NULL, data_type TEXT, string_length_exact INTEGER, string_length_max INTEGER,
      integer_max_digits INTEGER, decimal_max_fraction_digits INTEGER, format_source_refs_json TEXT NOT NULL,
      human_decision TEXT, raw_record_sha256 TEXT NOT NULL
    );
    CREATE TABLE semantic_constraint (
      constraint_id TEXT PRIMARY KEY, data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
      item_type TEXT NOT NULL, value_json TEXT NOT NULL, evidence_quote TEXT NOT NULL, source_refs_json TEXT NOT NULL,
      provenance_type TEXT NOT NULL
    );
    CREATE TABLE encoding_rule (
      encoding_rule_id TEXT PRIMARY KEY, data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
      rule_name TEXT NOT NULL, exact_length INTEGER, character_classes_json TEXT NOT NULL,
      evidence_quote TEXT NOT NULL, source_refs_json TEXT NOT NULL, provenance_type TEXT NOT NULL
    );
    CREATE TABLE encoding_segment (
      segment_id TEXT PRIMARY KEY, encoding_rule_id TEXT NOT NULL REFERENCES encoding_rule(encoding_rule_id),
      start_pos INTEGER NOT NULL, end_pos INTEGER NOT NULL, segment_name TEXT NOT NULL, segment_kind TEXT NOT NULL,
      character_classes_json TEXT NOT NULL, evidence_quote TEXT NOT NULL, source_refs_json TEXT NOT NULL
    );
    CREATE TABLE standard_reference (
      standard_reference_id TEXT PRIMARY KEY, data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
      standard_mention_raw TEXT NOT NULL, external_standard_id TEXT, mapping_status TEXT NOT NULL,
      representation_kind TEXT NOT NULL, evidence_quote TEXT NOT NULL, source_refs_json TEXT NOT NULL
    );
    CREATE TABLE external_code_table_usage (
      usage_id TEXT PRIMARY KEY, data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
      code_table_id TEXT NOT NULL, sqlite_table_name TEXT NOT NULL, external_standard_id TEXT,
      selection_mode TEXT NOT NULL, final_value_columns_json TEXT NOT NULL, final_value_template TEXT,
      substring_start INTEGER, substring_end INTEGER, usage_role TEXT NOT NULL, selection_criteria_json TEXT NOT NULL,
      evidence_quote TEXT NOT NULL, source_refs_json TEXT NOT NULL, provenance_type TEXT NOT NULL
    );
    CREATE TABLE local_code_table_usage (
      usage_id TEXT PRIMARY KEY, data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
      local_code_set_id TEXT NOT NULL, selection_field TEXT NOT NULL, extension_policy TEXT NOT NULL,
      selection_criteria_json TEXT NOT NULL,
      evidence_quote TEXT NOT NULL, source_refs_json TEXT NOT NULL, provenance_type TEXT NOT NULL
    );
    CREATE TABLE text_pattern_constraint (
      pattern_id TEXT PRIMARY KEY, data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
      pattern_name TEXT NOT NULL, regex_pattern TEXT NOT NULL, semantic_checks_json TEXT NOT NULL,
      punctuation_policy TEXT NOT NULL, evidence_quote TEXT NOT NULL, source_refs_json TEXT NOT NULL,
      provenance_type TEXT NOT NULL
    );
    CREATE TABLE human_overlay (
      overlay_id TEXT PRIMARY KEY, data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
      review_annotation TEXT NOT NULL, review_source_ref TEXT NOT NULL, disposition TEXT NOT NULL,
      operation_json TEXT NOT NULL, rationale TEXT NOT NULL
    );
    CREATE INDEX idx_semantic_element ON semantic_constraint(data_element_code);
    CREATE INDEX idx_local_usage_element ON local_code_table_usage(data_element_code);
    CREATE INDEX idx_external_usage_element ON external_code_table_usage(data_element_code);
    """)


def create_local_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE local_code_set_registry (
      local_code_set_id TEXT PRIMARY KEY, sqlite_table_name TEXT NOT NULL UNIQUE,
      set_name TEXT NOT NULL, source_layer TEXT NOT NULL, provenance_type TEXT NOT NULL,
      hierarchy_kind TEXT NOT NULL, completeness_status TEXT NOT NULL,
      source_refs_json TEXT NOT NULL, value_count INTEGER NOT NULL, canonical_hash TEXT NOT NULL UNIQUE
    );
    """)


def create_physical_local_table(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"""
      CREATE TABLE {q(name)} (
        source_ordinal INTEGER NOT NULL, source_ref TEXT NOT NULL, provenance_type TEXT NOT NULL,
        node_key TEXT NOT NULL UNIQUE, parent_node_key TEXT, hierarchy_level INTEGER,
        condition_dimension TEXT, condition_value TEXT,
        value_kind TEXT NOT NULL, literal_value TEXT, value_code TEXT, value_label TEXT,
        is_selectable INTEGER NOT NULL CHECK(is_selectable IN (0,1)), raw_value_json TEXT NOT NULL
      )
    """)
    conn.execute(f"CREATE INDEX {q(name + '_parent')} ON {q(name)}(parent_node_key)")
    conn.execute(f"CREATE INDEX {q(name + '_code')} ON {q(name)}(value_code, literal_value)")


class LocalRegistry:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.by_hash: dict[str, str] = {}
        self.rows_for_excel: list[dict[str, Any]] = []

    def register(self, *, set_id: str, set_name: str, nodes: list[dict[str, Any]], source_refs: list[str],
                 provenance: str, hierarchy_kind: str = "FLAT", completeness: str = "COMPLETE") -> str:
        normalized = [{key: node.get(key) for key in ("node_key", "parent_node_key", "level", "condition_dimension", "condition_value", "value_kind", "literal_value", "value_code", "value_label", "selectable")} for node in nodes]
        signature = digest({"nodes": normalized, "hierarchy_kind": hierarchy_kind})
        if signature in self.by_hash:
            return self.by_hash[signature]
        table = local_table_name(set_id)
        suffix = 2
        candidate = table
        while self.conn.execute("SELECT 1 FROM local_code_set_registry WHERE sqlite_table_name=?", (candidate,)).fetchone():
            candidate = f"{table[:55]}_{suffix}"
            suffix += 1
        table = candidate
        create_physical_local_table(self.conn, table)
        for ordinal, node in enumerate(nodes, start=1):
            raw = canonical(node)
            self.conn.execute(
                f"INSERT INTO {q(table)} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ordinal, node.get("source_ref") or source_refs[0], provenance, node["node_key"], node.get("parent_node_key"),
                 node.get("level"), node.get("condition_dimension"), node.get("condition_value"),
                 node.get("value_kind", "LITERAL"), node.get("literal_value"), node.get("code"), node.get("label"),
                 int(bool(node.get("selectable", True))), raw),
            )
            self.rows_for_excel.append({"local_code_set_id": set_id, "sqlite_table_name": table, **node})
        self.conn.execute(
            "INSERT INTO local_code_set_registry VALUES (?,?,?,?,?,?,?,?,?,?)",
            (set_id, table, set_name, SOURCE_LAYER, provenance, hierarchy_kind, completeness,
             canonical(source_refs), len(nodes), signature),
        )
        self.by_hash[signature] = set_id
        return set_id


def nodes_from_agent_set(value_set: dict[str, Any]) -> list[dict[str, Any]]:
    condition = value_set.get("condition") or {}
    nodes = []
    for index, value in enumerate(value_set["values"], start=1):
        literal_value = normalise_other(value.get("literal_value"))
        code = value.get("value_code")
        label = value.get("value_label")
        key_value = literal_value or f"{code}|{label}"
        nodes.append({
            "node_key": f"V{index}:{key_value}", "parent_node_key": None, "level": 1,
            "condition_dimension": condition.get("dimension_name"), "condition_value": condition.get("dimension_value"),
            "value_kind": value["value_kind"], "literal_value": literal_value, "code": code, "label": label,
            "selectable": 1, "source_ref": (value.get("source_refs") or value_set["source_refs"])[0],
        })
    return nodes


def insert_record(conn: sqlite3.Connection, record: dict[str, Any], core_status: str) -> None:
    format_data = record["hard_format_constraints"]
    conn.execute(
        "INSERT INTO data_element VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (record["data_element_code"], record["data_element_name"], record["description"], record["extraction_status"],
         record.get("scope_status", "IN_SCOPE"), core_status, format_data["format_parse_status"], format_data.get("data_type"),
         format_data.get("string_length_exact"), format_data.get("string_length_max"), format_data.get("integer_max_digits"),
         format_data.get("decimal_max_fraction_digits"), canonical(format_data["source_refs"]), record.get("human_decision"), digest(record)),
    )
    code = record["data_element_code"]
    for ordinal, item in enumerate(record.get("semantic_constraints", []), start=1):
        conn.execute("INSERT INTO semantic_constraint VALUES (?,?,?,?,?,?,?)", (f"SC_{code}_{ordinal}", code, item["constraint_item_type"], canonical(item["value"]), item["evidence_quote"], canonical(item["source_refs"]), item.get("provenance_type", "EAST_SOURCE")))
    for ordinal, rule in enumerate(record.get("encoding_rules", []), start=1):
        rid = f"ER_{code}_{ordinal}"
        conn.execute("INSERT INTO encoding_rule VALUES (?,?,?,?,?,?,?,?)", (rid, code, rule["rule_name"], rule.get("exact_length"), canonical(rule["character_classes"]), rule["evidence_quote"], canonical(rule["source_refs"]), rule.get("provenance_type", "EAST_SOURCE")))
        for seg_no, segment in enumerate(rule["segments"], start=1):
            conn.execute("INSERT INTO encoding_segment VALUES (?,?,?,?,?,?,?,?,?)", (f"ES_{code}_{ordinal}_{seg_no}", rid, segment["start_pos"], segment["end_pos"], segment["segment_name"], segment["segment_kind"], canonical(segment["character_classes"]), segment["evidence_quote"], canonical(segment["source_refs"])))
    for ordinal, standard in enumerate(record.get("standard_references", []), start=1):
        conn.execute("INSERT INTO standard_reference VALUES (?,?,?,?,?,?,?,?)", (f"SR_{code}_{ordinal}", code, standard["standard_mention_raw"], standard.get("external_standard_id"), standard["mapping_status"], standard["representation_kind"], standard["evidence_quote"], canonical(standard["source_refs"])))
    for ordinal, usage in enumerate(record.get("code_table_usages", []), start=1):
        conn.execute("INSERT INTO external_code_table_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"EU_{code}_{ordinal}", code, usage["code_table_id"], usage["sqlite_table_name"], usage.get("external_standard_id"), usage["selection_mode"], canonical(usage["final_value_columns"]), usage.get("final_value_template"), usage.get("substring_start"), usage.get("substring_end"), "VALUE_DOMAIN", canonical({}), usage["evidence_quote"], canonical(usage["source_refs"]), usage.get("provenance_type", "EAST_SOURCE")))


def east_source_refs(record: dict[str, Any]) -> list[str]:
    """Find the original Attachment-3 source refs already validated by Agent1."""
    refs: list[str] = []
    for group in ("semantic_constraints", "code_value_sets", "encoding_rules", "standard_references", "code_table_usages"):
        for item in record.get(group, []):
            refs.extend(item.get("source_refs", []))
    return list(dict.fromkeys(refs))


def annotation_disposition(code: str, text: str) -> tuple[str, str]:
    if code in {"001011", "001037", "002002", "002016", "003007", "003008", "003014", "005004", "005038", "009002", "009003", "010010", "010024", "010028", "010029", "010030", "010071"}:
        return "APPLIED_CORE", "可由 EAST 原文、附件码表或明确人工码表裁决直接物化。"
    if "码表" in text and "需要外部知识" not in text and "暂定" not in text:
        return "APPLIED_CORE", "人工明确给出闭集码表；保留人工批示来源。"
    if "无特殊要求" in text:
        return "APPLIED_CORE", "人工确认不增加格式外约束。"
    if any(marker in text for marker in ("树", "外部知识", "241", "自然编码", "编码规则")):
        return "DEFER_EXTENSION", "依赖对象树、外部知识或生成策略；保留原 EAST 事实，不在核心版补造。"
    return "CORE_RETAIN_RAW", "未形成可机械化的新增核心事实；保留 Agent1 已验证 EAST 提取。"


def make_pattern(conn: sqlite3.Connection, human: dict[str, dict[str, str]]) -> None:
    source = human["010024"]["source_ref"]
    conn.execute("INSERT INTO text_pattern_constraint VALUES (?,?,?,?,?,?,?,?,?)", (
        "PAT_010024_INVESTMENT_ASSET_RATIO", "010024", "投资资产种类及比例", 
        r"^(?:[0-9]{1,3}%|[0-9]{1,3}%-[0-9]{1,3}%):[^;:%]+(?:;(?:[0-9]{1,3}%|[0-9]{1,3}%-[0-9]{1,3}%):[^;:%]+)*$",
        canonical({"percentage_min": 0, "percentage_max": 100, "range_lower_lte_upper": True, "delimiter": ";", "punctuation": "ASCII_HALF_WIDTH"}),
        "ASCII_HALF_WIDTH:semicolon=; colon=: hyphen=- percent=%", human["010024"]["annotation"], canonical([source]), "HUMAN_DECISION",
    ))


def build_excel(output: Path, sqlite_path: Path, local_path: Path, review_rows: list[list[Any]], registry_rows: list[list[Any]], local_rows: list[list[Any]], external_rows: list[list[Any]], audit_rows: list[list[Any]]) -> None:
    """Use agent-xlsx only; preserve SQLite as the machine authority."""
    work = output / "核心库完整人工审查.xlsx"
    tmp = output / ".excel_payload"
    tmp.mkdir(exist_ok=True)
    sheets = [
        ("数据元核心约束", review_rows), ("人工批示处理", registry_rows), ("本地码表", local_rows),
        ("外部码表引用", external_rows), ("构建审计", audit_rows),
    ]
    for index, (_, data) in enumerate(sheets):
        (tmp / f"sheet_{index}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    command = ["uvx", "--with", "click", "agent-xlsx"]
    subprocess.run(command + ["write", str(work), "A1", "--from-json", str(tmp / "sheet_0.json")], check=True)
    subprocess.run(command + ["sheet", str(work), "--rename", "Sheet", "--new-name", sheets[0][0]], check=True)
    for index, (name, _) in enumerate(sheets[1:], start=1):
        subprocess.run(command + ["sheet", str(work), "--create", name], check=True)
        subprocess.run(command + ["write", str(work), "A1", "--sheet", name, "--from-json", str(tmp / f"sheet_{index}.json")], check=True)
    for name, data in sheets:
        last_col = chr(64 + min(len(data[0]), 26))
        subprocess.run(command + ["format", str(work), f"A1:{last_col}1", "--sheet", name, "--bold", "--fill-color", "1F4E78", "--font-color", "FFFFFF", "--wrap-text", "--vertical", "center"], check=True)
    subprocess.run(command + ["recalc", str(work), "--check-only"], check=True)
    shutil.rmtree(tmp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--human-review-csv", type=Path, default=DEFAULT_HUMAN_CSV)
    parser.add_argument("--self-business-csv", type=Path, default=DEFAULT_SELF_CSV)
    parser.add_argument("--reference-library", type=Path, default=REFERENCE_LIBRARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"输出目录已有内容，拒绝覆盖：{output}")
    output.mkdir(parents=True, exist_ok=True)
    if not args.reference_library.exists():
        raise SystemExit(f"缺少规范码表库：{args.reference_library}")
    raw_review = json.loads(args.base_review.read_text(encoding="utf-8"))
    if raw_review["publication_status"] != "review_only_not_released" or len(raw_review["records"]) != 301:
        raise SystemExit("基础 Agent1 审查资产身份或记录数不符合预期")
    human = read_human_csv(args.human_review_csv)
    self_rows = read_csv_rows(args.self_business_csv)
    records = {item["data_element_code"]: copy.deepcopy(item) for item in raw_review["records"]}
    if set(human) - set(records):
        raise SystemExit("人工审查表含未知数据元编码")

    overlay_rows: list[dict[str, Any]] = []
    for code, record in records.items():
        operations = apply_confirmed_overrides(record)
        annotation = human.get(code)
        if annotation:
            disposition, rationale = annotation_disposition(code, annotation["annotation"])
            overlay_rows.append({"code": code, "name": record["data_element_name"], "annotation": annotation["annotation"], "source_ref": annotation["source_ref"], "disposition": disposition, "operation": operations, "rationale": rationale})

    extra_operations = add_manual_simple_sets(records, human)
    for op in extra_operations:
        existing = next(item for item in overlay_rows if item["code"] == op["data_element_code"])
        existing["operation"].append(op)

    # 003007/003008 form one shared correspondence table, and 010010/010071 one
    # shared rating table.  Remove the incomplete one-sided LLM table first.
    records["003007"]["code_value_sets"] = []
    rating_values = manual_code_values()["010010"]

    core_path = output / "constraint_asset_core.sqlite"
    local_path = output / "local_code_value_library_core.sqlite"
    core = sqlite3.connect(core_path)
    local = sqlite3.connect(local_path)
    create_constraint_schema(core)
    create_local_schema(local)
    registry = LocalRegistry(local)

    core.execute("INSERT INTO asset_version VALUES (?,?,?,?,?,?,?,?,?)", (
        CORE_VERSION, SOURCE_LAYER, "review_only_not_released", now(), sha_file(args.base_review), sha_file(args.human_review_csv),
        str(args.reference_library.resolve()), sha_file(args.reference_library),
        "核心版仅物化 EAST 范围事实及可追溯人工裁决；树、外部知识、AGENT_241 策略均不纳入。",
    ))

    # Insert effective records and normal Agent1 local code sets.
    for code in sorted(records):
        record = records[code]
        if record.get("scope_status") == "EXCLUDED_NO_CORRESPONDING_FIELD":
            core_status = "EXCLUDED_NO_CORRESPONDING_FIELD"
        elif any(item["code"] == code and item["disposition"] == "DEFER_EXTENSION" for item in overlay_rows):
            core_status = "CORE_PARTIAL_EXTENSION_PENDING"
        else:
            core_status = "CORE_EFFECTIVE"
        insert_record(core, record, core_status)
        for ordinal, value_set in enumerate(record.get("code_value_sets", []), start=1):
            if not value_set.get("values"):
                continue
            set_id = f"LCS_DE_{code}_{digest(value_set['set_name'])[:12]}"
            registered = registry.register(set_id=set_id, set_name=value_set["set_name"], nodes=nodes_from_agent_set(value_set), source_refs=value_set["source_refs"], provenance=value_set.get("provenance_type", "EAST_SOURCE"))
            criteria = {"scope_kind": value_set.get("scope_kind", "GLOBAL"), "condition": value_set.get("condition")}
            core.execute("INSERT INTO local_code_table_usage VALUES (?,?,?,?,?,?,?,?,?)", (f"LU_{code}_{ordinal}", code, registered, "literal_value_or_code_label", value_set["extension_policy"], canonical(criteria), value_set["evidence_quote"], canonical(value_set["source_refs"]), value_set.get("provenance_type", "EAST_SOURCE")))

    # 001008 is reference guidance only, not a code-value domain for an address.
    reference = human["001008"]
    core.execute("INSERT INTO external_code_table_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "EU_001008_REFERENCE_ONLY", "001008", "CD000003", "code_cd000003_code_set", "EXT_GBT_2260", "DIRECT_COLUMN", canonical(["代码取值", "代码名称"]), None, None, None,
        "REFERENCE_ONLY", canonical({"purpose": "地址组成参考；不得把行政区划代码直接替代地址文本"}), reference["annotation"], canonical([reference["source_ref"]]), "HUMAN_DECISION"))

    # Shared statistics code/name correspondence.
    stat_nodes, stat_incomplete = parse_statistical_subjects(human["003008"]["annotation"], human["003008"]["source_ref"])
    stat_set = registry.register(set_id="LCS_STATISTICAL_SUBJECT", set_name="统计科目代码与名称", nodes=stat_nodes, source_refs=[human["003007"]["source_ref"], human["003008"]["source_ref"]], provenance="HUMAN_DECISION", hierarchy_kind="TREE", completeness="PARTIAL" if stat_incomplete else "COMPLETE")
    for code, field in (("003007", "value_code"), ("003008", "value_label")):
        core.execute("INSERT INTO local_code_table_usage VALUES (?,?,?,?,?,?,?,?,?)", (f"LU_{code}_MANUAL_SHARED", code, stat_set, field, "CLOSED", canonical({"hierarchy_level": 3, "is_selectable": 1}), human[code]["annotation"], canonical([human[code]["source_ref"]]), "HUMAN_DECISION"))

    # Hierarchical risk/event tables.  The risk table deliberately remains partial
    # because column E itself uses ellipses/ranges; no missing leaf is invented.
    for code, name, parser_fn in (("009002", "客户风险预警信号", parse_risk_hierarchy), ("009003", "关注事件代码", parse_risk_hierarchy)):
        nodes, incomplete = parser_fn(human[code]["annotation"], human[code]["source_ref"])
        set_id = registry.register(set_id=f"LCS_DE_{code}", set_name=name, nodes=nodes, source_refs=[human[code]["source_ref"]], provenance="HUMAN_DECISION", hierarchy_kind="TREE", completeness="PARTIAL" if incomplete else "COMPLETE")
        core.execute("INSERT INTO local_code_table_usage VALUES (?,?,?,?,?,?,?,?,?)", (f"LU_{code}_MANUAL_TREE", code, set_id, "value_code", "CLOSED", canonical({"hierarchy_level": 3, "is_selectable": 1, "multiple_values_delimiter": "；"}), human[code]["annotation"], canonical([human[code]["source_ref"]]), "HUMAN_DECISION"))

    rating_nodes = [{"node_key": f"R{i}", "parent_node_key": None, "level": 1, "value_kind": "LITERAL", "literal_value": item, "code": None, "label": None, "selectable": 1, "source_ref": human["010010"]["source_ref"]} for i, item in enumerate(rating_values, start=1)]
    rating_set = registry.register(set_id="LCS_SHARED_CREDIT_RATING", set_name="信用评级", nodes=rating_nodes, source_refs=[human["010010"]["source_ref"], human["010071"]["source_ref"]], provenance="HUMAN_DECISION")
    for code in ("010010", "010071"):
        core.execute("INSERT INTO local_code_table_usage VALUES (?,?,?,?,?,?,?,?,?)", (f"LU_{code}_SHARED_RATING", code, rating_set, "literal_value", "CLOSED", canonical({"hierarchy_level": 1, "is_selectable": 1}), human[code]["annotation"], canonical([human[code]["source_ref"]]), "HUMAN_DECISION"))

    self_nodes = parse_self_business_scope(self_rows)
    self_set = registry.register(set_id="LCS_SELF_OPERATED_BUSINESS_SCOPE", set_name="自营报送范围业务分类", nodes=self_nodes, source_refs=["SRC_ATT3:自营报送范围!A3:C67"], provenance="EAST_SOURCE", hierarchy_kind="TREE")
    for code, level in (("010028", 1), ("010029", 2), ("010030", 3)):
        refs = east_source_refs(records[code]) + ["SRC_ATT3:自营报送范围!A3:C67"]
        core.execute("INSERT INTO local_code_table_usage VALUES (?,?,?,?,?,?,?,?,?)", (f"LU_{code}_SELF_SCOPE", code, self_set, "value_label", "CLOSED", canonical({"hierarchy_level": level, "is_selectable": 1}), records[code]["description"], canonical(refs), "EAST_SOURCE"))

    make_pattern(core, human)
    for ordinal, item in enumerate(overlay_rows, start=1):
        core.execute("INSERT INTO human_overlay VALUES (?,?,?,?,?,?,?)", (f"HO_{ordinal:03d}", item["code"], item["annotation"], item["source_ref"], item["disposition"], canonical(item["operation"]), item["rationale"]))

    integrity_core = core.execute("PRAGMA integrity_check").fetchone()[0]
    integrity_local = local.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity_core != "ok" or integrity_local != "ok":
        raise RuntimeError(f"SQLite 完整性检查失败: core={integrity_core}, local={integrity_local}")
    core.commit(); local.commit()

    # Human-review views, generated from the exact effective SQLite contents.
    review_rows = [["数据元编码", "数据元名称", "核心状态", "格式", "单字段约束数", "本地码表数", "外部码表数", "编码规则数", "人工批示去向", "数据元说明"]]
    overlay_by_code = {item["code"]: item["disposition"] for item in overlay_rows}
    for record in core.execute("""SELECT d.data_element_code,d.data_element_name,d.core_status,d.data_type,d.string_length_exact,d.string_length_max,d.integer_max_digits,d.decimal_max_fraction_digits,
      (SELECT count(*) FROM semantic_constraint s WHERE s.data_element_code=d.data_element_code),
      (SELECT count(*) FROM local_code_table_usage l WHERE l.data_element_code=d.data_element_code),
      (SELECT count(*) FROM external_code_table_usage e WHERE e.data_element_code=d.data_element_code),
      (SELECT count(*) FROM encoding_rule r WHERE r.data_element_code=d.data_element_code),d.description FROM data_element d ORDER BY d.data_element_code"""):
        fmt = " ".join(str(x) for x in record[3:8] if x is not None)
        review_rows.append([record[0], record[1], record[2], fmt, record[8], record[9], record[10], record[11], overlay_by_code.get(record[0], "NO_OTHER_REVIEW"), record[12]])
    registry_rows = [["数据元编码", "数据元名称", "人工批示", "处理去向", "已执行操作", "人工来源", "说明"]]
    for item in overlay_rows:
        registry_rows.append([item["code"], item["name"], item["annotation"], item["disposition"], canonical(item["operation"]), item["source_ref"], item["rationale"]])
    local_rows = [["本地码表ID", "物理表", "节点键", "父节点", "层级", "条件维度", "条件值", "码值", "代码", "名称", "可选", "域策略", "来源", "完整性"]]
    completeness = {row[0]: row[6] for row in local.execute("SELECT local_code_set_id,sqlite_table_name,set_name,source_layer,provenance_type,hierarchy_kind,completeness_status,source_refs_json,value_count,canonical_hash FROM local_code_set_registry")}
    policies = {row[0]: ",".join(sorted(set(filter(None, row[1].split(","))))) for row in core.execute("SELECT local_code_set_id, group_concat(DISTINCT extension_policy) FROM local_code_table_usage GROUP BY local_code_set_id")}
    for row in registry.rows_for_excel:
        local_rows.append([row["local_code_set_id"], row["sqlite_table_name"], row["node_key"], row.get("parent_node_key"), row.get("level"), row.get("condition_dimension"), row.get("condition_value"), row.get("literal_value"), row.get("code"), row.get("label"), row.get("selectable"), policies.get(row["local_code_set_id"], "NOT_DIRECTLY_USED"), row.get("source_ref"), completeness[row["local_code_set_id"]]])
    external_rows = [["数据元编码", "码表ID", "物理表", "外部标准", "选取列", "选择方式", "用途", "来源"]]
    for row in core.execute("SELECT data_element_code,code_table_id,sqlite_table_name,external_standard_id,final_value_columns_json,selection_mode,usage_role,source_refs_json FROM external_code_table_usage ORDER BY data_element_code"):
        external_rows.append(list(row))
    audit_rows = [["检查项", "结果"]]
    audit_rows += [["核心约束库完整性", integrity_core], ["本地码值库完整性", integrity_local], ["数据元数量", core.execute("SELECT count(*) FROM data_element").fetchone()[0]], ["本地码表数量", local.execute("SELECT count(*) FROM local_code_set_registry").fetchone()[0]], ["本地码值节点数", len(registry.rows_for_excel)], ["人工批示数量", len(overlay_rows)], ["待扩展批示数量", sum(1 for item in overlay_rows if item["disposition"] == "DEFER_EXTENSION")], ["部分码表", ", ".join(row[0] for row in local.execute("SELECT local_code_set_id FROM local_code_set_registry WHERE completeness_status='PARTIAL'")) or "无"], ["发布状态", "review_only_not_released"]]
    build_excel(output, core_path, local_path, review_rows, registry_rows, local_rows, external_rows, audit_rows)

    summary = {
        "asset_version": CORE_VERSION, "publication_status": "review_only_not_released", "source_layer": SOURCE_LAYER,
        "core_constraint_sqlite": str(core_path), "local_code_sqlite": str(local_path),
        "review_excel": str(output / "核心库完整人工审查.xlsx"), "data_element_count": len(records),
        "local_code_table_count": local.execute("SELECT count(*) FROM local_code_set_registry").fetchone()[0],
        "local_value_node_count": len(registry.rows_for_excel), "human_overlay_count": len(overlay_rows),
        "overlay_dispositions": dict(Counter(item["disposition"] for item in overlay_rows)),
        "partial_local_code_sets": [row[0] for row in local.execute("SELECT local_code_set_id FROM local_code_set_registry WHERE completeness_status='PARTIAL'")],
        "integrity": {"constraint_asset_core": integrity_core, "local_code_value_library": integrity_local},
    }
    (output / "核心库构建审计.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    core.close(); local.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
