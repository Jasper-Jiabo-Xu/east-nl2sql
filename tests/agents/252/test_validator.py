from __future__ import annotations

import copy
import importlib
import unittest
from pathlib import Path

from east_v5.artifacts import content_hash
from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
_closure_probe = importlib.import_module("east_v5.agents.230.probe")
_closure_builder = importlib.import_module("east_v5.agents.230.builder")
_orm_generator = importlib.import_module("east_v5.agents.251.generator")
_validator = importlib.import_module("east_v5.agents.252.validator")
try:
    _stub = importlib.import_module("agents.252.approved_260_stub")
except ModuleNotFoundError:
    _stub = importlib.import_module("tests.agents.252.approved_260_stub")

OrmValidator = _validator.OrmValidator


def _rehashed(package):
    p = copy.deepcopy(package)
    p["payload"]["code_hash"] = OrmValidator._code_hash(p["payload"]["orm_source_code"], p["payload"]["execution_contract"], p["payload"]["operations"])
    p["envelope"]["content_hash"] = content_hash(p["envelope"], p["payload"])
    return p


def _corrupt_static_ast(package):
    p = copy.deepcopy(package)
    p["payload"]["orm_source_code"] += "lambda: None\n"
    return _rehashed(p)


def _corrupt_api_allowlist(package):
    p = copy.deepcopy(package)
    p["payload"]["orm_source_code"] = p["payload"]["orm_source_code"].replace("transaction.update(", "transaction.delete(")
    return _rehashed(p)


def _corrupt_import_compile(package):
    p = copy.deepcopy(package)
    p["payload"]["orm_source_code"] += "x = 1 / 0\n"
    return _rehashed(p)


def _corrupt_empty_dry_run(package):
    p = copy.deepcopy(package)
    p["payload"]["orm_source_code"] = p["payload"]["orm_source_code"].replace("'write_count': 0", "'write_count': 1", 1)
    return _rehashed(p)


def _corrupt_object_detail_state(package):
    p = copy.deepcopy(package)
    p["payload"]["operations"][0]["depends_on"] = [p["payload"]["operations"][-1]["operation_id"]]
    return _rehashed(p)


class OrmValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = _closure_probe._structure()
        self.operation = _closure_builder.OperationClosureBuilder(ROOT).build(self.structure)
        self.builder = _orm_generator.RestrictedOrmGenerator(ROOT)
        self.restricted = self.builder.build(self.structure, self.operation)
        self.validator = OrmValidator(ROOT)

    def test_freeze_contract_hash_and_independent_260_stub(self) -> None:
        frozen = self.validator.freeze_orm(self.restricted, self.structure, self.operation)
        repeated = self.validator.freeze_orm(self.restricted, self.structure, self.operation)
        self.assertEqual(frozen, repeated)
        payload = frozen["payload"]
        self.assertEqual(payload["validated_hash"], self.restricted["payload"]["code_hash"])
        self.assertEqual(payload["validated_orm_plan"]["code_hash"], self.restricted["payload"]["code_hash"])
        self.assertEqual(payload["validator_module_version"], "v5.orm-validator/v2")
        self.assertEqual(payload["sequence_validation_report"]["empty_dry_run"]["write_count"], 0)
        self.assertEqual(payload["sequence_validation_report"]["object_detail_state"]["operation_count"], len(self.restricted["payload"]["operations"]))
        self.assertEqual(_stub.consume(frozen, ROOT)["empty_write_count"], 0)

    def test_input_immutability(self) -> None:
        before = copy.deepcopy((self.restricted, self.structure, self.operation))
        self.validator.freeze_orm(self.restricted, self.structure, self.operation)
        self.assertEqual((self.restricted, self.structure, self.operation), before)

    def test_each_validation_type_failure_aggregates_feedback(self) -> None:
        cases = [
            ("static_ast", _corrupt_static_ast),
            ("api_allowlist", _corrupt_api_allowlist),
            ("import_compile", _corrupt_import_compile),
            ("empty_dry_run", _corrupt_empty_dry_run),
            ("object_detail_state", _corrupt_object_detail_state),
        ]
        for expected_type, corrupt in cases:
            with self.subTest(validation_type=expected_type):
                defective = corrupt(self.restricted)
                with self.assertRaisesRegex(ContractError, "ORM_VALIDATION_REJECTED"):
                    self.validator.freeze_orm(defective, self.structure, self.operation)
                feedback = self.validator.build_validation_feedback(defective, self.structure, self.operation)
                self.assertIn(expected_type, feedback["payload"]["validation_types"])
                self.assertEqual(feedback["payload"]["decision"], "fail")
                self.assertTrue(feedback["payload"]["failed_items"])
                self.builder._validate_252_feedback(feedback, defective)

    def test_validation_feedback_not_emitted_for_valid_orm(self) -> None:
        with self.assertRaisesRegex(ContractError, "ORM_VALIDATION_NOT_FAILED"):
            self.validator.build_validation_feedback(self.restricted, self.structure, self.operation)

    def test_hash_drift_rejection(self) -> None:
        code_drift = copy.deepcopy(self.restricted)
        code_drift["payload"]["code_hash"] = "0" * 64
        code_drift["envelope"]["content_hash"] = content_hash(code_drift["envelope"], code_drift["payload"])
        with self.assertRaisesRegex(ContractError, "CODE_HASH_DRIFT"):
            self.validator.validate_restricted_orm(code_drift, self.structure, self.operation)
        envelope_drift = copy.deepcopy(self.restricted)
        envelope_drift["payload"]["orm_source_code"] += "# tampered\n"
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            self.validator.validate_restricted_orm(envelope_drift, self.structure, self.operation)

    def test_foundation_unknown_field_and_upstream_blocked_rejection(self) -> None:
        foundation = copy.deepcopy(self.restricted)
        foundation["envelope"]["mode"] = "foundation"
        foundation["envelope"]["content_hash"] = content_hash(foundation["envelope"], foundation["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_ORM_FORBIDDEN"):
            self.validator.validate_restricted_orm(foundation, self.structure, self.operation)
        unknown = copy.deepcopy(self.restricted)
        unknown["payload"]["unknown"] = True
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD:RESTRICTED_ORM"):
            self.validator.validate_restricted_orm(unknown, self.structure, self.operation)
        blocked = copy.deepcopy(self.restricted)
        blocked["envelope"]["status"] = "blocked_manual"
        blocked["envelope"]["content_hash"] = content_hash(blocked["envelope"], blocked["payload"])
        with self.assertRaisesRegex(ContractError, "UPSTREAM_BLOCKED_MANUAL"):
            self.validator.validate_restricted_orm(blocked, self.structure, self.operation)

    def test_closure_reference_mismatch_rejection(self) -> None:
        wrong = copy.deepcopy(self.restricted)
        wrong["payload"]["operation_closure_ref"]["content_hash"] = "0" * 64
        wrong["envelope"]["content_hash"] = content_hash(wrong["envelope"], wrong["payload"])
        with self.assertRaisesRegex(ContractError, "OPERATION_CLOSURE_REFERENCE_MISMATCH"):
            self.validator.validate_restricted_orm(wrong, self.structure, self.operation)


if __name__ == "__main__":
    unittest.main()
