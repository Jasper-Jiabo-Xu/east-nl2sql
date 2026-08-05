#!/usr/bin/env python3
"""Build the extension data-element constraint asset from the immutable core.

The core SQLite is copied byte-for-byte before extension-only tables are added.
No core EAST constraint, code set, source reference, or human decision is
rewritten.  This first extension binds organisation/department-related data
elements to the approved synthetic organisation tree; later trees (accounting
subjects etc.) are intentionally outside this build.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PHASE = ROOT / "kb/working/20260727_000_goal_mode_v1/构建过程层/04_字段多字段与对象明细状态提取-V2"
CORE_DB = PHASE / "core_east_asset_v1/20260805_人工审批后核心库/constraint_asset_core.sqlite"
TREE_DB = PHASE / "extension_assets_v1/20260805_机构部门统一树V1_人工审查/extension_org_department_tree_v1.sqlite"
OUTPUT_DIR = PHASE / "extension_assets_v1/20260805_扩展数据元约束资产V1_机构部门树绑定"

ASSET_VERSION = "constraint-assets-extension-v1-org-department-20260805"
TREE_ASSET_ID = "ORG_DEPARTMENT_UNIFIED_TREE_V1"
DESIGN_REF = "SRC_EXTENSION_DESIGN:扩展数据元约束资产V1_机构部门树绑定"
APPROVAL_REF = "SRC_HUMAN_APPROVAL:20260805_机构部门统一树V1审查通过"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mapping(
    ref_id: str, code: str, target_filter: dict[str, Any], lookup_key: str,
    projection: list[str], template: str | None, binding_mode: str,
    applicability: dict[str, Any], dependency_status: str, notes: str,
    value_translation: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "tree_reference_id": ref_id,
        "data_element_code": code,
        "tree_asset_id": TREE_ASSET_ID,
        "target_table": "org_unit_node",
        "node_filter_json": canonical(target_filter),
        "lookup_key_column": lookup_key,
        "value_projection_json": canonical(projection),
        "representation_template": template,
        "value_translation_json": canonical(value_translation or {}),
        "binding_mode": binding_mode,
        "applicability_condition_json": canonical(applicability),
        "dependency_status": dependency_status,
        "fallback_policy": "NO_FALLBACK_MANUAL_REVIEW",
        "notes": notes,
    }


MAPPINGS = (
    mapping("TREE_REF_001001", "001001", {"node_kind": "INSTITUTION"}, "node_id", ["formal_name"], "{formal_name}", "DIRECT_VALUE", {"record_subject_kind": "BANK_ORGANIZATION"}, "READY", "仅当本记录主体为本行机构时，名称取机构树正式名称。"),
    mapping("TREE_REF_001008", "001008", {"node_kind": "INSTITUTION"}, "node_id", ["region_code"], None, "CONTEXT_ONLY", {"record_subject_kind": "BANK_ORGANIZATION"}, "BLOCKED_EXTERNAL_MAPPING", "机构树只提供地区上下文；详细地址必须由后续地址知识资产生成，禁止拼造。"),
    mapping("TREE_REF_001010", "001010", {"node_kind": "INSTITUTION"}, "node_id", ["node_id", "org_unit_code"], None, "CONTEXT_ONLY", {"record_subject_kind": "BANK_ORGANIZATION"}, "BLOCKED_EXTERNAL_MAPPING", "12位支付行号或SWIFT行号不从合成机构号推导；需单独的外部金融机构代码映射资产。"),
    mapping("TREE_REF_001011", "001011", {"node_kind": "INSTITUTION"}, "node_id", ["node_id", "org_unit_code"], None, "CONTEXT_ONLY", {"record_subject_kind": "BANK_ORGANIZATION"}, "BLOCKED_EXTERNAL_MAPPING", "金融许可证号按既有编码约束生成；机构树只确定被许可机构上下文，不提供真实许可证映射。"),
    mapping("TREE_REF_001012", "001012", {"node_kind": "INSTITUTION"}, "node_id", ["org_unit_code"], "{org_unit_code}", "DIRECT_VALUE", {"record_subject_kind": "BANK_ORGANIZATION"}, "READY", "内部机构号直接引用唯一的合成 org_unit_code。"),
    mapping("TREE_REF_001013", "001013", {"node_kind": ["INSTITUTION", "DEPARTMENT"]}, "node_id", ["institution_level"], "{translated_value}", "TRANSLATED_VALUE", {"record_subject_kind": "BANK_ORGANIZATION_OR_INTERNAL_UNIT"}, "READY", "机构类别由机构层级/部门节点翻译得到；树中暂不产生虚拟机构。", {"HEAD_OFFICE": "管理机构", "PROVINCIAL_BRANCH": "营业机构", "CITY_BRANCH": "营业机构", "SUBBRANCH": "营业机构", "DEPARTMENT": "内设机构"}),
    mapping("TREE_REF_001018", "001018", {"node_kind": "DEPARTMENT"}, "node_id", ["formal_name"], "{formal_name}", "DIRECT_VALUE", {"record_subject_kind": "BANK_ORGANIZATION_OR_INTERNAL_UNIT"}, "READY", "所属部门使用部门树正式名称，避免只用显示名导致跨机构重名。"),
    mapping("TREE_REF_001029", "001029", {"institution_level": "HEAD_OFFICE"}, "node_id", ["node_id", "org_unit_code"], None, "CONTEXT_ONLY", {"reporter_role": "BANK_LEGAL_ENTITY"}, "BLOCKED_EXTERNAL_MAPPING", "人民银行统一编发的金融机构代码必须由独立映射资产提供；总行节点仅确定报告主体。"),
    mapping("TREE_REF_003015", "003015", {"node_kind": "INSTITUTION"}, "node_id", ["formal_name"], "{formal_name}", "DIRECT_VALUE", {"account_owner_entity_type": "BANK_ORGANIZATION"}, "READY", "仅当账户归属者为本行机构时，账户名称引用机构正式名称。"),
    mapping("TREE_REF_010076", "010076", {"institution_level": "HEAD_OFFICE"}, "node_id", ["node_id", "org_unit_code"], None, "CONTEXT_ONLY", {"issuer_role": "BANK_LEGAL_ENTITY"}, "BLOCKED_EXTERNAL_MAPPING", "发行机构代码取金融机构代码前6位；须等待金融机构代码映射资产，不能截取合成机构号。"),
)


def assert_tree_approved() -> dict[str, str]:
    if not TREE_DB.exists():
        raise RuntimeError(f"机构部门树不存在：{TREE_DB}")
    conn = sqlite3.connect(TREE_DB)
    try:
        meta = dict(conn.execute("SELECT asset_key,asset_value FROM asset_meta").fetchall())
        pending = conn.execute("SELECT COUNT(*) FROM org_unit_node WHERE review_status<>'APPROVED'").fetchone()[0]
        if meta.get("asset_status") != "approved_not_released" or pending:
            raise RuntimeError("机构部门树尚未审批通过；不得绑定到扩展约束资产")
        return meta
    finally:
        conn.close()


def source_refs_for(conn: sqlite3.Connection, code: str) -> tuple[str, str]:
    row = conn.execute("SELECT description,format_source_refs_json FROM data_element WHERE data_element_code=?", (code,)).fetchone()
    if not row:
        raise RuntimeError(f"核心库缺少数据元：{code}")
    refs = json.loads(row[1])
    return row[0], canonical(refs + [DESIGN_REF, APPROVAL_REF])


def build_extension(output: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not CORE_DB.exists():
        raise RuntimeError(f"核心库不存在：{CORE_DB}")
    tree_meta = assert_tree_approved()
    if output.exists():
        output.unlink()
    shutil.copy2(CORE_DB, output)
    core_sha = sha256_file(CORE_DB)
    tree_sha = sha256_file(TREE_DB)
    conn = sqlite3.connect(output)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE extension_asset_meta (
                meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL
            );
            CREATE TABLE tree_reference_usage (
                tree_reference_id TEXT PRIMARY KEY,
                data_element_code TEXT NOT NULL REFERENCES data_element(data_element_code),
                tree_asset_id TEXT NOT NULL,
                tree_sqlite_path_relative TEXT NOT NULL,
                tree_sqlite_sha256 TEXT NOT NULL,
                target_table TEXT NOT NULL,
                node_filter_json TEXT NOT NULL,
                lookup_key_column TEXT NOT NULL,
                value_projection_json TEXT NOT NULL,
                representation_template TEXT,
                value_translation_json TEXT NOT NULL,
                binding_mode TEXT NOT NULL CHECK(binding_mode IN ('DIRECT_VALUE','TRANSLATED_VALUE','CONTEXT_ONLY')),
                applicability_condition_json TEXT NOT NULL,
                dependency_status TEXT NOT NULL CHECK(dependency_status IN ('READY','BLOCKED_EXTERNAL_MAPPING')),
                fallback_policy TEXT NOT NULL CHECK(fallback_policy='NO_FALLBACK_MANUAL_REVIEW'),
                evidence_quote TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                provenance_type TEXT NOT NULL CHECK(provenance_type='EXTENSION_DESIGN'),
                review_status TEXT NOT NULL CHECK(review_status IN ('PENDING_HUMAN_REVIEW','APPROVED')),
                notes TEXT NOT NULL
            );
            CREATE INDEX idx_tree_reference_element ON tree_reference_usage(data_element_code);
            """
        )
        records: list[dict[str, Any]] = []
        relative_tree = str(TREE_DB.relative_to(PHASE))
        for item in MAPPINGS:
            description, refs_json = source_refs_for(conn, item["data_element_code"])
            record = {
                **item,
                "tree_sqlite_path_relative": relative_tree,
                "tree_sqlite_sha256": tree_sha,
                "evidence_quote": description,
                "source_refs_json": refs_json,
                "provenance_type": "EXTENSION_DESIGN",
                "review_status": "PENDING_HUMAN_REVIEW",
            }
            records.append(record)
        columns = list(records[0])
        conn.executemany(
            f"INSERT INTO tree_reference_usage ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [tuple(record[column] for column in columns) for record in records],
        )
        meta = {
            "asset_version": ASSET_VERSION,
            "source_layer": "EXTENSION",
            "publication_status": "review_only_not_released",
            "inherits_core_version": conn.execute("SELECT asset_version FROM asset_version LIMIT 1").fetchone()[0],
            "inherits_core_sqlite_sha256": core_sha,
            "core_inheritance_mode": "byte_copy_then_extension_tables_only",
            "tree_asset_id": TREE_ASSET_ID,
            "tree_asset_status": tree_meta["asset_status"],
            "tree_sqlite_sha256": tree_sha,
            "tree_approval_ref": tree_meta.get("approval_ref", APPROVAL_REF),
            "built_at_utc": now(),
            "not_in_scope": "科目树及003002/003003/003004将在该树建成后另行绑定；对象表不从CoreBank继承。",
        }
        conn.executemany("INSERT INTO extension_asset_meta VALUES (?,?)", list(meta.items()))
        conn.commit()
        return meta, records
    finally:
        conn.close()


