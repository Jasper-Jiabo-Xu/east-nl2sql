from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.experiments.s0_harness import HarnessError, consume_s0_manifest, load_json, run_harness, validate_dataset_manifest, validate_run_manifest


FIXTURE_ROOT = ROOT / "fixtures" / "experiments" / "s0_synthetic"
CONTRACT = FIXTURE_ROOT / "experiment_contract.json"
DATASET = FIXTURE_ROOT / "dataset_manifest.json"
RUN_MANIFEST = FIXTURE_ROOT / "baseline_run_manifest.template.json"


def write_json(root: Path, name: str, value: object) -> Path:
    path = root / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class S0HarnessTest(unittest.TestCase):
    def test_five_incompatible_routes_fail_before_key_or_worktree_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            summary = run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "out", "serial")
            rows = [json.loads(line) for line in (root / "out" / "predictions.jsonl").read_text().splitlines()]
            self.assertFalse((root / "out" / ".fixture-native-worktrees").exists())
        rejected = [row for row in rows if row["baseline_id"] != "Databao Agent"]
        self.assertEqual(len(rejected), 10)
        self.assertTrue(all(row["failure_code"] == "S0_METHOD_INCOMPATIBLE" and row["token_calls"] == 0 for row in rejected))
        self.assertEqual(summary["candidate_count"], 0)
        self.assertEqual(summary["failure_count"], 12)

    def test_databao_missing_runtime_is_stable_and_five_routes_remain_preflight_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "out", "serial")
            rows = [json.loads(line) for line in (root / "out" / "predictions.jsonl").read_text().splitlines()]
            self.assertTrue((root / "out" / "runs" / "databao_agent" / "attempt-01" / "trace.json").exists())
        databao = [row for row in rows if row["baseline_id"] == "Databao Agent"]
        self.assertEqual({row["failure_code"] for row in databao}, {"DATABAO_RUNTIME_OR_DB_UNAVAILABLE"})

    def test_route_matrix_drift_rejects_before_run(self) -> None:
        matrix = load_json(ROOT / "contracts/experiments/route_compatibility_matrix.json")
        matrix["routes"][0]["overall_runnable"] = True
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix_path = write_json(root, "matrix.json", matrix)
            with self.assertRaises(HarnessError) as raised:
                run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "out", "serial", route_matrix_path=matrix_path)
        self.assertEqual(raised.exception.code, "ROUTE_MATRIX_RUNNABILITY_DRIFT")

    def test_provider_contract_and_adapter_enum_are_closed(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["native_adapter_id"] = "surprise_native_v1"
        with self.assertRaises(HarnessError) as raised:
            validate_run_manifest(manifest)
        self.assertEqual(raised.exception.code, "UNKNOWN_NATIVE_ADAPTER")
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["model_contract"]["extra"] = True
        with self.assertRaises(HarnessError) as raised:
            validate_run_manifest(manifest)
        self.assertEqual(raised.exception.code, "INVALID_DEEPSEEK_PROVIDER")

    def test_dataset_leakage_and_receipt_drift_fail_closed(self) -> None:
        dataset = load_json(DATASET)
        dataset["questions"][0]["gold_sql"] = "SELECT prohibited"
        with self.assertRaises(HarnessError) as raised:
            validate_dataset_manifest(dataset)
        self.assertEqual(raised.exception.code, "S0_LEAKAGE_FIELD")
        receipt = {"s0_dataset_manifest_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "s0_bytes": len(DATASET.read_bytes()), "anchors": [{"qa_id": question["qa_id"]} for question in load_json(DATASET)["questions"]]}
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(consume_s0_manifest(DATASET, write_json(Path(temporary), "receipt.json", receipt))["question_count"], 2)

    def test_duplicate_output_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "out", "serial")
            with self.assertRaises(HarnessError) as raised:
                run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "out", "serial")
        self.assertEqual(raised.exception.code, "DUPLICATE_ATTEMPT")


if __name__ == "__main__":
    unittest.main()
