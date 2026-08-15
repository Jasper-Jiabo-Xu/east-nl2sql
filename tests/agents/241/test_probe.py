from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

probe = importlib.import_module("east_v5.agents.241.probe")


class ProbeTests(unittest.TestCase):
    def test_sanitized_probe_summary(self):
        result = probe.run_sanitized_probe(ROOT)
        summary = result["summary"]
        self.assertTrue(summary["attempt2_remapped"])
        self.assertTrue(summary["attempt3_blocked"])
        self.assertTrue(summary["bad_hash_rejected"])
        self.assertTrue(summary["foundation_operation_rejected"])
        self.assertTrue(summary["manifest_ok"])
        self.assertEqual(summary["event_records"], 2)
        self.assertEqual(summary["foundation_records"], 1)
        self.assertEqual(summary["stub_242_consumed"], "2-records")


if __name__ == "__main__":
    unittest.main()
