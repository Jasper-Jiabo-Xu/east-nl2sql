"""Fail-closed V5.1 two-phase Gold lifecycle control plane.

The module deliberately stores only hashes and opaque baseline/reviewer IDs.
Actual SQL, model responses and database locations remain in the approved local
data plane represented by the canonical V5 packages it validates below.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json, sha256


_TRANSPORT = {"envelope", "payload"}
_HASH = "0" * 64
_SCHEMA = "contracts/v5_1/gold-lifecycle.schema.json"
_CANONICAL = {
    "reviewed": "question-sql-dual-review-passed-package.schema.json",
    "regression": "regression-passed-data-orm.schema.json",
    "release": "release-candidate-package.schema.json",
    "receipt": "formal-release-receipt.schema.json",
}
_FAILURE_ROUTES = {
    "SQL_SEMANTIC_DEFECT": ("150", "110", True),
    "SQL_JOIN_DEFECT": ("150", "110", True),
    "SQL_AGGREGATION_DEFECT": ("150", "110", True),
    "SQL_DIALECT_DEFECT": ("150", "110", True),
    "DATA_BINDING_DEFECT": ("241", None, False),
    "DATA_DENSITY_DEFECT": ("241", None, False),
    "DATA_SELECTIVITY_DEFECT": ("241", None, False),
    "ORM_TRANSACTION_DEFECT": ("251", None, False),
    "ORM_EVENT_ORDER_DEFECT": ("251", None, False),
    "QUESTION_POLICY_AMBIGUITY": ("blocked_manual", None, False),
    "SOURCE_CONFLICT": ("blocked_manual", None, False),
}


def _fail(code: str) -> None:
    raise ContractError(code)


class GoldLifecycleController:
    """Creates immutable lifecycle views; it never performs 210, 260 or 010."""

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

    def _canonical(self, package: Any, kind: str) -> None:
        if not isinstance(package, dict) or set(package) != _TRANSPORT:
            _fail(f"{kind.upper()}_TRANSPORT_INVALID")
        try:
            validate_envelope(self.repo_root, package["envelope"], package["payload"])
            schema = load_json(self.repo_root / "contracts/packages" / _CANONICAL[kind])
            Draft202012Validator(schema, registry=self._registry(), format_checker=FormatChecker()).validate(package)
        except (ContractError, ValidationError) as exc:
            raise ContractError(f"{kind.upper()}_CANONICAL_INVALID") from exc

    def _validate_state(self, state: Any) -> None:
        try:
            Draft202012Validator(load_json(self.repo_root / _SCHEMA), format_checker=FormatChecker()).validate(state)
        except ValidationError as exc:
            _fail("GOLD_STATE_SCHEMA_INVALID")
        if state["artifact_hash"] != sha256({"artifact_id": state["artifact_id"], "payload": state["payload"]}):
            _fail("GOLD_STATE_HASH_DRIFT")
        payload = state["payload"]
        if payload["input_hashes"] != [item["content_hash"] for item in payload["parent_refs"]]:
            _fail("GOLD_INPUT_HASH_ORDER_DRIFT")
        locked = payload["candidate_lock"]["candidates"]
        if len({item["reviewer_id"] for item in locked}) != 6:
            _fail("CANDIDATE_REVIEWER_SET_INVALID")
        if payload["candidate_set_hash"] != sha256(locked):
            _fail("CANDIDATE_SET_HASH_DRIFT")
        state_name = payload["gold_state"]
        if state_name == "semantic_candidate" and (payload["execution_evidence_hash"] is not None or payload["final_gold_hash"] is not None):
            _fail("SEMANTIC_STATE_EVIDENCE_FORBIDDEN")
        if state_name == "execution_confirmed" and (payload["execution_evidence_hash"] is None or payload["final_gold_hash"] is not None):
            _fail("EXECUTION_STATE_EVIDENCE_INVALID")
        if state_name == "formal_released":
            if payload["execution_evidence_hash"] is None or payload["final_gold_hash"] is None or any(value is None for value in payload["release_evidence"].values()):
                _fail("FORMAL_RELEASE_EVIDENCE_MISSING")
            release = payload["release_evidence"]
            expected_final = sha256({"semantic": payload["semantic_decision_hash"], "execution": payload["execution_evidence_hash"], "candidate_set": payload["candidate_set_hash"], "release_candidate": release["release_candidate_ref"]["content_hash"], "release_receipt": release["release_receipt_ref"]["content_hash"]})
            if payload["final_gold_hash"] != expected_final:
                _fail("FINAL_GOLD_HASH_DRIFT")

    @staticmethod
    def _lineage(reviewed: dict[str, Any]) -> str:
        envelope = reviewed["envelope"]
        return sha256({key: envelope[key] for key in ("run_id", "qa_id", "trace_id", "attempt_no")})

    @staticmethod
    def _evidence(reviewed: dict[str, Any]) -> dict[str, Any]:
        payload = reviewed["payload"]
        return {
            "reviewed_question_sql_ref": artifact_ref(reviewed["envelope"]),
            "query_spec_ref": payload["query_specification_package"],
            "precheck_report_hash": payload["precheck_report"]["report_hash"],
            "deepseek_review_hash": payload["deepseek_review"]["review_hash"],
            "glm_review_hash": payload["glm_review"]["review_hash"],
            "adjudication_hash": payload["adjudication"]["report_hash"],
        }

    @staticmethod
    def _candidate_lock(candidates: Any) -> list[dict[str, Any]]:
        if not isinstance(candidates, list) or len(candidates) != 6:
            _fail("CANDIDATE_SET_SIZE_INVALID")
        result = copy.deepcopy(candidates)
        # Deterministic ordering makes the first-round lock independent of input order.
        result.sort(key=lambda item: item.get("reviewer_id", "") if isinstance(item, dict) else "")
        return result

    def _state(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = {"artifact_id": f"gold-{payload['qa_id']}-a{payload['attempt']}", "artifact_hash": _HASH, "payload": payload}
        result["artifact_hash"] = sha256({"artifact_id": result["artifact_id"], "payload": payload})
        self._validate_state(result)
        return result

    def semantic_candidate(self, reviewed_question_sql: dict[str, Any], candidates: list[dict[str, Any]], *, state_at: str) -> dict[str, Any]:
        """Lock six opaque first-round candidates after 150/160/double review evidence."""
        before = copy.deepcopy(reviewed_question_sql)
        self._canonical(reviewed_question_sql, "reviewed")
        if reviewed_question_sql["payload"]["package_hash"] != sha256({key: value for key, value in reviewed_question_sql["payload"].items() if key != "package_hash"}):
            _fail("SEMANTIC_PACKAGE_HASH_DRIFT")
        locked = self._candidate_lock(candidates)
        evidence = self._evidence(reviewed_question_sql)
        parent = artifact_ref(reviewed_question_sql["envelope"])
        payload = {
            "schema_version": "v5.1.gold-lifecycle/v1", "gold_state": "semantic_candidate",
            "qa_id": reviewed_question_sql["envelope"]["qa_id"], "run_id": reviewed_question_sql["envelope"]["run_id"], "trace_id": reviewed_question_sql["envelope"]["trace_id"], "attempt": reviewed_question_sql["envelope"]["attempt_no"],
            "lineage_id": self._lineage(reviewed_question_sql), "candidate_set_hash": sha256(locked),
            "semantic_decision_hash": sha256(evidence), "execution_evidence_hash": None, "final_gold_hash": None,
            "parent_refs": [parent], "input_hashes": [parent["content_hash"]], "state_at": state_at, "failure_code": None,
            "question_spec_evidence": evidence, "candidate_lock": {"round": 1, "candidates": locked},
            "release_evidence": {"release_candidate_ref": None, "release_receipt_ref": None},
        }
        if before != reviewed_question_sql:
            _fail("INPUT_MUTATED")
        return self._state(payload)

    def execution_confirmed(self, semantic: dict[str, Any], database_copy_regression: dict[str, Any], *, state_at: str) -> dict[str, Any]:
        self._validate_state(semantic)
        if semantic["payload"]["gold_state"] != "semantic_candidate":
            _fail("EXECUTION_TRANSITION_INVALID")
        self._canonical(database_copy_regression, "regression")
        source = semantic["payload"]
        envelope, payload_260 = database_copy_regression["envelope"], database_copy_regression["payload"]
        if (envelope["qa_id"], envelope["attempt_no"], self._lineage(database_copy_regression)) != (source["qa_id"], source["attempt"], source["lineage_id"]):
            _fail("REGRESSION_LINEAGE_DRIFT")
        if payload_260["question_sql_ref"] != source["question_spec_evidence"]["reviewed_question_sql_ref"]:
            _fail("REGRESSION_REVIEWED_REF_DRIFT")
        parent = artifact_ref(envelope)
        next_payload = copy.deepcopy(source)
        next_payload.update({"gold_state": "execution_confirmed", "execution_evidence_hash": parent["content_hash"], "state_at": state_at,
                             "parent_refs": [*source["parent_refs"], parent], "input_hashes": [*source["input_hashes"], parent["content_hash"]]})
        return self._state(next_payload)

    def restart_sql_attempt(self, previous: dict[str, Any], reviewed_question_sql: dict[str, Any], candidates: list[dict[str, Any]], *, state_at: str) -> dict[str, Any]:
        """Create the only permitted successor after a SQL/question remediation.

        The caller must have an SQL-route adjudication decision; this method
        enforces the resulting attempt boundary and deliberately does not carry
        old explanation, closure or execution evidence into the new candidate.
        """
        self._validate_state(previous)
        old = previous["payload"]
        if old["attempt"] >= 3:
            _fail("SQL_RETRY_ATTEMPT_EXHAUSTED")
        self._canonical(reviewed_question_sql, "reviewed")
        envelope = reviewed_question_sql["envelope"]
        if (envelope["qa_id"], envelope["run_id"], envelope["trace_id"], envelope["attempt_no"]) != (old["qa_id"], old["run_id"], old["trace_id"], old["attempt"] + 1):
            _fail("SQL_RETRY_LINEAGE_OR_ATTEMPT_DRIFT")
        result = self.semantic_candidate(reviewed_question_sql, candidates, state_at=state_at)
        if result["payload"]["semantic_decision_hash"] == old["semantic_decision_hash"]:
            _fail("SQL_RETRY_EVIDENCE_NOT_REFRESHED")
        return result

    def formal_released(self, execution: dict[str, Any], release_candidate: dict[str, Any], release_receipt: dict[str, Any], *, state_at: str) -> dict[str, Any]:
        self._validate_state(execution)
        if execution["payload"]["gold_state"] != "execution_confirmed":
            _fail("FORMAL_RELEASE_TRANSITION_INVALID")
        self._canonical(release_candidate, "release")
        self._canonical(release_receipt, "receipt")
        source, candidate, receipt = execution["payload"], release_candidate["payload"], release_receipt["payload"]
        if (release_candidate["envelope"]["qa_id"], release_candidate["envelope"]["attempt_no"], self._lineage(release_candidate)) != (source["qa_id"], source["attempt"], source["lineage_id"]):
            _fail("RELEASE_CANDIDATE_LINEAGE_DRIFT")
        if candidate["approved_question_sql_ref"] != source["question_spec_evidence"]["reviewed_question_sql_ref"]:
            _fail("RELEASE_REVIEWED_REF_DRIFT")
        if candidate["event_regression_passed_ref"]["content_hash"] != source["execution_evidence_hash"]:
            _fail("RELEASE_REGRESSION_REF_DRIFT")
        candidate_ref = artifact_ref(release_candidate["envelope"])
        if receipt["commit_status"] != "committed" or receipt["release_candidate_ref"] != candidate_ref:
            _fail("RELEASE_RECEIPT_INVALID")
        receipt_ref = artifact_ref(release_receipt["envelope"])
        next_payload = copy.deepcopy(source)
        next_payload.update({
            "gold_state": "formal_released", "state_at": state_at,
            "parent_refs": [*source["parent_refs"], candidate_ref, receipt_ref],
            "input_hashes": [*source["input_hashes"], candidate_ref["content_hash"], receipt_ref["content_hash"]],
            "release_evidence": {"release_candidate_ref": candidate_ref, "release_receipt_ref": receipt_ref},
        })
        next_payload["final_gold_hash"] = sha256({"semantic": source["semantic_decision_hash"], "execution": source["execution_evidence_hash"], "candidate_set": source["candidate_set_hash"], "release_candidate": candidate_ref["content_hash"], "release_receipt": receipt_ref["content_hash"]})
        return self._state(next_payload)

    def adjudicate_failure(self, state: dict[str, Any], failure_code: str) -> dict[str, Any]:
        """Return a stable non-mutating remediation decision for post-260 evidence."""
        self._validate_state(state)
        if failure_code not in _FAILURE_ROUTES:
            _fail("UNKNOWN_FAILURE_ROUTE")
        target, via, new_attempt = _FAILURE_ROUTES[failure_code]
        payload = state["payload"]
        if new_attempt and payload["attempt"] >= 3:
            target, via, new_attempt = "blocked_manual", None, False
        decision = {"schema_version": "v5.1.gold-adjudication/v1", "qa_id": payload["qa_id"], "lineage_id": payload["lineage_id"], "from_state": payload["gold_state"], "failure_code": failure_code, "route_target": target, "route_via": via, "new_attempt_required": new_attempt, "invalidates": ["150", "160", "dual_review", "210", "220", "230", "241", "242", "251", "252", "260"] if new_attempt else (["241", "242", "260"] if target == "241" else (["251", "252", "260"] if target == "251" else []))}
        return {**decision, "decision_hash": sha256(decision)}
