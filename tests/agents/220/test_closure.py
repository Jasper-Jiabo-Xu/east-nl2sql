from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(ROOT / "src"))
mod = importlib.import_module("east_v5.agents.220.closure")
build_closure = mod.build_closure
from east_v5.artifacts import content_hash
from east_v5.governance import ContractError


PROFILE = {"schema_version": "v5.foundation-profile/v1", "base_database_version": "v1", "target_classes": ["CUSTOMER"], "target_counts": {}, "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0"}


def fixture() -> dict:
    return json.loads((FIXTURES / "event-data-dual-review.json").read_text(encoding="utf-8"))


def rehash(package: dict) -> None:
    package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])


class ClosureTests(unittest.TestCase):
    def test_foundation_expands_readonly_assets(self):
        result = build_closure(PROFILE, [{"asset_version": "CA-V0.3.0", "matched_records": [{"record_type": "single_field", "data": {"table_id": "ACCOUNT", "field_id": "ID"}}]}])
        self.assertEqual(result["tables"], ["ACCOUNT", "CUSTOMER"])
        self.assertEqual(result["fields"], ["ACCOUNT.ID"])

    def test_event_owned_rejected(self):
        with self.assertRaisesRegex(ContractError, "FOUNDATION_EVENT_OWNED_REJECTED"):
            build_closure({**PROFILE, "target_classes": ["EVENT_OWNED:TX"]}, [])

    def test_version_drift_rejected(self):
        with self.assertRaisesRegex(ContractError, "ASSET_VERSION_DRIFT"):
            build_closure(PROFILE, [{"asset_version": "x", "matched_records": []}])

    def test_event_two_rounds_preserve_schema_hashes_and_lineage(self):
        data = fixture()
        requests = mod.event_query_rounds(data["event"], data["first_result"])
        self.assertEqual(requests[0]["field_scope"], ["FIXTURE_T001.F001"])
        self.assertEqual(requests[1]["previous_request_refs"], [
            {key: data["first_result"]["envelope"][key] for key in ("artifact_id", "version", "content_hash")}
        ])
        mod.validate_second_event_result(data["second_result"], data["first_result"])
        closure = mod.build_event_closure(data["event"], data["first_result"], data["second_result"])
        self.assertEqual(closure["tables"], ["FIXTURE_T001", "FIXTURE_T002"])
        self.assertEqual(closure["fields"], ["FIXTURE_T001.F001", "FIXTURE_T002.PK001"])

    def test_event_and_foundation_downstream_routes(self):
        closure = build_closure(PROFILE, [])
        for consumer in ("230", "241", "251", "252", "260"):
            self.assertEqual(mod.consume_downstream_stub("event_data", consumer, closure)["consumer"], consumer)
        self.assertEqual(mod.consume_downstream_stub("foundation", "241", closure)["mode"], "foundation")
        for consumer in ("230", "251", "252"):
            with self.assertRaisesRegex(ContractError, "FOUNDATION_DOWNSTREAM_REJECTED"):
                mod.consume_downstream_stub("foundation", consumer, closure)

    def test_event_rejects_bad_hash_unknown_field_and_asset_version_drift(self):
        data = fixture()
        bad_hash = copy.deepcopy(data["event"])
        bad_hash["payload"]["sql_gold"] = "SELECT altered"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            mod.event_query_rounds(bad_hash, data["first_result"])

        unknown = copy.deepcopy(data["event"])
        unknown["payload"]["unexpected"] = True
        rehash(unknown)
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED"):
            mod.event_query_rounds(unknown, data["first_result"])

        drift = copy.deepcopy(data["first_result"])
        drift["payload"]["asset_version"] = "TRG-V1.0.0"
        rehash(drift)
        with self.assertRaisesRegex(ContractError, "ASSET_VERSION_DRIFT"):
            mod.event_query_rounds(data["event"], drift)

    def test_second_result_requires_parent_and_third_attempt_is_blocked(self):
        data = fixture()
        missing_parent = copy.deepcopy(data["second_result"])
        missing_parent["envelope"]["parent_artifact_refs"] = []
        missing_parent["envelope"]["input_hashes"] = []
        rehash(missing_parent)
        with self.assertRaisesRegex(ContractError, "ASSET_RESULT_PARENT_MISSING"):
            mod.validate_second_event_result(missing_parent, data["first_result"])
        self.assertEqual(mod.retry_status(1), "candidate")
        self.assertEqual(mod.retry_status(3), "blocked_manual")
