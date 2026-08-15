"""Observable fact mapper for Agent 130: EAST可观察事实构造agent.

Maps penalty facts from Agent 120 to EAST-observable facts by
iteratively calling Agent 000 for constraint asset retrieval and
building the EAST-OBSERVABLE-FACT-PACKAGE.  The LLM determines
observability type and proxy expressions at runtime; this module
provides deterministic validation, hard-code checks and schema
verification.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.governance import ContractError, load_json

SCHEMA_VERSION_PENALTY_FACT = "penalty-fact-v1"
SCHEMA_VERSION_CONSTRAINT_QUERY = "constraint-query-request-v1"
SCHEMA_VERSION_CONSTRAINT_ASSET = "constraint-asset-v1"
SCHEMA_VERSION_OBSERVABLE_FACT = "east-observable-fact-v1"

OBSERVABILITY_TYPES = frozenset({"direct", "indirect", "unobservable"})
COVERAGE_STATUSES = frozenset({"complete", "partial", "blocked"})
ASSET_VERSIONS = frozenset({"CA-V0.3.0", "TRG-V1.0.0"})
QUERY_PURPOSES = frozenset({
    "constraint_lookup", "field_explanation", "table_explanation",
    "relationship_lookup", "closure_expansion",
})
SOURCE_TYPES = frozenset({
    "constraint_asset", "east_material", "model", "qna", "review",
})
MAX_ITERATIONS = 3

_OBS_ID = re.compile(r"^obs-[0-9]{3}$")
_FACT_ID = re.compile(r"^fact-[0-9]{3}$")
_CALLER_AGENT_ID = "130"
_CALLER_STAGE = "observable_fact"


def _fail(code: str) -> None:
    raise ContractError(code)


# --------------------------------------------------------------------------- #
#  Schema helpers                                                              #
# --------------------------------------------------------------------------- #

def _load_and_validate_schema(schema_path: Path, payload: dict[str, Any], error_code: str) -> None:
    schema = load_json(schema_path)
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        raise ContractError(error_code) from exc


# --------------------------------------------------------------------------- #
#  Public validation entry points                                              #
# --------------------------------------------------------------------------- #

def validate_penalty_fact_package(repo_root: Path, payload: dict[str, Any]) -> None:
    """Validate a PENALTY-FACT-PACKAGE from Agent 120."""
    if not isinstance(payload, dict):
        _fail("PAYLOAD_NOT_OBJECT")
    if payload.get("penalty_fact_package_schema_version") != SCHEMA_VERSION_PENALTY_FACT:
        _fail("SCHEMA_VERSION_UNSUPPORTED:PENALTY_FACT_PACKAGE")
    _load_and_validate_schema(
        repo_root / "contracts" / "packages" / "penalty-fact-package.schema.json",
        payload,
        "SCHEMA_VALIDATION_FAILED:PENALTY_FACT_PACKAGE",
    )
    # Hard-code: source_facts must be non-empty
    if not payload.get("source_facts"):
        _fail("EMPTY_PENALTY_FACTS")


def validate_constraint_query_request(repo_root: Path, payload: dict[str, Any]) -> None:
    """Validate a CONSTRAINT-QUERY-REQUEST before sending to Agent 000."""
    if not isinstance(payload, dict):
        _fail("PAYLOAD_NOT_OBJECT")
    if payload.get("caller_agent_id") != _CALLER_AGENT_ID:
        _fail("INVALID_CALLER_AGENT_ID")
    if payload.get("caller_stage") != _CALLER_STAGE:
        _fail("INVALID_CALLER_STAGE")
    if payload.get("query_purpose") not in QUERY_PURPOSES:
        _fail("INVALID_QUERY_PURPOSE")


def validate_constraint_asset_package(repo_root: Path, payload: dict[str, Any]) -> None:
    """Validate a CONSTRAINT-ASSET-PACKAGE returned by Agent 000."""
    if not isinstance(payload, dict):
        _fail("PAYLOAD_NOT_OBJECT")
    if payload.get("asset_version") not in ASSET_VERSIONS:
        _fail("ASSET_VERSION_MISMATCH")


def validate_observable_fact_package(repo_root: Path, payload: dict[str, Any]) -> None:
    """Full validation of an EAST-OBSERVABLE-FACT-PACKAGE: hard rules + schema."""
    if not isinstance(payload, dict):
        _fail("PAYLOAD_NOT_OBJECT")
    if payload.get("east_observable_fact_package_schema_version") != SCHEMA_VERSION_OBSERVABLE_FACT:
        _fail("SCHEMA_VERSION_UNSUPPORTED:EAST_OBSERVABLE_FACT_PACKAGE")

    # Hard-code checks BEFORE schema validation for precise error codes
    if payload.get("coverage_status") not in COVERAGE_STATUSES:
        _fail("COVERAGE_STATUS_INVALID")
    if payload.get("asset_version") not in ASSET_VERSIONS:
        _fail("ASSET_VERSION_MISMATCH")

    seen_ids: set[str] = set()
    for obs in payload.get("observable_facts", []):
        oid = obs.get("observable_fact_id")
        if oid in seen_ids:
            _fail("OBSERVABLE_FACT_ID_DUPLICATE")
        seen_ids.add(oid)
        if obs.get("observability_type") not in OBSERVABILITY_TYPES:
            _fail("OBSERVABILITY_TYPE_INVALID")

        for ref in obs.get("constraint_asset_refs", []):
            if ref.get("source_type") not in SOURCE_TYPES:
                _fail("CONSTRAINT_ASSET_REF_SOURCE_TYPE_INVALID")

    # Schema validation after hard-code checks
    _load_and_validate_schema(
        repo_root / "contracts" / "packages" / "east-observable-fact-package.schema.json",
        payload,
        "SCHEMA_VALIDATION_FAILED:EAST_OBSERVABLE_FACT_PACKAGE",
    )


# --------------------------------------------------------------------------- #
#  Task 1: Plan constraint query request                                       #
# --------------------------------------------------------------------------- #

def plan_constraint_query(
    penalty_fact_payload: dict[str, Any],
    *,
    request_id: str = "req-001",
    previous_request_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Build a CONSTRAINT-QUERY-REQUEST from a PENALTY-FACT-PACKAGE.

    Scans source_facts to determine what EAST tables, fields and
    relationships need to be queried from the constraint asset store
    via Agent 000.
    """
    facts = penalty_fact_payload.get("source_facts", [])
    if not facts:
        _fail("EMPTY_PENALTY_FACTS")

    # Collect tables and fields mentioned in structured facts
    table_scope: set[str] = set()
    field_scope: set[str] = set()
    target_asset_types: set[str] = set()

    for fact in facts:
        sf = fact.get("structured_fact", {})
        # We don't know tables/fields yet — that's what we're querying for.
        # Based on fact_type, we request the appropriate asset types.
        fact_type = fact.get("fact_type", "")
        if fact_type in ("subject", "behavior", "object"):
            target_asset_types.add("constraint")
            target_asset_types.add("east_material")
        elif fact_type in ("time", "amount", "condition"):
            target_asset_types.add("field_explanation")
            target_asset_types.add("constraint")
        elif fact_type in ("result", "regulatory_conclusion"):
            target_asset_types.add("constraint")
            target_asset_types.add("qna")
        else:
            target_asset_types.add("constraint")

    return {
        "request_id": request_id,
        "caller_agent_id": _CALLER_AGENT_ID,
        "caller_stage": _CALLER_STAGE,
        "query_purpose": "constraint_lookup",
        "natural_language_intent": (
            "根据处罚事实类型查询对应的EAST标准表、字段、关系和约束定义"
        ),
        "target_asset_types": sorted(target_asset_types),
        "table_scope": sorted(table_scope) if table_scope else [],
        "field_scope": sorted(field_scope) if field_scope else [],
        "relationship_scope": [],
        "required_output_fields": [
            "table_id", "field_id", "field_name", "field_type",
            "constraints", "relationships", "hierarchy",
        ],
        "previous_request_refs": previous_request_refs or [],
        "max_rows": 500,
    }


