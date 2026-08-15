from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(ROOT / "src"))
probe = importlib.import_module("east_v5.agents.220.probe")


class ProbeTests(unittest.TestCase):
    def test_probe_recomputes_two_dynamic_event_packages_and_foundation_package(self):
        events = [
            json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            for name in ("event-data-dual-review.json", "event-data-dynamic-second.json")
        ]
        foundation = json.loads((ROOT / "fixtures" / "artifacts" / "foundation-profile-valid.json").read_text(encoding="utf-8"))
        summary = probe.run_sanitized_probe(events, foundation)["summary"]
        self.assertEqual(len(summary["event_refs"]), 2)
        self.assertNotEqual(summary["event_refs"][0], summary["event_refs"][1])
        self.assertEqual(summary["event_consumers"], ["230", "241", "251", "252", "260"])
        self.assertEqual(summary["foundation_consumers"], ["241", "260"])
        self.assertTrue(summary["third_attempt_blocked_manual"])
