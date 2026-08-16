from __future__ import annotations

import copy
import importlib
import unittest
from pathlib import Path

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
_closure_builder = importlib.import_module("east_v5.agents.230.builder")
_closure_probe = importlib.import_module("east_v5.agents.230.probe")
_generator = importlib.import_module("east_v5.agents.251.generator")
_probe = importlib.import_module("east_v5.agents.251.probe")


class _Transaction:
    def __init__(self, calls): self.calls = calls
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, table, values): self.calls.append(("read", table, values))
    def check(self, table, values): self.calls.append(("check", table, values))
    def insert(self, table, values): self.calls.append(("insert", table, values))
    def update(self, table, values): self.calls.append(("update", table, values))


class _Context:
    def __init__(self): self.calls = []
    def transaction(self): return _Transaction(self.calls)


class RestrictedOrmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = _closure_probe._structure()
        self.operation = _closure_builder.OperationClosureBuilder(ROOT).build(self.structure)
        self.builder = _generator.RestrictedOrmGenerator(ROOT)
        self.package = self.builder.build(self.structure, self.operation)

    def test_generates_deterministic_data_free_package_and_252_stub_consumes(self) -> None:
        repeated = self.builder.build(self.structure, self.operation)
        self.assertEqual(self.package, repeated)
        self.assertEqual(self.package["envelope"]["status"], "pending_validation")
        self.assertIn("UNSPECIFIED", str(self.package["payload"]["execution_contract"]["binding_slots"]))
        self.assertEqual(_probe.consume_252_stub(self.package, self.structure, self.operation, self.builder)["empty_write_count"], 0)
        namespace = {}
        exec(compile(self.package["payload"]["orm_source_code"], "<251-test-sandbox>", "exec"), {"__builtins__": {}}, namespace)
        context = _Context()
        self.assertEqual(namespace["apply"](context, {}), {"execution_mode": "empty", "executed_operation_ids": [], "write_count": 0})
        self.assertEqual(context.calls, [])

    def test_rejects_foundation_hash_drift_unknown_source_and_raw_sql(self) -> None:
        foundation = copy.deepcopy(self.structure); foundation["envelope"]["mode"] = "foundation"; foundation["envelope"]["content_hash"] = content_hash(foundation["envelope"], foundation["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_ORM_FORBIDDEN"):
            self.builder.build(foundation, self.operation)
        source = copy.deepcopy(self.package); source["payload"]["orm_source_code"] += "transaction.execute('INSERT')\n"; source["envelope"]["content_hash"] = content_hash(source["envelope"], source["payload"])
        with self.assertRaisesRegex(ContractError, "CODE_HASH_OR_API_DRIFT"):
            self.builder.validate_restricted_orm(source, self.structure, self.operation)
        unknown = copy.deepcopy(self.package); unknown["payload"]["unknown"] = True
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD:RESTRICTED_ORM"):
            self.builder.validate_restricted_orm(unknown, self.structure, self.operation)

    def test_252_and_260_feedback_create_immutable_revisions_and_attempt_three_blocks(self) -> None:
        feedback = {"orm_plan_ref": artifact_ref(self.package["envelope"]), "decision": "fail", "validation_types": ["static_ast"], "failed_items": [{"failed_rule_ids": ["fixture"]}]}
        second = self.builder.apply_252_feedback(self.package, feedback, self.structure, self.operation)
        self.assertEqual((second["envelope"]["version"], second["envelope"]["attempt_no"]), (2, 2))
        self.assertEqual(second["envelope"]["supersedes_ref"], artifact_ref(self.package["envelope"]))
        third = self.builder.apply_260_feedback(second, {"route_target": "251", "error_code": "ORM_PLAN_ERROR", "input_orm_ref": artifact_ref(second["envelope"])}, self.structure, self.operation)
        self.assertEqual((third["envelope"]["version"], third["envelope"]["attempt_no"], third["envelope"]["status"]), (3, 3, "blocked_manual"))
        with self.assertRaisesRegex(ContractError, "REGRESSION_NOT_ROUTED_TO_251"):
            self.builder.apply_260_feedback(second, {"route_target": "241"}, self.structure, self.operation)