# --------------------------------------------------------------------------- #
#  Task 2: Build observable fact package                                       #
# --------------------------------------------------------------------------- #

def _determine_observability(
    fact: dict[str, Any],
    matched_records: list[dict[str, Any]],
) -> str:
    """Determine observability type for a penalty fact.

    - direct: the fact maps to a single EAST field with exact match
    - indirect: the fact maps through cross-table relations or computed conditions
    - unobservable: no EAST field/relationship can express the fact
    """
    if not matched_records:
        return "unobservable"

    direct_count = 0
    indirect_count = 0
    for record in matched_records:
        rtype = record.get("record_type", "")
        if rtype in ("field", "table_field"):
            direct_count += 1
        elif rtype in ("relationship", "cross_table", "computed"):
            indirect_count += 1

    if direct_count > 0:
        return "direct"
    if indirect_count > 0:
        return "indirect"
    return "unobservable"


def _build_mapping_matrix(
    fact: dict[str, Any],
    matched_records: list[dict[str, Any]],
    obs_id: str,
) -> list[dict[str, Any]]:
    """Build mapping_matrix entries for one penalty fact."""
    fact_id = fact.get("penalty_fact_id", "")
    entries: list[dict[str, Any]] = []
    for record in matched_records:
        data = record.get("data", {})
        entries.append({
            "penalty_fact_ref": fact_id,
            "proxy_expression": data.get("proxy_expression", ""),
            "table_field_path": data.get("table_field_path", ""),
            "asset_evidence_ref": data.get("asset_evidence_ref", record.get("source_refs", [""])[0] if record.get("source_refs") else ""),
        })
    if not entries:
        entries.append({
            "penalty_fact_ref": fact_id,
            "proxy_expression": "",
            "table_field_path": "",
            "asset_evidence_ref": "",
        })
    return entries


