"""160: deterministic, read-only precheck of pending question-SQL candidates.

160 is hard-coded verification with no LLM decision authority.  A passing
candidate is frozen into ``question_sql_pending_dual_review`` for 170/180; a
failing candidate returns a precise ``precheck_failed_feedback`` for 150.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS
from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json, sha256

SCHEMAS = {
    "question_sql_pending_precheck": "contracts/packages/question-sql-pending-precheck-package.schema.json",
    "precheck_failed_feedback": "contracts/packages/precheck-failed-feedback-package.schema.json",
    "question_sql_pending_dual_review": "contracts/packages/question-sql-pending-dual-review-package.schema.json",
    "query_specification_package": "contracts/packages/query-specification-package.schema.json",
}

# Canonical rule order drives the deterministic precheck_report.
REPORT_RULE_ORDER = (
    "PC-SQL-001", "PC-SQL-002", "PC-SQL-003", "PC-SQL-004", "PC-SQL-005",
    "PC-SQL-006", "PC-SQL-007", "PC-SQL-008", "PC-REF-001", "PC-REF-002",
    "PC-REF-003", "PC-MAP-001", "PC-MAP-002", "PC-EVI-001", "PC-FMT-001",
    "PC-LIN-001",
)
RULE_LABELS = {
    "PC-SQL-001": "SQL 非空",
    "PC-SQL-002": "只读单语句（禁写操作/DDL/多语句）",
    "PC-SQL-003": "禁 SELECT *",
    "PC-SQL-004": "禁动态时间",
    "PC-SQL-005": "表/字段在 sql_schema_scope 内",
    "PC-SQL-006": "字段限定无歧义",
    "PC-SQL-007": "引号标识符在范围内",
    "PC-SQL-008": "SQL 可由 SQLite 解析",
    "PC-REF-001": "query_spec_ref 为合法引用",
    "PC-REF-002": "penalty_fact_package_ref 为合法引用",
    "PC-REF-003": "observable_fact_package_ref 为合法引用",
    "PC-MAP-001": "规格逐项映射完整覆盖冻结规格项",
    "PC-MAP-002": "映射片段可在 question/SQL 中定位",
    "PC-EVI-001": "证据引用充分（至少 2 条非空）",
    "PC-FMT-001": "SQL 方言为 sqlite",
    "PC-LIN-001": "候选与查询规格血缘一致",
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _is_ref(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"artifact_id", "version", "content_hash"}:
        return False
    if not isinstance(value["artifact_id"], str) or not value["artifact_id"]:
        return False
    if not isinstance(value["version"], int) or isinstance(value["version"], bool) or value["version"] < 1:
        return False
    return isinstance(value["content_hash"], str) and re.fullmatch(r"[0-9a-f]{64}", value["content_hash"]) is not None


def _sql_violations(sql: Any, scope: Any) -> list[tuple[str, str, Any, Any, str]]:
    """Deterministic SQL checks; each violation is a machine-fixable item."""
    violations: list[tuple[str, str, Any, Any, str]] = []
    if not isinstance(sql, str):
        return [("PC-SQL-001", "sql_gold", "非空只读 SQL", sql, "sql_gold 不是非空字符串")]
    statement = sql.strip()
    if not statement:
        return [("PC-SQL-001", "sql_gold", "非空只读 SQL", sql, "sql_gold 为空")]

    readonly_ok = True
    if not re.match(r"^(SELECT|WITH)\b", statement, flags=re.I):
        readonly_ok = False
        violations.append(("PC-SQL-002", "sql_gold", "以 SELECT/WITH 开头的只读语句", sql, "不是只读 SELECT/WITH 语句"))
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM)\b", statement, flags=re.I):
        readonly_ok = False
        violations.append(("PC-SQL-002", "sql_gold", "无写操作/DDL 关键字", sql, "包含写操作或 DDL 关键字"))
    if ";" in statement[:-1] or statement.count(";") > 1:
        readonly_ok = False
        violations.append(("PC-SQL-002", "sql_gold", "单条语句", sql, "包含多条语句"))
    if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?(?:[A-Za-z_]\w*\.)?\*", statement, flags=re.I):
        violations.append(("PC-SQL-003", "sql_gold", "无 SELECT *", sql, "禁止 SELECT *"))
    if re.search(r"\b(CURRENT_DATE|CURRENT_TIME|CURRENT_TIMESTAMP|DATE\s*\(\s*['\"]now|DATETIME\s*\(\s*['\"]now)", statement, flags=re.I):
        violations.append(("PC-SQL-004", "sql_gold", "无动态时间函数", sql, "禁止动态时间函数"))
    if not readonly_ok:
        return violations

    allowed: dict[str, set[str]] = {}
    scope_ok = True
    if not isinstance(scope, dict) or not isinstance(scope.get("allowed_tables"), list) or not scope["allowed_tables"]:
        scope_ok = False
    else:
        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        for item in scope["allowed_tables"]:
            if not isinstance(item, dict) or not isinstance(item.get("table_id"), str) or not isinstance(item.get("allowed_fields"), list):
                scope_ok = False
                break
            fields = [f for f in item["allowed_fields"] if isinstance(f, str)]
            if (not identifier.fullmatch(item["table_id"])) or not fields or any(not identifier.fullmatch(f) for f in fields):
                scope_ok = False
                break
            allowed[item["table_id"]] = set(fields)
    if not scope_ok:
        violations.append(("PC-SQL-005", "sql_gold", "有效的 sql_schema_scope", scope, "无法确定 SQL 表/字段范围"))
        return violations

    known = set(allowed) | {field for fields in allowed.values() for field in fields}
    known.update(re.findall(r'\bAS\s+["`\[]?([A-Za-z_][A-Za-z0-9_]*)', statement, flags=re.I))
    known.update(re.findall(r'\b(?:FROM|JOIN)\s+["`\[]?[A-Za-z_][A-Za-z0-9_]*["`\]]?\s+(?:AS\s+)?["`\[]?([A-Za-z_][A-Za-z0-9_]*)', statement, flags=re.I))
    known.update(re.findall(r'\bWITH\s+["`\[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]?\s+AS\s*\(', statement, flags=re.I))
    quoted = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"|`([A-Za-z_][A-Za-z0-9_]*)`|\[([A-Za-z_][A-Za-z0-9_]*)\]', statement)
    for item in quoted:
        name = next(name for name in item if name)
        if name not in known:
            violations.append(("PC-SQL-007", "sql_gold", "引号标识符必须命名冻结表/字段或局部别名", f'"{name}"', "引号标识符超出范围"))

    connection = sqlite3.connect(":memory:")
    try:
        for table, fields in allowed.items():
            quoted_table = '"' + table.replace('"', '""') + '"'
            columns = ", ".join('"' + field.replace('"', '""') + '"' for field in sorted(fields))
            connection.execute(f"CREATE TABLE {quoted_table} ({columns})")

        def authorizer(action: int, _arg1: str | None, _arg2: str | None, _database: str | None, _source: str | None) -> int:
            permitted = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
            return sqlite3.SQLITE_OK if action in permitted else sqlite3.SQLITE_DENY

        connection.set_authorizer(authorizer)
        parameters = re.findall(r"(?<!:)([:@$][A-Za-z_]\w*)", statement)
        if parameters:
            connection.execute("EXPLAIN " + statement, {parameter[1:]: None for parameter in parameters}).fetchall()
        else:
            connection.execute("EXPLAIN " + statement, tuple(None for _ in re.findall(r"\?", statement))).fetchall()
    except sqlite3.DatabaseError as exc:
        detail = str(exc).lower()
        if "ambiguous column name" in detail:
            violations.append(("PC-SQL-006", "sql_gold", "字段必须有唯一限定", sql, "存在歧义或未限定的字段"))
        elif "no such table" in detail:
            violations.append(("PC-SQL-005", "sql_gold", "表必须在 sql_schema_scope 内", sql, "SQL 引用了范围外的表"))
        else:
            missing = re.search(r"no such column:\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)", detail)
            if missing:
                name = missing.group(1)
                if "." not in name:
                    violations.append(("PC-SQL-006", "sql_gold", "字段必须限定到明确表", sql, "存在未限定的范围外字段"))
                else:
                    violations.append(("PC-SQL-005", "sql_gold", "字段必须在 sql_schema_scope 内", sql, "SQL 引用了范围外的字段"))
            else:
                violations.append(("PC-SQL-008", "sql_gold", "SQL 可由 SQLite 解析", sql, f"SQL 解析失败：{str(exc)}"))
    finally:
        connection.close()
    return violations


def _collect_failures(package: dict[str, Any], query_spec: dict[str, Any]) -> list[tuple[str, str, Any, Any, str]]:
    payload, envelope = package["payload"], package["envelope"]
    spec_envelope, spec = query_spec["envelope"], query_spec["payload"]
    failures: list[tuple[str, str, Any, Any, str]] = []

    failures.extend(_sql_violations(payload.get("sql_gold"), spec.get("sql_schema_scope")))

    for field, rule_id in (("query_spec_ref", "PC-REF-001"), ("penalty_fact_package_ref", "PC-REF-002"), ("observable_fact_package_ref", "PC-REF-003")):
        ref = payload.get(field)
        if not _is_ref(ref):
            failures.append((rule_id, field, "合法 artifact 引用（artifact_id+version+content_hash）", ref, f"{field} 不是合法引用"))

    mapping = payload.get("specification_mapping")
    if not isinstance(mapping, list) or len(mapping) != len(MAPPED_SPEC_ITEMS) or any(not isinstance(item, dict) or set(item) != {"spec_item", "question_fragment", "sql_fragment"} for item in mapping):
        failures.append(("PC-MAP-001", "specification_mapping", "覆盖全部冻结规格项且字段齐全", mapping, "规格映射不完整或字段缺失"))
    elif {item["spec_item"] for item in mapping} != set(MAPPED_SPEC_ITEMS):
        failures.append(("PC-MAP-001", "specification_mapping", "覆盖全部冻结规格项", sorted(item["spec_item"] for item in mapping), "规格映射项集合与冻结规格不一致"))
    else:
        question, sql = payload.get("clear_question", ""), payload.get("sql_gold", "")
        for item in mapping:
            if not item["question_fragment"] or item["question_fragment"] not in question:
                failures.append(("PC-MAP-002", "specification_mapping", "question_fragment 可定位到 clear_question", item, "question_fragment 无法在 clear_question 中定位"))
            if not item["sql_fragment"] or item["sql_fragment"] not in sql:
                failures.append(("PC-MAP-002", "specification_mapping", "sql_fragment 可定位到 sql_gold", item, "sql_fragment 无法在 sql_gold 中定位"))

    evidence = payload.get("evidence_refs")
    if not isinstance(evidence, list) or len(evidence) < 2 or any(not isinstance(item, str) or not item for item in evidence):
        failures.append(("PC-EVI-001", "evidence_refs", "至少 2 条非空证据引用", evidence, "证据引用不足或含空项"))

    if payload.get("sql_dialect") != "sqlite":
        failures.append(("PC-FMT-001", "sql_dialect", "sqlite", payload.get("sql_dialect"), "SQL 方言必须为 sqlite"))

    if _is_ref(payload.get("query_spec_ref")) and payload.get("query_spec_ref") != artifact_ref(spec_envelope):
        failures.append(("PC-LIN-001", "query_spec_ref", "与解析出的查询规格引用一致", payload.get("query_spec_ref"), "query_spec_ref 与查询规格不一致"))
    if _is_ref(payload.get("penalty_fact_package_ref")) and payload.get("penalty_fact_package_ref") != spec.get("penalty_fact_package_ref"):
        failures.append(("PC-LIN-001", "penalty_fact_package_ref", "与查询规格处罚事实引用一致", payload.get("penalty_fact_package_ref"), "处罚事实包引用与查询规格不一致"))
    if _is_ref(payload.get("observable_fact_package_ref")) and payload.get("observable_fact_package_ref") != spec.get("observable_fact_package_ref"):
        failures.append(("PC-LIN-001", "observable_fact_package_ref", "与查询规格可观察事实引用一致", payload.get("observable_fact_package_ref"), "可观察事实包引用与查询规格不一致"))
    for key in ("run_id", "qa_id", "trace_id"):
        if envelope.get(key) != spec_envelope.get(key):
            failures.append(("PC-LIN-001", f"envelope.{key}", "与查询规格血缘一致", envelope.get(key), f"候选 {key} 与查询规格不一致"))
    return failures


def _report_for(checked_at: str) -> dict[str, Any]:
    rules = [{"rule_id": rule_id, "status": "pass", "detail": RULE_LABELS[rule_id]} for rule_id in REPORT_RULE_ORDER]
    report = {"decision": "pass", "rules": rules, "checked_at": checked_at}
    report["report_hash"] = sha256(report)
    return report


def _evidence_summary(payload: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    tables: set[str] = set()
    fields: set[str] = set()
    for item in spec.get("sql_schema_scope", {}).get("allowed_tables", []):
        if not isinstance(item, dict):
            continue
        table = item.get("table_id")
        if isinstance(table, str) and table:
            tables.add(table)
            for field in item.get("allowed_fields", []):
                if isinstance(field, str) and field:
                    fields.add(f"{table}.{field}")
    data_elements = sorted({
        f"{item.get('source_table')}.{item.get('field_id')}"
        for item in spec.get("return_fields", [])
        if isinstance(item, dict) and item.get("source_table") and item.get("field_id")
    })
    relationships: set[str] = set()
    for related in spec.get("related_objects_and_path", []):
        if not isinstance(related, dict):
            continue
        for join in related.get("join_fields", []):
            if isinstance(join, dict) and join.get("from_field") and join.get("to_field"):
                relationships.add(f"{join['from_field']}->{join['to_field']}")
    return {
        "tables": sorted(tables),
        "fields": sorted(fields),
        "data_elements": data_elements,
        "relationships": sorted(relationships),
        "source_refs": sorted(dict.fromkeys(item for item in payload.get("evidence_refs", []) if isinstance(item, str) and item)),
    }


class PrecheckAgent:
    """Deterministic 160 precheck: validate, then freeze or feed back."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validate_package(self, package: dict[str, Any], artifact_type: str, expected_producer: str) -> None:
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != artifact_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        if envelope["producer_id"] != expected_producer:
            _fail("PRECHECK_PRODUCER_REJECTED")
        try:
            resources = []
            for path in [self.repo_root / "contracts/common/common-envelope.schema.json", *sorted((self.repo_root / "contracts/packages").glob("*.schema.json"))]:
                item = load_json(path)
                resources.append((item["$id"], Resource.from_contents(item)))
            Draft202012Validator(load_json(self.repo_root / SCHEMAS[artifact_type]), registry=Registry().with_resources(resources)).validate(payload)
        except ValidationError as exc:
            raise ContractError(f"SCHEMA_VALIDATION_FAILED:{artifact_type}") from exc

    def validate_pending_precheck(self, package: dict[str, Any]) -> None:
        self._validate_package(package, "question_sql_pending_precheck", "150")

    def validate_query_spec(self, package: dict[str, Any]) -> None:
        self._validate_package(package, "query_specification_package", "140")

    def validate_dual_review(self, package: dict[str, Any]) -> None:
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != "question_sql_pending_dual_review":
            _fail("ARTIFACT_TYPE_MISMATCH")
        if envelope["producer_id"] != "160":
            _fail("DUAL_REVIEW_PRODUCER_REJECTED")
        try:
            resources = []
            for path in [self.repo_root / "contracts/common/common-envelope.schema.json", *sorted((self.repo_root / "contracts/packages").glob("*.schema.json"))]:
                item = load_json(path)
                resources.append((item["$id"], Resource.from_contents(item)))
            Draft202012Validator(load_json(self.repo_root / SCHEMAS["question_sql_pending_dual_review"]), registry=Registry().with_resources(resources)).validate(payload)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:question_sql_pending_dual_review") from exc
        if payload["package_hash"] != sha256({key: value for key, value in payload.items() if key != "package_hash"}):
            _fail("PACKAGE_HASH_DRIFT")

    def validate_feedback(self, package: dict[str, Any]) -> None:
        self._validate_package(package, "precheck_failed_feedback", "160")

    def precheck(self, package: dict[str, Any], query_spec: dict[str, Any], *, checked_at: str | None = None) -> dict[str, Any]:
        self.validate_pending_precheck(package)
        self.validate_query_spec(query_spec)
        failures = _collect_failures(package, query_spec)
        if failures:
            failed_items = [
                {"failed_rule_ids": [rule_id], "error_locations": [location], "expected_values": expected, "actual_values": actual, "error_details": detail}
                for (rule_id, location, expected, actual, detail) in failures
            ]
            return {"decision": "fail", "failed_items": failed_items, "report": None}
        return {"decision": "pass", "failed_items": [], "report": _report_for(checked_at or datetime.now(timezone.utc).isoformat())}

    def build_dual_review(
        self, package: dict[str, Any], query_spec: dict[str, Any], result: dict[str, Any], *,
        review_round: int | None = None, created_at: str | None = None,
        artifact_id: str | None = None, version: int | None = None,
        supersedes_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if result.get("decision") != "pass" or not isinstance(result.get("report"), dict):
            _fail("PRECHECK_NOT_PASSED")
        envelope, payload = package["envelope"], package["payload"]
        spec_envelope, spec = query_spec["envelope"], query_spec["payload"]
        if artifact_ref(spec_envelope) != payload["query_spec_ref"]:
            _fail("QUERY_SPEC_LINEAGE_REJECTED")
        if review_round is None:
            review_round = envelope["attempt_no"]
        if review_round not in (1, 2, 3):
            _fail("REVIEW_ROUND_OUT_OF_RANGE")
        report = dict(result["report"])
        candidate_content = {
            "clear_question": payload["clear_question"],
            "sql_gold": payload["sql_gold"],
            "sql_explanation": payload["sql_explanation"],
            "business_event_candidates": payload["business_event_candidates"],
            "specification_mapping": payload["specification_mapping"],
        }
        dual_payload = {
            "candidate_ref": artifact_ref(envelope),
            "candidate_content": candidate_content,
            "query_specification_package": payload["query_spec_ref"],
            "penalty_fact_package": payload["penalty_fact_package_ref"],
            "observable_fact_package": payload["observable_fact_package_ref"],
            "constraint_evidence_summary": _evidence_summary(payload, spec),
            "precheck_report": report,
            "review_round": review_round,
        }
        dual_payload["package_hash"] = sha256(dual_payload)
        dual_envelope = {
            "artifact_id": artifact_id or f"{payload['candidate_id']}-dual-review", "artifact_type": "question_sql_pending_dual_review",
            "run_id": envelope["run_id"], "qa_id": envelope["qa_id"], "version": version if version is not None else envelope["version"],
            "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": supersedes_ref,
            "attempt_no": envelope["attempt_no"], "producer_id": "160",
            "parent_artifact_refs": [artifact_ref(envelope)], "input_hashes": [envelope["content_hash"]],
            "status": "candidate", "mode": envelope["mode"], "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "trace_id": envelope["trace_id"], "storage_locator": None,
        }
        dual_envelope["content_hash"] = content_hash(dual_envelope, dual_payload)
        dual = {"envelope": dual_envelope, "payload": dual_payload}
        self.validate_dual_review(dual)
        return dual

    def build_feedback(
        self, package: dict[str, Any], result: dict[str, Any], *,
        created_at: str | None = None, artifact_id: str | None = None,
    ) -> dict[str, Any]:
        if result.get("decision") != "fail" or not isinstance(result.get("failed_items"), list) or not result["failed_items"]:
            _fail("PRECHECK_NOT_FAILED")
        envelope, payload = package["envelope"], package["payload"]
        feedback_payload = {
            "candidate_ref": artifact_ref(envelope),
            "precheck_decision": "fail",
            "failed_items": result["failed_items"],
        }
        feedback_envelope = {
            "artifact_id": artifact_id or f"{payload['candidate_id']}-precheck-feedback", "artifact_type": "precheck_failed_feedback",
            "run_id": envelope["run_id"], "qa_id": envelope["qa_id"], "version": 1,
            "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None,
            "attempt_no": envelope["attempt_no"], "producer_id": "160",
            "parent_artifact_refs": [artifact_ref(envelope)], "input_hashes": [envelope["content_hash"]],
            "status": "candidate", "mode": envelope["mode"], "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "trace_id": envelope["trace_id"], "storage_locator": None,
        }
        feedback_envelope["content_hash"] = content_hash(feedback_envelope, feedback_payload)
        feedback = {"envelope": feedback_envelope, "payload": feedback_payload}
        self.validate_feedback(feedback)
        return feedback


def consume_170_180_stub(repo_root: Path, package: dict[str, Any]) -> dict[str, str]:
    """Independent downstream consumer (170/180) of a frozen dual-review package."""
    agent = PrecheckAgent(repo_root)
    agent.validate_dual_review(package)
    payload = package["payload"]
    if payload["review_round"] != package["envelope"]["attempt_no"]:
        _fail("REVIEW_ROUND_MISMATCH")
    if payload["precheck_report"]["decision"] != "pass":
        _fail("PRECHECK_REPORT_NOT_PASS")
    return {"consumer": "170/180", "package_hash": payload["package_hash"], "review_round": str(payload["review_round"])}
