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
try:
    closure_cases = importlib.import_module("tests.agents.220.test_closure")
except ModuleNotFoundError:
    closure_cases = importlib.import_module("agents.220.test_closure")


class ProbeTests(unittest.TestCase):
    def test_probe_recomputes_two_dynamic_event_packages_and_foundation_package(self):
        first = closure_cases.event_source(run_id="220-probe-first")
        second = closure_cases.event_source(run_id="220-probe-second")
        events = [
            {"reviewed_question_sql": first[0], "event_query_context": first[1]},
            {"reviewed_question_sql": second[0], "event_query_context": second[1]},
        ]
        foundation = json.loads((ROOT / "fixtures" / "artifacts" / "foundation-task-package-valid.json").read_text(encoding="utf-8"))
        summary = probe.run_sanitized_probe(events, foundation)["summary"]
        self.assertEqual(len(summary["event_refs"]), 2)
        self.assertNotEqual(summary["event_refs"][0], summary["event_refs"][1])
        self.assertEqual(summary["event_consumers"], ["230", "241", "251", "252", "260"])
        self.assertEqual(summary["foundation_consumers"], ["241", "260"])
        self.assertTrue(summary["third_attempt_blocked_manual"])
