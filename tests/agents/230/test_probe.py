from __future__ import annotations

import sys
import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class ProbeTests(unittest.TestCase):
    def test_sanitized_probe_covers_success_and_rejections(self) -> None:
        summary = importlib.import_module("east_v5.agents.230.probe").run_sanitized_probe()["summary"]
        self.assertEqual(summary["consumers"], ["241", "251"])
        self.assertTrue(summary["foundation_rejected"])
        self.assertTrue(summary["hash_drift_rejected"])
        self.assertTrue(summary["dependency_cycle_rejected"])
        self.assertTrue(summary["third_attempt_blocked_manual"])


if __name__ == "__main__":
    unittest.main()
