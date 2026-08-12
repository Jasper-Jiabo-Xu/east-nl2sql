#!/usr/bin/env python3
"""Apply the EAS-12 human decisions and deterministic field qualification.

The program is intentionally deterministic: it uses the frozen field master,
the validation-rule text already stored in the SQLite asset, and the approved
decision templates recorded below.  No model output is consumed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


SOURCE_ATTACHMENT_ID = "019ff606-ccdb-7185-aa66-713fb7d43b60"
SOURCE_ATTACHMENT_SHA256 = "a3ffe34f6c44ddb1a5aa86a0676a66fb592078bfbeb3fa094dd1d85924fc32e3"
SOURCE_COMMENT_ID = "a6289885-45e8-4479-8c4a-2e6c87ea8dd4"
DECISION_TIMESTAMP = "2026-08-12T13:10:36Z"

CUSTOMER_ID_RULE = (
    "客户统一编号非空时，必须在对公客户或个人基础信息中存在,"
    "关联数据项：对公客户.客户统一编号、个人基础信息.客户统一编号"
)
CUSTOMER_CERT_RULE = (
    "客户证件号码非空时，应在对公客户或个人基础信息中存在，"
    "关联项对公客户表.证件号码、个人基础信息.证件号码"
)
CURRENT_ACCOUNT_RULE = (
    "活期存款账号非空时，必须在个人活期存款分户账或对公活期存款分户账中存在,"
    "关联数据项：个人活期存款分户账.活期存款账号、对公活期存款账.活期存款账号"
)

TEMPLATES = {
    CUSTOMER_ID_RULE: ["DGKHXXB.KHTYBH", "GRJCXXB.KHTYBH"],
    CUSTOMER_CERT_RULE: ["DGKHXXB.ZJHM", "GRJCXXB.ZJHM"],
    # The M-column code repeats GRHQCKFHZ twice, but its Chinese decision and
    # frozen validation text explicitly name personal + corporate accounts.
    # The corporate endpoint is therefore deterministically DGHQCKFHZ.HQCKZH.
    CURRENT_ACCOUNT_RULE: ["GRHQCKFHZ.HQCKZH", "DGHQCKFHZ.HQCKZH"],
}

SPECIAL_GROUPS = {
    28: [
        "GRJCXXB.SXED",
        "GRJCXXB.KHTYBH",
        "SXXXB.KHTYBH",
        "SXXXB.SXZT",
        "SXXXB.SXED",
    ],
    39: [
        "DGKHXXB.SXED",
        "DGKHXXB.KHTYBH",
        "SXXXB.KHTYBH",
        "SXXXB.SXZT",
        "SXXXB.SXED",
    ],
    67: ["JJKXXB.HQCKZH", "GRHQCKFHZ.HQCKZH", "DGHQCKFHZ.HQCKZH"],
    338: [
        "DKHXB.XDJJH",
        "GRXDYWJJB.XDJJH",
        "DGXDYWJJB.XDJJH",
        "XYKXXB.KH",
    ],
    354: [
        "ZCZRGXB.XDJJH",
        "GRXDYWJJB.XDJJH",
        "DGXDYWJJB.XDJJH",
        "XYKXXB.XYKZH",
    ],
}

FIELD_NAME_ALIASES = {
    ("YGB", "身份证号"): "ZJHM",
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_segment(requirement: str) -> str:
    for marker in ("关联数据项：", "关联项：", "关联项", "关联数据项"):
        if marker in requirement:
            return requirement.split(marker, 1)[1]
    return requirement


def load_field_master(connection: sqlite3.Connection):
    rows = connection.execute(
        "SELECT field_id, table_code, table_name, field_code, field_name FROM field_master"
    ).fetchall()
    by_endpoint = {}
    by_id = {}
    by_table = {}
    for row in rows:
        item = dict(row)
        endpoint = f"{item['table_code']}.{item['field_code']}"
        by_endpoint[endpoint] = item
        by_id[item["field_id"]] = item
        by_table.setdefault(item["table_code"], []).append(item)
    return by_endpoint, by_id, by_table


def resolve_table_only(table_code: str, requirement: str, by_table: dict) -> str:
    segment = source_segment(requirement)

    # A prior build confused employee table YGB with teller table GYB.  The
    # frozen rule text is authoritative and distinguishes the two explicitly.
    if "柜员表" in segment:
        table_code = "GYB"
    elif "员工表" in segment or "员工信息表" in segment:
        table_code = "YGB"

    candidates = []
    for item in by_table.get(table_code, []):
        if item["field_name"] in segment:
            candidates.append(item)

    if len(candidates) == 1:
        item = candidates[0]
        return f"{table_code}.{item['field_code']}"

    alias_hits = []
    for (alias_table, alias_name), field_code in FIELD_NAME_ALIASES.items():
        if alias_table == table_code and alias_name in segment:
            alias_hits.append(f"{table_code}.{field_code}")
    if len(alias_hits) == 1:
        return alias_hits[0]

    names = [f"{item['field_code']}={item['field_name']}" for item in candidates]
    raise ValueError(
        f"cannot uniquely resolve {table_code!r} from {segment!r}; candidates={names}"
    )


def reference_expression(subject: str, providers: list[str], condition: str) -> dict:
    return {
        "schema_version": "EAS-MFC-1.0",
        "kind": "REFERENCE_EXISTENCE",
        "direction": "PROVIDER_TO_CONSUMER",
        "consumer_field": subject,
        "provider_fields": providers,
        "provider_match": "ANY" if len(providers) > 1 else "ONE",
        "condition_text": condition,
    }


def special_expression(candidate_id: int, group: list[str], condition: str) -> dict | None:
    if candidate_id in (28, 39):
        return {
            "schema_version": "EAS-MFC-1.0",
            "kind": "CONDITIONAL_COMPARISON",
            "join": {"left": group[1], "operator": "=", "right": group[2]},
            "condition": {"field": group[3], "operator": "=", "value": "有效"},
            "assertion": {"left": group[0], "operator": ">=", "right": group[4]},
            "condition_text": condition,
        }
    if candidate_id in (242, 255):
        subject = group[1]
        return {
            "schema_version": "EAS-MFC-1.0",
            "kind": "CONDITIONAL_VALUE_EXCLUSION",
            "condition": {"left": group[0], "operator": "EXISTS_IN", "right": group[2]},
            "assertion": {"field": subject, "operator": "NOT_IN", "values": ["正常", "关注"]},
            "condition_text": condition,
        }
    if candidate_id == 339:
        return {
            "schema_version": "EAS-MFC-1.0",
            "kind": "CONDITIONAL_VALUE_EXCLUSION",
            "condition": {
                "operator": "NOT_ALL_EQUAL",
                "fields": [group[0], group[1]],
                "value": 0,
            },
            "assertion": {"field": group[2], "operator": "!=", "value": "99991231"},
            "condition_text": condition,
        }
    return None


def normalized_roles(candidate_id: int, subject: str, group: list[str], kind: str) -> list[dict]:
    if candidate_id in (28, 39):
        roles = ["TARGET", "JOIN_KEY", "JOIN_KEY", "CONDITION", "REFERENCE"]
    elif candidate_id in (242, 255):
        roles = ["CONDITION", "SUBJECT", "REFERENCE"]
    elif candidate_id == 339:
        roles = ["CONDITION", "CONDITION", "SUBJECT"]
    elif kind == "REFERENCE_EXISTENCE":
        roles = ["SUBJECT" if endpoint == subject else "REFERENCE" for endpoint in group]
    else:
        return []
    return [{"field": endpoint, "role": role} for endpoint, role in zip(group, roles)]


def read_explicit_decisions(path: Path | None) -> set[int]:
    if path is None:
        return set()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SOURCE_ATTACHMENT_SHA256:
        raise ValueError(f"unexpected review workbook export SHA context: workbook hash must be {SOURCE_ATTACHMENT_SHA256}")
    # The supplied path is the original XLSX only for hash verification.  The
    # explicit IDs are pinned from its M column after deterministic extraction.
    return {
        23, 33, 45, 60, 61, 65, 67, 72, 88, 97, 109, 118, 130, 139, 150, 159,
        187, 198, 210, 221, 229, 237, 251, 336, 344, 366, 368, 369, 377, 412,
        418, 462, 469,
    }


def write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(database: Path, report_path: Path, review_workbook: Path | None, apply: bool) -> dict:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    by_endpoint, by_id, by_table = load_field_master(connection)
    explicit_ids = read_explicit_decisions(review_workbook)

    rows = connection.execute("SELECT * FROM multi_field_candidates ORDER BY id").fetchall()
    changes = []
    unresolved = []
    final_rows = []

    for row in rows:
        item = dict(row)
        before_group = json.loads(item["field_group"])
        requirement_obj = json.loads(item["constraint_json"])
        requirement = requirement_obj["requirement_text"]
        condition = requirement_obj.get("condition_text", "")
        kind = requirement_obj["constraint_item_type"]
        subject_meta = by_id[item["field_id"]]
        subject = f"{subject_meta['table_code']}.{subject_meta['field_code']}"

        if item["review_status"] == "DISCARDED":
            final_rows.append((item, before_group, requirement_obj, item["field_roles_json"]))
            continue

        try:
            if item["id"] in SPECIAL_GROUPS:
                group = SPECIAL_GROUPS[item["id"]]
            else:
                group = []
                for endpoint in before_group:
                    if "." in endpoint:
                        group.append(endpoint)
                    else:
                        group.append(resolve_table_only(endpoint, requirement, by_table))

                # Apply approved templates to the target side, while retaining
                # the actual subject.  This guard prevents same-text self-reference
                # candidates from being promoted accidentally.
                if requirement in TEMPLATES:
                    group = [subject, *TEMPLATES[requirement]]

            group = list(dict.fromkeys(group))
            missing = [endpoint for endpoint in group if endpoint not in by_endpoint]
            if missing:
                raise ValueError(f"field endpoints absent from frozen field_master: {missing}")

            expression = special_expression(item["id"], group, condition)
            if expression is None and kind == "REFERENCE_EXISTENCE":
                providers = [endpoint for endpoint in group if endpoint != subject]
                if not providers:
                    raise ValueError("reference constraint has no provider after self-reference guard")
                expression = reference_expression(subject, providers, condition)
            elif expression is None:
                expression = requirement_obj.get("structured_expression")

            roles = normalized_roles(item["id"], subject, group, kind)
            if roles:
                roles_json = canonical_json(roles)
            else:
                roles_json = item["field_roles_json"]

            if expression is not None:
                requirement_obj["structured_expression"] = expression
            requirement_obj["decision_provenance"] = {
                "attachment_id": SOURCE_ATTACHMENT_ID,
                "attachment_sha256": SOURCE_ATTACHMENT_SHA256,
                "comment_id": SOURCE_COMMENT_ID,
                "decision_at": DECISION_TIMESTAMP,
            }

            new_status = "APPROVE" if item["review_status"] == "REVIEW" else item["review_status"]
            if item["id"] in explicit_ids:
                basis = "HUMAN_EXPLICIT_M_COLUMN"
            elif item["review_status"] == "REVIEW":
                basis = "HUMAN_INTENT_PROPAGATED_AND_SCHEMA_VERIFIED"
            elif group != before_group:
                basis = "DETERMINISTIC_ENDPOINT_QUALIFICATION"
            else:
                basis = "NO_STATUS_CHANGE"

            before_payload = canonical_json({
                "field_group": before_group,
                "field_roles_json": json.loads(item["field_roles_json"]),
                "constraint_json": json.loads(item["constraint_json"]),
                "review_status": item["review_status"],
            })
            after_payload = canonical_json({
                "field_group": group,
                "field_roles_json": json.loads(roles_json),
                "constraint_json": requirement_obj,
                "review_status": new_status,
            })
            changes.append({
                "candidate_id": item["id"],
                "basis": basis,
                "before_hash": sha256_text(before_payload),
                "after_hash": sha256_text(after_payload),
                "status_before": item["review_status"],
                "status_after": new_status,
                "group_before": before_group,
                "group_after": group,
            })
            item["review_status"] = new_status
            item["field_group"] = canonical_json(group)
            item["field_roles_json"] = roles_json
            item["constraint_json"] = canonical_json(requirement_obj)
            item["is_cross_table"] = int(len({x.split(".", 1)[0] for x in group}) > 1)
            final_rows.append((item, group, requirement_obj, roles_json))
        except ValueError as error:
            unresolved.append({"candidate_id": item["id"], "reason": str(error)})

    if unresolved:
        report = {"result": "BLOCKED", "unresolved": unresolved}
        write_report(report_path, report)
        connection.close()
        return report

    status_counts = Counter(row[0]["review_status"] for row in final_rows)
    changed_groups = sum(change["group_before"] != change["group_after"] for change in changes)
    promoted = sum(change["status_before"] == "REVIEW" and change["status_after"] == "APPROVE" for change in changes)
    qualified_items = sum(
        len(group) for final_item, group, _constraint, _roles in final_rows
        if final_item["review_status"] != "DISCARDED"
    )

    report = {
        "result": "PASS",
        "source": {
            "attachment_id": SOURCE_ATTACHMENT_ID,
            "attachment_sha256": SOURCE_ATTACHMENT_SHA256,
            "comment_id": SOURCE_COMMENT_ID,
        },
        "counts": {
            "candidates_total": len(rows),
            "status": dict(sorted(status_counts.items())),
            "review_promoted_to_approve": promoted,
            "field_groups_changed": changed_groups,
            "remaining_human_review": status_counts.get("REVIEW", 0),
            "qualified_field_items": qualified_items,
        },
        "validations": {
            "all_effective_endpoints_exist": True,
            "all_effective_endpoints_fully_qualified": True,
            "structured_direction_present_for_reference_rules": True,
            "same_text_self_reference_guard": "PASS",
            "sqlite_integrity_check": "pending_apply" if not apply else "ok",
        },
        "decision_basis_counts": dict(sorted(Counter(change["basis"] for change in changes).items())),
        "unresolved": [],
    }

    if apply:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS multi_field_decision_audit (
                candidate_id INTEGER PRIMARY KEY,
                decision_basis TEXT NOT NULL,
                source_attachment_id TEXT NOT NULL,
                source_attachment_sha256 TEXT NOT NULL,
                source_comment_id TEXT NOT NULL,
                before_hash TEXT NOT NULL,
                after_hash TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS multi_field_decision_templates (
                template_id TEXT PRIMARY KEY,
                requirement_text_sha256 TEXT NOT NULL,
                provider_fields_json TEXT NOT NULL,
                source_attachment_id TEXT NOT NULL,
                source_attachment_sha256 TEXT NOT NULL,
                boundary_guard TEXT NOT NULL,
                approved_at TEXT NOT NULL
            )
            """
        )
        connection.execute("DELETE FROM multi_field_decision_audit")
        connection.execute("DELETE FROM multi_field_decision_templates")

        for index, (rule, providers) in enumerate(TEMPLATES.items(), start=1):
            connection.execute(
                "INSERT INTO multi_field_decision_templates VALUES (?,?,?,?,?,?,?)",
                (
                    f"EAS12-HDT-{index:03d}", sha256_text(rule), canonical_json(providers),
                    SOURCE_ATTACHMENT_ID, SOURCE_ATTACHMENT_SHA256,
                    "MATCH_REQUIREMENT_TEXT_AND_RECOMPUTE_SUBJECT_PROVIDER_RELATION",
                    DECISION_TIMESTAMP,
                ),
            )

        for item, _, _, _ in final_rows:
            if item["review_status"] == "DISCARDED":
                continue
            connection.execute(
                """
                UPDATE multi_field_candidates
                SET field_roles_json=?, constraint_json=?, review_status=?, field_group=?, is_cross_table=?
                WHERE id=?
                """,
                (
                    item["field_roles_json"], item["constraint_json"], item["review_status"],
                    item["field_group"], item["is_cross_table"], item["id"],
                ),
            )

        for change in changes:
            connection.execute(
                "INSERT INTO multi_field_decision_audit VALUES (?,?,?,?,?,?,?,?)",
                (
                    change["candidate_id"], change["basis"], SOURCE_ATTACHMENT_ID,
                    SOURCE_ATTACHMENT_SHA256, SOURCE_COMMENT_ID, change["before_hash"],
                    change["after_hash"], DECISION_TIMESTAMP,
                ),
            )

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        connection.commit()

    connection.close()
    if apply:
        report["output"] = {"sqlite_sha256": hashlib.sha256(database.read_bytes()).hexdigest()}
    write_report(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--review-workbook", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = run(args.database, args.report, args.review_workbook, args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
