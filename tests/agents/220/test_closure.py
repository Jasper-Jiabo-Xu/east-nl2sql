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
from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError


def event_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def foundation_fixture() -> dict:
    return json.loads((ROOT / "fixtures" / "artifacts" / "foundation-task-package-valid.json").read_text(encoding="utf-8"))


def rehash(package: dict) -> None:
    package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])


def asset(source: dict, request: dict, records: list[dict], parent: dict) -> dict:
    envelope = {
        "artifact_id": f"asset:{request['request_id']}", "artifact_type": "constraint_asset_package",
        "run_id": source["run_id"], "qa_id": source["qa_id"], "version": 1,
        "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": source["attempt_no"], "producer_id": "000",
        "parent_artifact_refs": [parent], "input_hashes": [parent["content_hash"]],
        "status": "candidate", "mode": source["mode"], "created_at": source["created_at"],
        "trace_id": source["trace_id"], "storage_locator": None,
    }
    payload = {
        "request_id": request["request_id"], "asset_version": "CA-V0.3.0", "executed_queries": [],
        "matched_records": records,
        "constraint_summary": {"total_matched": len(records), "asset_types_covered": [record["record_type"] for record in records]},
        "unmatched_items": [], "query_trace": [],
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def event_results(event: dict) -> tuple[list[dict], dict, dict]:
    requests = mod.event_query_rounds(event)
    source = event["envelope"]
    first = asset(source, requests[0], [{
        "record_type": "single_field", "data": {"table_id": requests[0]["table_scope"][0], "field_id": requests[0]["field_scope"][0].split(".", 1)[1]},
        "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": [],
    }], mod._ref_for_request(requests[0]))
    second = asset(source, requests[1], [{
        "record_type": "cross_table", "data": {"from": requests[1]["relationship_scope"][0].split("->", 1)[0], "to": requests[1]["relationship_scope"][0].split("->", 1)[1]},
        "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": [],
    }], artifact_ref(first["envelope"]))
    return requests, first, second


class ClosureTests(unittest.TestCase):
    def test_two_dynamic_event_fixtures_produce_distinct_requests_and_packages(self):
        first_event = event_fixture("event-data-dual-review.json")
        second_event = event_fixture("event-data-dynamic-second.json")
        first_requests, first_assets, first_relations = event_results(first_event)
        second_requests, second_assets, second_relations = event_results(second_event)
        first = mod.build_event_closure(first_event, first_assets, first_relations)
        second = mod.build_event_closure(second_event, second_assets, second_relations)
        self.assertEqual(first_requests[0]["request_id"], "eas29-fixture-run:220:1")
        self.assertEqual(second_requests[0]["request_id"], "eas29-other-run:220:1")
        self.assertEqual(mod.event_query_rounds(first_event, first_assets)[1]["previous_request_refs"], [artifact_ref(first_assets["envelope"])])
        self.assertEqual(first["payload"]["fields"], ["FIXTURE_T001.F001", "FIXTURE_T002.PK001"])
        self.assertIn("FIXTURE_ORDER.ORDER_ID", second["payload"]["fields"])
        self.assertNotEqual(first["envelope"]["content_hash"], second["envelope"]["content_hash"])
        self.assertEqual(first["envelope"]["input_hashes"], [ref["content_hash"] for ref in first["envelope"]["parent_artifact_refs"]])
        mod.validate_structure_closure_package(first)
        mod.validate_structure_closure_package(second)

    def test_event_rejects_hash_unknown_seed_and_lineage_drift(self):
        event = event_fixture("event-data-dual-review.json")
        bad_hash = copy.deepcopy(event); bad_hash["payload"]["sql_gold"] = "SELECT changed"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            mod.event_query_rounds(bad_hash)
        unknown = copy.deepcopy(event); unknown["payload"]["unexpected"] = True; rehash(unknown)
        with self.assertRaisesRegex(ContractError, "SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED"):
            mod.event_query_rounds(unknown)
        mismatch = copy.deepcopy(event); mismatch["payload"]["specification_mapping"] = [{"field": "OTHER_TABLE.OTHER_FIELD"}]
        mismatch["payload"]["package_hash"] = mod._hash({key: value for key, value in mismatch["payload"].items() if key != "package_hash"}); rehash(mismatch)
        with self.assertRaisesRegex(ContractError, "EVENT_SEED_SQL_MISMATCH"):
            mod.event_query_rounds(mismatch)
        _, asset_one, asset_two = event_results(event)
        for key, value in (("qa_id", "different"), ("run_id", "other-run"), ("trace_id", "other-trace"), ("producer_id", "999"), ("mode", "foundation")):
            drift = copy.deepcopy(asset_two); drift["envelope"][key] = value; rehash(drift)
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, "(ASSET_RESULT_(LINEAGE_MISMATCH|ENVELOPE_INVALID)|QA_ID_REQUIRED)"):
                mod.build_event_closure(event, asset_one, drift)

    def test_foundation_validates_full_profile_and_result_lineage(self):
        profile = foundation_fixture()
        request = {"request_id": "foundation-fixture:220:1"}
        result = asset(profile["envelope"], request, [{
            "record_type": "single_field", "data": {"table_id": "FIXTURE_CUSTOMER", "field_id": "CUSTOMER_ID"},
            "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": [],
        }], artifact_ref(profile["envelope"]))
        closure = mod.build_closure(profile, [result])
        self.assertEqual(closure["envelope"]["mode"], "foundation")
        self.assertEqual(closure["envelope"]["producer_id"], "220")
        self.assertIn("FIXTURE_CUSTOMER.CUSTOMER_ID", closure["payload"]["fields"])
        mod.validate_structure_closure_package(closure)

        for mutation in (
            lambda p: p["payload"].pop("target_counts"),
            lambda p: p["payload"].update({"unexpected": True}),
            lambda p: p["envelope"].update({"producer_id": "010"}),
        ):
            invalid = foundation_fixture(); mutation(invalid); rehash(invalid)
            with self.assertRaisesRegex(ContractError, "FOUNDATION_TASK_PACKAGE_SCHEMA_INVALID"):
                mod.build_closure(invalid, [])
        bad_run = copy.deepcopy(result); bad_run["envelope"]["run_id"] = "other-run"; rehash(bad_run)
        with self.assertRaisesRegex(ContractError, "ASSET_RESULT_LINEAGE_MISMATCH"):
            mod.build_closure(profile, [bad_run])
        for key, value in (("qa_id", "QA-other"), ("trace_id", "other-trace"), ("producer_id", "999"), ("mode", "event_data")):
            drift = copy.deepcopy(result); drift["envelope"][key] = value; rehash(drift)
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, "(ASSET_RESULT_(LINEAGE_MISMATCH|ENVELOPE_INVALID)|QA_ID_REQUIRED)"):
                mod.build_closure(profile, [drift])

    def test_downstream_stubs_consume_registered_package_and_foundation_route_is_restricted(self):
        event = event_fixture("event-data-dual-review.json")
        _, first, second = event_results(event)
        event_closure = mod.build_event_closure(event, first, second)
        for consumer in ("230", "241", "251", "252", "260"):
            self.assertEqual(mod.consume_downstream_stub(consumer, event_closure)["consumer"], consumer)
        profile = foundation_fixture()
        foundation_closure = mod.build_closure(profile, [])
        self.assertEqual(mod.consume_downstream_stub("241", foundation_closure)["mode"], "foundation")
        for consumer in ("230", "251", "252"):
            with self.assertRaisesRegex(ContractError, "FOUNDATION_DOWNSTREAM_REJECTED"):
                mod.consume_downstream_stub(consumer, foundation_closure)

    def test_third_attempt_is_blocked_manual_and_output_tampering_is_rejected(self):
        self.assertEqual(mod.retry_status(3), "blocked_manual")
        event = event_fixture("event-data-dual-review.json")
        _, first, second = event_results(event)
        closure = mod.build_event_closure(event, first, second)
        closure["payload"]["fields"].append("TAMPER.FIELD")
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            mod.consume_downstream_stub("230", closure)
