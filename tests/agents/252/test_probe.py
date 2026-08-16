from __future__ import annotations

import importlib
import unittest

run_sanitized_probe = importlib.import_module("east_v5.agents.252.probe").run_sanitized_probe


class ProbeTests(unittest.TestCase):
    def test_sanitized_probe(self) -> None:
        summary = run_sanitized_probe()["summary"]
        self.assertTrue(summary["code_hash_preserved"])
        self.assertTrue(summary["source_drift_rejected"])
        self.assertEqual(summary["operation_count"], 5)
        self.assertEqual(summary["feedback_validation_types"], ["api_allowlist"])
