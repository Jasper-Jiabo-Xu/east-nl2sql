#!/usr/bin/env python3
"""Build the review-only synthetic nationwide bank organisation/department tree.

This is an EXTENSION asset.  It deliberately does *not* open ``corebank.db`` and
does not copy any legacy bank node, code, name, employee, customer, teller, or
product record.  The CoreBank material is used only as a schema reference for
the modelling dimensions (parent institution, aggregation relationship, type,
status, region and validity period).  Geographic codes are validated against
the existing Attachment-4/GB reference SQLite library.

The authority for programs is the generated SQLite file.  The XLSX is an
inspection-only human review view.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PHASE = ROOT / "kb/working/20260727_000_goal_mode_v1/构建过程层/04_字段多字段与对象明细状态提取-V2"
REFERENCE_DB = PHASE / "code_value_libraries/20260805_一表一码表_层级补全_v4/code_reference_library_v3.sqlite"
OUTPUT_DIR = PHASE / "extension_assets_v1/20260805_机构部门统一树V1_人工审查"

ASSET_VERSION = "extension-org-department-tree-v1-20260805"
BANK_NAME = "EAST虚拟全国银行"
COREBANK_SCHEMA_REF = "SRC_COREBANK_SCHEMA:info_analyzed_non_empty_datetime_adj.xlsx!公共机构码表"
GB_REGION_REF = "SRC_AMC_CODE:县及县以上行政区划"
DESIGN_REF = "SRC_EXTENSION_DESIGN:机构部门统一树V1"


@dataclass(frozen=True)
class DepartmentType:
    code: str
    name: str
    description: str
    applicable_levels: tuple[str, ...]


DEPARTMENTS: tuple[DepartmentType, ...] = (
    DepartmentType("STRATEGY", "战略发展部", "全行战略、规划与经营分析。", ("HEAD_OFFICE",)),
    DepartmentType("CORPORATE", "公司金融部", "法人客户与公司金融业务管理。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH", "CITY_BRANCH")),
    DepartmentType("RETAIL", "零售金融部", "个人客户与零售金融业务管理。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH", "CITY_BRANCH")),
    DepartmentType("TREASURY", "金融市场部", "资金、同业与金融市场业务管理。", ("HEAD_OFFICE",)),
    DepartmentType("CREDIT", "授信管理部", "授信政策、审查与额度管理。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH")),
    DepartmentType("RISK", "风险管理部", "风险识别、计量、监测与报告。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH", "CITY_BRANCH", "SUBBRANCH")),
    DepartmentType("COMPLIANCE", "合规内控部", "合规、内控、反洗钱与检查。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH", "CITY_BRANCH", "SUBBRANCH")),
    DepartmentType("OPERATIONS", "运营管理部", "运营流程、账务与网点运营管理。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH", "CITY_BRANCH", "SUBBRANCH")),
    DepartmentType("FINANCE", "计划财务部", "预算、核算、财务管理与统计。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH", "CITY_BRANCH")),
    DepartmentType("TECHNOLOGY", "信息科技部", "信息系统、数据治理与科技运行。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH")),
    DepartmentType("HR", "人力资源部", "人力资源、培训与组织发展。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH")),
    DepartmentType("ADMIN", "综合管理部", "行政、文秘、采购与综合协调。", ("HEAD_OFFICE", "PROVINCIAL_BRANCH", "CITY_BRANCH", "SUBBRANCH")),
    DepartmentType("CUSTOMER_SERVICE", "客户服务部", "厅堂服务、客户服务与投诉处理。", ("SUBBRANCH",)),
)


# The second tier deliberately covers every provincial-level region through a
# capital/municipality branch, plus several additional city branches.  The
# codes are national administrative division references, not legacy-bank data.
CAPITAL_CITY_CODES: dict[str, str] = {
    "110000": "110000", "120000": "120000", "130000": "130100", "140000": "140100",
    "150000": "150100", "210000": "210100", "220000": "220100", "230000": "230100",
    "310000": "310000", "320000": "320100", "330000": "330100", "340000": "340100",
    "350000": "350100", "360000": "360100", "370000": "370100", "410000": "410100",
    "420000": "420100", "430000": "430100", "440000": "440100", "450000": "450100",
    "460000": "460100", "500000": "500000", "510000": "510100", "520000": "520100",
    "530000": "530100", "540000": "540100", "610000": "610100", "620000": "620100",
    "630000": "630100", "640000": "640100", "650000": "650100",
}
ADDITIONAL_CITY_CODES: tuple[str, ...] = (
    "130200", "150200", "210200", "320500", "330200", "350200", "370200",
    "410300", "420500", "440300", "510700",
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_code(value: Any) -> str:
    """Keep six-digit administrative codes stable when SQLite exposes numerics."""
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):06d}"
    if isinstance(value, int):
        return f"{value:06d}"
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6) if text.isdigit() and len(text) < 6 else text


def fetch_regions() -> dict[str, dict[str, Any]]:
    if not REFERENCE_DB.exists():
        raise RuntimeError(f"缺少行政区划参考库：{REFERENCE_DB}")
    connection = sqlite3.connect(REFERENCE_DB)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT "代码取值" AS code, "代码名称" AS name, hierarchy_level,
                   parent_code, code_path, "行政区划层级" AS level_name
            FROM code_cd000003_code_set
            WHERE "代码取值" IS NOT NULL
            """
        ).fetchall()
    finally:
        connection.close()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["code"] = normalise_code(item["code"])
        item["parent_code"] = normalise_code(item["parent_code"]) if item["parent_code"] is not None else None
        item["code_path"] = "/".join(normalise_code(part) for part in str(item["code_path"] or "").split("/") if part)
        result[item["code"]] = item
    return result


