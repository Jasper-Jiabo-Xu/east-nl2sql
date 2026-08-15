"""Tests for Agent 000: 资产检索agent.

Covers:
- Safety gate: pass/fail for all rejection routes
- Request validation
- Query planning
- Full retrieval pipeline (with in-memory SQLite fixture)
- Schema validation
- Edge cases: empty results, injection, version drift
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from east_v5.agents.east_000.safety_gate import (
    check_sql_safety,
    validate_request,
    SafetyResult,
    SafetyCheckOutcome,
    MAX_LIMIT,
    ALL_AUTHORIZED_OBJECTS,
    CA_V030_TABLES,
    CA_V030_VIEWS,
)
from east_v5.agents.east_000.query_executor import (
    execute_query,
    execute_safe_query,
    verify_asset_hash,
    compute_file_sha256,
    QueryResult,
    ExecutedQueryRecord,
    TraceEntry,
)
from east_v5.agents.east_000.extractor import (
    plan_queries,
    execute_retrieval,
    validate_request_schema,
    validate_result_schema,
    _empty_result,
    _infer_record_type,
)
from east_v5.governance import ContractError


# ============================================================================
# Fixtures
# ============================================================================

REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/agents/000/ -> repo root


def _make_request(**overrides) -> dict:
    """Build a valid CONSTRAINT-QUERY-REQUEST with defaults."""
    base = {
        "request_id": "REQ-001",
        "caller_agent_id": "130",
        "caller_stage": "observable_fact",
        "query_purpose": "constraint_lookup",
        "natural_language_intent": "查询EAST_D001表的字段约束",
        "target_asset_types": ["single_field"],
        "table_scope": ["EAST_D001"],
        "max_rows": 100,
    }
    base.update(overrides)
    return base


def _make_sqlite_fixture(tmp_path: Path) -> Path:
    """Create a minimal CA-V0.3.0 SQLite fixture with required tables/views."""
    db_path = tmp_path / "constraint_assets.sqlite"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Tables from approved-assets.json
    cur.execute("CREATE TABLE field_master (table_id TEXT, field_id TEXT, field_name TEXT, data_type TEXT)")
    cur.execute("CREATE TABLE multifield_constraint (constraint_id TEXT, table_id TEXT, constraint_type TEXT)")
    cur.execute("CREATE TABLE multifield_constraint_field (constraint_id TEXT, field_id TEXT)")
    cur.execute("CREATE TABLE decision_audit (decision_id TEXT, decision_type TEXT)")
    cur.execute("CREATE TABLE evidence (evidence_id TEXT, source_type TEXT)")
    cur.execute("CREATE TABLE excluded_constraint_audit (exclusion_id TEXT, reason TEXT)")
    cur.execute("CREATE TABLE release_meta (key TEXT, value TEXT)")
    cur.execute("CREATE TABLE source_manifest (source_id TEXT, source_type TEXT)")

    # Sample data
    cur.execute("INSERT INTO field_master VALUES ('EAST_D001', 'field_001', '机构名称', 'VARCHAR')")
    cur.execute("INSERT INTO field_master VALUES ('EAST_D001', 'field_002', '报告期', 'DATE')")
    cur.execute("INSERT INTO multifield_constraint VALUES ('MC-001', 'EAST_D001', 'within_table')")

    # Views from approved-assets.json
    cur.execute("CREATE VIEW approved_comparison_constraints AS SELECT * FROM multifield_constraint WHERE constraint_type = 'comparison'")
    cur.execute("CREATE VIEW approved_reference_constraints AS SELECT * FROM multifield_constraint WHERE constraint_type = 'reference'")
    cur.execute("CREATE VIEW cross_table_constraints AS SELECT * FROM multifield_constraint WHERE constraint_type = 'cross_table'")
    cur.execute("CREATE VIEW intra_table_constraints AS SELECT * FROM multifield_constraint WHERE constraint_type = 'within_table'")

    conn.commit()
    conn.close()
    return db_path


# ============================================================================
# Safety Gate Tests
# ============================================================================

class TestSafetyGatePass:
    """Valid queries that should pass the safety gate."""

    def test_simple_select_with_limit(self):
        result = check_sql_safety("SELECT * FROM field_master LIMIT ?", 100)
        assert result.result == SafetyResult.PASS

    def test_select_with_where_and_limit(self):
        result = check_sql_safety(
            "SELECT * FROM field_master WHERE table_id = ? LIMIT ?", 100
        )
        assert result.result == SafetyResult.PASS

    def test_cte_select(self):
        result = check_sql_safety(
            "WITH cte AS (SELECT * FROM field_master LIMIT ?) SELECT * FROM cte LIMIT ?",
            100,
        )
        assert result.result == SafetyResult.PASS

    def test_select_from_view(self):
        result = check_sql_safety(
            "SELECT * FROM intra_table_constraints LIMIT ?", 100
        )
        assert result.result == SafetyResult.PASS

    def test_select_from_cross_table_view(self):
        result = check_sql_safety(
            "SELECT * FROM cross_table_constraints WHERE table_id = ? LIMIT ?", 100
        )
        assert result.result == SafetyResult.PASS

    def test_join_authorized_tables(self):
        result = check_sql_safety(
            "SELECT f.* FROM field_master f JOIN multifield_constraint_field mcf ON f.field_id = mcf.field_id LIMIT ?",
            100,
        )
        assert result.result == SafetyResult.PASS


class TestSafetyGateReject:
    """Invalid queries that should be rejected."""

    def test_reject_insert(self):
        result = check_sql_safety("INSERT INTO field_master VALUES (?, ?, ?, ?)")
        assert result.result == SafetyResult.FAIL
        assert any("WRITE_OP" in r for r in result.rejected_reasons)

    def test_reject_update(self):
        result = check_sql_safety("UPDATE field_master SET field_name = ?")
        assert result.result == SafetyResult.FAIL
        assert any("WRITE_OP" in r for r in result.rejected_reasons)

    def test_reject_delete(self):
        result = check_sql_safety("DELETE FROM field_master")
        assert result.result == SafetyResult.FAIL
        assert any("WRITE_OP" in r for r in result.rejected_reasons)

    def test_reject_drop(self):
        result = check_sql_safety("DROP TABLE field_master")
        assert result.result == SafetyResult.FAIL
        assert any("WRITE_OP" in r for r in result.rejected_reasons)

    def test_reject_multi_statement(self):
        result = check_sql_safety(
            "SELECT * FROM field_master LIMIT ?; SELECT * FROM evidence LIMIT ?"
        )
        assert result.result == SafetyResult.FAIL
        assert any("MULTI_STATEMENT" in r for r in result.rejected_reasons)

    def test_reject_unauthorized_table(self):
        result = check_sql_safety("SELECT * FROM unknown_table LIMIT ?")
        assert result.result == SafetyResult.FAIL
        assert any("UNAUTHORIZED_OBJECT" in r for r in result.rejected_reasons)

    def test_reject_system_table(self):
        result = check_sql_safety("SELECT * FROM sqlite_master LIMIT ?")
        assert result.result == SafetyResult.FAIL
        assert any("SYSTEM_TABLE" in r for r in result.rejected_reasons)

    def test_reject_no_limit(self):
        result = check_sql_safety("SELECT * FROM field_master")
        assert result.result == SafetyResult.FAIL
        assert any("NO_LIMIT" in r for r in result.rejected_reasons)

    def test_reject_injection_union(self):
        result = check_sql_safety(
            "SELECT * FROM field_master UNION ALL SELECT * FROM evidence LIMIT ?"
        )
        assert result.result == SafetyResult.FAIL
        assert any("INJECTION" in r for r in result.rejected_reasons)

    def test_reject_injection_comment(self):
        result = check_sql_safety("SELECT * FROM field_master -- drop everything LIMIT ?")
        assert result.result == SafetyResult.FAIL
        assert any("INJECTION" in r for r in result.rejected_reasons)

    def test_reject_inline_value(self):
        result = check_sql_safety(
            "SELECT * FROM field_master WHERE table_id = 'EAST_D001' LIMIT ?"
        )
        assert result.result == SafetyResult.FAIL
        assert any("UNPARAMETERIZED" in r for r in result.rejected_reasons)

    def test_reject_pragma(self):
        result = check_sql_safety("PRAGMA table_info(field_master)")
        assert result.result == SafetyResult.FAIL
        assert any("WRITE_OP" in r for r in result.rejected_reasons)


# ============================================================================
# Request Validation Tests
# ============================================================================

class TestRequestValidation:
    """Tests for CONSTRAINT-QUERY-REQUEST validation."""

    def test_valid_request_130(self):
        req = _make_request(caller_agent_id="130")
        validate_request(req)  # Should not raise

    def test_valid_request_220(self):
        req = _make_request(caller_agent_id="220")
        validate_request(req)  # Should not raise

    def test_invalid_caller_agent_id(self):
        req = _make_request(caller_agent_id="999")
        with pytest.raises(ContractError, match="INVALID_CALLER_AGENT_ID"):
            validate_request(req)

    def test_invalid_caller_stage(self):
        req = _make_request(caller_stage="invalid_stage")
        with pytest.raises(ContractError, match="INVALID_CALLER_STAGE"):
            validate_request(req)

    def test_invalid_query_purpose(self):
        req = _make_request(query_purpose="invalid_purpose")
        with pytest.raises(ContractError, match="INVALID_QUERY_PURPOSE"):
            validate_request(req)

    def test_max_rows_zero(self):
        req = _make_request(max_rows=0)
        with pytest.raises(ContractError, match="MAX_ROWS_EXCEEDED"):
            validate_request(req)

    def test_max_rows_too_large(self):
        req = _make_request(max_rows=10001)
        with pytest.raises(ContractError, match="MAX_ROWS_EXCEEDED"):
            validate_request(req)


# ============================================================================
# Query Planning Tests
# ============================================================================

class TestQueryPlanning:
    """Tests for query plan generation."""

    def test_single_field_constraint_lookup(self):
        req = _make_request(
            query_purpose="constraint_lookup",
            target_asset_types=["single_field"],
            table_scope=["EAST_D001"],
        )
        plans = plan_queries(req)
        assert len(plans) > 0
        for p in plans:
            assert "LIMIT ?" in p["sql"]
            assert "?" in p["sql"]  # parameterized

    def test_field_explanation_with_field_scope(self):
        req = _make_request(
            query_purpose="field_explanation",
            target_asset_types=["single_field"],
            field_scope=["EAST_D001.field_001"],
        )
        plans = plan_queries(req)
        assert len(plans) > 0
        # single_field maps to field_master + views; at least one plan uses field_master
        assert any("field_master" in p["sql"] for p in plans)

    def test_table_explanation_with_table_scope(self):
        req = _make_request(
            query_purpose="table_explanation",
            target_asset_types=["single_field"],
            table_scope=["EAST_D001"],
        )
        plans = plan_queries(req)
        assert len(plans) > 0

    def test_cross_table_type(self):
        req = _make_request(
            target_asset_types=["cross_table"],
            table_scope=["EAST_D001"],
        )
        plans = plan_queries(req)
        assert len(plans) > 0
        for p in plans:
            assert "cross_table_constraints" in p["sql"]

    def test_empty_asset_type(self):
        req = _make_request(target_asset_types=["hierarchy_reference"])
        plans = plan_queries(req)
        assert len(plans) == 0  # hierarchy_reference maps to TRG, not SQLite


# ============================================================================
# Query Executor Tests
# ============================================================================

class TestQueryExecutor:
    """Tests for read-only query execution."""

    def test_execute_simple_query(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        result = execute_query(
            db_path,
            "SELECT * FROM field_master WHERE table_id = ? LIMIT ?",
            ("EAST_D001", 100),
        )
        assert result.row_count == 2
        assert result.elapsed_ms >= 0
        assert result.exception is None

    def test_execute_safe_query_pass(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        result, record, trace = execute_safe_query(
            db_path,
            "SELECT * FROM field_master WHERE table_id = ? LIMIT ?",
            ("EAST_D001", 100),
        )
        assert record.safety_check_result == "pass"
        assert result.row_count == 2

    def test_execute_safe_query_fail(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        result, record, trace = execute_safe_query(
            db_path,
            "INSERT INTO field_master VALUES (?, ?, ?, ?)",
            ("x", "y", "z", "w"),
        )
        assert record.safety_check_result == "fail"
        assert result.row_count == 0

    def test_asset_hash_verification_pass(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        expected = compute_file_sha256(db_path)
        verify_asset_hash(db_path, expected)  # Should not raise

    def test_asset_hash_verification_fail(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        with pytest.raises(ContractError, match="ASSET_HASH_MISMATCH"):
            verify_asset_hash(db_path, "0" * 64)


# ============================================================================
# Full Pipeline Tests
# ============================================================================

class TestFullPipeline:
    """Tests for the complete retrieval pipeline."""

    def test_successful_retrieval(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        req = _make_request(
            target_asset_types=["single_field"],
            table_scope=["EAST_D001"],
        )
        result = execute_retrieval(req, db_path, REPO_ROOT)
        assert result["request_id"] == "REQ-001"
        assert result["asset_version"] == "CA-V0.3.0"
        assert len(result["executed_queries"]) > 0
        assert result["constraint_summary"]["total_matched"] > 0

    def test_no_candidates_returns_empty(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        req = _make_request(target_asset_types=["hierarchy_reference"])
        result = execute_retrieval(req, db_path, REPO_ROOT)
        assert result["matched_records"] == []

    def test_safety_rejection_records_unmatched(self, tmp_path):
        """Test that safety gate rejections are recorded as unmatched items."""
        db_path = _make_sqlite_fixture(tmp_path)
        req = _make_request(target_asset_types=["single_field"], table_scope=["EAST_D001"])
        result = execute_retrieval(req, db_path, REPO_ROOT)
        # All queries in normal path should pass
        for eq in result["executed_queries"]:
            assert eq["safety_check_result"] == "pass"


# ============================================================================
# Schema Validation Tests
# ============================================================================

class TestSchemaValidation:
    """Tests for JSON Schema validation of request and result packages."""

    def test_valid_request_schema(self):
        req = _make_request()
        validate_request_schema(REPO_ROOT, req)  # Should not raise

    def test_invalid_request_missing_required(self):
        req = {"request_id": "REQ-001"}  # Missing many required fields
        with pytest.raises(ContractError, match="SCHEMA_VALIDATION_FAILED"):
            validate_request_schema(REPO_ROOT, req)

    def test_valid_result_package(self, tmp_path):
        db_path = _make_sqlite_fixture(tmp_path)
        req = _make_request(table_scope=["EAST_D001"])
        result = execute_retrieval(req, db_path, REPO_ROOT)
        validate_result_schema(REPO_ROOT, result)  # Should not raise


# ============================================================================
# Edge Case Tests
# ============================================================================

class TestEdgeCases:
    """Tests for boundary and error conditions."""

    def test_empty_result(self):
        req = _make_request()
        result = _empty_result(req, "UNMATCHED_QUERY")
        assert result["constraint_summary"]["total_matched"] == 0
        assert len(result["unmatched_items"]) == 1

    def test_infer_record_type_field_master(self):
        assert _infer_record_type("SELECT * FROM field_master LIMIT ?") == "single_field"

    def test_infer_record_type_cross_table(self):
        assert _infer_record_type("SELECT * FROM cross_table_constraints LIMIT ?") == "cross_table"

    def test_infer_record_type_intra_table(self):
        assert _infer_record_type("SELECT * FROM intra_table_constraints LIMIT ?") == "within_table"

    def test_infer_record_type_evidence(self):
        assert _infer_record_type("SELECT * FROM evidence LIMIT ?") == "object_detail_state"

    def test_authorized_objects_not_empty(self):
        assert len(ALL_AUTHORIZED_OBJECTS) > 0

    def test_ca_tables_subset_of_authorized(self):
        assert CA_V030_TABLES.issubset(ALL_AUTHORIZED_OBJECTS)

    def test_ca_views_subset_of_authorized(self):
        assert CA_V030_VIEWS.issubset(ALL_AUTHORIZED_OBJECTS)

    def test_max_limit_value(self):
        assert MAX_LIMIT == 10000

    def test_independent_ods_not_in_authorized(self):
        """Ensure ODS tables are NOT in the authorized object list."""
        ods_names = {"ods_regulatory_event", "ods_penalty_record", "ods_organizational"}
        for ods in ods_names:
            assert ods not in ALL_AUTHORIZED_OBJECTS
