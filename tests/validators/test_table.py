from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.governance import ContractError
from east_v5.validators.expression import make_rule
from east_v5.validators.snapshot import Snapshot
from east_v5.validators.table import validate_table_rule
from east_v5.validators.result import (
    VIOLATION_COMPARISON,
    VIOLATION_CONDITIONAL_COMPARISON,
    VIOLATION_CONDITIONAL_VALUE_EXCLUSION,
)


def rule(constraint_id, expression):
    return make_rule(constraint_id, "CA-MULTIFIELD-20260812-003", "CA-V0.3.0", "COMPARISON", "INTRA_TABLE", expression)


def codes(table_code, rule_dict, rows):
    return [v["code"] for v in validate_table_rule(rule_dict, Snapshot({table_code: rows}).table(table_code))]


class TableValidatorTests(unittest.TestCase):
    def test_always_comparison(self) -> None:
        r = rule("MFC-000029", {"assertion": {"left": "GRJCXXB.YYED", "operator": "<=", "right": "GRJCXXB.SXED"}, "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0"})
        self.assertEqual(codes("GRJCXXB", r, [{"YYED": "10", "SXED": "20"}]), [])
        self.assertEqual(codes("GRJCXXB", r, [{"YYED": "30", "SXED": "20"}]), [VIOLATION_COMPARISON])
        # empty operands make the rule not apply
        self.assertEqual(codes("GRJCXXB", r, [{"YYED": None, "SXED": "20"}]), [])

    def test_conditional_comparison_guarded_by_inequality(self) -> None:
        r = rule("MFC-000190", {
            "assertion": {"left": "GRXDFHZ.DKJE", "operator": ">=", "right": "GRXDFHZ.DKYE"},
            "condition": {"field": "GRXDFHZ.DKJE", "operator": "!=", "value": 0},
            "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0",
        })
        self.assertEqual(codes("GRXDFHZ", r, [{"DKJE": "100", "DKYE": "50"}]), [])
        self.assertEqual(codes("GRXDFHZ", r, [{"DKJE": "10", "DKYE": "50"}]), [VIOLATION_COMPARISON])
        # condition false (DKJE == 0) -> rule skipped
        self.assertEqual(codes("GRXDFHZ", r, [{"DKJE": "0", "DKYE": "50"}]), [])

    def test_conditional_comparison_guarded_by_equality(self) -> None:
        r = rule("MFC-000100", {
            "assertion": {"left": "GRHQCKFHZMX.ZHYE", "operator": ">=", "right": "GRHQCKFHZMX.JYJE"},
            "condition": {"field": "GRHQCKFHZMX.JYJDBZ", "operator": "=", "value": "贷"},
            "kind": "CONDITIONAL_COMPARISON", "schema_version": "EAS-MFC-1.0",
        })
        self.assertEqual(codes("GRHQCKFHZMX", r, [{"ZHYE": "100", "JYJE": "50", "JYJDBZ": "贷"}]), [])
        self.assertEqual(codes("GRHQCKFHZMX", r, [{"ZHYE": "10", "JYJE": "50", "JYJDBZ": "贷"}]), [VIOLATION_CONDITIONAL_COMPARISON])
        self.assertEqual(codes("GRHQCKFHZMX", r, [{"ZHYE": "10", "JYJE": "50", "JYJDBZ": "借"}]), [])

    def test_conditional_value_exclusion_not_all_equal(self) -> None:
        r = rule("MFC-000339", {
            "assertion": {"field": "DKHXB.SHRQ", "operator": "!=", "value": "99991231"},
            "condition": {"fields": ["DKHXB.SHBJ", "DKHXB.SHLX"], "operator": "NOT_ALL_EQUAL", "value": 0},
            "kind": "CONDITIONAL_VALUE_EXCLUSION", "schema_version": "EAS-MFC-1.0",
        })
        self.assertEqual(codes("DKHXB", r, [{"SHRQ": "20260101", "SHBJ": "1", "SHLX": "0"}]), [])
        self.assertEqual(codes("DKHXB", r, [{"SHRQ": "99991231", "SHBJ": "1", "SHLX": "0"}]), [VIOLATION_CONDITIONAL_VALUE_EXCLUSION])
        # both flags zero -> condition false -> skipped
        self.assertEqual(codes("DKHXB", r, [{"SHRQ": "99991231", "SHBJ": "0", "SHLX": "0"}]), [])

    def test_violation_carries_asset_identity_and_location(self) -> None:
        r = rule("MFC-000029", {"assertion": {"left": "GRJCXXB.YYED", "operator": "<=", "right": "GRJCXXB.SXED"}, "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0"})
        violations = validate_table_rule(r, Snapshot({"GRJCXXB": [{"YYED": "30", "SXED": "20"}]}).table("GRJCXXB"))
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v["constraint_id"], "MFC-000029")
        self.assertEqual(v["asset_id"], "CA-MULTIFIELD-20260812-003")
        self.assertEqual(v["location"]["table_code"], "GRJCXXB")
        self.assertEqual(v["location"]["endpoint"], "GRJCXXB.YYED")
        self.assertEqual(v["location"]["record_index"], 0)

    def test_scope_mismatch_and_unknown_kind_are_rejected(self) -> None:
        cross = make_rule("MFC-000028", "CA-MULTIFIELD-20260812-003", "CA-V0.3.0", "COMPARISON", "CROSS_TABLE", {
            "assertion": {"left": "GRJCXXB.SXED", "operator": ">=", "right": "SXXXB.SXED"},
            "condition": {"field": "SXXXB.SXZT", "operator": "=", "value": "有效"},
            "join": {"left": "GRJCXXB.KHTYBH", "operator": "=", "right": "SXXXB.KHTYBH"},
            "kind": "CONDITIONAL_COMPARISON", "schema_version": "EAS-MFC-1.0",
        })
        with self.assertRaisesRegex(ContractError, "INVALID_INPUT"):
            validate_table_rule(cross, Snapshot({"GRJCXXB": []}).table("GRJCXXB"))
        bad = rule("MFC-X", {"assertion": {"left": "A.X", "operator": "<=", "right": "A.Y"}, "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0"})
        bad["kind"] = "BOGUS"
        with self.assertRaisesRegex(ContractError, "UNKNOWN_RULE_KIND"):
            validate_table_rule(bad, Snapshot({"A": []}).table("A"))

    def test_validator_does_not_mutate_input(self) -> None:
        r = rule("MFC-000029", {"assertion": {"left": "GRJCXXB.YYED", "operator": "<=", "right": "GRJCXXB.SXED"}, "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0"})
        rows = [{"YYED": "30", "SXED": "20"}]
        before = copy.deepcopy(rows)
        validate_table_rule(r, Snapshot({"GRJCXXB": rows}).table("GRJCXXB"))
        self.assertEqual(rows, before)


if __name__ == "__main__":
    unittest.main()