def first_county_for_city(regions: dict[str, dict[str, Any]], city_code: str) -> str:
    city = regions[city_code]
    city_level = city["hierarchy_level"]
    # A municipality itself is provincial-level; its first county is therefore
    # directly below the province.  Ordinary cities use their city code as the
    # parent.  Choosing the first official child is only a coverage seed, not a
    # claim about a real bank's branch footprint.
    candidates = [
        item for item in regions.values()
        if item["hierarchy_level"] == 3
        and item["parent_code"] == city_code
    ]
    if not candidates and city_level == 1:
        candidates = [
            item for item in regions.values()
            if item["hierarchy_level"] == 3 and str(item["code_path"]).startswith(f"{city_code}/")
        ]
    if not candidates:
        raise RuntimeError(f"未找到城市/直辖市 {city_code} 的县区节点")
    return sorted(candidates, key=lambda item: str(item["code"]))[0]["code"]


def make_node(
    node_id: str, parent_node_id: str | None, node_kind: str, level: str,
    unit_type_code: str, unit_code: str, display_name: str, region_code: str | None,
    regional_scope: str, department_type_code: str | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "bank_name": BANK_NAME,
        "department_type_code": department_type_code,
        "legacy_data_imported": False,
        "notes": "全量合成的扩展层节点；不得作为真实银行组织信息使用。",
    }
    return {
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "node_kind": node_kind,
        "institution_level": level,
        "unit_type_code": unit_type_code,
        "org_unit_code": unit_code,
        "display_name": display_name,
        "formal_name": None,
        "region_code": region_code,
        "region_reference_table": "code_cd000003_code_set" if region_code else None,
        "regional_scope": regional_scope,
        "status_code": "ACTIVE",
        "effective_from": "2020-01-01",
        "effective_to": None,
        "is_synthetic": 1,
        "source_class": "SYNTHETIC_EXTENSION_DESIGN",
        "review_status": "PENDING_HUMAN_REVIEW",
        "source_refs_json": canonical([COREBANK_SCHEMA_REF, GB_REGION_REF, DESIGN_REF]),
        "attributes_json": canonical(attrs),
    }


def assign_formal_names(nodes: list[dict[str, Any]]) -> None:
    """Set the canonical Chinese path name, starting at the synthetic bank root."""
    by_id = {node["node_id"]: node for node in nodes}
    memo: dict[str, str] = {}

    def resolve(node_id: str) -> str:
        if node_id in memo:
            return memo[node_id]
        node = by_id[node_id]
        if node["parent_node_id"] is None:
            value = BANK_NAME
        else:
            parent_name = resolve(str(node["parent_node_id"]))
            segment = str(node["display_name"])
            if segment.startswith(BANK_NAME):
                segment = segment[len(BANK_NAME):]
            value = parent_name + segment
        memo[node_id] = value
        return value

    for node in nodes:
        node["formal_name"] = resolve(str(node["node_id"]))


