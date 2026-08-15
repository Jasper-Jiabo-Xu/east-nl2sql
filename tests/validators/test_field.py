from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.governance import ContractError
from east_v5.validators.field import (
    validate_field,
    validate_field_column,
)
from east_v5.validators.result import (
    VIOLATION_CODE_DOMAIN,
    VIOLATION_DATA_TYPE,
    VIOLATION_ENCODING_RULE,
    VIOLATION_FORBIDDEN_CHARACTER_SET,
    VIOLATION_FORBIDDEN_VALUE,
    VIOLATION_NULLABLE,
    VIOLATION_PRIMARY_KEY,
    VIOLATION_STRING_LENGTH,
    VIOLATION_UNIQUE,
    VIOLATION_VALUE_RANGE,
)


def rule(kind, spec, endpoint="YGB.YHJGDM"):
    return {"rule_kind": kind, "constraint_id": "SFC-1", "asset_id": "CA-FIELD-1", "asset_version": "CA-V0.2.0", "endpoint": endpoint, "spec": spec}


def codes(rule_dict, value):
    return [item["code"] for item in validate_field(rule_dict, value)]


class FieldValidatorTests(unittest.TestCase):
    def test_nullable(self) -> None:
        r = rule("NULLABLE", {"nullable": "NO"})
        self.assertEqual(codes(r, "abc"), [])
        self.assertEqual(codes(r, None), [VIOLATION_NULLABLE])
        self.assertEqual(codes(r, ""), [VIOLATION_NULLABLE])
        # nullable=YES imposes no constraint
        self.assertEqual(codes(rule("NULLABLE", {"nullable": "YES"}), None), [])

    def test_string_length(self) -> None:
        exact = rule("STRING_LENGTH", {"mode": "EXACT", "length": 3})
        self.assertEqual(codes(exact, "abc"), [])
        self.assertEqual(codes(exact, "ab"), [VIOLATION_STRING_LENGTH])
        maximum = rule("STRING_LENGTH", {"mode": "MAX", "length": 3})
        self.assertEqual(codes(maximum, "ab"), [])
        self.assertEqual(codes(maximum, "abcd"), [VIOLATION_STRING_LENGTH])
        legacy_max = rule("STRING_LENGTH", {"length_max": 3})
        self.assertEqual(codes(legacy_max, "abc"), [])
        self.assertEqual(codes(legacy_max, "abcd"), [VIOLATION_STRING_LENGTH])
        self.assertEqual(codes(exact, None), [])

    def test_code_domain(self) -> None:
        r = rule("CODE_DOMAIN", {"allowed_values": ["A", "B"]})
        self.assertEqual(codes(r, "A"), [])
        self.assertEqual(codes(r, "C"), [VIOLATION_CODE_DOMAIN])
        self.assertEqual(codes(r, None), [])

    def test_encoding_rule_format_and_classes(self) -> None:
        fmt = rule("ENCODING_RULE", {"format": "YYYYMMDD"})
        self.assertEqual(codes(fmt, "20260815"), [])
        self.assertEqual(codes(fmt, "2026-08-15"), [VIOLATION_ENCODING_RULE])
        classes = rule("ENCODING_RULE", {"character_classes": ["DIGIT"], "exact_length": 6})
        self.assertEqual(codes(classes, "123456"), [])
        self.assertEqual(codes(classes, "12ab56"), [VIOLATION_ENCODING_RULE])

    def test_forbidden_value(self) -> None:
        r = rule("FORBIDDEN_VALUE", {"forbidden_value": "99991231"})
        self.assertEqual(codes(r, "20260101"), [])
        self.assertEqual(codes(r, "99991231"), [VIOLATION_FORBIDDEN_VALUE])
        self.assertEqual(codes(r, None), [])

    def test_forbidden_character_set(self) -> None:
        r = rule("FORBIDDEN_CHARACTER_SET", {"characters": ["~"], "match_mode": "CONTAINS_ANY", "normalization": "NFKC"})
        self.assertEqual(codes(r, "abc"), [])
        self.assertEqual(codes(r, "a~b"), [VIOLATION_FORBIDDEN_CHARACTER_SET])

    def test_value_range(self) -> None:
        r = rule("VALUE_RANGE", {"min": 0, "max": 1})
        self.assertEqual(codes(r, "0.5"), [])
        self.assertEqual(codes(r, "0"), [])
        self.assertEqual(codes(r, "2"), [VIOLATION_VALUE_RANGE])
        self.assertEqual(codes(r, "abc"), [VIOLATION_VALUE_RANGE])
        self.assertEqual(codes(r, None), [])

    def test_data_type(self) -> None:
        integer = rule("DATA_TYPE", {"data_type": "INTEGER"})
        self.assertEqual(codes(integer, "42"), [])
        self.assertEqual(codes(integer, "4.5"), [VIOLATION_DATA_TYPE])
        decimal = rule("DATA_TYPE", {"data_type": "DECIMAL", "decimal_max_fraction_digits": 2})
        self.assertEqual(codes(decimal, "4.50"), [])
        self.assertEqual(codes(decimal, "4.123"), [VIOLATION_DATA_TYPE])
        string = rule("DATA_TYPE", {"data_type": "STRING", "string_length_exact": 3})
        self.assertEqual(codes(string, "abc"), [])
        self.assertEqual(codes(string, "abcd"), [VIOLATION_DATA_TYPE])

    def test_unique_and_primary_key_columns(self) -> None:
        unique = rule("UNIQUE", {"unique": True})
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            validate_field(unique, "x")
        self.assertEqual([v["code"] for v in validate_field_column(unique, ["1", "2", "3"])], [])
        self.assertEqual([v["code"] for v in validate_field_column(unique, ["1", "2", "1"])], [VIOLATION_UNIQUE])
        pk = rule("PRIMARY_KEY", {"primary_key": True})
        self.assertEqual([v["code"] for v in validate_field_column(pk, ["1", "2", "3"])], [])
        result = validate_field_column(pk, ["1", None, "2", "1"])
        self.assertEqual([v["code"] for v in result], [VIOLATION_PRIMARY_KEY, VIOLATION_PRIMARY_KEY])
        self.assertEqual(result[0]["location"]["record_index"], 1)

    def test_unknown_kind_and_missing_asset_are_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "UNKNOWN_RULE_KIND"):
            validate_field(rule("NOT_A_KIND", {}), "x")
        bad = rule("NULLABLE", {"nullable": "NO"})
        bad["asset_id"] = ""
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            validate_field(bad, "x")

    def test_validators_do_not_mutate_input(self) -> None:
        r = rule("NULLABLE", {"nullable": "NO"})
        original = copy.deepcopy(r)
        value = ""
        validate_field(r, value)
        self.assertEqual(r, original)
        self.assertEqual(value, "")
        column = ["1", "2", "1"]
        before = list(column)
        validate_field_column(rule("UNIQUE", {"unique": True}), column)
        self.assertEqual(column, before)


if __name__ == "__main__":
    unittest.main()
