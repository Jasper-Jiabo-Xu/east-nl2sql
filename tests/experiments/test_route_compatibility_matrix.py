from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RouteCompatibilityMatrixTests(unittest.TestCase):
    def test_six_routes_are_closed_and_only_databao_is_launchable(self) -> None:
        matrix = json.loads((ROOT / "contracts/experiments/route_compatibility_matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(matrix["matrix_id"], "EAS-125-six-route-dual-axis-v1")
        routes = matrix["routes"]
        self.assertEqual([route["baseline_id"] for route in routes], ["DeepEye-SQL", "DataGallery-Text2SQL", "JoyDataAgent-SQL", "Databao Agent", "ReFoRCE", "AutoLink"])
        launchable = [route for route in routes if route["overall_runnable"]]
        self.assertEqual(launchable, [{"baseline_id": "Databao Agent", "provider_compatibility": "SUPPORTED_NATIVE_CONFIG", "s0_method_compatibility": "SUPPORTED_NATIVE_METHOD", "overall_runnable": True, "launcher_contract": "databao_launcher_contract.json"}])
        self.assertEqual(routes[-1]["provider_compatibility"], "SUPPORTED_VIA_HASHED_PROVIDER_OVERLAY")
        self.assertEqual(routes[-1]["s0_method_compatibility"], "S0_METHOD_INCOMPATIBLE")