def build_nodes(regions: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    missing = sorted((set(CAPITAL_CITY_CODES) | set(ADDITIONAL_CITY_CODES)) - set(regions))
    if missing:
        raise RuntimeError(f"行政区划参考表缺少所需节点：{missing}")
    if len(CAPITAL_CITY_CODES) != 31:
        raise RuntimeError("省级分行必须覆盖 31 个省级行政区划")

    nodes: list[dict[str, Any]] = []
    relations: list[dict[str, str]] = []
    root_id = "ORG-0000"
    nodes.append(make_node(
        root_id, None, "INSTITUTION", "HEAD_OFFICE", "HEAD_OFFICE", "VNB-000-000-000",
        f"{BANK_NAME}总行", None, "NATIONAL",
    ))

    institution_nodes: list[dict[str, Any]] = [nodes[0]]
    province_nodes: dict[str, dict[str, Any]] = {}
    city_nodes: list[dict[str, Any]] = []
    subbranch_parents: list[tuple[dict[str, Any], str]] = []
    serial = 1
    for province_code in sorted(CAPITAL_CITY_CODES):
        province = regions[province_code]
        node_id = f"ORG-P-{province_code}"
        node = make_node(
            node_id, root_id, "INSTITUTION", "PROVINCIAL_BRANCH", "PROVINCIAL_BRANCH",
            f"VNB-{province_code}-000-000", f"{BANK_NAME}{province['name']}分行",
            province_code, "PROVINCE",
        )
        nodes.append(node); institution_nodes.append(node); province_nodes[province_code] = node
        relations.append({"from_node_id": node_id, "to_node_id": root_id, "relation_type": "TREE_PARENT"})
        serial += 1

    city_pairs = list(CAPITAL_CITY_CODES.items()) + [
        (next(code for code in CAPITAL_CITY_CODES if code == f"{city_code[:2]}0000"), city_code)
        for city_code in ADDITIONAL_CITY_CODES
    ]
    per_province_city_serial: dict[str, int] = defaultdict(int)
    for province_code, city_code in city_pairs:
        # A municipality's provincial branch already has city jurisdiction.
        # Creating another same-name city branch would make the formal path
        # duplicate (e.g. “北京市分行北京市分行”).  Its subbranch therefore
        # reports directly to the municipal branch.
        if city_code == province_code:
            subbranch_parents.append((province_nodes[province_code], city_code))
            continue
        per_province_city_serial[province_code] += 1
        city = regions[city_code]
        number = per_province_city_serial[province_code]
        node_id = f"ORG-C-{city_code}-{number:02d}"
        node = make_node(
            node_id, province_nodes[province_code]["node_id"], "INSTITUTION", "CITY_BRANCH", "CITY_BRANCH",
            f"VNB-{province_code}-{number:03d}-000", f"{BANK_NAME}{city['name']}分行",
            city_code, "CITY",
        )
        nodes.append(node); institution_nodes.append(node); city_nodes.append(node)
        subbranch_parents.append((node, city_code))
        relations.append({"from_node_id": node_id, "to_node_id": province_nodes[province_code]["node_id"], "relation_type": "TREE_PARENT"})

    for owner, city_code in subbranch_parents:
        county_code = first_county_for_city(regions, city_code)
        county = regions[county_code]
        node_id = f"ORG-S-{county_code}"
        node = make_node(
            node_id, owner["node_id"], "INSTITUTION", "SUBBRANCH", "SUBBRANCH",
            f"{owner['org_unit_code'][:-3]}001", f"{BANK_NAME}{county['name']}支行",
            county_code, "COUNTY",
        )
        nodes.append(node); institution_nodes.append(node)
        relations.append({"from_node_id": node_id, "to_node_id": owner["node_id"], "relation_type": "TREE_PARENT"})

    departments_by_level = {
        "HEAD_OFFICE": ["STRATEGY", "CORPORATE", "RETAIL", "TREASURY", "CREDIT", "RISK", "COMPLIANCE", "OPERATIONS", "FINANCE", "TECHNOLOGY", "HR", "ADMIN"],
        "PROVINCIAL_BRANCH": ["CORPORATE", "RETAIL", "CREDIT", "RISK", "COMPLIANCE", "OPERATIONS", "FINANCE", "TECHNOLOGY", "HR", "ADMIN"],
        "CITY_BRANCH": ["CORPORATE", "RETAIL", "RISK", "COMPLIANCE", "OPERATIONS", "FINANCE", "ADMIN"],
        "SUBBRANCH": ["CUSTOMER_SERVICE", "RISK", "COMPLIANCE", "OPERATIONS", "ADMIN"],
    }
    names = {item.code: item.name for item in DEPARTMENTS}
    for owner in list(institution_nodes):
        for order, department_code in enumerate(departments_by_level[owner["institution_level"]], start=1):
            node_id = f"DEP-{owner['node_id']}-{order:02d}"
            node = make_node(
                node_id, owner["node_id"], "DEPARTMENT", "DEPARTMENT", "FUNCTIONAL_DEPARTMENT",
                f"{owner['org_unit_code']}-D{order:02d}", names[department_code], None,
                "INHERIT_PARENT", department_code,
            )
            nodes.append(node)
            relations.append({"from_node_id": node_id, "to_node_id": owner["node_id"], "relation_type": "TREE_PARENT"})

    assign_formal_names(nodes)
    return nodes, relations


def create_database(path: Path, nodes: list[dict[str, Any]], relations: list[dict[str, str]]) -> dict[str, Any]:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(
            """
            CREATE TABLE asset_meta (
                asset_key TEXT PRIMARY KEY,
                asset_value TEXT NOT NULL
            );
            CREATE TABLE department_type_catalog (
                department_type_code TEXT PRIMARY KEY,
                department_type_name TEXT NOT NULL,
                description TEXT NOT NULL,
                applicable_levels_json TEXT NOT NULL,
                source_class TEXT NOT NULL,
                review_status TEXT NOT NULL
            );
            CREATE TABLE org_unit_node (
                node_id TEXT PRIMARY KEY,
                parent_node_id TEXT REFERENCES org_unit_node(node_id),
                node_kind TEXT NOT NULL CHECK(node_kind IN ('INSTITUTION','DEPARTMENT')),
                institution_level TEXT NOT NULL CHECK(institution_level IN ('HEAD_OFFICE','PROVINCIAL_BRANCH','CITY_BRANCH','SUBBRANCH','DEPARTMENT')),
                unit_type_code TEXT NOT NULL,
                org_unit_code TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                formal_name TEXT NOT NULL UNIQUE,
                region_code TEXT,
                region_reference_table TEXT,
                regional_scope TEXT NOT NULL CHECK(regional_scope IN ('NATIONAL','PROVINCE','CITY','COUNTY','INHERIT_PARENT')),
                status_code TEXT NOT NULL CHECK(status_code IN ('ACTIVE','INACTIVE','PLANNED')),
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                is_synthetic INTEGER NOT NULL CHECK(is_synthetic=1),
                source_class TEXT NOT NULL CHECK(source_class='SYNTHETIC_EXTENSION_DESIGN'),
                review_status TEXT NOT NULL CHECK(review_status IN ('PENDING_HUMAN_REVIEW','APPROVED','REJECTED')),
                source_refs_json TEXT NOT NULL,
                attributes_json TEXT NOT NULL,
                CHECK((node_kind='DEPARTMENT' AND institution_level='DEPARTMENT' AND region_code IS NULL AND regional_scope='INHERIT_PARENT')
                   OR (node_kind='INSTITUTION' AND institution_level<>'DEPARTMENT'))
            );
            CREATE TABLE org_unit_relation (
                from_node_id TEXT NOT NULL REFERENCES org_unit_node(node_id),
                to_node_id TEXT NOT NULL REFERENCES org_unit_node(node_id),
                relation_type TEXT NOT NULL CHECK(relation_type IN ('TREE_PARENT','ACCOUNTING_AGGREGATION')),
                source_class TEXT NOT NULL CHECK(source_class='SYNTHETIC_EXTENSION_DESIGN'),
                review_status TEXT NOT NULL CHECK(review_status IN ('PENDING_HUMAN_REVIEW','APPROVED','REJECTED')),
                PRIMARY KEY (from_node_id, to_node_id, relation_type)
            );
            CREATE INDEX ix_org_unit_parent ON org_unit_node(parent_node_id);
            CREATE INDEX ix_org_unit_kind_level ON org_unit_node(node_kind, institution_level);
            CREATE INDEX ix_org_unit_region ON org_unit_node(region_code);
            CREATE INDEX ix_org_relation_to ON org_unit_relation(to_node_id, relation_type);
            """
        )
        conn.executemany(
            "INSERT INTO department_type_catalog VALUES (?,?,?,?,?,?)",
            [(item.code, item.name, item.description, canonical(list(item.applicable_levels)), "SYNTHETIC_EXTENSION_DESIGN", "PENDING_HUMAN_REVIEW") for item in DEPARTMENTS],
        )
        fields = list(nodes[0])
        conn.executemany(
            f"INSERT INTO org_unit_node ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
            [tuple(node[field] for field in fields) for node in nodes],
        )
        conn.executemany(
            "INSERT INTO org_unit_relation VALUES (?,?,?,?,?)",
            [(row["from_node_id"], row["to_node_id"], row["relation_type"], "SYNTHETIC_EXTENSION_DESIGN", "PENDING_HUMAN_REVIEW") for row in relations],
        )
        meta = {
            "asset_version": ASSET_VERSION,
            "asset_layer": "EXTENSION",
            "asset_status": "review_only_not_released",
            "bank_identity": BANK_NAME,
            "record_provenance": "all nodes synthetic; no CoreBank records imported",
            "corebank_usage_boundary": "schema dimensions only; no data values, codes, names, distributions, or object relationships imported",
            "authority": "SQLite is runtime authority; XLSX is human review only",
            "built_at_utc": utc_now(),
        }
        conn.executemany("INSERT INTO asset_meta VALUES (?,?)", list(meta.items()))
        conn.commit()
    finally:
        conn.close()
    return meta


def validate(path: Path, regions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        nodes = conn.execute("SELECT * FROM org_unit_node").fetchall()
        relations = conn.execute("SELECT * FROM org_unit_relation WHERE relation_type='TREE_PARENT'").fetchall()
        errors: list[str] = []
        by_id = {row["node_id"]: row for row in nodes}
        roots = [row for row in nodes if row["parent_node_id"] is None]
        if len(roots) != 1 or roots[0]["node_id"] != "ORG-0000":
            errors.append("根节点必须且只能是 ORG-0000")
        if len(relations) != len(nodes) - 1:
            errors.append("TREE_PARENT 关系数量必须等于节点数减一")
        for row in nodes:
            if not row["is_synthetic"]:
                errors.append(f"发现非合成节点：{row['node_id']}")
            if row["region_code"] and row["region_code"] not in regions:
                errors.append(f"地区代码不在 CD000003：{row['node_id']}")
            if row["node_kind"] == "DEPARTMENT":
                parent = by_id.get(row["parent_node_id"])
                if not parent or parent["node_kind"] != "INSTITUTION":
                    errors.append(f"部门必须直接挂在机构下：{row['node_id']}")
            if not row["formal_name"] or not str(row["formal_name"]).startswith(BANK_NAME):
                errors.append(f"正式名称必须由银行根名称开始：{row['node_id']}")
            visited: set[str] = set()
            current = row
            while current["parent_node_id"]:
                parent_id = current["parent_node_id"]
                if parent_id in visited:
                    errors.append(f"发现循环：{row['node_id']}")
                    break
                visited.add(parent_id)
                if parent_id not in by_id:
                    errors.append(f"父节点不存在：{row['node_id']}")
                    break
                current = by_id[parent_id]
        province_count = conn.execute("SELECT COUNT(*) FROM org_unit_node WHERE institution_level='PROVINCIAL_BRANCH'").fetchone()[0]
        if province_count != 31:
            errors.append(f"省级分行数量应为31，实际为{province_count}")
        formal_name_count = conn.execute("SELECT COUNT(DISTINCT formal_name) FROM org_unit_node").fetchone()[0]
        if formal_name_count != len(nodes):
            errors.append("正式名称必须全局唯一")
        return {
            "status": "PASS" if not errors else "FAIL",
            "errors": errors,
            "node_count": len(nodes),
            "tree_relation_count": len(relations),
            "node_counts_by_kind": dict(Counter(row["node_kind"] for row in nodes)),
            "node_counts_by_level": dict(Counter(row["institution_level"] for row in nodes)),
            "province_branch_count": province_count,
            "formal_name_count": formal_name_count,
        }
    finally:
        conn.close()


def write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    # The staging CSV is consumed by agent-xlsx, so a BOM would become part of
    # the first Excel header.  Keep it plain UTF-8.
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def write_excel(output: Path, nodes: list[dict[str, Any]], relations: list[dict[str, str]], validation: dict[str, Any], meta: dict[str, Any]) -> None:
    csv_dir = output.parent / "_xlsx_staging"
    if csv_dir.exists():
        shutil.rmtree(csv_dir)
    csv_dir.mkdir(parents=True)
    try:
        node_headers = [
            "node_id", "parent_node_id", "node_kind", "institution_level", "unit_type_code", "org_unit_code",
            "display_name", "formal_name", "region_code", "region_reference_table", "regional_scope", "status_code",
            "effective_from", "effective_to", "is_synthetic", "source_class", "review_status", "source_refs_json", "attributes_json",
        ]
        write_csv(csv_dir / "tree.csv", node_headers, nodes)
        write_csv(csv_dir / "relations.csv", ["from_node_id", "to_node_id", "relation_type"], relations)
        write_csv(csv_dir / "departments.csv", ["department_type_code", "department_type_name", "description", "applicable_levels", "source_class", "review_status"], [
            {"department_type_code": item.code, "department_type_name": item.name, "description": item.description,
             "applicable_levels": "、".join(item.applicable_levels), "source_class": "SYNTHETIC_EXTENSION_DESIGN", "review_status": "PENDING_HUMAN_REVIEW"}
            for item in DEPARTMENTS
        ])
        review_rows = [
            {"审查主题": "身份与范围", "当前设计": f"{BANK_NAME}；所有节点均为合成。", "请人工确认": "银行名称、全国覆盖范围与层级是否合适。"},
            {"审查主题": "机构与部门统一树", "当前设计": "部门作为 DEPARTMENT 节点，直接挂在 INSTITUTION 节点下。", "请人工确认": "是否允许未来存在部门下级部门；本 V1 暂不生成。"},
            {"审查主题": "层级", "当前设计": "总行→省级分行→城市分行→支行→部门；直辖市分行直接管理支行。", "请人工确认": "是否需要新增区域管理中心、县域支行或村镇银行层。"},
            {"审查主题": "正式名称", "当前设计": "formal_name 从根节点拼接至当前节点，作为正式中文名称。", "请人工确认": "名称拼接口径与是否保留“总行”字样。"},
            {"审查主题": "地区引用", "当前设计": "仅保存行政区划代码并引用 CD000003；不复制国标值。", "请人工确认": "地区覆盖与分行/支行投放密度。"},
            {"审查主题": "CoreBank 边界", "当前设计": "只参考字段关系维度；没有读取或导入 CoreBank 记录。", "请人工确认": "同意作为 EXTENSION 结构参考。"},
            {"审查主题": "对象边界", "当前设计": "员工、柜员、客户均不在本树中，也不从 CoreBank 继承。", "请人工确认": "对象池将在独立资产中生成。"},
        ]
        write_csv(csv_dir / "review.csv", ["审查主题", "当前设计", "请人工确认"], review_rows)
        audit_rows = [{"指标": key, "值": canonical(value) if isinstance(value, (dict, list)) else value} for key, value in {**meta, **validation}.items()]
        write_csv(csv_dir / "audit.csv", ["指标", "值"], audit_rows)

        if output.exists():
            output.unlink()
        cmd = ["uvx", "--with", "click", "agent-xlsx", "write", str(output), "A1", "--from-csv", str(csv_dir / "tree.csv"), "--sheet", "统一机构部门树"]
        subprocess.run(cmd, check=True, cwd=ROOT)
        for sheet, csv_name in [("部门类型目录", "departments.csv"), ("树关系", "relations.csv"), ("人工审查说明", "review.csv"), ("构建审计", "audit.csv")]:
            subprocess.run(["uvx", "--with", "click", "agent-xlsx", "sheet", str(output), "--create", sheet], check=True, cwd=ROOT)
            subprocess.run(["uvx", "--with", "click", "agent-xlsx", "write", str(output), "A1", "--from-csv", str(csv_dir / csv_name), "--sheet", sheet], check=True, cwd=ROOT)
        for sheet in ["统一机构部门树", "部门类型目录", "树关系", "人工审查说明", "构建审计"]:
            subprocess.run([
                "uvx", "--with", "click", "agent-xlsx", "format", str(output), "A1:Z1", "--sheet", sheet,
                "--bold", "--fill-color", "1F4E78", "--font-color", "FFFFFF", "--horizontal", "center", "--wrap-text",
            ], check=True, cwd=ROOT)
    finally:
        shutil.rmtree(csv_dir, ignore_errors=True)


def write_design_markdown(path: Path, validation: dict[str, Any]) -> None:
    content = f"""# 机构部门统一树 V1（人工审查版）

状态：`review_only_not_released`  
版本：`{ASSET_VERSION}`

## 适用边界

- 这是扩展层的合成全国性银行组织结构，主体为 **{BANK_NAME}**。
- 员工、柜员、客户均不是树节点，也未从 CoreBank 导入。
- CoreBank 仅提供了“机构码、上划机构码、汇总机构码、机构类型、营业状态、地区、开办日期”等建模维度的结构参考；本资产没有读取、复制或暴露真实数据库记录。
- 行政区划只引用既有码表库中的 `code_cd000003_code_set`，不复制国标值。

## 固定合同

`org_unit_node` 是运行时权威表。节点固定使用 `INSTITUTION`、`DEPARTMENT` 两类；父子关系固定存入 `org_unit_relation(relation_type='TREE_PARENT')`。

树形层级为：`总行 → 省级分行 → 城市分行 → 支行 → 部门`。直辖市不另设同名城市分行，由直辖市分行直接管理支行。

`display_name` 是节点显示名称；`formal_name` 是从根节点拼接到当前节点的正式中文名称，例如：`EAST虚拟全国银行北京市分行东城区支行战略发展部`。

部门视为一种机构单元，但 V1 中只允许直接隶属某个机构节点，不生成“部门的部门”。员工、柜员、岗位以后通过独立对象池/关系表引用 `org_unit_node.node_id`。

## 验收结果

- 节点总数：{validation['node_count']}
- 机构/部门节点数：{canonical(validation['node_counts_by_kind'])}
- 层级分布：{canonical(validation['node_counts_by_level'])}
- 省级分行覆盖：{validation['province_branch_count']}/31
- 结构校验：{validation['status']}

## 不发布条件

- 仍有 `PENDING_HUMAN_REVIEW` 节点；
- 银行名称、地区投放密度、部门目录、层级设计任一项未审批；
- 发现将真实 CoreBank 数据、对象记录或真实银行组织信息写入本资产。
"""
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    regions = fetch_regions()
    nodes, relations = build_nodes(regions)
    sqlite_path = output_dir / "extension_org_department_tree_v1.sqlite"
    meta = create_database(sqlite_path, nodes, relations)
    validation = validate(sqlite_path, regions)
    if validation["status"] != "PASS":
        raise RuntimeError("树结构校验失败：" + "; ".join(validation["errors"]))
    (output_dir / "构建审计.json").write_text(canonical({**meta, **validation, "sqlite_sha256": sha256_file(sqlite_path)}), encoding="utf-8")
    write_design_markdown(output_dir / "机构部门统一树V1设计.md", validation)
    write_excel(output_dir / "机构部门统一树V1_人工审查.xlsx", nodes, relations, validation, meta)
    print(canonical({"output_dir": str(output_dir), "sqlite": str(sqlite_path), "validation": validation}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
