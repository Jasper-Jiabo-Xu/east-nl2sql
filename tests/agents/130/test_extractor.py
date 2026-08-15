"""Tests for Agent 130: EAST可观察事实构造agent.

Covers:
- Penalty fact package validation
- Constraint query request planning
- Observable fact package building (direct/indirect/unobservable)
- Review feedback handling
- Full pipeline with ObservableFactMapper
- Schema validation
- Edge cases: empty facts, iteration limit, coverage status
"""
from __future__ import annotations

import unittest
from pathlib import Path

from east_v5.agents.east_130.extractor import (
    ObservableFactMapper,
    plan_constraint_query,
    build_observable_facts,
    handle_review_feedback,
    validate_penalty_fact_package,
    validate_observable_fact_package,
    OBSERVABILITY_TYPES,
    COVERAGE_STATUSES,
    MAX_ITERATIONS,
    _CALLER_AGENT_ID,
    _CALLER_STAGE,
)
from east_v5.governance import ContractError


# ============================================================================
# Helpers
# ============================================================================

REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/agents/130/ -> repo root


def _make_penalty_fact_package(**overrides) -> dict:
    """Build a valid PENALTY-FACT-PACKAGE with defaults."""
    _evidence_ref = {
        "url": "https://example.com/doc1",
        "publishing_org": "测试机构",
        "publish_date": "2024-01-01",
        "access_date": "2024-06-01",
        "applicable_time": "2024-01-01~2024-12-31",
        "snippet": "测试片段",
    }
    base = {
        "source_facts": [
            {
                "penalty_fact_id": "fact-001",
                "fact_type": "subject",
                "structured_fact": {
                    "subject": "某银行",
                    "predicate": "被处罚单位",
                    "object": "某银行股份有限公司",
                    "qualifier": None,
                    "value": None,
                },
                "original_text": "某银行股份有限公司",
                "source_span_refs": ["span-0001"],
                "must_preserve_in_question": "yes",
            },
            {
                "penalty_fact_id": "fact-002",
                "fact_type": "behavior",
                "structured_fact": {
                    "subject": "某银行",
                    "predicate": "违法违规事实",
                    "object": "贷款三查不到位",
                    "qualifier": None,
                    "value": None,
                },
                "original_text": "贷款三查不到位",
                "source_span_refs": ["span-0002"],
                "must_preserve_in_question": "yes",
            },
        ],
        "external_evidence": {
            "penalty_intent": {"description": "测试", "evidence_refs": [_evidence_ref]},
            "regulatory_rules": [],
            "business_meaning": {"description": "测试", "evidence_refs": []},
            "penalty_background": {"description": "测试", "evidence_refs": []},
        },
        "evidence_conflicts": [],
        "uncertainties": [],
        "penalty_fact_package_schema_version": "penalty-fact-v1",
    }
    base.update(overrides)
    return base


def _make_constraint_asset_package(**overrides) -> dict:
    """Build a valid CONSTRAINT-ASSET-PACKAGE with defaults."""
    base = {
        "request_id": "REQ-001",
        "asset_version": "CA-V0.3.0",
        "executed_queries": [
            {
                "sql": "SELECT * FROM field_master WHERE table_id = ? LIMIT ?",
                "params": ["EAST_D001", 100],
                "safety_check_result": "pass",
                "row_count": 2,
                "elapsed_ms": 10,
            }
        ],
        "matched_records": [
            {
                "record_type": "field",
                "data": {
                    "table_id": "EAST_D001",
                    "field_id": "field_001",
                    "field_name": "机构名称",
                    "field_type": "VARCHAR",
                    "proxy_expression": "机构名称 = 被处罚单位",
                    "table_field_path": "EAST_D001.field_001",
                    "asset_evidence_ref": "field_master:EAST_D001.field_001",
                },
                "source_refs": ["fact-001"],
                "hierarchy_refs": [],
            },
            {
                "record_type": "relationship",
                "data": {
                    "table_id": "EAST_D002",
                    "field_id": "field_010",
                    "proxy_expression": "贷款三查 → 贷后管理字段间接表达",
                    "table_field_path": "EAST_D002.field_010",
                    "asset_evidence_ref": "constraint:MC-001",
                },
                "source_refs": ["fact-002"],
                "hierarchy_refs": ["fact-002"],
            },
        ],
        "constraint_summary": {
            "total_matched": 2,
            "by_type": {"field": 1, "relationship": 1},
        },
        "unmatched_items": [],
        "query_trace": [],
        "constraint_asset_package_schema_version": "constraint-asset-v1",
    }
    base.update(overrides)
    return base


# ============================================================================
# Validation Tests
# ============================================================================

