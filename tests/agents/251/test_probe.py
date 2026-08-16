from __future__ import annotations

import unittest
import importlib

run_sanitized_probe = importlib.import_module("east_v5.agents.251.probe").run_sanitized_probe


class ProbeTests(unittest.TestCase):
    def test_sanitized_probe(self) -> None:
        summary = run_sanitized_probe()["summary"]
        self.assertTrue(summary["empty_zero_write"])
        self.assertTrue(summary["stub_252_consumed"])
        self.assertTrue(summary["source_drift_rejected"])
        self.assertTrue(summary["feedback_revision"])
