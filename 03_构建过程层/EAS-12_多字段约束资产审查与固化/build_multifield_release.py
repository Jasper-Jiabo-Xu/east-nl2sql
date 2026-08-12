#!/usr/bin/env python3
"""Build the immutable CA-V0.3.0-multifield delivery database.

Only approved EAS-12 multifield constraints are published. The two rejected
self-references remain in a separate audit table. All transformations are
deterministic and use only the reviewed SQLite asset plus frozen evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ASSET_VERSION = "CA-V0.3.0-multifield"
ARTIFACT_ID = "CA-MULTIFIELD-20260812-003"
BUILT_AT = "2026-08-12T14:31:01Z"


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def comparison_definitions() -> dict[int, tuple[list[str], list[str], dict]]:
    def binary(left: str, operator: str, right: str, condition: dict | None = None):
        expression = {
            "schema_version": "EAS-MFC-1.0",
            "kind": "COMPARISON",
            "assertion": {"left": left, "operator": operator, "right": right},
        }
        if condition:
            expression["condition"] = condition
        return [left, right], ["ASSERTION_LEFT", "ASSERTION_RIGHT"], expression

    result = {
        29: binary("GRJCXXB.YYED", "<=", "GRJCXXB.SXED"),
        40: binary("DGKHXXB.YYED", "<=", "DGKHXXB.SXED"),
        53: binary("JTKHB.JTYYED", "<=", "JTKHB.JTSXED"),
        56: binary("JTKHB.CYYYED", "<=", "JTKHB.JTSXED"),
        190: binary("GRXDFHZ.DKJE", ">=", "GRXDFHZ.DKYE", {"field": "GRXDFHZ.DKJE", "operator": "!=", "value": 0}),
        213: binary("DGXDFHZ.DKJE", ">=", "DGXDFHZ.DKYE", {"field": "DGXDFHZ.DKJE", "operator": "!=", "value": 0}),
        241: binary("GRXDYWJJB.DKJE", ">=", "GRXDYWJJB.DKYE", {"field": "GRXDYWJJB.DKJE", "operator": "!=", "value": 0}),
        243: binary("GRXDYWJJB.LJQKQS", ">=", "GRXDYWJJB.LXQKQS"),
        254: binary("DGXDYWJJB.DKJE", ">=", "DGXDYWJJB.DKYE", {"field": "DGXDYWJJB.DKJE", "operator": "!=", "value": 0}),
        256: binary("DGXDYWJJB.LJQKQS", ">=", "DGXDYWJJB.LXQKQS"),
        278: binary("YTDKXXB.YTDKZJE", ">=", "YTDKXXB.CDDKJE", {"field": "YTDKXXB.YTDKZJE", "operator": "!=", "value": 0}),
        279: binary("YTDKXXB.YFFDKJE", "<=", "YTDKXXB.YTDKZJE", {"field": "YTDKXXB.YFFDKJE", "operator": "!=", "value": 0}),
        280: binary("YTDKXXB.CDDKJE", ">=", "YTDKXXB.YFFCDDKJE", {"field": "YTDKXXB.CDDKJE", "operator": "!=", "value": 0}),
        305: binary("RZZLYWB.XYZYE", "<=", "RZZLYWB.XYZJE", {"field": "RZZLYWB.XYZYE", "operator": "!=", "value": 0}),
        368: binary("XYKXXB.BBSXYE", "<=", "XYKXXB.BBXYED"),
        369: binary("XYKXXB.WBSXYE", "<=", "XYKXXB.WBXYED"),
    }

    for candidate_id, table in ((100, "GRHQCKFHZMX"), (121, "GRDQCKFHZMX"), (141, "DGHQCKFHZMX"), (161, "DGDQCKFHZMX")):
        left, right, condition_field = f"{table}.ZHYE", f"{table}.JYJE", f"{table}.JYJDBZ"
        result[candidate_id] = (
            [left, right, condition_field],
            ["ASSERTION_LEFT", "ASSERTION_RIGHT", "CONDITION"],
            {
                "schema_version": "EAS-MFC-1.0",
                "kind": "CONDITIONAL_COMPARISON",
                "condition": {"field": condition_field, "operator": "=", "value": "贷"},
                "assertion": {"left": left, "operator": ">=", "right": right},
            },
        )

    for candidate_id, local_table in ((28, "GRJCXXB"), (39, "DGKHXXB")):
        group = [
            f"{local_table}.SXED", f"{local_table}.KHTYBH", "SXXXB.KHTYBH",
            "SXXXB.SXZT", "SXXXB.SXED",
        ]
        result[candidate_id] = (
            group,
            ["ASSERTION_LEFT", "JOIN_LEFT", "JOIN_RIGHT", "CONDITION", "ASSERTION_RIGHT"],
            {
                "schema_version": "EAS-MFC-1.0",
                "kind": "CONDITIONAL_COMPARISON",
                "join": {"left": group[1], "operator": "=", "right": group[2]},
                "condition": {"field": group[3], "operator": "=", "value": "有效"},
                "assertion": {"left": group[0], "operator": ">=", "right": group[4]},
            },
        )

    for candidate_id, table in ((242, "GRXDYWJJB"), (255, "DGXDYWJJB")):
        group = [f"{table}.DKWJFL", f"{table}.XDJJH", "DKHXB.XDJJH"]
        result[candidate_id] = (
            group,
            ["ASSERTION_FIELD", "CONDITION_LEFT", "CONDITION_RIGHT"],
            {
                "schema_version": "EAS-MFC-1.0",
                "kind": "CONDITIONAL_VALUE_EXCLUSION",
                "condition": {"left": group[1], "operator": "EXISTS_IN", "right": group[2]},
                "assertion": {"field": group[0], "operator": "NOT_IN", "values": ["正常", "关注"]},
            },
        )

    result[339] = (
        ["DKHXB.SHRQ", "DKHXB.SHBJ", "DKHXB.SHLX"],
        ["ASSERTION_FIELD", "CONDITION", "CONDITION"],
        {
            "schema_version": "EAS-MFC-1.0",
            "kind": "CONDITIONAL_VALUE_EXCLUSION",
            "condition": {"fields": ["DKHXB.SHBJ", "DKHXB.SHLX"], "operator": "NOT_ALL_EQUAL", "value": 0},
            "assertion": {"field": "DKHXB.SHRQ", "operator": "!=", "value": "99991231"},
        },
    )

    for candidate_id, left, right in (
        (436, "ZYZJJYXXB.HTDQRQ", "ZYZJJYXXB.HTYDRQ"),
        (447, "ZYZJYWYEB.DQRQ", "ZYZJYWYEB.QXRQ"),
    ):
        result[candidate_id] = (
            [left, right],
            ["ASSERTION_LEFT", "ASSERTION_RIGHT"],
            {
                "schema_version": "EAS-MFC-1.0",
                "kind": "CONDITIONAL_COMPARISON",
                "condition": {"fields": [left, right], "operator": "ALL_NOT_IN", "values": [None, "99991231"]},
                "assertion": {"left": left, "operator": ">=", "right": right},
            },
        )

    return result


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE release_meta (
  meta_key TEXT PRIMARY KEY,
  meta_value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE source_manifest (
  source_file_id TEXT PRIMARY KEY,
  source_file_sha256 TEXT NOT NULL,
  source_file_path TEXT NOT NULL,
  sheet_name TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE field_master (
  field_id TEXT PRIMARY KEY,
  table_code TEXT NOT NULL,
  table_name TEXT NOT NULL,
  field_code TEXT NOT NULL,
  field_name TEXT NOT NULL,
  data_element_code TEXT,
  source_row INTEGER,
  endpoint TEXT NOT NULL UNIQUE,
  UNIQUE(table_code, field_code)
) WITHOUT ROWID;
CREATE TABLE evidence (
  evidence_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  content TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  cell_range TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE multifield_constraint (
  constraint_id TEXT PRIMARY KEY,
  source_candidate_id INTEGER NOT NULL UNIQUE,
  field_id TEXT NOT NULL REFERENCES field_master(field_id),
  constraint_item_type TEXT NOT NULL CHECK(constraint_item_type IN ('REFERENCE_EXISTENCE','COMPARISON')),
  condition_type TEXT NOT NULL,
  condition_text TEXT NOT NULL,
  requirement_text TEXT NOT NULL,
  field_group_json TEXT NOT NULL CHECK(json_valid(field_group_json)),
  field_roles_json TEXT NOT NULL CHECK(json_valid(field_roles_json)),
  structured_expression_json TEXT NOT NULL CHECK(json_valid(structured_expression_json)),
  scope TEXT NOT NULL CHECK(scope IN ('INTRA_TABLE','CROSS_TABLE')),
  ods_classification TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL CHECK(json_valid(evidence_refs_json)),
  approval_status TEXT NOT NULL CHECK(approval_status='APPROVED'),
  approval_basis TEXT NOT NULL,
  content_sha256 TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE multifield_constraint_field (
  constraint_id TEXT NOT NULL REFERENCES multifield_constraint(constraint_id),
  field_ordinal INTEGER NOT NULL,
  field_ref TEXT NOT NULL,
  field_role TEXT NOT NULL,
  PRIMARY KEY(constraint_id, field_ordinal),
  FOREIGN KEY(field_ref) REFERENCES field_master(endpoint)
) WITHOUT ROWID;
CREATE TABLE excluded_constraint_audit (
  source_candidate_id INTEGER PRIMARY KEY,
  field_id TEXT NOT NULL,
  requirement_text TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL CHECK(json_valid(evidence_refs_json)),
  disposition TEXT NOT NULL CHECK(disposition='DISCARDED'),
  reason_code TEXT NOT NULL,
  approval_ref TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE decision_audit (
  source_candidate_id INTEGER PRIMARY KEY,
  decision_basis TEXT NOT NULL,
  source_attachment_id TEXT NOT NULL,
  source_attachment_sha256 TEXT NOT NULL,
  source_comment_id TEXT NOT NULL,
  before_hash TEXT NOT NULL,
  after_hash TEXT NOT NULL,
  applied_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE VIEW approved_reference_constraints AS
SELECT * FROM multifield_constraint WHERE constraint_item_type='REFERENCE_EXISTENCE';
CREATE VIEW approved_comparison_constraints AS
SELECT * FROM multifield_constraint WHERE constraint_item_type='COMPARISON';
CREATE VIEW cross_table_constraints AS
SELECT * FROM multifield_constraint WHERE scope='CROSS_TABLE';
CREATE VIEW intra_table_constraints AS
SELECT * FROM multifield_constraint WHERE scope='INTRA_TABLE';
"""


