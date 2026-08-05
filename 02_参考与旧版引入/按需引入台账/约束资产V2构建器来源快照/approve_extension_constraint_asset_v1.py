#!/usr/bin/env python3
"""Apply the recorded human approval to V1 organisation-tree bindings only.

The inherited core tables are never changed.  This records that the ten newly
introduced tree-reference rules have been reviewed, while keeping the package a
partial foundation asset rather than a complete four-layer constraint release.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "kb/working/20260727_000_goal_mode_v1/构建过程层/04_字段多字段与对象明细状态提取-V2/extension_assets_v1/20260805_扩展数据元约束资产V1_机构部门树绑定/constraint_asset_extension_v1.sqlite"
APPROVAL_REF = "SRC_HUMAN_APPROVAL:20260805_扩展数据元机构部门树绑定_没什么问题"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    db = args.db.resolve()
    conn = sqlite3.connect(db)
    try:
        conn.execute("BEGIN")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS extension_approval_log (
                 approval_id TEXT PRIMARY KEY, approval_scope TEXT NOT NULL,
                 approval_status TEXT NOT NULL, approval_ref TEXT NOT NULL,
                 approved_at_utc TEXT NOT NULL, note TEXT NOT NULL)"""
        )
        conn.execute("UPDATE tree_reference_usage SET review_status='APPROVED' WHERE review_status='PENDING_HUMAN_REVIEW'")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR REPLACE INTO extension_asset_meta VALUES (?,?)", ("publication_status", "approved_partial_not_released"))
        conn.execute("INSERT OR REPLACE INTO extension_asset_meta VALUES (?,?)", ("approval_ref", APPROVAL_REF))
        conn.execute("INSERT OR REPLACE INTO extension_asset_meta VALUES (?,?)", ("approved_at_utc", now))
        conn.execute(
            "INSERT OR REPLACE INTO extension_approval_log VALUES (?,?,?,?,?,?)",
            ("APPROVAL_CA_V0_1_0_ORG_DEPT_BINDINGS", "TREE_REFERENCE_USAGE", "APPROVED", APPROVAL_REF, now,
             "用户确认十条机构/部门树引用无问题；完整四层约束资产尚未完成。"),
        )
        conn.commit()
        total, approved = conn.execute("SELECT COUNT(*),SUM(review_status='APPROVED') FROM tree_reference_usage").fetchone()
        print({"tree_reference_total": total, "tree_reference_approved": approved, "publication_status": conn.execute("SELECT meta_value FROM extension_asset_meta WHERE meta_key='publication_status'").fetchone()[0]})
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
