"""Strict, side-effect-free orchestration for the V5 210 data stage.

The coordinator emits only immutable packages and explicit dispatch intents.  A
runtime adapter owns task invocation and artifact-registry persistence; this
module must never generate records/ORM/SQL or open a database.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json, sha256

from . import foundation


_TRANSPORT_KEYS = {"envelope", "payload"}
_REF_KEYS = {"artifact_id", "version", "content_hash"}
_ROUTES = {
    "DATA_VALUE_ERROR": "241",
    "ORM_PLAN_ERROR": "251",
    "SQL_EXECUTION_ERROR": "010",
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

    def begin_event(self, approved: dict[str, Any]) -> dict[str, Any]:
        """Start the event data/ORM forks from one immutable 110 package."""
        reviewed = self.build_reviewed_question_sql(approved)
        return {
            "reviewed_question_sql": reviewed,
            "dispatches": [
                {"target": "220", "kind": "structure_closure", "input_ref": artifact_ref(reviewed["envelope"])},
                {"target": "230", "kind": "operation_closure", "question_sql_ref": artifact_ref(approved["envelope"]), "business_event_candidates": copy.deepcopy(reviewed["payload"]["approved_business_events"])},
            ],
        }

    def dispatch_event_branches(self, reviewed: dict[str, Any], structure: dict[str, Any], operation: dict[str, Any]) -> list[dict[str, Any]]:
        """Dispatch 241 and 251 only after both event closures are available."""
        self._validate(reviewed, "reviewed-question-sql-package.schema.json", "210_REVIEWED_INPUT_REJECTED")
        for package, artifact_type, producer in ((structure, "structure_closure", "220"), (operation, "operation_closure", "230")):
            if not isinstance(package, dict) or set(package) != _TRANSPORT_KEYS:
                _fail("210_CLOSURE_TRANSPORT_REJECTED")
            validate_envelope(self.repo_root, package["envelope"], package["payload"])
            if (package["envelope"]["artifact_type"], package["envelope"]["producer_id"], package["envelope"]["mode"]) != (artifact_type, producer, "event_data"):
                _fail("210_CLOSURE_ROUTE_REJECTED")
        self._same_context(reviewed, structure, operation)
        return [
            {"target": "241", "kind": "bound_data", "structure_closure_ref": artifact_ref(structure["envelope"]), "operation_closure_ref": artifact_ref(operation["envelope"])},
            {"target": "251", "kind": "restricted_orm", "structure_closure_ref": artifact_ref(structure["envelope"]), "operation_closure_ref": artifact_ref(operation["envelope"])},
        ]

    def join_event_validations(self, approved: dict[str, Any], verified_data: dict[str, Any], frozen_orm: dict[str, Any]) -> dict[str, Any]:
        """Authorize 260 only after both validated event branches converge."""
        self._validate(approved, "question-sql-dual-review-passed-package.schema.json", "210_DUAL_REVIEW_REJECTED")
        self._validate(verified_data, "verified-bound-data-package.schema.json", "210_DATA_BRANCH_REJECTED")
        self._validate(frozen_orm, "frozen-orm-package.schema.json", "210_ORM_BRANCH_REJECTED")
        if (verified_data["envelope"]["producer_id"], verified_data["envelope"]["mode"], verified_data["envelope"]["status"]) != ("242", "event_data", "validated"):
            _fail("210_DATA_BRANCH_STATE_REJECTED")
        if (frozen_orm["envelope"]["producer_id"], frozen_orm["envelope"]["mode"], frozen_orm["envelope"]["status"]) != ("252", "event_data", "validated"):
            _fail("210_ORM_BRANCH_STATE_REJECTED")
        self._same_context(approved, verified_data, frozen_orm)
        return {"target": "260", "kind": "database_copy_regression", "mode": "event_data", "verified_data_ref": artifact_ref(verified_data["envelope"]), "frozen_orm_ref": artifact_ref(frozen_orm["envelope"]), "approved_question_sql_ref": artifact_ref(approved["envelope"]), "query_spec_ref": approved["payload"]["query_specification_package"]}

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
        if len(data_refs) != 1 or regression["payload"]["question_sql_ref"] != artifact_ref(approved["envelope"]) or regression["payload"]["query_spec_ref"] != approved["payload"]["query_specification_package"]:
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
            "package_hashes": {"question_sql": approved["envelope"]["content_hash"], "data": data_refs[0]["content_hash"], "orm": regression["payload"]["orm_plan_ref"]["content_hash"], "regression": regression["envelope"]["content_hash"]},
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
        if artifact_ref(task["envelope"]) not in parents or payload_260["structure_closure_ref"] not in parents:
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