class TestPenaltyFactValidation(unittest.TestCase):
    """Tests for PENALTY-FACT-PACKAGE validation."""

    def test_valid_penalty_fact_package(self):
        pkg = _make_penalty_fact_package()
        validate_penalty_fact_package(REPO_ROOT, pkg)  # Should not raise

    def test_empty_source_facts(self):
        pkg = _make_penalty_fact_package(source_facts=[])
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED"):
            validate_penalty_fact_package(REPO_ROOT, pkg)

    def test_wrong_schema_version(self):
        pkg = _make_penalty_fact_package()
        pkg["penalty_fact_package_schema_version"] = "wrong-v1"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VERSION_UNSUPPORTED"):
            validate_penalty_fact_package(REPO_ROOT, pkg)

    def test_payload_not_object(self):
        with self.assertRaisesRegex(ContractError, "PAYLOAD_NOT_OBJECT"):
            validate_penalty_fact_package(REPO_ROOT, "not a dict")


class TestObservableFactValidation(unittest.TestCase):
    """Tests for EAST-OBSERVABLE-FACT-PACKAGE validation."""

    def test_valid_observable_fact_package(self):
        pkg = _make_constraint_asset_package()
        pf_pkg = _make_penalty_fact_package()
        result = build_observable_facts(pf_pkg, pkg)
        validate_observable_fact_package(REPO_ROOT, result)  # Should not raise

    def test_wrong_schema_version(self):
        pkg = _make_constraint_asset_package()
        pf_pkg = _make_penalty_fact_package()
        result = build_observable_facts(pf_pkg, pkg)
        result["east_observable_fact_package_schema_version"] = "wrong-v1"
        with self.assertRaisesRegex(ContractError, "SCHEMA_VERSION_UNSUPPORTED"):
            validate_observable_fact_package(REPO_ROOT, result)

    def test_payload_not_object(self):
        with self.assertRaisesRegex(ContractError, "PAYLOAD_NOT_OBJECT"):
            validate_observable_fact_package(REPO_ROOT, "not a dict")

    def test_invalid_coverage_status(self):
        pkg = _make_constraint_asset_package()
        pf_pkg = _make_penalty_fact_package()
        result = build_observable_facts(pf_pkg, pkg)
        result["coverage_status"] = "invalid"
        with self.assertRaisesRegex(ContractError, "COVERAGE_STATUS_INVALID"):
            validate_observable_fact_package(REPO_ROOT, result)

    def test_invalid_observability_type(self):
        pkg = _make_constraint_asset_package()
        pf_pkg = _make_penalty_fact_package()
        result = build_observable_facts(pf_pkg, pkg)
        result["observable_facts"][0]["observability_type"] = "magic"
        with self.assertRaisesRegex(ContractError, "OBSERVABILITY_TYPE_INVALID"):
            validate_observable_fact_package(REPO_ROOT, result)

    def test_invalid_asset_version(self):
        pkg = _make_constraint_asset_package()
        pf_pkg = _make_penalty_fact_package()
        result = build_observable_facts(pf_pkg, pkg)
        result["asset_version"] = "UNKNOWN-V0.1"
        with self.assertRaisesRegex(ContractError, "ASSET_VERSION_MISMATCH"):
            validate_observable_fact_package(REPO_ROOT, result)


# ============================================================================
# Constraint Query Planning Tests
# ============================================================================

class TestPlanConstraintQuery(unittest.TestCase):
    """Tests for Task 1: CONSTRAINT-QUERY-REQUEST planning."""

    def test_basic_query_planning(self):
        pf_pkg = _make_penalty_fact_package()
        result = plan_constraint_query(pf_pkg)
        self.assertEqual(result["caller_agent_id"], _CALLER_AGENT_ID)
        self.assertEqual(result["caller_stage"], _CALLER_STAGE)
        self.assertEqual(result["query_purpose"], "constraint_lookup")
        self.assertIn("constraint", result["target_asset_types"])

    def test_custom_request_id(self):
        pf_pkg = _make_penalty_fact_package()
        result = plan_constraint_query(pf_pkg, request_id="REQ-CUSTOM")
        self.assertEqual(result["request_id"], "REQ-CUSTOM")

    def test_previous_request_refs(self):
        pf_pkg = _make_penalty_fact_package()
        result = plan_constraint_query(pf_pkg, previous_request_refs=["REQ-001"])
        self.assertEqual(result["previous_request_refs"], ["REQ-001"])

    def test_empty_facts_raises(self):
        pf_pkg = _make_penalty_fact_package(source_facts=[])
        with self.assertRaisesRegex(ContractError, "EMPTY_PENALTY_FACTS"):
            plan_constraint_query(pf_pkg)