def validate(output: Path, expected_core_sha: str, expected_tree_sha: str) -> dict[str, Any]:
    conn = sqlite3.connect(output)
    try:
        errors: list[str] = []
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"SQLite integrity_check={integrity}")
        meta = dict(conn.execute("SELECT meta_key,meta_value FROM extension_asset_meta").fetchall())
        if meta.get("inherits_core_sqlite_sha256") != expected_core_sha:
            errors.append("核心库 SHA 不一致")
        if meta.get("tree_sqlite_sha256") != expected_tree_sha:
            errors.append("机构树 SHA 不一致")
        usage_count = conn.execute("SELECT COUNT(*) FROM tree_reference_usage").fetchone()[0]
        ready_count = conn.execute("SELECT COUNT(*) FROM tree_reference_usage WHERE dependency_status='READY'").fetchone()[0]
        blocked_count = conn.execute("SELECT COUNT(*) FROM tree_reference_usage WHERE dependency_status='BLOCKED_EXTERNAL_MAPPING'").fetchone()[0]
        if usage_count != len(MAPPINGS):
            errors.append("机构部门树引用数量不完整")
        pending_count = conn.execute("SELECT COUNT(*) FROM tree_reference_usage WHERE review_status='PENDING_HUMAN_REVIEW'").fetchone()[0]
        if pending_count != len(MAPPINGS):
            errors.append("新建树引用应全部处于人工审查状态")
        for code in ("001012", "001013", "001018"):
            if not conn.execute("SELECT COUNT(*) FROM tree_reference_usage WHERE data_element_code=? AND dependency_status='READY'", (code,)).fetchone()[0]:
                errors.append(f"{code} 必须存在 READY 树引用")
        return {
            "status": "PASS" if not errors else "FAIL", "errors": errors,
            "tree_reference_count": usage_count, "ready_reference_count": ready_count,
            "blocked_reference_count": blocked_count,
            "pending_review_reference_count": pending_count,
            "core_data_element_count": conn.execute("SELECT COUNT(*) FROM data_element").fetchone()[0],
        }
    finally:
        conn.close()


