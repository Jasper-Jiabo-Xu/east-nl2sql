from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jsonschema import Draft202012Validator

from east_v5.governance import ContractError, load_json
from east_v5.validators import (
    RESULT_SCHEMA_VERSION,
    VERDICT_FAIL,
    VERDICT_PASS,
    make_result,
    make_violation,
    verify_result,
)
from east_v5.validators.result import VIOLATION_NULLABLE, VIOLATION_REFERENCE_EXISTENCE


def violation(code=VIOLATION_NULLABLE, rule_kind="NULLABLE", **location_kwargs):
    location = {"table_code": "YGB", "endpoint": "YGB.YHJGDM", "field_code": "YHJGDM", "record_index": 0}
    location.update(location_kwargs)
    endpoint = location["endpoint"]
    if endpoint and "." in endpoint:
        location["table_code"], location["field_code"] = endpoint.split(".", 1)
    return make_violation(
        code, "C-1", "A-1", rule_kind,
        table_code=location["table_code"], endpoint=location["endpoint"],
        field_code=location["field_code"], record_index=location["record_index"],
        message="must not be empty",
    )


class ResultTests(unittest.TestCase):
    def test_pass_and_fail_verdicts_are_derived_not_supplied(self) -> None:
        passed = make_result("east_v5.validators.field", "CA-V0.2.0", [])
        self.assertEqual(passed["verdict"], VERDICT_PASS)
        self.assertEqual(passed["violations"], [])
        failed = make_result("east_v5.validators.field", "CA-V0.2.0", [violation()])
        self.assertEqual(failed["verdict"], VERDICT_FAIL)
        self.assertEqual(len(failed["violations"]), 1)

    def test_result_roundtrips_through_verify_and_schema(self) -> None:
        result = make_result("east_v5.validators.cross_table", "CA-V0.3.0", [
            violation(code=VIOLATION_REFERENCE_EXISTENCE, rule_kind="REFERENCE_EXISTENCE"),
        ])
        self.assertEqual(result["schema_version"], RESULT_SCHEMA_VERSION)
        self.assertEqual(verify_result(result), result)
        schema = load_json(ROOT / "contracts" / "validators" / "validator-result.schema.json")
        Draft202012Validator(schema).validate(result)

    def test_content_hash_detects_tampering(self) -> None:
        result = make_result("east_v5.validators.field", "CA-V0.2.0", [])
        tampered = dict(result)
        tampered["verdict"] = VERDICT_FAIL
        with self.assertRaisesRegex(ContractError, "RESULT_SCHEMA_DRIFT"):
            verify_result(tampered)
        tampered = dict(result)
        tampered["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "RESULT_SCHEMA_DRIFT"):
            verify_result(tampered)

    def test_verdict_must_match_violations(self) -> None:
        result = make_result("east_v5.validators.field", "CA-V0.2.0", [])
        bad = dict(result)
        bad["verdict"] = VERDICT_FAIL
        with self.assertRaisesRegex(ContractError, "RESULT_SCHEMA_DRIFT"):
            verify_result(bad)

    def test_unknown_violation_code_and_bad_location_are_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            make_violation("NOT_A_CODE", "C-1", "A-1", "X")
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            make_violation(VIOLATION_NULLABLE, "C-1", "A-1", "NULLABLE", record_index=-1)

    def test_violation_error_location_is_precise(self) -> None:
        item = violation(endpoint="JGXXB.JRXKZH", record_index=3)
        self.assertEqual(item["location"], {"table_code": "JGXXB", "endpoint": "JGXXB.JRXKZH", "field_code": "JRXKZH", "record_index": 3})


if __name__ == "__main__":
    unittest.main()
