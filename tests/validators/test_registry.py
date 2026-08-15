from __future__ import annotations

import hashlib
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jsonschema import Draft202012Validator

from east_v5.governance import ContractError, canonical_bytes, load_json
from east_v5.validators import (
    build_registry,
    dispatch_validator,
    make_result,
    validators_for_rule_kind,
    verify_registry,
    verify_result,
)
from east_v5.validators.expression import make_rule
from east_v5.validators.snapshot import Snapshot
from east_v5.validators.table import validate_table_rule
from east_v5.validators.result import VERDICT_PASS


class RegistryTests(unittest.TestCase):
    def test_registry_is_reproducible_and_hash_verified(self) -> None:
        first, second = build_registry(), build_registry()
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(verify_registry(first), first)
        tampered = dict(first)
        tampered["content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "RESULT_SCHEMA_DRIFT"):
            verify_registry(tampered)

    def test_committed_coverage_matrix_matches_code_and_schema(self) -> None:
        registry = build_registry()
        committed = load_json(ROOT / "contracts" / "validators" / "coverage-matrix.json")
        self.assertEqual(committed, registry)
        schema = load_json(ROOT / "contracts" / "validators" / "validator-registry.schema.json")
        Draft202012Validator(schema).validate(registry)
        # every category in the coverage matrix names a known validator
        known = {definition["validator_id"] for definition in registry["validators"]}
        for category, validators in registry["coverage_categories"].items():
            self.assertTrue(set(validators).issubset(known), category)

    def test_registry_covers_all_ten_acceptance_categories(self) -> None:
        registry = build_registry()
        self.assertEqual(set(registry["coverage_categories"]), {
            "type", "format", "code_value", "null", "range", "conditional_required",
            "comparison", "cascade", "reference", "cross_record",
        })
        self.assertEqual(len(registry["rule_kind_index"]), 14)

    def test_dispatch_routes_every_kind_to_a_validator(self) -> None:
        self.assertEqual(dispatch_validator("NULLABLE"), "east_v5.validators.field")
        self.assertEqual(dispatch_validator("COMPARISON", "INTRA_TABLE"), "east_v5.validators.table")
        self.assertEqual(dispatch_validator("CONDITIONAL_COMPARISON", "INTRA_TABLE"), "east_v5.validators.table")
        self.assertEqual(dispatch_validator("CONDITIONAL_COMPARISON", "CROSS_TABLE"), "east_v5.validators.cross_table")
        self.assertEqual(dispatch_validator("REFERENCE_EXISTENCE"), "east_v5.validators.cross_table")
        with self.assertRaisesRegex(ContractError, "SCOPE_REQUIRED"):
            dispatch_validator("CONDITIONAL_COMPARISON")
        with self.assertRaisesRegex(ContractError, "UNKNOWN_RULE_KIND"):
            dispatch_validator("NOPE")
        self.assertEqual(validators_for_rule_kind("CONDITIONAL_COMPARISON"), [
            "east_v5.validators.cross_table", "east_v5.validators.table",
        ])

    def test_downstream_stubs_consume_result_and_registry(self) -> None:
        rule = make_rule("MFC-000029", "CA-MULTIFIELD-20260812-003", "CA-V0.3.0", "COMPARISON", "INTRA_TABLE", {
            "assertion": {"left": "GRJCXXB.YYED", "operator": "<=", "right": "GRJCXXB.SXED"},
            "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0",
        })
        violations = validate_table_rule(rule, Snapshot({"GRJCXXB": [{"YYED": "10", "SXED": "20"}]}).table("GRJCXXB"))
        # 242 emits a verified result envelope; 260 consumes the verdict + hash.
        result = make_result("east_v5.validators.table", "CA-V0.3.0", violations)
        self.assertEqual(result["verdict"], VERDICT_PASS)
        consumer = verify_result(result)
        self.assertEqual(consumer["constraint_asset_version"], "CA-V0.3.0")
        # 252 freezes a deterministic hash of the registry content as its proof.
        registry = build_registry()
        probe = dict(registry)
        probe["content_sha256"] = ""
        frozen = hashlib.sha256(canonical_bytes(probe)).hexdigest()
        self.assertEqual(len(frozen), 64)
        self.assertEqual(frozen, registry["content_sha256"])

    def test_performance_baseline_5000_rows(self) -> None:
        rule = make_rule("MFC-000029", "CA-MULTIFIELD-20260812-003", "CA-V0.3.0", "COMPARISON", "INTRA_TABLE", {
            "assertion": {"left": "GRJCXXB.YYED", "operator": "<=", "right": "GRJCXXB.SXED"},
            "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0",
        })
        rows = [{"YYED": "100", "SXED": "50"} for _ in range(5000)]
        started = time.perf_counter()
        violations = validate_table_rule(rule, Snapshot({"GRJCXXB": rows}).table("GRJCXXB"))
        elapsed = time.perf_counter() - started
        self.assertEqual(len(violations), 5000)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
