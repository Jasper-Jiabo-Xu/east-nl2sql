from __future__ import annotations

import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_probe = importlib.import_module("east_v5.agents.242.probe")


class ProbeTests(unittest.TestCase):
    def test_sanitized_probe_contract(self) -> None:
        result = _probe.run_sanitized_probe(ROOT)
        summary = result["summary"]
        self.assertTrue(summary["input_immutable"])
        self.assertTrue(summary["field_defect_rejected"])
        self.assertEqual(summary["feedback_decision"], "fail")
        self.assertEqual(summary["feedback_items"], 1)
        self.assertEqual(summary["total_checks"], 3)
        self.assertEqual(summary["universe_constraint_count"], 3)
        self.assertEqual(summary["universe_sources"], ["CA-V0.2.0", "CA-V0.3.0", "TRG-V1.0.0"])
        self.assertEqual(summary["foundation_total_checks"], 1)
        self.assertEqual(result["transport"]["envelope"]["artifact_type"], "verified_bound_data")
        self.assertEqual(result["transport"]["envelope"]["producer_id"], "242")
        self.assertEqual({item["layer"] for item in summary["module_results"]}, {"field", "table", "cross_table"})


if __name__ == "__main__":
    unittest.main()
