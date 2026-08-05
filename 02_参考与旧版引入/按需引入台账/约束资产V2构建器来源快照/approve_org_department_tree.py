#!/usr/bin/env python3
"""Apply the recorded human approval to the synthetic organisation-tree SQLite.

This is an approval overlay, not a rebuild: node content is untouched.  The
asset remains non-released and contains no real CoreBank records.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "kb/working/20260727_000_goal_mode_v1/构建过程层/04_字段多字段与对象明细状态提取-V2/extension_assets_v1/20260805_机构部门统一树V1_人工审查/extension_org_department_tree_v1.sqlite"
APPROVAL_REF = "SRC_HUMAN_APPROVAL:20260805_机构部门统一树V1审查通过"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"未找到机构树 SQLite：{db}")
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS approval_log (
                 approval_id TEXT PRIMARY KEY, approval_scope TEXT NOT NULL,
                 approval_status TEXT NOT NULL, approval_ref TEXT NOT NULL,
                 approved_at_utc TEXT NOT NULL, note TEXT NOT NULL)"""
        )
        conn.execute("BEGIN")
        conn.execute("UPDATE org_unit_node SET review_status='APPROVED' WHERE review_status='PENDING_HUMAN_REVIEW'")
        conn.execute("UPDATE org_unit_relation SET review_status='APPROVED' WHERE review_status='PENDING_HUMAN_REVIEW'")
        conn.execute("UPDATE department_type_catalog SET review_status='APPROVED' WHERE review_status='PENDING_HUMAN_REVIEW'")
        conn.execute("INSERT OR REPLACE INTO asset_meta(asset_key,asset_value) VALUES (?,?)", ("asset_status", "approved_not_released"))
        conn.execute("INSERT OR REPLACE INTO asset_meta(asset_key,asset_value) VALUES (?,?)", ("approval_ref", APPROVAL_REF))
        conn.execute("INSERT OR REPLACE INTO asset_meta(asset_key,asset_value) VALUES (?,?)", ("approved_at_utc", now))
        conn.execute(
            "INSERT OR REPLACE INTO approval_log VALUES (?,?,?,?,?,?)",
            ("APPROVAL_ORG_DEPT_TREE_V1_20260805", "ALL_NODES_RELATIONS_AND_DEPARTMENT_TYPES", "APPROVED", APPROVAL_REF, now,
             "人工作为扩展资产通过；仍不属于正式数据发布。"),
        )
        conn.commit()
        rows = {
            "node_review_status": conn.execute("SELECT review_status,COUNT(*) FROM org_unit_node GROUP BY review_status").fetchall(),
            "relation_review_status": conn.execute("SELECT review_status,COUNT(*) FROM org_unit_relation GROUP BY review_status").fetchall(),
            "department_type_review_status": conn.execute("SELECT review_status,COUNT(*) FROM department_type_catalog GROUP BY review_status").fetchall(),
            "asset_status": conn.execute("SELECT asset_value FROM asset_meta WHERE asset_key='asset_status'").fetchone()[0],
        }
        print(rows)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
