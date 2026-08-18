"""Strict, side-effect-free orchestration for the V5 210 data stage.

The coordinator emits only immutable packages and explicit dispatch intents.  A
runtime adapter owns task invocation and artifact-registry persistence; this
module must never generate records/ORM/SQL or open a database.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
_query_binding = importlib.import_module("east_v5.agents.110.query_binding")
named_placeholders, query_bindings = _query_binding.named_placeholders, _query_binding.query_bindings
resolve_declaration, validate_declarations = _query_binding.resolve_declaration, _query_binding.validate_declarations
from east_v5.governance import ContractError, load_json, sha256

from . import foundation


_TRANSPORT_KEYS = {"envelope", "payload"}
_REF_KEYS = {"artifact_id", "version", "content_hash"}
_SQL_TABLE_ALIAS = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?", re.IGNORECASE)
_SQL_QUALIFIED_FIELD = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
_SQL_BARE_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_SQL_KEYWORDS = {"select", "from", "join", "on", "where", "and", "or", "as", "distinct", "group", "by", "order", "having", "limit", "asc", "desc", "count", "sum", "avg", "min", "max", "case", "when", "then", "else", "end", "is", "null", "not", "in", "like", "between"}
_ROUTES = {
    "DATA_VALUE_ERROR": "241",
    "ORM_PLAN_ERROR": "251",
    "SQL_EXECUTION_ERROR": "010",
    "QUERY_PARAMETER_BINDING_ERROR": "010",
    "FOUNDATION_REQUIRED": "210",
    "MANUAL_REVIEW_REQUIRED": "manual",
}


def _fail(code: str) -> None:
    raise ContractError(code)


class DataStageCoordinator:
    """Validate 210 inputs, make deterministic dispatches, and build releases."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _registry(self) -> Registry:
        paths = [
            self.repo_root / "contracts/common/common-envelope.schema.json",
            self.repo_root / "contracts/v5-runtime-packages.schema.json",
            *sorted((self.repo_root / "contracts/packages").glob("*.schema.json")),
        ]
        return Registry().with_resources(
            [(load_json(path)["$id"], Resource.from_contents(load_json(path))) for path in paths]
        )

    def _validate(self, package: Any, schema_name: str, code: str) -> None:
        if not isinstance(package, dict) or set(package) != _TRANSPORT_KEYS:
            _fail(f"{code}:TRANSPORT")
        try:
            validate_envelope(self.repo_root, package["envelope"], package["payload"])
            schema = load_json(self.repo_root / "contracts/packages" / schema_name)
            Draft202012Validator(schema, registry=self._registry(), format_checker=FormatChecker()).validate(package)
        except (ValidationError, ContractError) as exc:
            raise ContractError(code) from exc

    @staticmethod
    def _reference(value: Any, code: str) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _REF_KEYS:
            _fail(code)
        return value

    @staticmethod
    def _same_context(*packages: dict[str, Any]) -> None:
        contexts = {(item["envelope"]["run_id"], item["envelope"]["qa_id"], item["envelope"]["trace_id"]) for item in packages}
        if len(contexts) != 1:
            _fail("210_CONTEXT_MISMATCH")

    @classmethod
    def _same_context_and_attempt(cls, *packages: dict[str, Any]) -> None:
        """All Foundation hand-offs are one immutable run/attempt lineage."""
        cls._same_context(*packages)
        if len({item["envelope"]["attempt_no"] for item in packages}) != 1:
            _fail("210_ATTEMPT_MISMATCH")

    @staticmethod
    def _parent_with_hash(package: dict[str, Any], expected_hash: str, label: str) -> dict[str, Any]:
        matches = [item for item in package["envelope"]["parent_artifact_refs"] if item["content_hash"] == expected_hash]
        if len(matches) != 1:
            _fail(f"210_SOURCE_LINEAGE_MISSING:{label}")
        return matches[0]

    @staticmethod
    def _wrap(
        artifact_type: str,
        artifact_id: str,
        payload: dict[str, Any],
        *,
        source: dict[str, Any],
        mode: str,
        parents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        envelope = {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "run_id": source["envelope"]["run_id"],
            "qa_id": source["envelope"]["qa_id"] if mode != "foundation" else source["envelope"]["qa_id"],
            "version": 1,
            "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64,
            "supersedes_ref": None,
            "attempt_no": source["envelope"]["attempt_no"],
            "producer_id": "210",
            "parent_artifact_refs": parents,
            "input_hashes": [item["content_hash"] for item in parents],
            "status": "candidate",
            "mode": mode,
            "created_at": source["envelope"]["created_at"],
            "trace_id": source["envelope"]["trace_id"],
            "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}

    def build_reviewed_question_sql(self, approved: dict[str, Any]) -> dict[str, Any]:
        """Create the distinct 210 reviewed artifact from 110's passed artifact."""
        before = copy.deepcopy(approved)
        self._validate(approved, "question-sql-dual-review-passed-package.schema.json", "210_DUAL_REVIEW_REJECTED")
        envelope, source = approved["envelope"], approved["payload"]
        if source["package_hash"] != sha256({key: value for key, value in source.items() if key != "package_hash"}):
            _fail("210_DUAL_REVIEW_HASH_DRIFT")
        precheck = self._parent_with_hash(approved, source["precheck_report"]["report_hash"], "precheck")
        deepseek = self._parent_with_hash(approved, source["deepseek_review"]["review_hash"], "deepseek")
        glm = self._parent_with_hash(approved, source["glm_review"]["review_hash"], "glm")
        payload = {
            "qa_id": envelope["qa_id"],
            "candidate_ref": source["candidate_ref"],
            "query_spec_ref": source["query_specification_package"],
            "clear_question": source["candidate_content"]["clear_question"],
            "sql_gold": source["candidate_content"]["sql_gold"],
            "sql_explanation": json.dumps(source["candidate_content"]["sql_explanation"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "approved_business_events": source["candidate_content"]["business_event_candidates"],
            "specification_mapping": source["candidate_content"]["specification_mapping"],
            "evidence_refs": [source["penalty_fact_package"], source["observable_fact_package"]],
            "precheck_report_ref": precheck,
            "deepseek_review_ref": deepseek,
            "glm_review_ref": glm,
            "package_hash": "0" * 64,
            "approved_at": envelope["created_at"],
        }
        payload["package_hash"] = sha256({key: value for key, value in payload.items() if key != "package_hash"})
        result = self._wrap("reviewed_question_sql", f"210-reviewed-{envelope['artifact_id']}", payload, source=approved, mode="event_data", parents=[artifact_ref(envelope)])
        self._validate(result, "reviewed-question-sql-package.schema.json", "210_REVIEWED_OUTPUT_REJECTED")
        if before != approved:
            _fail("210_INPUT_MUTATED")
        return result

    def _validate_query_specification_package(self, query_spec: dict[str, Any]) -> None:
        if not isinstance(query_spec, dict) or set(query_spec) != _TRANSPORT_KEYS:
            _fail("210_QUERY_SPEC_REJECTED:TRANSPORT")
        validate_envelope(self.repo_root, query_spec["envelope"], query_spec["payload"])
        envelope = query_spec["envelope"]
        if (envelope["artifact_type"], envelope["producer_id"], envelope["mode"]) != ("query_specification_package", "140", "question_sql"):
            _fail("210_QUERY_SPEC_REJECTED:ENVELOPE")
        try:
            Draft202012Validator(load_json(self.repo_root / "contracts/packages/query-specification-package.schema.json")).validate(query_spec["payload"])
        except ValidationError as exc:
            raise ContractError("210_QUERY_SPEC_REJECTED:SCHEMA") from exc

    @staticmethod
    def _sql_fields(sql: str, scope: dict[str, set[str]]) -> tuple[set[str], dict[str, str]]:
        aliases: dict[str, str] = {}
        for table, alias in _SQL_TABLE_ALIAS.findall(sql):
            if alias and alias.lower() in _SQL_KEYWORDS:
                alias = ""
            if table not in scope or table in aliases or (alias and alias in aliases):
                _fail("210_FIELD_PROJECTION_SQL_UNRESOLVED")
            aliases[table] = table
            if alias:
                aliases[alias] = table
        if not aliases:
            _fail("210_FIELD_PROJECTION_SQL_UNRESOLVED")
        fields: set[str] = set()
        for qualifier, field in _SQL_QUALIFIED_FIELD.findall(sql):
            table = aliases.get(qualifier)
            if table is None or field not in scope[table]:
                _fail("210_FIELD_PROJECTION_SCOPE_VIOLATION")
            fields.add(f"{table}.{field}")
        sql_without_literals = re.sub(r"'(?:''|[^'])*'", "", sql)
        for match in _SQL_BARE_IDENTIFIER.finditer(sql_without_literals):
            identifier = match.group(0)
            if (match.start() > 0 and sql_without_literals[match.start() - 1] == ":") or identifier.lower() in _SQL_KEYWORDS or identifier in aliases or re.search(rf"\.[ ]*{re.escape(identifier)}\b", sql_without_literals):
                continue
            matches = [f"{table}.{identifier}" for table in sorted(set(aliases.values())) if identifier in scope[table]]
            if len(matches) == 1:
                fields.add(matches[0])
            elif not matches:
                _fail("210_FIELD_PROJECTION_SCOPE_VIOLATION")
            else:
                _fail("210_FIELD_PROJECTION_SQL_UNRESOLVED")
        if not fields:
            _fail("210_FIELD_PROJECTION_SQL_UNRESOLVED")
        return fields, aliases

    @classmethod
    def _mapping_fields(cls, fragment: Any, sql_fields: set[str], scope: dict[str, set[str],], aliases: dict[str, str]) -> list[str]:
        if not isinstance(fragment, str) or not fragment.strip():
            _fail("210_FIELD_PROJECTION_MAPPING_UNRESOLVED")
        selected: set[str] = set()
        for qualifier, field in _SQL_QUALIFIED_FIELD.findall(fragment):
            table = aliases.get(qualifier)
            if table is None:
                _fail("210_FIELD_PROJECTION_ALIAS_AMBIGUOUS")
            path = f"{table}.{field}"
            if path not in sql_fields or field not in scope.get(table, set()):
                _fail("210_FIELD_PROJECTION_MAPPING_UNRESOLVED")
            selected.add(path)
        for match in _SQL_BARE_IDENTIFIER.finditer(fragment):
            identifier = match.group(0)
            if (match.start() > 0 and fragment[match.start() - 1] == ":") or identifier.lower() in _SQL_KEYWORDS or any(identifier == part for pair in _SQL_QUALIFIED_FIELD.findall(fragment) for part in pair):
                continue
            matches = sorted(path for path in sql_fields if path.rsplit(".", 1)[1] == identifier)
            if len(matches) != 1:
                _fail("210_FIELD_PROJECTION_MAPPING_UNRESOLVED" if not matches else "210_FIELD_PROJECTION_ALIAS_AMBIGUOUS")
            selected.add(matches[0])
        if not selected:
            _fail("210_FIELD_PROJECTION_MAPPING_UNRESOLVED")
        return sorted(selected)

    def _validate_query_parameter_binding(self, binding: dict[str, Any], approved: dict[str, Any], query_spec: dict[str, Any]) -> None:
        self._validate(binding, "query-parameter-binding-package.schema.json", "210_QUERY_BINDING_REJECTED")
        env, payload = binding["envelope"], binding["payload"]
        approved_ref, query_ref = artifact_ref(approved["envelope"]), artifact_ref(query_spec["envelope"])
        if (payload["source_question_sql_ref"], payload["source_query_spec_ref"]) != (approved_ref, query_ref): _fail("210_QUERY_BINDING_LINEAGE_REJECTED")
        if env["parent_artifact_refs"] != [approved_ref, query_ref] or env["input_hashes"] != [approved_ref["content_hash"], query_ref["content_hash"]]: _fail("210_QUERY_BINDING_LINEAGE_REJECTED")
        if (env["run_id"], env["qa_id"], env["trace_id"], env["attempt_no"]) != (approved["envelope"]["run_id"], approved["envelope"]["qa_id"], approved["envelope"]["trace_id"], approved["envelope"]["attempt_no"]): _fail("210_QUERY_BINDING_CONTEXT_DRIFT")
        candidate = approved["payload"]["candidate_content"]
        names = validate_declarations(candidate["sql_gold"], candidate["query_parameter_bindings"])
        if payload["sql_hash"] != hashlib.sha256(candidate["sql_gold"].strip().encode("utf-8")).hexdigest() or payload["binding_hash"] != sha256({key: value for key, value in payload.items() if key != "binding_hash"}): _fail("210_QUERY_BINDING_HASH_DRIFT")
        expected = sorted((resolve_declaration(item, query_spec) for item in candidate["query_parameter_bindings"]), key=lambda item: item["name"])
        if tuple(item["name"] for item in expected) != names or payload["parameters"] != expected: _fail("210_QUERY_BINDING_VALUE_DRIFT")

    def build_event_query_context(self, approved: dict[str, Any], query_spec: dict[str, Any], binding: dict[str, Any], reviewed: dict[str, Any] | None = None) -> dict[str, Any]:
        """Project field seeds from frozen sources; never infer them in 220."""
        before = copy.deepcopy((approved, query_spec, binding, reviewed))
        self._validate(approved, "question-sql-dual-review-passed-package.schema.json", "210_DUAL_REVIEW_REJECTED")
        self._validate_query_specification_package(query_spec)
        self._validate_query_parameter_binding(binding, approved, query_spec)
        reviewed = self.build_reviewed_question_sql(approved) if reviewed is None else reviewed
        self._validate(reviewed, "reviewed-question-sql-package.schema.json", "210_REVIEWED_INPUT_REJECTED")
        approved_ref, query_ref, binding_ref, reviewed_ref = (artifact_ref(approved["envelope"]), artifact_ref(query_spec["envelope"]), artifact_ref(binding["envelope"]), artifact_ref(reviewed["envelope"]))
        if (approved["payload"]["query_specification_package"] != query_ref or reviewed["payload"]["query_spec_ref"] != query_ref
                or reviewed["envelope"]["parent_artifact_refs"] != [approved_ref]):
            _fail("210_EVENT_CONTEXT_LINEAGE_REJECTED")
        self._same_context_and_attempt(query_spec, approved, reviewed)
        scope = {item["table_id"]: set(item["allowed_fields"]) for item in query_spec["payload"]["sql_schema_scope"]["allowed_tables"]}
        if not scope or any(not fields for fields in scope.values()):
            _fail("210_FIELD_PROJECTION_SCOPE_VIOLATION")
        approved_mapping = approved["payload"]["candidate_content"]["specification_mapping"]
        if reviewed["payload"]["specification_mapping"] != approved_mapping:
            _fail("210_FIELD_PROJECTION_MAPPING_COVERAGE_REJECTED")
        sql_fields, aliases = self._sql_fields(reviewed["payload"]["sql_gold"], scope)
        projection: list[dict[str, Any]] = []
        seen_items: set[str] = set()
        for item in approved_mapping:
            if not isinstance(item, dict) or set(item) - {"spec_item", "question_fragment", "sql_fragment"} or not isinstance(item.get("spec_item"), str) or not item["spec_item"] or item["spec_item"] in seen_items:
                _fail("210_FIELD_PROJECTION_MAPPING_COVERAGE_REJECTED")
            seen_items.add(item["spec_item"])
            projection.append({"spec_item": item["spec_item"], "fields": self._mapping_fields(item.get("sql_fragment"), sql_fields, scope, aliases)})
        projection.sort(key=lambda item: item["spec_item"])
        payload = {"schema_version": "v5.event-query-context/v2", "source_query_spec_ref": query_ref, "source_question_sql_ref": approved_ref, "query_parameter_binding_ref": binding_ref, "reviewed_question_sql_ref": reviewed_ref, "field_projection": projection, "projection_hash": "0" * 64}
        payload["projection_hash"] = sha256({key: value for key, value in payload.items() if key != "projection_hash"})
        result = self._wrap("event_query_context", f"210-event-context-{approved['envelope']['artifact_id']}", payload, source=approved, mode="event_data", parents=[query_ref, approved_ref, binding_ref, reviewed_ref])
        self._validate(result, "event-query-context-package.schema.json", "210_EVENT_CONTEXT_OUTPUT_REJECTED")
        if before != (approved, query_spec, binding, None if before[3] is None else reviewed):
            _fail("210_INPUT_MUTATED")
        return result

    def begin_event(self, approved: dict[str, Any], query_spec: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
        """Start event work with 220; 230 requires its authenticated output."""
        reviewed = self.build_reviewed_question_sql(approved)
        context = self.build_event_query_context(approved, query_spec, binding, reviewed)
        return {
            "reviewed_question_sql": reviewed,
            "event_query_context": context, "query_parameter_binding": binding,
            "dispatches": [{"target": "220", "kind": "structure_closure", "reviewed_question_sql_ref": artifact_ref(reviewed["envelope"]), "event_query_context_ref": artifact_ref(context["envelope"])}],
        }

    def dispatch_event_operation(self, reviewed: dict[str, Any], context: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
        """After strict 220 authentication, provide its closure to event-only 230."""
        self._validate(reviewed, "reviewed-question-sql-package.schema.json", "210_REVIEWED_INPUT_REJECTED")
        closure = importlib.import_module("east_v5.agents.220.closure")
        try:
            closure.validate_reviewed_question_sql(reviewed)
            closure.validate_event_query_context(context, reviewed)
            closure.validate_structure_closure_package(structure)
        except ContractError as exc:
            raise ContractError("210_EVENT_STRUCTURE_REJECTED") from exc
        if structure["envelope"]["mode"] != "event_data" or structure["envelope"]["status"] == "blocked_manual":
            _fail("210_EVENT_CLOSURE_STATE_REJECTED")
        if (artifact_ref(reviewed["envelope"]) not in structure["envelope"]["parent_artifact_refs"]
                or artifact_ref(context["envelope"]) not in structure["envelope"]["parent_artifact_refs"]):
            _fail("210_EVENT_STRUCTURE_LINEAGE_REJECTED")
        self._same_context_and_attempt(reviewed, structure)
        return {
            "target": "230", "kind": "operation_closure",
            "reviewed_question_sql_ref": artifact_ref(reviewed["envelope"]),
            "event_query_context_ref": artifact_ref(context["envelope"]),
            "structure_closure_ref": artifact_ref(structure["envelope"]),
        }

    def dispatch_event_branches(self, reviewed: dict[str, Any], context: dict[str, Any], structure: dict[str, Any], operation: dict[str, Any]) -> list[dict[str, Any]]:
        """Dispatch 241 and 251 only after both event closures are available."""
        self._validate(reviewed, "reviewed-question-sql-package.schema.json", "210_REVIEWED_INPUT_REJECTED")
        closure = importlib.import_module("east_v5.agents.220.closure")
        operation_builder = importlib.import_module("east_v5.agents.230.builder").OperationClosureBuilder(self.repo_root)
        try:
            closure.validate_reviewed_question_sql(reviewed)
            closure.validate_event_query_context(context, reviewed)
            closure.validate_structure_closure_package(structure)
            operation_builder.validate_operation_closure_package(operation)
        except ContractError as exc:
            raise ContractError("210_EVENT_CLOSURE_REJECTED") from exc
        structure_ref = artifact_ref(structure["envelope"])
        if structure["envelope"]["mode"] != "event_data" or operation["envelope"]["status"] == "blocked_manual" or structure["envelope"]["status"] == "blocked_manual":
            _fail("210_EVENT_CLOSURE_STATE_REJECTED")
        if (artifact_ref(reviewed["envelope"]) not in structure["envelope"]["parent_artifact_refs"]
                or artifact_ref(context["envelope"]) not in structure["envelope"]["parent_artifact_refs"]):
            _fail("210_EVENT_STRUCTURE_LINEAGE_REJECTED")
        if operation["envelope"]["parent_artifact_refs"] != [structure_ref]:
            _fail("210_EVENT_OPERATION_LINEAGE_REJECTED")
        self._same_context_and_attempt(reviewed, structure, operation)
        return [
            {"target": "241", "kind": "bound_data", "structure_closure_ref": artifact_ref(structure["envelope"]), "operation_closure_ref": artifact_ref(operation["envelope"])},
            {"target": "251", "kind": "restricted_orm", "structure_closure_ref": artifact_ref(structure["envelope"]), "operation_closure_ref": artifact_ref(operation["envelope"])},
        ]

    def join_event_validations(
        self,
        approved: dict[str, Any],
        reviewed: dict[str, Any],
        context: dict[str, Any],
        structure: dict[str, Any],
        operation: dict[str, Any],
        restricted_orm: dict[str, Any],
        verified_data: dict[str, Any],
        frozen_orm: dict[str, Any],
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        """Authorize 260 only for one approved root and its validated event branches."""
        self._validate(approved, "question-sql-dual-review-passed-package.schema.json", "210_DUAL_REVIEW_REJECTED")
        self._validate(reviewed, "reviewed-question-sql-package.schema.json", "210_REVIEWED_INPUT_REJECTED")
        closure = importlib.import_module("east_v5.agents.220.closure")
        operation_builder = importlib.import_module("east_v5.agents.230.builder").OperationClosureBuilder(self.repo_root)
        orm_generator = importlib.import_module("east_v5.agents.251.generator").RestrictedOrmGenerator(self.repo_root)
        try:
            closure.validate_reviewed_question_sql(reviewed)
            closure.validate_event_query_context(context, reviewed)
            closure.validate_structure_closure_package(structure)
            operation_builder.validate_operation_closure_package(operation)
            orm_generator.validate_restricted_orm(restricted_orm, structure, operation)
        except ContractError as exc:
            raise ContractError("210_EVENT_CLOSURE_REJECTED") from exc
        approved_ref = artifact_ref(approved["envelope"])
        reviewed_ref = artifact_ref(reviewed["envelope"])
        if reviewed["envelope"]["parent_artifact_refs"] != [approved_ref]:
            _fail("210_EVENT_REVIEWED_LINEAGE_REJECTED")
        if (reviewed_ref not in structure["envelope"]["parent_artifact_refs"]
                or artifact_ref(context["envelope"]) not in structure["envelope"]["parent_artifact_refs"]):
            _fail("210_EVENT_STRUCTURE_LINEAGE_REJECTED")
        self._validate(verified_data, "verified-bound-data-package.schema.json", "210_DATA_BRANCH_REJECTED")
        self._validate(frozen_orm, "frozen-orm-package.schema.json", "210_ORM_BRANCH_REJECTED")
        if (verified_data["envelope"]["producer_id"], verified_data["envelope"]["mode"], verified_data["envelope"]["status"]) != ("242", "event_data", "validated"):
            _fail("210_DATA_BRANCH_STATE_REJECTED")
        if (frozen_orm["envelope"]["producer_id"], frozen_orm["envelope"]["mode"], frozen_orm["envelope"]["status"]) != ("252", "event_data", "validated"):
            _fail("210_ORM_BRANCH_STATE_REJECTED")
        structure_ref, operation_ref, restricted_ref = (
            artifact_ref(structure["envelope"]), artifact_ref(operation["envelope"]), artifact_ref(restricted_orm["envelope"]),
        )
        data_payload = verified_data["payload"]
        if (data_payload["source_data_package_ref"] not in verified_data["envelope"]["parent_artifact_refs"]
                or data_payload["validated_data_package"]["structure_closure_ref"] != structure_ref
                or data_payload["validated_data_package"]["operation_closure_ref"] != operation_ref):
            _fail("210_EVENT_DATA_LINEAGE_REJECTED")
        if (frozen_orm["payload"]["source_orm_plan_ref"] != restricted_ref
                or frozen_orm["envelope"]["parent_artifact_refs"] != [restricted_ref]):
            _fail("210_EVENT_ORM_LINEAGE_REJECTED")
        self._validate(binding, "query-parameter-binding-package.schema.json", "210_QUERY_BINDING_REJECTED")
        if context["payload"]["query_parameter_binding_ref"] != artifact_ref(binding["envelope"]): _fail("210_QUERY_BINDING_CONTEXT_REJECTED")
        self._same_context_and_attempt(approved, reviewed, context, structure, operation, restricted_orm, verified_data, frozen_orm, binding)
        return {"target": "260", "kind": "database_copy_regression", "mode": "event_data", "verified_data_ref": artifact_ref(verified_data["envelope"]), "frozen_orm_ref": artifact_ref(frozen_orm["envelope"]), "reviewed_question_sql_ref": reviewed_ref, "event_query_context_ref": artifact_ref(context["envelope"]), "query_parameter_binding_ref": artifact_ref(binding["envelope"]), "approved_question_sql_ref": approved_ref, "query_spec_ref": approved["payload"]["query_specification_package"]}

    def begin_foundation(self, task_payload: dict[str, Any], *, run_id: str, trace_id: str, created_at: str, parents: list[dict[str, Any]]) -> dict[str, Any]:
        """Start Foundation with 220 only; 241 requires 220's closure first."""
        task = foundation.build_foundation_task_package(task_payload, run_id=run_id, trace_id=trace_id, created_at=created_at, parents=parents)
        profile = foundation.build_foundation_profile(task)
        return {"foundation_task": task, "foundation_profile": profile, "dispatches": [{"target": "220", "kind": "structure_closure", "foundation_task_ref": artifact_ref(task["envelope"])}]}

    def dispatch_foundation_data(self, task: dict[str, Any], structure: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        """After authenticated 220 output, dispatch the only Foundation 241 step."""
        foundation._closure.validate_foundation_task_package(task)
        foundation._closure.validate_foundation_profile_projection(profile, task)
        try:
            foundation._closure.validate_structure_closure_package(structure)
        except ContractError as exc:
            raise ContractError("210_FOUNDATION_STRUCTURE_REJECTED") from exc
        if structure["envelope"]["mode"] != "foundation" or structure["payload"].get("foundation_task_ref") != artifact_ref(task["envelope"]):
            _fail("210_FOUNDATION_TASK_REF_DRIFT")
        self._same_context_and_attempt(task, structure, profile)
        return {
            "target": "241", "kind": "bound_data", "mode": "foundation",
            "foundation_task_ref": artifact_ref(task["envelope"]),
            "foundation_profile_ref": artifact_ref(profile["envelope"]),
            "structure_closure_ref": artifact_ref(structure["envelope"]),
        }

    def dispatch_foundation_regression(self, task: dict[str, Any], structure: dict[str, Any], verified_data: dict[str, Any]) -> dict[str, Any]:
        """Foundation explicitly excludes 230/251/252 and sends only its data chain to 260."""
        foundation._closure.validate_foundation_task_package(task)
        try:
            foundation._closure.validate_structure_closure_package(structure)
        except ContractError as exc:
            raise ContractError("210_FOUNDATION_STRUCTURE_REJECTED") from exc
        if structure["envelope"]["mode"] != "foundation" or structure["payload"].get("foundation_task_ref") != artifact_ref(task["envelope"]):
            _fail("210_FOUNDATION_TASK_REF_DRIFT")
        self._validate(verified_data, "verified-bound-data-package.schema.json", "210_FOUNDATION_DATA_REJECTED")
        if (verified_data["envelope"]["producer_id"], verified_data["envelope"]["mode"], verified_data["envelope"]["status"]) != ("242", "foundation", "validated"):
            _fail("210_FOUNDATION_DATA_STATE_REJECTED")
        self._same_context_and_attempt(task, structure, verified_data)
        bound = verified_data["payload"]["validated_data_package"]
        if bound.get("foundation_task_ref") != artifact_ref(task["envelope"]) or bound.get("structure_closure_ref") != artifact_ref(structure["envelope"]):
            _fail("210_FOUNDATION_DATA_BINDING_DRIFT")
        return {"target": "260", "kind": "database_copy_regression", "mode": "foundation", "foundation_task_ref": artifact_ref(task["envelope"]), "structure_closure_ref": artifact_ref(structure["envelope"]), "verified_data_ref": artifact_ref(verified_data["envelope"])}

    def route_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Route a 260 failure by its frozen code; never reinterpret its cause."""
        self._validate(feedback, "sql-regression-failed-feedback.schema.json", "210_260_FEEDBACK_REJECTED")
        payload = feedback["payload"]
        code, target = payload["failure_details"]["error_code"], payload["route_target"]
        if code not in _ROUTES or target != _ROUTES[code] or payload["retry_count"] != feedback["envelope"]["attempt_no"]:
            _fail("210_260_ROUTE_CONFLICT")
        if feedback["envelope"]["attempt_no"] == 3 and (code, target) != ("MANUAL_REVIEW_REQUIRED", "manual"):
            _fail("210_THIRD_ATTEMPT_NOT_MANUAL")
        if code == "MANUAL_REVIEW_REQUIRED" and feedback["envelope"]["attempt_no"] != 3:
            _fail("210_MANUAL_REVIEW_ATTEMPT_INVALID")
        if code == "FOUNDATION_REQUIRED" and payload["mode"] != "event_data":
            _fail("210_FOUNDATION_ROUTE_MODE_REJECTED")
        return {"target": target, "kind": "manual" if target == "manual" else "retry_or_rollback", "reason": code, "attempt_no": payload["retry_count"], "feedback_ref": artifact_ref(feedback["envelope"]), "requires_explicit_foundation_task": code == "FOUNDATION_REQUIRED"}

    def build_event_release(self, approved: dict[str, Any], regression: dict[str, Any], *, target_database_version: str, target_question_dataset_version: str) -> dict[str, Any]:
        """Assemble, but do not publish, the event formal-release candidate."""
        self._validate(approved, "question-sql-dual-review-passed-package.schema.json", "210_DUAL_REVIEW_REJECTED")
        self._validate(regression, "regression-passed-data-orm.schema.json", "210_EVENT_REGRESSION_REJECTED")
        if not isinstance(target_database_version, str) or not target_database_version or not isinstance(target_question_dataset_version, str) or not target_question_dataset_version:
            _fail("210_RELEASE_TARGET_VERSION_REQUIRED")
        self._same_context(approved, regression)
        data_refs = regression["payload"]["data_package_refs"]
        approved_ref = artifact_ref(approved["envelope"])
        query_spec_ref = approved["payload"]["query_specification_package"]
        if (
            len(data_refs) != 1
            or regression["payload"]["question_sql_ref"] != approved_ref
            or regression["payload"]["query_spec_ref"] != query_spec_ref
            or regression["payload"]["reviewed_question_sql_ref"] not in regression["envelope"]["parent_artifact_refs"]
            or regression["payload"]["event_query_context_ref"] not in regression["envelope"]["parent_artifact_refs"]
            or regression["payload"]["query_parameter_binding_ref"] not in regression["envelope"]["parent_artifact_refs"]
        ):
            _fail("210_EVENT_REGRESSION_LINEAGE_REJECTED")
        payload = {
            "release_candidate_id": f"210-release-event-{regression['payload']['regression_package_id']}",
            "release_mode": "event_data",
            "approved_question_sql_ref": artifact_ref(approved["envelope"]),
            "event_regression_passed_ref": artifact_ref(regression["envelope"]),
            "foundation_regression_report_ref": None,
            "target_database_version": target_database_version,
            "target_question_dataset_version": target_question_dataset_version,
            "idempotency_key": sha256({"mode": "event_data", "approved": artifact_ref(approved["envelope"]), "regression": artifact_ref(regression["envelope"]), "database": target_database_version, "dataset": target_question_dataset_version}),
            "expected_write_summary": {"orm_execution": {"insert_or_update": regression["payload"]["sandbox_execution_report"]["write_count"]}},
            "package_hashes": {"question_sql": approved["envelope"]["content_hash"], "data": data_refs[0]["content_hash"], "orm": regression["payload"]["orm_plan_ref"]["content_hash"], "query_binding": regression["payload"]["query_parameter_binding_hash"], "regression": regression["envelope"]["content_hash"]},
            "resume_qa_ref": None,
        }
        result = self._wrap("release_candidate", f"210-release-event-{regression['envelope']['artifact_id']}", payload, source=regression, mode="event_data", parents=[artifact_ref(approved["envelope"]), artifact_ref(regression["envelope"])])
        self._validate(result, "release-candidate-package.schema.json", "210_RELEASE_CANDIDATE_REJECTED")
        return result

    def build_foundation_release(self, task: dict[str, Any], regression: dict[str, Any]) -> dict[str, Any]:
        """Assemble a Foundation candidate from 260's frozen, verified write batch."""
        foundation._closure.validate_foundation_task_package(task)
        self._validate(regression, "foundation-regression-report.schema.json", "210_FOUNDATION_REGRESSION_REJECTED")
        payload_260 = regression["payload"]
        self._same_context_and_attempt(task, regression)
        if payload_260["foundation_task_ref"] != artifact_ref(task["envelope"]) or len(payload_260["validated_data_package_refs"]) != 1:
            _fail("210_FOUNDATION_REGRESSION_LINEAGE_REJECTED")
        if payload_260["target_database_version"] != task["payload"]["target_database_version"]:
            _fail("210_FOUNDATION_TARGET_DATABASE_VERSION_DRIFT")
        parents = regression["envelope"]["parent_artifact_refs"]
        data_ref = payload_260["validated_data_package_refs"][0]
        evidence_refs = payload_260["data_validation_evidence_refs"]
        if (artifact_ref(task["envelope"]) not in parents or payload_260["structure_closure_ref"] not in parents
                or data_ref not in parents or any(reference not in parents for reference in evidence_refs)):
            _fail("210_FOUNDATION_REGRESSION_LINEAGE_REJECTED")
        batch_hash = sha256({key: payload_260["foundation_write_batch"][key] for key in ("transaction_groups", "sql_statements", "parameter_sets", "execution_order", "expected_write_counts")})
        report_hash = sha256({key: value for key, value in payload_260.items() if key != "report_hash"})
        if batch_hash != payload_260["foundation_write_batch_hash"] or report_hash != payload_260["report_hash"]:
            _fail("210_FOUNDATION_REGRESSION_HASH_DRIFT")
        expected = {table: {"insert": item["planned_count"], "update": 0} for table, item in payload_260["table_write_summary"].items()}
        payload = {
            "release_candidate_id": f"210-release-foundation-{payload_260['foundation_regression_report_id']}",
            "release_mode": "foundation",
            "approved_question_sql_ref": None,
            "event_regression_passed_ref": None,
            "foundation_regression_report_ref": artifact_ref(regression["envelope"]),
            "target_database_version": payload_260["target_database_version"],
            "target_question_dataset_version": None,
            "idempotency_key": sha256({"mode": "foundation", "task": artifact_ref(task["envelope"]), "regression": artifact_ref(regression["envelope"])}),
            "expected_write_summary": expected,
            "package_hashes": {"foundation_task": task["envelope"]["content_hash"], "data": payload_260["validated_data_package_refs"][0]["content_hash"], "write_batch": payload_260["foundation_write_batch_hash"], "regression_report": regression["envelope"]["content_hash"]},
            "resume_qa_ref": task["payload"]["resume_qa_ref"],
        }
        result = self._wrap("release_candidate", f"210-release-foundation-{regression['envelope']['artifact_id']}", payload, source=regression, mode="foundation", parents=[artifact_ref(task["envelope"]), artifact_ref(regression["envelope"])])
        self._validate(result, "release-candidate-package.schema.json", "210_RELEASE_CANDIDATE_REJECTED")
        return result
