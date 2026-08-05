#!/usr/bin/env python3
"""Apply the recorded human approval to the synthetic chart-of-accounts tree."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from build_synthetic_chart_of_accounts_tree import (
    ASSET_VERSION, OUTPUT_DIR, sha256_file, validate, write_design, write_excel,
)


APPROVAL_REF = "SRC_HUMAN_APPROVAL:20260805_科目树四级扩展合同及发布批准"
APPROVAL_NOTE = "用户确认：不设虚拟四级；允许三级同级补充，并以8位、前6位为三级父级的方式按需扩展四级；批准发布。"
APPROVED_DIR = OUTPUT_DIR.parent / "20260805_会计科目树V1_人工审批后"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    source = OUTPUT_DIR / "synthetic_chart_of_accounts_tree_v1.sqlite"
    if not source.exists():
        raise RuntimeError(f"缺少待审批科目树：{source}")
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    target = APPROVED_DIR / "synthetic_chart_of_accounts_tree_v1.sqlite"
    shutil.copy2(source, target)
    conn = sqlite3.connect(target)
    try:
        current = dict(conn.execute("SELECT asset_key,asset_value FROM asset_meta").fetchall())
        if current.get("asset_status") != "review_only_not_released":
            raise RuntimeError(f"待审批资产状态异常：{current.get('asset_status')}")
        pending_nodes = conn.execute("SELECT COUNT(*) FROM account_node WHERE review_status='PENDING_HUMAN_REVIEW'").fetchone()[0]
        pending_bindings = conn.execute("SELECT COUNT(*) FROM east_data_element_binding_plan WHERE review_status='PENDING_HUMAN_REVIEW'").fetchone()[0]
        if not pending_nodes or pending_bindings != 3:
            raise RuntimeError("待审批节点或三个数据元绑定计划不完整")
        now = utc_now()
        conn.execute("UPDATE account_node SET review_status='APPROVED' WHERE review_status='PENDING_HUMAN_REVIEW'")
        conn.execute("UPDATE east_data_element_binding_plan SET review_status='APPROVED', dependency_status='READY' WHERE review_status='PENDING_HUMAN_REVIEW'")
        conn.execute("UPDATE asset_meta SET asset_value=? WHERE asset_key='asset_status'", ("approved_not_released",))
        conn.execute("INSERT INTO asset_meta VALUES ('approval_ref',?)", (APPROVAL_REF,))
        conn.execute("INSERT INTO asset_meta VALUES ('approved_at_utc',?)", (now,))
        conn.execute("INSERT INTO asset_meta VALUES ('approval_note',?)", (APPROVAL_NOTE,))
        conn.commit()
    finally:
        conn.close()
    audit = validate(target)
    if audit["status"] != "PASS":
        raise RuntimeError(json.dumps(audit, ensure_ascii=False))
    conn = sqlite3.connect(target)
    try:
        if conn.execute("SELECT COUNT(*) FROM account_node WHERE review_status<>'APPROVED'").fetchone()[0]:
            raise RuntimeError("存在未审批节点")
        if conn.execute("SELECT COUNT(*) FROM east_data_element_binding_plan WHERE review_status<>'APPROVED' OR dependency_status<>'READY'").fetchone()[0]:
            raise RuntimeError("存在未审批或未就绪的数据元绑定")
    finally:
        conn.close()
    digest = sha256_file(target)
    write_design(APPROVED_DIR / "会计科目树V1设计.md", audit, digest, "approved_not_released", APPROVAL_REF)
    write_excel(APPROVED_DIR / "会计科目树V1_人工审查.xlsx", target, audit)
    (APPROVED_DIR / "会计科目树构建审计.json").write_text(
        json.dumps({"asset_sha256": digest, "approval_ref": APPROVAL_REF, "approval_note": APPROVAL_NOTE, "audit": audit}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"approved_dir": str(APPROVED_DIR), "asset_sha256": digest, "audit": audit}, ensure_ascii=False))


if __name__ == "__main__":
    main()