def write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader(); writer.writerows(rows)


def write_excel(path: Path, records: list[dict[str, Any]], meta: dict[str, Any], audit: dict[str, Any]) -> None:
    stage = path.parent / "_xlsx_staging"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    try:
        # Excel eagerly coerces 001012 into a number.  Keep the review view
        # visually lossless with an explicit prefix; SQLite retains the exact
        # six-character data_element_code as the runtime key.
        display_columns = ["tree_reference_id", "data_element_code_display", "data_element_name", "binding_mode", "dependency_status", "target_table", "node_filter_json", "lookup_key_column", "value_projection_json", "representation_template", "value_translation_json", "applicability_condition_json", "fallback_policy", "notes", "review_status"]
        conn = sqlite3.connect(path.parent / "constraint_asset_extension_v1.sqlite")
        try:
            names = dict(conn.execute("SELECT data_element_code,data_element_name FROM data_element").fetchall())
            core_rows = []
            ref_summary = dict(conn.execute(
                "SELECT data_element_code,group_concat(tree_reference_id || ':' || binding_mode || '/' || dependency_status, '；') FROM tree_reference_usage GROUP BY data_element_code"
            ).fetchall())
            for row in conn.execute(
                """SELECT data_element_code,data_element_name,extraction_status,scope_status,core_status,format_status,
                          data_type,string_length_exact,string_length_max,integer_max_digits,decimal_max_fraction_digits,human_decision
                   FROM data_element ORDER BY data_element_code"""
            ):
                core_rows.append({
                    "data_element_code_display": f"DE-{row[0]}", "data_element_name": row[1],
                    "extraction_status": row[2], "scope_status": row[3], "core_status": row[4], "format_status": row[5],
                    "data_type": row[6], "string_length_exact": row[7], "string_length_max": row[8],
                    "integer_max_digits": row[9], "decimal_max_fraction_digits": row[10], "human_decision": row[11],
                    "org_department_tree_binding": ref_summary.get(row[0], ""),
                })
        finally:
            conn.close()
        rows = [{**record, "data_element_code_display": f"DE-{record['data_element_code']}", "data_element_name": names[record["data_element_code"]]} for record in records]
        write_csv(stage / "usage.csv", display_columns, [{column: row.get(column) for column in display_columns} for row in rows])
        core_headers = ["data_element_code_display", "data_element_name", "extraction_status", "scope_status", "core_status", "format_status", "data_type", "string_length_exact", "string_length_max", "integer_max_digits", "decimal_max_fraction_digits", "human_decision", "org_department_tree_binding"]
        write_csv(stage / "core.csv", core_headers, core_rows)
        inherit_rows = [
            {"核验项": "核心库继承", "结果": "通过", "说明": f"复制核心 SQLite；基线 SHA256={meta['inherits_core_sqlite_sha256']}"},
            {"核验项": "机构树版本", "结果": meta["tree_asset_status"], "说明": f"{TREE_ASSET_ID}；SHA256={meta['tree_sqlite_sha256']}"},
            {"核验项": "核心约束改写", "结果": "未发生", "说明": "扩展库仅新增 extension_asset_meta、tree_reference_usage 两张表。"},
            {"核验项": "不发布条件", "结果": "仍适用", "说明": "核心继承与机构树已核验；新增树引用待人工批准，BLOCKED 项不可绕过。"},
        ]
        write_csv(stage / "inherit.csv", ["核验项", "结果", "说明"], inherit_rows)
        review_rows = [
            {"范围": "可直接引用", "内容": "001012内部机构号、001013机构类别、001018所属部门，以及限定场景下的名称/账户名称。", "处理": "READY；严格按 node_id 查树。"},
            {"范围": "仅上下文", "内容": "支付行号、许可证号、金融机构代码、发行机构代码、机构地址。", "处理": "BLOCKED_EXTERNAL_MAPPING；缺映射则人工审核。"},
            {"范围": "本次不含", "内容": "003002/003003/003004 科目树绑定；员工、柜员、客户对象池。", "处理": "等待独立资产，不从 CoreBank 继承。"},
        ]
        write_csv(stage / "review.csv", ["范围", "内容", "处理"], review_rows)
        audit_rows = [{"指标": key, "值": canonical(value) if isinstance(value, (dict, list)) else value} for key, value in {**meta, **audit}.items()]
        write_csv(stage / "audit.csv", ["指标", "值"], audit_rows)
        if path.exists(): path.unlink()
        subprocess.run(["uvx", "--with", "click", "agent-xlsx", "write", str(path), "A1", "--from-csv", str(stage / "usage.csv"), "--sheet", "机构部门树引用"], check=True, cwd=ROOT)
        for sheet, source in [("完整继承数据元", "core.csv"), ("核心继承核验", "inherit.csv"), ("人工审查说明", "review.csv"), ("构建审计", "audit.csv")]:
            subprocess.run(["uvx", "--with", "click", "agent-xlsx", "sheet", str(path), "--create", sheet], check=True, cwd=ROOT)
            subprocess.run(["uvx", "--with", "click", "agent-xlsx", "write", str(path), "A1", "--from-csv", str(stage / source), "--sheet", sheet], check=True, cwd=ROOT)
        for sheet in ["机构部门树引用", "完整继承数据元", "核心继承核验", "人工审查说明", "构建审计"]:
            subprocess.run(["uvx", "--with", "click", "agent-xlsx", "format", str(path), "A1:Z1", "--sheet", sheet, "--bold", "--fill-color", "1F4E78", "--font-color", "FFFFFF", "--horizontal", "center", "--wrap-text"], check=True, cwd=ROOT)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def write_design(path: Path, audit: dict[str, Any]) -> None:
    path.write_text(f"""# 扩展数据元约束资产 V1：机构部门树绑定

状态：`review_only_not_released`  
版本：`{ASSET_VERSION}`

## 继承边界

本库由核心 SQLite 复制得到，再仅增加 `extension_asset_meta` 和 `tree_reference_usage`。核心 EAST 约束、码表引用、来源与人工决策均未改写。

## 树引用

- `001012 内部机构号` → `org_unit_node.org_unit_code`，直接取值。
- `001013 机构类别` → 机构层级/部门节点翻译：总行=管理机构，分支=营业机构，部门=内设机构；当前不生成虚拟机构。
- `001018 所属部门` → 部门节点 `formal_name`，直接取值。
- 其他机构编码/地址字段只取得树上下文；无独立外部映射时必须人工审核，不能用合成机构号伪装为国家统一代码。

## 本次不含

科目树和 `003002/003003/003004` 的绑定尚未建设；员工、柜员、客户对象池不从 CoreBank 继承。

## 验收

- 核心数据元继承数：{audit['core_data_element_count']}
- 树引用：{audit['tree_reference_count']}（READY={audit['ready_reference_count']}，BLOCKED={audit['blocked_reference_count']}，待审查={audit['pending_review_reference_count']}）
- 校验：{audit['status']}
""", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve(); output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "constraint_asset_extension_v1.sqlite"
    meta, records = build_extension(output)
    audit = validate(output, sha256_file(CORE_DB), sha256_file(TREE_DB))
    if audit["status"] != "PASS":
        raise RuntimeError("扩展资产校验失败：" + "; ".join(audit["errors"]))
    (output_dir / "扩展资产构建审计.json").write_text(canonical({**meta, **audit, "extension_sqlite_sha256": sha256_file(output)}), encoding="utf-8")
    write_design(output_dir / "扩展数据元约束资产V1设计.md", audit)
    write_excel(output_dir / "扩展数据元约束资产V1_人工审查.xlsx", records, meta, audit)
    print(canonical({"output": str(output), "audit": audit}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
