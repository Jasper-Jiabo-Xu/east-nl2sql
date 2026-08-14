from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.governance import ContractError
from east_v5.validators.cross_table import validate_cross_table_rule
from east_v5.validators.expression import make_rule
from east_v5.validators.snapshot import Snapshot
from east_v5.validators.result import (
    VIOLATION_CONDITIONAL_COMPARISON,
    VIOLATION_CONDITIONAL_VALUE_EXCLUSION,
    VIOLATION_REFERENCE_EXISTENCE,
)


def reference(constraint_id, consumer, providers, match):
    return make_rule(constraint_id, "CA-MULTIFIELD-20260812-003", "CA-V0.3.0", "REFERENCE_EXISTENCE", "CROSS_TABLE", {
        "kind": "REFERENCE_EXISTENCE", "schema_version": "EAS-MFC-1.0", "condition_text": "non-empty must exist",
        "consumer_field": consumer, "direction": "PROVIDER_TO_CONSUMER",
        "provider_fields": providers, "provider_match": match,
    })


def comparison(constraint_id, expression):
    return make_rule(constraint_id, "CA-MULTIFIELD-20260812-003", "CA-V0.3.0", "COMPARISON", "CROSS_TABLE", expression)


def codes(rule_dict, tables):
    return [v["code"] for v in validate_cross_table_rule(rule_dict, Snapshot(tables))]


class CrossTableValidatorTests(unittest.TestCase):
    def test_reference_existence_one(self) -> None:
        r = reference("MFC-000001", "YGB.YHJGDM", ["JGXXB.YHJGDM"], "ONE")
        tables = {"YGB": [{"YHJGDM": "001"}, {"YHJGDM": "999"}, {"YHJGDM": ""}], "JGXXB": [{"YHJGDM": "001"}]}
        self.assertEqual(codes(r, tables), [VIOLATION_REFERENCE_EXISTENCE])
        self.assertEqual(codes(r, {"YGB": [{"YHJGDM": "001"}], "JGXXB": [{"YHJGDM": "001"}]}), [])

    def test_reference_existence_any(self) -> None:
        r = reference("MFC-X", "YGB.YHJGDM", ["JGXXB.YHJGDM", "JGXXB.ZGDM"], "ANY")
        tables = {"YGB": [{"YHJGDM": "001"}, {"YHJGDM": "002"}], "JGXXB": [{"YHJGDM": "001", "ZGDM": "002"}]}
        self.assertEqual(codes(r, tables), [])
        self.assertEqual(codes(r, {"YGB": [{"YHJGDM": "003"}], "JGXXB": [{"YHJGDM": "001", "ZGDM": "002"}]}), [VIOLATION_REFERENCE_EXISTENCE])

    def test_conditional_comparison_with_join(self) -> None:
        r = comparison("MFC-000028", {
            "assertion": {"left": "GRJCXXB.SXED", "operator": ">=", "right": "SXXXB.SXED"},
            "condition": {"field": "SXXXB.SXZT", "operator": "=", "value": "有效"},
            "join": {"left": "GRJCXXB.KHTYBH", "operator": "=", "right": "SXXXB.KHTYBH"},
            "kind": "CONDITIONAL_COMPARISON", "schema_version": "EAS-MFC-1.0",
        })
        tables = {
            "GRJCXXB": [{"KHTYBH": "K1", "SXED": "200"}, {"KHTYBH": "K1", "SXED": "50"}],
            "SXXXB": [{"KHTYBH": "K1", "SXZT": "有效", "SXED": "100"}],
        }
        self.assertEqual(codes(r, tables), [VIOLATION_CONDITIONAL_COMPARISON])
        self.assertEqual(codes(r, {"GRJCXXB": [{"KHTYBH": "K1", "SXED": "200"}], "SXXXB": [{"KHTYBH": "K1", "SXZT": "有效", "SXED": "100"}]}), [])
        # condition false -> skipped
        self.assertEqual(codes(r, {"GRJCXXB": [{"KHTYBH": "K1", "SXED": "50"}], "SXXXB": [{"KHTYBH": "K1", "SXZT": "失效", "SXED": "100"}]}), [])

    def test_conditional_value_exclusion_exists_in(self) -> None:
        r = comparison("MFC-000242", {
            "assertion": {"field": "GRXDYWJJB.DKWJFL", "operator": "NOT_IN", "values": ["正常", "关注"]},
            "condition": {"left": "GRXDYWJJB.XDJJH", "operator": "EXISTS_IN", "right": "DKHXB.XDJJH"},
            "kind": "CONDITIONAL_VALUE_EXCLUSION", "schema_version": "EAS-MFC-1.0",
        })
        tables = {
            "GRXDYWJJB": [{"XDJJH": "X1", "DKWJFL": "次级"}, {"XDJJH": "X1", "DKWJFL": "正常"}, {"XDJJH": "X9", "DKWJFL": "正常"}],
            "DKHXB": [{"XDJJH": "X1"}],
        }
        self.assertEqual(codes(r, tables), [VIOLATION_CONDITIONAL_VALUE_EXCLUSION])

    def test_violation_identity_and_location(self) -> None:
        r = reference("MFC-000001", "YGB.YHJGDM", ["JGXXB.YHJGDM"], "ONE")
        violations = validate_cross_table_rule(r, Snapshot({"YGB": [{"YHJGDM": "999"}], "JGXXB": [{"YHJGDM": "001"}]}))
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v["constraint_id"], "MFC-000001")
        self.assertEqual(v["asset_id"], "CA-MULTIFIELD-20260812-003")
        self.assertEqual(v["location"]["table_code"], "YGB")
        self.assertEqual(v["location"]["endpoint"], "YGB.YHJGDM")
        self.assertEqual(v["location"]["record_index"], 0)

    def test_unknown_kind_and_ambiguous_provider_are_rejected(self) -> None:
        bad = reference("MFC-X", "YGB.YHJGDM", ["JGXXB.YHJGDM"], "ONE")
        bad["kind"] = "BOGUS"
        with self.assertRaisesRegex(ContractError, "UNKNOWN_RULE_KIND"):
            validate_cross_table_rule(bad, Snapshot({}))
        ambiguous = comparison("MFC-X", {
            "assertion": {"left": "A.P", "operator": ">=", "right": "B.Q"},
            "condition": {"field": "C.Z", "operator": "=", "value": "x"},
            "join": {"left": "A.K", "operator": "=", "right": "B.K"},
            "kind": "CONDITIONAL_COMPARISON", "schema_version": "EAS-MFC-1.0",
        })
        with self.assertRaisesRegex(ContractError, "EXPRESSION_INVALID"):
            validate_cross_table_rule(ambiguous, Snapshot({"A": [], "B": [], "C": []}))

    def test_validator_does_not_mutate_input(self) -> None:
        r = reference("MFC-000001", "YGB.YHJGDM", ["JGXXB.YHJGDM"], "ONE")
        tables = {"YGB": [{"YHJGDM": "999"}], "JGXXB": [{"YHJGDM": "001"}]}
        before = copy.deepcopy(tables)
        validate_cross_table_rule(r, Snapshot(tables))
        self.assertEqual(tables, before)


if __name__ == "__main__":
    unittest.main()