def _build_constraint_asset_refs(
    matched_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build constraint_asset_refs from matched records."""
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in matched_records:
        for sref in record.get("source_refs", []):
            if sref not in seen:
                seen.add(sref)
                refs.append({
                    "source_type": "constraint_asset",
                    "source_id": sref,
                })
    # Ensure at least one ref
    if not refs:
        refs.append({
            "source_type": "constraint_asset",
            "source_id": "pending",
        })
    return refs


def build_observable_facts(
    penalty_fact_payload: dict[str, Any],
    constraint_asset_payload: dict[str, Any],
    *,
    iteration: int = 1,
) -> dict[str, Any]:
    """Build an EAST-OBSERVABLE-FACT-PACKAGE from penalty facts and
    constraint assets.

    This is the core mapping function.  The LLM provides the semantic
    mapping at runtime; this function provides deterministic assembly
    and validation scaffolding.
    """
    if iteration > MAX_ITERATIONS:
        _fail("MAX_ITERATION_EXCEEDED")

    facts = penalty_fact_payload.get("source_facts", [])
    if not facts:
        _fail("EMPTY_PENALTY_FACTS")

    asset_version = constraint_asset_payload.get("asset_version", "")
    if asset_version not in ASSET_VERSIONS:
        _fail("ASSET_VERSION_MISMATCH")

    matched_records = constraint_asset_payload.get("matched_records", [])
    unmatched_items = constraint_asset_payload.get("unmatched_items", [])

    observable_facts: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    has_unobservable_must_preserve = False
    obs_seq = 1

    for fact in facts:
        fact_id = fact.get("penalty_fact_id", "")
        must_preserve = fact.get("must_preserve_in_question", "no")

        # Find matched records for this fact (by source_refs overlap)
        fact_records = [
            r for r in matched_records
            if fact_id in r.get("source_refs", []) or
               any(fact_id in s for s in r.get("hierarchy_refs", []))
        ]

        obs_type = _determine_observability(fact, fact_records)
        obs_id = f"obs-{obs_seq:03d}"
        obs_seq += 1

        if obs_type == "unobservable" and must_preserve in ("yes", "conditional"):
            has_unobservable_must_preserve = True

        obs_fact: dict[str, Any] = {
            "observable_fact_id": obs_id,
            "penalty_fact_refs": [fact_id],
            "topic": fact.get("structured_fact", {}).get("predicate", ""),
            "main_object": fact.get("structured_fact", {}).get("object", ""),
            "query_grain": fact.get("structured_fact", {}).get("subject", ""),
            "entry_table": "",
            "observability_type": obs_type,
            "mapping_matrix": _build_mapping_matrix(fact, fact_records, obs_id),
            "constraint_asset_refs": _build_constraint_asset_refs(fact_records),
        }

        # Add optional fields based on observability
        if obs_type == "unobservable":
            obs_fact["unobservable_parts"] = [{
                "penalty_fact_ref": fact_id,
                "reason": "EAST标准表中未找到可直接或间接表达该事实的字段或关系",
            }]
            if must_preserve in ("yes", "conditional"):
                unresolved.append({
                    "item_ref": fact_id,
                    "reason": f"must_preserve={must_preserve}的事实在EAST中不可观察",
                    "needs_human_review": True,
                })

        if obs_type == "indirect":
            obs_fact["risk_screening_boundary"] = "间接筛查结果不直接认定违规，需结合其他证据"

        # Populate entry_table from matched records
        for rec in fact_records:
            data = rec.get("data", {})
            if data.get("table_id"):
                obs_fact["entry_table"] = data["table_id"]
                break

        observable_facts.append(obs_fact)

    # Determine coverage status
    if not observable_facts:
        coverage_status = "blocked"
    elif has_unobservable_must_preserve:
        coverage_status = "partial"
    elif any(obs["observability_type"] == "unobservable" for obs in observable_facts):
        coverage_status = "partial"
    else:
        coverage_status = "complete"

    # Check for mapping completeness
    must_preserve_ids = {
        f["penalty_fact_id"]
        for f in facts
        if f.get("must_preserve_in_question") in ("yes", "conditional")
    }
    mapped_ids: set[str] = set()
    for obs in observable_facts:
        mapped_ids.update(obs.get("penalty_fact_refs", []))
    missing_ids = must_preserve_ids - mapped_ids
    if missing_ids:
        coverage_status = "partial"
        for mid in sorted(missing_ids):
            unresolved.append({
                "item_ref": mid,
                "reason": "must_preserve事实未在mapping_matrix中覆盖",
                "needs_human_review": True,
            })

    # Add unresolved from constraint asset unmatched items
    for item in unmatched_items:
        unresolved.append({
            "item_ref": item.get("item_ref", ""),
            "reason": item.get("reason", "约束资产查询未命中"),
            "needs_human_review": item.get("needs_human_review", False),
        })

    return {
        "observable_facts": observable_facts,
        "coverage_status": coverage_status,
        "unresolved_items": unresolved,
        "asset_version": asset_version,
        "east_observable_fact_package_schema_version": SCHEMA_VERSION_OBSERVABLE_FACT,
    }


# --------------------------------------------------------------------------- #
#  Task 3: Handle review feedback                                              #
# --------------------------------------------------------------------------- #

def handle_review_feedback(
    penalty_fact_payload: dict[str, Any],
    constraint_asset_payload: dict[str, Any],
    review_report: dict[str, Any],
    *,
    previous_iteration: int = 1,
) -> dict[str, Any]:
    """Re-process after 170/180 review identifies OBSERVABLE_MAPPING_ERROR.

    Iteratively re-queries constraint assets and rebuilds the
    observable fact package up to MAX_ITERATIONS times.
    """
    new_iteration = previous_iteration + 1
    if new_iteration > MAX_ITERATIONS:
        _fail("MAX_ITERATION_EXCEEDED")

    # Validate the review report contains expected error type
    error_types = review_report.get("error_types", [])
    if "OBSERVABLE_MAPPING_ERROR" not in error_types:
        _fail("REVIEW_ROUTE_INVALID")

    # Re-build observable facts with expanded scope
    result = build_observable_facts(
        penalty_fact_payload,
        constraint_asset_payload,
        iteration=new_iteration,
    )

    # Mark as from review iteration
    if result.get("unresolved_items") is not None:
        result["unresolved_items"].append({
            "item_ref": f"review-iteration-{new_iteration}",
            "reason": f"基于审核反馈第{new_iteration}次迭代重建",
            "needs_human_review": False,
        })

    return result


# --------------------------------------------------------------------------- #
#  Stateful mapper with full validation                                        #
# --------------------------------------------------------------------------- #

class ObservableFactMapper:
    """Stateful mapper that validates input/output and supports
    review-driven re-mapping.

    Usage::

        mapper = ObservableFactMapper(repo_root)
        mapper.validate_penalty_input(penalty_payload)

        # Task 1: plan query
        query_req = mapper.plan_query(penalty_payload)

        # Task 2: build observable facts
        obs_payload = mapper.build(penalty_payload, constraint_payload)
        mapper.validate_output(obs_payload)

        # Task 3: review feedback
        obs_payload_v2 = mapper.re_map(
            penalty_payload, constraint_payload, review_report, iteration=1
        )
        mapper.validate_output(obs_payload_v2)
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._iteration = 0

    def validate_penalty_input(self, payload: dict[str, Any]) -> None:
        validate_penalty_fact_package(self.repo_root, payload)

    def validate_constraint_input(self, payload: dict[str, Any]) -> None:
        validate_constraint_asset_package(self.repo_root, payload)

    def validate_output(self, payload: dict[str, Any]) -> None:
        validate_observable_fact_package(self.repo_root, payload)

    def plan_query(
        self,
        penalty_payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.validate_penalty_input(penalty_payload)
        return plan_constraint_query(penalty_payload, **kwargs)

    def build(
        self,
        penalty_payload: dict[str, Any],
        constraint_payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.validate_penalty_input(penalty_payload)
        self.validate_constraint_input(constraint_payload)
        result = build_observable_facts(penalty_payload, constraint_payload, **kwargs)
        self.validate_output(result)
        return result

    def re_map(
        self,
        penalty_payload: dict[str, Any],
        constraint_payload: dict[str, Any],
        review_report: dict[str, Any],
        *,
        iteration: int = 1,
    ) -> dict[str, Any]:
        self.validate_penalty_input(penalty_payload)
        self.validate_constraint_input(constraint_payload)
        result = handle_review_feedback(
            penalty_payload, constraint_payload, review_report,
            previous_iteration=iteration,
        )
        self.validate_output(result)
        self._iteration = iteration + 1
        return result
