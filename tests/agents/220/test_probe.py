from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
probe = importlib.import_module("east_v5.agents.220.probe")


class ProbeTests(unittest.TestCase):
    def test_sanitized_probe_exercises_two_round_event_route(self):
        summary = probe.run_sanitized_probe(ROOT)["summary"]
        self.assertEqual((summary["first_request"], summary["second_request"]), ("220-event-r1", "220-event-r2"))
        self.assertEqual(summary["event_consumers"], ["230", "241", "251", "252", "260"])
        self.assertTrue(summary["bad_hash_rejected"])
        self.assertTrue(summary["unknown_field_rejected"])
        self.assertTrue(summary["third_attempt_blocked_manual"])
