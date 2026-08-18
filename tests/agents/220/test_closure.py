from __future__ import annotations

import copy
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256

mod = importlib.import_module("east_v5.agents.220.closure")
coordinator_mod = importlib.import_module("east_v5.agents.210.scheduler")
try:
    helper = importlib.import_module("tests.agents.210.test_scheduler")
except ModuleNotFoundError:
    helper = importlib.import_module("agents.210.test_scheduler")


def event_source(*, run_id: str = "220-run") -> tuple[dict, dict, dict]:
    """A real 140+110→210 source; the test never hand-writes 210 output."""
    approved = helper.dual_review()
    approved["envelope"].update({"run_id": run_id, "qa_id": "QA-220", "trace_id": "220-trace"})
    approved["payload"]["candidate_content"].update({
        "sql_gold": "SELECT T1.F001, T2.PK001 FROM FIXTURE_T001 T1 JOIN FIXTURE_T002 T2 ON T1.F001 = T2.PK001",
        "specification_mapping": [
            {"spec_item": "left", "question_fragment": "left", "sql_fragment": "T1.F001"},
            {"spec_item": "right", "question_fragment": "right", "sql_fragment": "T2.PK001"},
        ],
        "business_event_candidates": [{"event_name": "join", "objective": "join", "objects": ["left", "right"], "state_changes": ["FIXTURE_T001.F001->FIXTURE_T002.PK001"]}],
    })
    approved["payload"]["package_hash"] = sha256({key: value for key, value in approved["payload"].items() if key != "package_hash"})
    approved["envelope"]["content_hash"] = content_hash(approved["envelope"], approved["payload"])
    spec = helper.query_spec_for(approved)
    spec["payload"].update({"query_spec_id": "qspec-220", "sql_schema_scope": {"allowed_tables": [{"table_id": "FIXTURE_T001", "allowed_fields": ["F001"]}, {"table_id": "FIXTURE_T002", "allowed_fields": ["PK001"]}]}, "return_fields": [{"field_id": "F001", "display_name": "left", "source_table": "FIXTURE_T001"}, {"field_id": "PK001", "display_name": "right", "source_table": "FIXTURE_T002"}]})
    spec["envelope"]["content_hash"] = content_hash(spec["envelope"], spec["payload"])
    helper.bind_query_spec(approved, spec)
    started = coordinator_mod.DataStageCoordinator(ROOT).begin_event(approved, spec)
    return started["reviewed_question_sql"], started["event_query_context"], spec


def asset(source: dict, request: dict, records: list[dict], parent: dict) -> dict:
    envelope = {"artifact_id": f"asset:{request['request_id']}", "artifact_type": "constraint_asset_package", "run_id": source["run_id"], "qa_id": source["qa_id"], "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": source["attempt_no"], "producer_id": "000", "parent_artifact_refs": [parent], "input_hashes": [parent["content_hash"]], "status": "candidate", "mode": source["mode"], "created_at": source["created_at"], "trace_id": source["trace_id"], "storage_locator": None}
    payload = {"request_id": request["request_id"], "asset_version": "CA-V0.3.0", "executed_queries": [], "matched_records": records, "constraint_summary": {"total_matched": len(records), "asset_types_covered": [record["record_type"] for record in records]}, "unmatched_items": [], "query_trace": []}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def event_results(reviewed: dict, context: dict) -> tuple[list[dict], dict, dict]:
    requests = mod.event_query_rounds(reviewed, context)
    source = reviewed["envelope"]
    first = asset(source, requests[0], [{"record_type": "single_field", "data": {"table_id": "FIXTURE_T001", "field_id": "F001"}, "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": []}], mod._ref_for_request(requests[0]))
    second = asset(source, requests[1], [{"record_type": "cross_table", "data": {"from": "FIXTURE_T001.F001", "to": "FIXTURE_T002.PK001"}, "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": []}], artifact_ref(first["envelope"]))
    return requests, first, second


class ClosureTests(unittest.TestCase):
    def test_real_210_alias_projection_is_the_only_event_seed(self):
        reviewed, context, _ = event_source()
        requests, first, second = event_results(reviewed, context)
        closure = mod.build_event_closure(reviewed, context, first, second)
        self.assertEqual(context["payload"]["field_projection"], [{"spec_item": "left", "fields": ["FIXTURE_T001.F001"]}, {"spec_item": "right", "fields": ["FIXTURE_T002.PK001"]}])
        self.assertEqual(requests[0]["field_scope"], ["FIXTURE_T001.F001", "FIXTURE_T002.PK001"])
        self.assertIn(artifact_ref(context["envelope"]), closure["envelope"]["parent_artifact_refs"])
        self.assertEqual(mod.consume_downstream_stub("260", closure)["consumer"], "260")

    def test_missing_context_hash_and_trace_drift_are_hard_rejected(self):
        reviewed, context, _ = event_source()
        with self.assertRaises(TypeError):
            mod.event_query_rounds(reviewed)  # type: ignore[call-arg]
        bad = copy.deepcopy(context); bad["payload"]["projection_hash"] = "0" * 64; bad["envelope"]["content_hash"] = content_hash(bad["envelope"], bad["payload"])
        with self.assertRaisesRegex(ContractError, "EVENT_CONTEXT_HASH_DRIFT"):
            mod.event_query_rounds(reviewed, bad)
        bad = copy.deepcopy(context); bad["envelope"]["trace_id"] = "other"; bad["envelope"]["content_hash"] = content_hash(bad["envelope"], bad["payload"])
        with self.assertRaisesRegex(ContractError, "EVENT_CONTEXT_LINEAGE_MISMATCH"):
            mod.event_query_rounds(reviewed, bad)

    def test_replay_is_byte_stable_and_context_projection_tamper_is_rejected(self):
        reviewed, context, _ = event_source()
        _, first, second = event_results(reviewed, context)
        self.assertEqual(mod.build_event_closure(reviewed, context, first, second), mod.build_event_closure(reviewed, context, first, second))
        tampered = copy.deepcopy(context); tampered["envelope"]["parent_artifact_refs"] = tampered["envelope"]["parent_artifact_refs"][:2]; tampered["envelope"]["input_hashes"] = [item["content_hash"] for item in tampered["envelope"]["parent_artifact_refs"]]; tampered["envelope"]["content_hash"] = content_hash(tampered["envelope"], tampered["payload"])
        with self.assertRaisesRegex(ContractError, "EVENT_CONTEXT_PARENT_LINEAGE_REJECTED"):
            mod.event_query_rounds(reviewed, tampered)

    def test_self_consistent_source_question_ref_tamper_is_rejected(self):
        reviewed, context, _ = event_source()
        tampered = copy.deepcopy(context)
        tampered["payload"]["source_question_sql_ref"] = {"artifact_id": "other-approved-question-sql", "version": 1, "content_hash": "f" * 64}
        tampered["payload"]["projection_hash"] = sha256({key: value for key, value in tampered["payload"].items() if key != "projection_hash"})
        tampered["envelope"]["parent_artifact_refs"] = [tampered["payload"][key] for key in ("source_query_spec_ref", "source_question_sql_ref", "reviewed_question_sql_ref")]
        tampered["envelope"]["input_hashes"] = [ref["content_hash"] for ref in tampered["envelope"]["parent_artifact_refs"]]
        tampered["envelope"]["content_hash"] = content_hash(tampered["envelope"], tampered["payload"])
        with self.assertRaisesRegex(ContractError, "EVENT_CONTEXT_SOURCE_QUESTION_LINEAGE_REJECTED"):
            mod.event_query_rounds(reviewed, tampered)


if __name__ == "__main__":
    unittest.main()