# ============================================================================
# Observable Fact Building Tests
# ============================================================================

class TestBuildObservableFacts(unittest.TestCase):
    """Tests for Task 2: EAST-OBSERVABLE-FACT-PACKAGE building."""

    def test_build_with_direct_and_indirect(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        result = build_observable_facts(pf_pkg, ca_pkg)

        self.assertEqual(len(result["observable_facts"]), 2)
        self.assertIn(result["coverage_status"], COVERAGE_STATUSES)
        self.assertEqual(result["asset_version"], "CA-V0.3.0")
        self.assertEqual(
            result["east_observable_fact_package_schema_version"],
            "east-observable-fact-v1",
        )

        # fact-001 maps to "field" record -> direct
        self.assertEqual(result["observable_facts"][0]["observability_type"], "direct")
        # fact-002 maps to "relationship" record -> indirect
        self.assertEqual(result["observable_facts"][1]["observability_type"], "indirect")

    def test_build_with_unobservable(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package(matched_records=[])
        result = build_observable_facts(pf_pkg, ca_pkg)

        # All facts should be unobservable
        for obs in result["observable_facts"]:
            self.assertEqual(obs["observability_type"], "unobservable")
            self.assertIn("unobservable_parts", obs)

        # Coverage should be partial (must_preserve=yes facts are unobservable)
        self.assertEqual(result["coverage_status"], "partial")

    def test_coverage_complete(self):
        """When all facts are observable, coverage is complete."""
        pf_pkg = _make_penalty_fact_package()
        # Both facts have matched records
        ca_pkg = _make_constraint_asset_package()
        result = build_observable_facts(pf_pkg, ca_pkg)
        self.assertEqual(result["coverage_status"], "complete")

    def test_asset_version_mismatch_raises(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package(asset_version="UNKNOWN-V0.1")
        with self.assertRaisesRegex(ContractError, "ASSET_VERSION_MISMATCH"):
            build_observable_facts(pf_pkg, ca_pkg)

    def test_empty_penalty_facts_raises(self):
        pf_pkg = _make_penalty_fact_package(source_facts=[])
        ca_pkg = _make_constraint_asset_package()
        with self.assertRaisesRegex(ContractError, "EMPTY_PENALTY_FACTS"):
            build_observable_facts(pf_pkg, ca_pkg)

    def test_max_iteration_exceeded_raises(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        with self.assertRaisesRegex(ContractError, "MAX_ITERATION_EXCEEDED"):
            build_observable_facts(pf_pkg, ca_pkg, iteration=4)

    def test_observable_fact_ids_sequential(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        result = build_observable_facts(pf_pkg, ca_pkg)
        ids = [obs["observable_fact_id"] for obs in result["observable_facts"]]
        self.assertEqual(ids, ["obs-001", "obs-002"])

    def test_mapping_matrix_entries(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        result = build_observable_facts(pf_pkg, ca_pkg)

        # obs-001 should have mapping_matrix with fact-001 ref
        obs_001 = result["observable_facts"][0]
        self.assertTrue(any(
            e["penalty_fact_ref"] == "fact-001"
            for e in obs_001["mapping_matrix"]
        ))

    def test_constraint_asset_refs_populated(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        result = build_observable_facts(pf_pkg, ca_pkg)

        for obs in result["observable_facts"]:
            self.assertGreater(len(obs["constraint_asset_refs"]), 0)

    def test_risk_screening_boundary_for_indirect(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        result = build_observable_facts(pf_pkg, ca_pkg)

        # obs-002 is indirect, should have risk_screening_boundary
        obs_002 = result["observable_facts"][1]
        self.assertEqual(obs_002["observability_type"], "indirect")
        self.assertIn("risk_screening_boundary", obs_002)

    def test_unresolved_items_from_unmatched(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package(
            unmatched_items=[{"item_ref": "item-1", "reason": "未命中", "needs_human_review": False}]
        )
        result = build_observable_facts(pf_pkg, ca_pkg)
        self.assertTrue(any(
            u["item_ref"] == "item-1" for u in result["unresolved_items"]
        ))


# ============================================================================
# Review Feedback Tests
# ============================================================================

class TestReviewFeedback(unittest.TestCase):
    """Tests for Task 3: review feedback handling."""

    def test_review_feedback_rebuild(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        review = {
            "error_types": ["OBSERVABLE_MAPPING_ERROR"],
            "error_details": [],
        }
        result = handle_review_feedback(pf_pkg, ca_pkg, review, previous_iteration=1)
        self.assertIn(result["coverage_status"], COVERAGE_STATUSES)
        # Should have a review iteration unresolved item
        self.assertTrue(any(
            "review-iteration-2" in u.get("item_ref", "")
            for u in result.get("unresolved_items", [])
        ))

    def test_review_feedback_invalid_route(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        review = {
            "error_types": ["OTHER_ERROR"],
            "error_details": [],
        }
        with self.assertRaisesRegex(ContractError, "REVIEW_ROUTE_INVALID"):
            handle_review_feedback(pf_pkg, ca_pkg, review, previous_iteration=1)

    def test_max_iteration_exceeded_on_review(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        review = {
            "error_types": ["OBSERVABLE_MAPPING_ERROR"],
            "error_details": [],
        }
        with self.assertRaisesRegex(ContractError, "MAX_ITERATION_EXCEEDED"):
            handle_review_feedback(pf_pkg, ca_pkg, review, previous_iteration=3)


# ============================================================================
# ObservableFactMapper (Stateful) Tests
# ============================================================================

class TestObservableFactMapper(unittest.TestCase):
    """Tests for the stateful ObservableFactMapper class."""

    def test_full_pipeline(self):
        mapper = ObservableFactMapper(REPO_ROOT)

        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()

        # Validate inputs
        mapper.validate_penalty_input(pf_pkg)
        mapper.validate_constraint_input(ca_pkg)

        # Task 1: plan query
        query_req = mapper.plan_query(pf_pkg)
        self.assertEqual(query_req["caller_agent_id"], _CALLER_AGENT_ID)

        # Task 2: build
        result = mapper.build(pf_pkg, ca_pkg)
        self.assertEqual(len(result["observable_facts"]), 2)

        # Validate output
        mapper.validate_output(result)

    def test_re_map_pipeline(self):
        mapper = ObservableFactMapper(REPO_ROOT)

        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package()
        review = {
            "error_types": ["OBSERVABLE_MAPPING_ERROR"],
            "error_details": [],
        }

        result = mapper.re_map(pf_pkg, ca_pkg, review, iteration=1)
        self.assertIn(result["coverage_status"], COVERAGE_STATUSES)


# ============================================================================
# Constant and Edge Case Tests
# ============================================================================

class TestConstants(unittest.TestCase):
    """Tests for module-level constants."""

    def test_observability_types(self):
        self.assertEqual(OBSERVABILITY_TYPES, frozenset({"direct", "indirect", "unobservable"}))

    def test_coverage_statuses(self):
        self.assertEqual(COVERAGE_STATUSES, frozenset({"complete", "partial", "blocked"}))

    def test_max_iterations(self):
        self.assertEqual(MAX_ITERATIONS, 3)

    def test_caller_agent_id(self):
        self.assertEqual(_CALLER_AGENT_ID, "130")

    def test_caller_stage(self):
        self.assertEqual(_CALLER_STAGE, "observable_fact")


class TestEdgeCases(unittest.TestCase):
    """Tests for boundary and error conditions."""

    def test_single_fact(self):
        pf_pkg = _make_penalty_fact_package(source_facts=[
            {
                "penalty_fact_id": "fact-001",
                "fact_type": "subject",
                "structured_fact": {
                    "subject": "某银行", "predicate": "被处罚单位",
                    "object": "某银行", "qualifier": None, "value": None,
                },
                "original_text": "某银行",
                "source_span_refs": [],
                "must_preserve_in_question": "yes",
            },
        ])
        ca_pkg = _make_constraint_asset_package()
        result = build_observable_facts(pf_pkg, ca_pkg)
        self.assertEqual(len(result["observable_facts"]), 1)

    def test_trg_asset_version(self):
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package(asset_version="TRG-V1.0.0")
        result = build_observable_facts(pf_pkg, ca_pkg)
        self.assertEqual(result["asset_version"], "TRG-V1.0.0")

    def test_no_matched_records_no_source_refs_overlap(self):
        """When matched records don't reference the fact IDs, all facts are unobservable."""
        pf_pkg = _make_penalty_fact_package()
        ca_pkg = _make_constraint_asset_package(matched_records=[
            {
                "record_type": "field",
                "data": {
                    "table_id": "EAST_D001",
                    "field_id": "field_999",
                    "proxy_expression": "不相关",
                    "table_field_path": "EAST_D001.field_999",
                    "asset_evidence_ref": "ref-999",
                },
                "source_refs": ["fact-999"],  # No overlap with fact-001/fact-002
                "hierarchy_refs": [],
            },
        ])
        result = build_observable_facts(pf_pkg, ca_pkg)
        # All facts should be unobservable since no records match their IDs
        for obs in result["observable_facts"]:
            self.assertEqual(obs["observability_type"], "unobservable")


if __name__ == "__main__":
    unittest.main()
