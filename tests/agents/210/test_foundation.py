from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.artifacts import artifact_ref
from east_v5.governance import ContractError

producer = importlib.import_module("east_v5.agents.210.foundation")


class FoundationTaskProducerTests(unittest.TestCase):
    def test_produces_complete_task_and_deterministic_profile(self):
        task = producer.build_foundation_task_package({"schema_version": "v5.foundation-task-package/v1", "foundation_task_id": "eas60-210", "foundation_mode": "initial_seed", "trigger_reason": "sanitized", "target_database_version": "fixture-db-v1", "target_object_types": ["FIXTURE_CUSTOMER"], "target_table_field_scope": {"FIXTURE_CUSTOMER": ["C001"]}, "target_counts": {"FIXTURE_CUSTOMER": 1}, "distribution_targets": {"FIXTURE_CUSTOMER": {"default": 1}}, "hierarchy_asset_refs": [{"artifact_id": "TRG-V1.0.0", "version": 1, "content_hash": "b" * 64}], "prohibited_record_types": ["EVENT_OWNED"], "resume_qa_ref": None, "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0"}, run_id="run", trace_id="trace", created_at="2026-08-15T00:00:00+00:00", parents=[])
        profile = producer.build_foundation_profile(task)
        self.assertEqual(profile["payload"]["foundation_task_ref"], artifact_ref(task["envelope"]))
        self.assertEqual(profile["payload"]["target_counts"], task["payload"]["target_counts"])

    def test_rejects_noncomplete_or_scope_drifting_intent(self):
        with self.assertRaisesRegex(ContractError, "FOUNDATION_TASK_PACKAGE_SCHEMA_INVALID"):
            producer.build_foundation_task_package({"foundation_task_id": "missing"}, run_id="run", trace_id="trace", created_at="2026-08-15T00:00:00+00:00", parents=[])