def build(source_path: Path, output_path: Path) -> dict:
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    approved = source.execute(
        "SELECT * FROM multi_field_candidates WHERE review_status='APPROVE' ORDER BY id"
    ).fetchall()
    discarded = source.execute(
        "SELECT * FROM multi_field_candidates WHERE review_status='DISCARDED' ORDER BY id"
    ).fetchall()
    if len(approved) != 470 or len(discarded) != 2:
        raise ValueError(f"unexpected review counts: approved={len(approved)}, discarded={len(discarded)}")

    definitions = comparison_definitions()
    if len(definitions) != 27:
        raise ValueError(f"comparison definitions incomplete: {len(definitions)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    target = sqlite3.connect(output_path)
    target.executescript(SCHEMA)

    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    meta = {
        "artifact_id": ARTIFACT_ID,
        "asset_version": ASSET_VERSION,
        "publication_status": "PENDING_PR_HUMAN_APPROVAL",
        "built_at_utc": BUILT_AT,
        "source_reviewed_sqlite_sha256": source_sha,
        "source_human_attachment_id": "019ff606-ccdb-7185-aa66-713fb7d43b60",
        "source_human_attachment_sha256": "a3ffe34f6c44ddb1a5aa86a0676a66fb592078bfbeb3fa094dd1d85924fc32e3",
        "source_publication_instruction_comment_id": "c7ce0266-f7cc-49e2-b7d5-abba1e8d353d",
        "consumer_guard": "仅包含已批准多字段约束；不包含单字段、ODS、Foundation对象池或业务数据。",
    }
    target.executemany("INSERT INTO release_meta VALUES (?,?)", sorted(meta.items()))

    for row in source.execute("SELECT * FROM source_manifest"):
        target.execute("INSERT INTO source_manifest VALUES (?,?,?,?)", tuple(row))

    referenced_fields: set[str] = set()
    referenced_evidence: set[str] = set()
    output_rows = []
    field_rows = []

    audit_basis = {
        row["candidate_id"]: row["decision_basis"]
        for row in source.execute("SELECT candidate_id,decision_basis FROM multi_field_decision_audit")
    }

    for row in approved:
        source_id = row["id"]
        constraint = json.loads(row["constraint_json"])
        original_group = json.loads(row["field_group"])
        item_type = constraint["constraint_item_type"]

        if source_id in definitions:
            group, roles, expression = definitions[source_id]
            item_type = "COMPARISON"
            role_items = [{"field": field, "role": role} for field, role in zip(group, roles)]
        else:
            if item_type != "REFERENCE_EXISTENCE":
                raise ValueError(f"candidate {source_id}: missing comparison definition")
            group = original_group
            expression = constraint.get("structured_expression")
            if not expression or expression.get("direction") != "PROVIDER_TO_CONSUMER":
                raise ValueError(f"candidate {source_id}: missing structured reference direction")
            consumer = expression["consumer_field"]
            providers = expression["provider_fields"]
            role_items = [{"field": consumer, "role": "CONSUMER"}] + [
                {"field": field, "role": "PROVIDER"} for field in providers
            ]
            group = [consumer, *providers]

        group = list(dict.fromkeys(group))
        roles_by_field = {item["field"]: item["role"] for item in role_items}
        role_items = [{"field": field, "role": roles_by_field[field]} for field in group]
        table_codes = {field.split(".", 1)[0] for field in group}
        scope = "CROSS_TABLE" if len(table_codes) > 1 else "INTRA_TABLE"
        evidence_refs = json.loads(row["evidence_refs_json"])
        for ref in evidence_refs:
            referenced_evidence.add(ref["evidence_id"])
        referenced_fields.update(group)

        constraint_id = f"MFC-{source_id:06d}"
        payload = {
            "constraint_id": constraint_id,
            "source_candidate_id": source_id,
            "field_id": row["field_id"],
            "constraint_item_type": item_type,
            "condition_type": constraint["condition_type"],
            "condition_text": constraint.get("condition_text", ""),
            "requirement_text": constraint["requirement_text"],
            "field_group": group,
            "field_roles": role_items,
            "structured_expression": expression,
            "scope": scope,
            "ods_classification": row["ods_classification"],
            "evidence_refs": evidence_refs,
            "approval_status": "APPROVED",
            "approval_basis": audit_basis[source_id],
        }
        content_hash = digest_text(canonical(payload))
        output_rows.append((
            constraint_id, source_id, row["field_id"], item_type,
            constraint["condition_type"], constraint.get("condition_text", ""),
            constraint["requirement_text"], canonical(group), canonical(role_items),
            canonical(expression), scope, row["ods_classification"], canonical(evidence_refs),
            "APPROVED", audit_basis[source_id], content_hash,
        ))
        for ordinal, field in enumerate(group, start=1):
            field_rows.append((constraint_id, ordinal, field, roles_by_field[field]))

    placeholders = ",".join("?" for _ in referenced_fields)
    for row in source.execute(
        f"SELECT field_id,table_code,table_name,field_code,field_name,data_element_code,source_row,"
        f"table_code||'.'||field_code "
        f"FROM field_master WHERE table_code||'.'||field_code IN ({placeholders}) ORDER BY field_id",
        sorted(referenced_fields),
    ):
        target.execute("INSERT INTO field_master VALUES (?,?,?,?,?,?,?,?)", tuple(row))

    placeholders = ",".join("?" for _ in referenced_evidence)
    for row in source.execute(
        f"SELECT evidence_id,source_type,content,coalesce(sheet_name,''),coalesce(cell_range,'') "
        f"FROM evidence WHERE evidence_id IN ({placeholders}) ORDER BY evidence_id",
        sorted(referenced_evidence),
    ):
        target.execute("INSERT INTO evidence VALUES (?,?,?,?,?)", tuple(row))

    target.executemany(
        "INSERT INTO multifield_constraint VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", output_rows
    )
    target.executemany("INSERT INTO multifield_constraint_field VALUES (?,?,?,?)", field_rows)

    for row in discarded:
        constraint = json.loads(row["constraint_json"])
        target.execute(
            "INSERT INTO excluded_constraint_audit VALUES (?,?,?,?,?,?,?)",
            (
                row["id"], row["field_id"], constraint["requirement_text"], row["evidence_refs_json"],
                "DISCARDED", "SELF_REFERENCE_WITHOUT_DISTINCT_PROVIDER_RECORD",
                "EAS-12人工批准废弃",
            ),
        )

    for row in source.execute("SELECT * FROM multi_field_decision_audit ORDER BY candidate_id"):
        target.execute("INSERT INTO decision_audit VALUES (?,?,?,?,?,?,?,?)", tuple(row))

    checks = {
        "approved_constraints": target.execute("SELECT count(*) FROM multifield_constraint").fetchone()[0],
        "reference_constraints": target.execute("SELECT count(*) FROM approved_reference_constraints").fetchone()[0],
        "comparison_constraints": target.execute("SELECT count(*) FROM approved_comparison_constraints").fetchone()[0],
        "intra_table_constraints": target.execute("SELECT count(*) FROM intra_table_constraints").fetchone()[0],
        "cross_table_constraints": target.execute("SELECT count(*) FROM cross_table_constraints").fetchone()[0],
        "excluded_constraints": target.execute("SELECT count(*) FROM excluded_constraint_audit").fetchone()[0],
        "constraint_field_rows": target.execute("SELECT count(*) FROM multifield_constraint_field").fetchone()[0],
        "referenced_fields": target.execute("SELECT count(*) FROM field_master").fetchone()[0],
        "referenced_evidence": target.execute("SELECT count(*) FROM evidence").fetchone()[0],
    }
    if checks["approved_constraints"] != 470 or checks["excluded_constraints"] != 2:
        raise ValueError(f"release coverage failed: {checks}")
    if checks["reference_constraints"] != 443 or checks["comparison_constraints"] != 27:
        raise ValueError(f"type coverage failed: {checks}")
    if checks["referenced_evidence"] != 470:
        raise ValueError(f"evidence coverage failed: {checks}")

    integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != "ok" or foreign_keys:
        raise ValueError(f"SQLite validation failed: integrity={integrity}, fk={foreign_keys}")

    target.commit()
    target.execute("VACUUM")
    target.close()
    source.close()
    checks["sqlite_integrity_check"] = integrity
    checks["foreign_key_violations"] = len(foreign_keys)
    checks["sqlite_sha256"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
    checks["sqlite_size_bytes"] = output_path.stat().st_size
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    checks = build(args.source, args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
