from __future__ import annotations

import copy
import importlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, sha256
from east_v5.validators import REGISTRY_SCHEMA_VERSION
try:
    from constraint_assets.published_assets import PublishedTrgRuntime
except ModuleNotFoundError:  # direct module execution from the repository root
    from tests.constraint_assets.published_assets import PublishedTrgRuntime

_probe = importlib.import_module("east_v5.agents.242.probe")
_validator = importlib.import_module("east_v5.agents.242.validator")
_resolver_mod = importlib.import_module("east_v5.agents.242.resolver")
_fixture = importlib.import_module("east_v5.agents.242.sanitized_fixture")
try:
    _stub = importlib.import_module("agents.242.approved_260_stub")
except ModuleNotFoundError:
    _stub = importlib.import_module("tests.agents.242.approved_260_stub")

DataValidator = _validator.DataValidator
AssetBoundResolver = _resolver_mod.AssetBoundResolver
ConstraintAssetQueryService = _resolver_mod.ConstraintAssetQueryService
verify_query_receipts = _resolver_mod.verify_query_receipts
universe_content_hash = _resolver_mod.universe_content_hash
SanitizedRuntime = _fixture.SanitizedRuntime
PAGINATION_TOTAL = _fixture.PAGINATION_TOTAL

_structure = _probe._structure
_foundation_structure = _probe._foundation_structure
_operation = _probe._operation
_bound_data = _probe._bound_data
_record = _probe._record
_field_value = _probe._field_value
_wrap = _probe._wrap
_valid_event_records = _probe._valid_event_records
_valid_foundation_records = _probe._valid_foundation_records


def _big_structure() -> dict:
    payload = {
        "schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0", "tables": ["BIG"],
        "fields": ["BIG.F1", "BIG.F2"], "references": [],
    }
    return _wrap("structure_closure", "eas32-big-structure", payload, producer="220", mode="event_data", qa_id="QA-EAS32")


def _graph_edge() -> dict:
    edge = {
        "source_table": "FIXTURE_T002", "source_field": "FIXTURE_T002.PK001",
        "target_table": "FIXTURE_T001", "target_field": "FIXTURE_T001.F001",
        "edge_type": "REFERENCE",
        "expression": {
            "direction": "PROVIDER_TO_CONSUMER",
            "provider_fields": ["FIXTURE_T002.PK001"],
            "consumer_field": "FIXTURE_T001.F001",
        },
    }
    edge["canonical_edge_hash"] = sha256(edge)
    return edge


class _ResolveDriftResolver(AssetBoundResolver):
    def resolve(self, constraint_id, scope):
        rule, content = super().resolve(constraint_id, scope)
        modified = copy.deepcopy(content)
        if "structured_expression_json" in modified:
            modified["structured_expression_json"] = "{}"
        elif "value_json" in modified:
            modified["value_json"] = "{}"
        return rule, modified


class DataValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = SanitizedRuntime()
        self.resolver: AssetBoundResolver = self.runtime.resolver()
        self.approved = self.resolver.sources
        self.validator = DataValidator(ROOT)
        self.structure = _structure()
        self.operation = _operation()

    def tearDown(self) -> None:
        self.runtime.close()

    def _event(self):
        return _bound_data(self.structure, operation=self.operation, records=_valid_event_records())

    def _foundation(self):
        return _bound_data(_foundation_structure(), records=_valid_foundation_records())

    # ------------------------------------------------------------ success path

    def test_freeze_contract_hash_and_260_stub(self) -> None:
        event = self._event()
        frozen = self.validator.freeze_bound_data(event, self.structure, self.resolver)
        payload = frozen["payload"]
        self.assertEqual(payload["schema_version"], "v5.verified-bound-data/v1")
        self.assertEqual(payload["validated_hash"], sha256(event["payload"]))
        self.assertEqual(payload["source_data_package_ref"], artifact_ref(event["envelope"]))
        self.assertEqual(payload["validator_registry_version"], REGISTRY_SCHEMA_VERSION)
        self.assertEqual(payload["validated_at"], event["envelope"]["created_at"])
        report = payload["data_validation_report"]
        self.assertEqual(report["total_checks"], 3)
        self.assertEqual(len(report["query_receipts"]), 6)  # 2 tables x 3 sources
        universe = report["constraint_universe"]
        self.assertEqual(len(universe["constraints"]), 3)
        self.assertEqual(universe["closure_ref"], artifact_ref(self.structure["envelope"]))
        self.assertEqual(universe["sources"], self.approved)
        consumed = _stub.consume(frozen, ROOT, approved=self.approved)
        self.assertEqual(consumed["decision"], "pass")
        self.assertEqual(consumed["total_checks"], 3)

    def test_reproducible_without_validated_at_injection(self) -> None:
        event = self._event()
        first = self.validator.freeze_bound_data(event, self.structure, self.resolver)
        second = self.validator.freeze_bound_data(event, self.structure, self.resolver)
        self.assertEqual(first, second)

    def test_foundation_freeze_valid_nonzero_checks(self) -> None:
        foundation = self._foundation()
        frozen = self.validator.freeze_bound_data(foundation, _foundation_structure(), self.resolver)
        self.assertEqual(frozen["payload"]["data_validation_report"]["total_checks"], 1)

    def test_input_immutability(self) -> None:
        event = self._event()
        before = copy.deepcopy(event)
        self.validator.freeze_bound_data(event, self.structure, self.resolver)
        self.assertEqual(event, before)

    # ------------------------------------------------------------ layer failures

    def test_field_layer_failure_feedback(self) -> None:
        defective = _bound_data(self.structure, operation=self.operation, records=[
            _record("FIXTURE_T001", "rec-t1", [_field_value("F001", "A"), _field_value("F002", None, is_null=True)]),
            _record("FIXTURE_T002", "rec-t2", [_field_value("PK001", "A")], role="background"),
        ])
        feedback = self.validator.build_validation_feedback(defective, self.structure, self.resolver)
        self.assertEqual(feedback["payload"]["failed_items"][0]["failed_module_ids"], ["east_v5.validators.field"])

    def test_table_layer_failure_feedback(self) -> None:
        defective = _bound_data(self.structure, operation=self.operation, records=[
            _record("FIXTURE_T001", "rec-t1", [_field_value("F001", "B"), _field_value("F002", "A")]),
            _record("FIXTURE_T002", "rec-t2", [_field_value("PK001", "B")], role="background"),
        ])
        feedback = self.validator.build_validation_feedback(defective, self.structure, self.resolver)
        self.assertEqual(feedback["payload"]["failed_items"][0]["failed_module_ids"], ["east_v5.validators.table"])

    def test_cross_table_layer_failure_feedback(self) -> None:
        defective = _bound_data(self.structure, operation=self.operation, records=[
            _record("FIXTURE_T001", "rec-t1", [_field_value("F001", "A"), _field_value("F002", "B")]),
            _record("FIXTURE_T002", "rec-t2", [_field_value("PK001", "X")], role="background"),
        ])
        feedback = self.validator.build_validation_feedback(defective, self.structure, self.resolver)
        self.assertEqual(feedback["payload"]["failed_items"][0]["failed_module_ids"], ["east_v5.validators.cross_table"])

    def test_multi_layer_failure_aggregates_all(self) -> None:
        defective = _bound_data(self.structure, operation=self.operation, records=[
            _record("FIXTURE_T001", "rec-t1", [_field_value("F001", "A"), _field_value("F002", None, is_null=True)]),
            _record("FIXTURE_T002", "rec-t2", [_field_value("PK001", "X")], role="background"),
        ])
        feedback = self.validator.build_validation_feedback(defective, self.structure, self.resolver)
        modules = {item["failed_module_ids"][0] for item in feedback["payload"]["failed_items"]}
        self.assertEqual(modules, {"east_v5.validators.field", "east_v5.validators.cross_table"})

    def test_feedback_not_emitted_for_valid_data(self) -> None:
        with self.assertRaisesRegex(ContractError, "VALIDATION_NOT_FAILED"):
            self.validator.build_validation_feedback(self._event(), self.structure, self.resolver)

    # ------------------------------------------------------------ pagination + matrix

    def test_pagination_closes_over_limit(self) -> None:
        universe = self.resolver.enumerate(_big_structure())
        self.assertEqual(len(universe["constraints"]), PAGINATION_TOTAL)
        self.assertEqual(len({item["constraint_id"] for item in universe["constraints"]}), PAGINATION_TOTAL)
        multifield_pages = [r for r in self.resolver.receipts if r["query_method"] == "constraints_for_table"]
        self.assertEqual(len(multifield_pages), 2)  # 100 + 22
        self.assertEqual(multifield_pages[0]["total"], PAGINATION_TOTAL)
        self.assertEqual(multifield_pages[0]["returned_count"] + multifield_pages[1]["returned_count"], PAGINATION_TOTAL)

    def test_three_source_matrix_no_missing_no_duplicate(self) -> None:
        universe = self.resolver.enumerate(self.structure)
        methods = {r["query_method"] for r in self.resolver.receipts}
        self.assertEqual(methods, {"constraints_for_table", "field_rules_for_table", "graph_edges_for_table"})
        self.assertEqual(len(universe["constraints"]), 3)
        self.assertEqual(len({(c["constraint_id"], c["scope"]) for c in universe["constraints"]}), 3)

    def test_published_trg_production_resolver_preflight_is_deterministic(self) -> None:
        """242 consumes the actual frozen graph through the production service."""
        runtime = PublishedTrgRuntime()
        try:
            raw_edges = [json.loads(line) for line in runtime.paths["edges"].read_text(encoding="utf-8").splitlines() if line]
            tables = sorted({edge["source_table"] for edge in raw_edges} | {edge["target_table"] for edge in raw_edges})
            payload = {
                "schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0",
                "graph_version": "TRG-V1.0.0", "tables": tables, "fields": [], "references": [],
            }
            closure = _wrap("structure_closure", "eas112-published-trg-preflight", payload, producer="220", mode="event_data", qa_id="QA-EAS112")
            first_resolver = AssetBoundResolver(ROOT, ConstraintAssetQueryService(runtime.service()), control_path=runtime.control)
            second_resolver = AssetBoundResolver(ROOT, ConstraintAssetQueryService(runtime.service()), control_path=runtime.control)
            first, second = first_resolver.enumerate(closure), second_resolver.enumerate(closure)
            self.assertEqual(first, second)
            with sqlite3.connect(f"file:{runtime.paths['single_field']}?mode=ro&immutable=1", uri=True) as connection:
                self.assertEqual(connection.execute("SELECT review_status, COUNT(*) FROM single_field_constraints GROUP BY review_status ORDER BY review_status").fetchall(), [("CANDIDATE", 3508)])
            graph_receipts = [receipt for receipt in first_resolver.receipts if receipt["query_method"] == "graph_edges_for_table"]
            field_receipts = [receipt for receipt in first_resolver.receipts if receipt["query_method"] == "field_rules_for_table"]
            self.assertEqual({receipt["table_code"] for receipt in graph_receipts}, set(tables))
            self.assertEqual(sum(receipt["complete"] for receipt in graph_receipts), len(tables))
            self.assertTrue(all(receipt["total"] == 0 for receipt in field_receipts))
        finally:
            runtime.close()

    def test_graph_consumer_rejects_legacy_and_inconsistent_records(self) -> None:
        valid = _graph_edge()
        edges: set[tuple[str, str]] = set()
        AssetBoundResolver._ingest_graph([valid], edges)
        self.assertEqual(edges, {("FIXTURE_T002", "FIXTURE_T001")})

        invalid_records = [
            {"provider_table_code": "FIXTURE_T002", "consumer_table_code": "FIXTURE_T001", "edge_type": "REFERENCE"},
            {key: value for key, value in valid.items() if key != "source_table"},
            {**valid, "source_table": "OTHER"},
            {**valid, "expression": {**valid["expression"], "direction": "CONSUMER_TO_PROVIDER"}},
            {**valid, "expression": {**valid["expression"], "provider_fields": ["FIXTURE_T002.OTHER"]}},
            {**valid, "canonical_edge_hash": "0" * 64},
        ]
        for record in invalid_records:
            with self.subTest(record=record):
                with self.assertRaisesRegex(ContractError, "QUERY_RECEIPT_INVALID"):
                    AssetBoundResolver._ingest_graph([record], set())

    # ------------------------------------------------------------ fail closed (service)

    def test_deleted_row_fails_closed(self) -> None:
        with sqlite3.connect(self.runtime.sqlite) as con:
            con.execute("DELETE FROM multifield_constraint WHERE constraint_id = 'C-CMP-T001'")
        with self.assertRaisesRegex(ContractError, "ASSET_PAYLOAD_HASH_DRIFT"):
            self.validator.freeze_bound_data(self._event(), self.structure, self.resolver)

    def test_replaced_body_fails_closed(self) -> None:
        with sqlite3.connect(self.runtime.sqlite) as con:
            con.execute(
                "UPDATE multifield_constraint SET structured_expression_json = ? WHERE constraint_id = 'C-CMP-T001'",
                (json.dumps({"assertion": {"left": "FIXTURE_T001.F001", "operator": ">=", "right": "FIXTURE_T001.F002"}, "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0"}),),
            )
        with self.assertRaisesRegex(ContractError, "ASSET_PAYLOAD_HASH_DRIFT"):
            self.validator.freeze_bound_data(self._event(), self.structure, self.resolver)

    def test_pagination_not_closed_rejected(self) -> None:
        pages = self.runtime.service().constraints_for_table("BIG", limit=100)
        self.assertFalse(pages["complete"])
        with self.assertRaisesRegex(ContractError, "QUERY_RECEIPT_INCOMPLETE"):
            verify_query_receipts([pages], self.approved, method="multifield_constraints_for_table", table_code="BIG")

    def test_non_constraint_asset_service_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_SERVICE_REQUIRED"):
            ConstraintAssetQueryService(object())

    def test_cursor_replay_rejected_by_service(self) -> None:
        service = self.runtime.service()
        first = service.constraints_for_table("BIG", limit=100)
        self.assertEqual(service.constraints_for_table("BIG", limit=100, cursor=first["next_cursor"])["returned_count"], 22)
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_CURSOR_INVALID"):
            service.constraints_for_table("BIG", limit=100, cursor=first["next_cursor"])

    # ------------------------------------------------------------ fail closed (forged)

    def _forged(self, universe):
        class _Forged:
            receipts: list = []
            sources = self.approved

            def enumerate(self, sc):
                return universe

            def resolve(self, cid, scope):
                raise ContractError("UNKNOWN_CONSTRAINT")

        return _Forged()

    def test_forged_empty_universe_fails(self) -> None:
        forged = {
            "schema_version": "v5.constraint-universe/v2",
            "closure_ref": artifact_ref(self.structure["envelope"]),
            "sources": {},
            "constraints": [],
            "content_sha256": "",
        }
        forged["content_sha256"] = universe_content_hash(forged)
        with self.assertRaisesRegex(ContractError, "UNIVERSE_SOURCE_SET_INVALID"):
            self.validator.freeze_bound_data(self._event(), self.structure, self._forged(forged))

    def test_wrong_source_hash_fails(self) -> None:
        base = self.resolver.enumerate(self.structure)
        tampered = copy.deepcopy(base)
        tampered["sources"]["CA-V0.3.0"]["content_hash"] = "0" * 64
        tampered["content_sha256"] = universe_content_hash(tampered)
        with self.assertRaisesRegex(ContractError, "UNIVERSE_SOURCE_DRIFT"):
            self.validator.freeze_bound_data(self._event(), self.structure, self._forged(tampered))

    def test_cross_closure_replay_fails(self) -> None:
        base = self.resolver.enumerate(self.structure)
        tampered = copy.deepcopy(base)
        tampered["closure_ref"] = artifact_ref(_foundation_structure()["envelope"])
        tampered["content_sha256"] = universe_content_hash(tampered)
        with self.assertRaisesRegex(ContractError, "UNIVERSE_CLOSURE_MISMATCH"):
            self.validator.freeze_bound_data(self._event(), self.structure, self._forged(tampered))

    def test_duplicate_constraint_fails(self) -> None:
        base = self.resolver.enumerate(self.structure)
        tampered = copy.deepcopy(base)
        tampered["constraints"].append(copy.deepcopy(tampered["constraints"][0]))
        tampered["content_sha256"] = universe_content_hash(tampered)
        with self.assertRaisesRegex(ContractError, "UNIVERSE_DUPLICATE_CONSTRAINT"):
            self.validator.freeze_bound_data(self._event(), self.structure, self._forged(tampered))

    def test_resolve_drift_fails(self) -> None:
        resolver = _ResolveDriftResolver(ROOT, self.resolver.service, control_path=self.runtime.control)
        with self.assertRaisesRegex(ContractError, "RULE_CONTENT_HASH_DRIFT"):
            self.validator.freeze_bound_data(self._event(), self.structure, resolver)

    # ------------------------------------------------------------ rejections

    def test_hash_drift_rejection(self) -> None:
        event = copy.deepcopy(self._event())
        event["envelope"]["content_hash"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.validator.validate_bound_data(event, self.structure)

    def test_foundation_operation_and_event_missing_operation_rejection(self) -> None:
        foundation = copy.deepcopy(self._foundation())
        foundation["payload"]["operation_closure_ref"] = artifact_ref(self.operation["envelope"])
        foundation["envelope"]["content_hash"] = content_hash(foundation["envelope"], foundation["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_OPERATION_CLOSURE_FORBIDDEN"):
            self.validator.validate_bound_data(foundation, _foundation_structure())

        event = copy.deepcopy(self._event())
        event["payload"]["operation_closure_ref"] = None
        event["envelope"]["content_hash"] = content_hash(event["envelope"], event["payload"])
        with self.assertRaisesRegex(ContractError, "OPERATION_CLOSURE_REQUIRED"):
            self.validator.validate_bound_data(event, self.structure)

    def test_unknown_field_blocked_and_closure_mismatch_rejection(self) -> None:
        unknown = copy.deepcopy(self._event())
        unknown["payload"]["unknown"] = True
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD:BOUND_DATA"):
            self.validator.validate_bound_data(unknown, self.structure)

        blocked = copy.deepcopy(self._event())
        blocked["envelope"]["status"] = "blocked_manual"
        blocked["envelope"]["content_hash"] = content_hash(blocked["envelope"], blocked["payload"])
        with self.assertRaisesRegex(ContractError, "UPSTREAM_BLOCKED_MANUAL"):
            self.validator.validate_bound_data(blocked, self.structure)

        mismatch = copy.deepcopy(self._event())
        mismatch["payload"]["structure_closure_ref"]["content_hash"] = "0" * 64
        mismatch["envelope"]["content_hash"] = content_hash(mismatch["envelope"], mismatch["payload"])
        with self.assertRaisesRegex(ContractError, "STRUCTURE_CLOSURE_REFERENCE_MISMATCH"):
            self.validator.validate_bound_data(mismatch, self.structure)


if __name__ == "__main__":
    unittest.main()
