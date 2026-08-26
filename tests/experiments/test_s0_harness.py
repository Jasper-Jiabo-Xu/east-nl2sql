from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

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
    def test_concurrent_and_serial_runs_have_same_collection_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            concurrent_summary = run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "concurrent", "concurrent")
            serial_summary = run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "serial", "serial")

        self.assertEqual(concurrent_summary["baseline_count"], 6)
        self.assertEqual(concurrent_summary["candidate_count"], 12)
        self.assertEqual(concurrent_summary["failure_count"], 0)
        self.assertEqual(concurrent_summary["collection_hash"], serial_summary["collection_hash"])
        self.assertTrue(concurrent_summary["cache_namespaces_unique"])
        self.assertTrue(concurrent_summary["trace_refs_by_baseline_isolated"])

    def test_leakage_field_fails_closed(self) -> None:
        dataset = load_json(DATASET)
        dataset["questions"][0]["gold_sql"] = "SELECT should_not_be_visible"

        with tempfile.TemporaryDirectory() as tmp:
            leaked = write_json(Path(tmp), "dataset.json", dataset)
            with self.assertRaises(HarnessError) as raised:
                validate_dataset_manifest(load_json(leaked))

        self.assertEqual(raised.exception.code, "S0_LEAKAGE_FIELD")

    def test_unknown_model_id_fails_closed(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["model_contract"]["model_id"] = "gpt-agent-runtime"

        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "manifest.json", manifest)
            with self.assertRaises(HarnessError) as raised:
                validate_run_manifest(load_json(path))

        self.assertEqual(raised.exception.code, "UNKNOWN_MODEL_ID")

    def test_missing_endpoint_fails_closed(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["model_contract"]["base_url"] = ""

        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "manifest.json", manifest)
            with self.assertRaises(HarnessError) as raised:
                validate_run_manifest(load_json(path))

        self.assertEqual(raised.exception.code, "MISSING_ENDPOINT")

    def test_cache_namespace_collision_fails_closed(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][1]["cache_namespace"] = manifest["baselines"][0]["cache_namespace"]

        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "manifest.json", manifest)
            with self.assertRaises(HarnessError) as raised:
                validate_run_manifest(load_json(path))

        self.assertEqual(raised.exception.code, "CACHE_NAMESPACE_COLLISION")

    def test_illegal_sql_output_is_stable_failure_code(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["command"].extend(["--mode", "illegal-sql"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = write_json(root, "manifest.json", manifest)
            summary = run_harness(CONTRACT, DATASET, manifest_path, root / "out", "serial")
            rows = (root / "out" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual(summary["failure_count"], 1)
        self.assertIn('"failure_code": "ILLEGAL_SQL_OUTPUT"', "\n".join(rows))

    def test_duplicate_attempt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "out", "serial")
            with self.assertRaises(HarnessError) as raised:
                run_harness(CONTRACT, DATASET, RUN_MANIFEST, root / "out", "serial")

        self.assertEqual(raised.exception.code, "DUPLICATE_ATTEMPT")

    def test_transport_errors_retry_three_times(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["command"].extend(["--mode", "transport-error"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = write_json(root, "manifest.json", manifest)
            summary = run_harness(CONTRACT, DATASET, manifest_path, root / "out", "serial")

        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["candidate_count"], 10)

    def test_budget_exceeded_is_stable_failure_code(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["command"].extend(["--mode", "budget-exceeded"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = write_json(root, "manifest.json", manifest)
            summary = run_harness(CONTRACT, DATASET, manifest_path, root / "out", "serial")
            rows = (root / "out" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual(summary["failure_count"], 1)
        self.assertIn('"failure_code": "BUDGET_EXCEEDED"', "\n".join(rows))

    def test_timeout_is_stable_failure_code(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["timeout_seconds"] = 1
        manifest["baselines"][0]["command"].extend(["--mode", "sleep"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = write_json(root, "manifest.json", manifest)
            summary = run_harness(CONTRACT, DATASET, manifest_path, root / "out", "serial")
            rows = (root / "out" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual(summary["failure_count"], 1)
        self.assertIn('"failure_code": "TIMEOUT"', "\n".join(rows))

    def test_consume_s0_receipt_and_reject_mutation(self) -> None:
        dataset = load_json(DATASET)
        receipt = {
            "s0_dataset_manifest_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            "s0_bytes": len(DATASET.read_bytes()),
            "anchors": [{"qa_id": question["qa_id"]} for question in dataset["questions"]],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt_path = write_json(root, "receipt.json", receipt)
            summary = consume_s0_manifest(DATASET, receipt_path)
            self.assertEqual(summary["question_count"], 2)

            mutated = copy.deepcopy(dataset)
            mutated["questions"][0]["answer_contract"] = {"forbidden": True}
            mutated_path = write_json(root, "mutated.json", mutated)
            with self.assertRaises(HarnessError) as raised:
                consume_s0_manifest(mutated_path, receipt_path)

        self.assertEqual(raised.exception.code, "S0_LEAKAGE_FIELD")


if __name__ == "__main__":
    unittest.main()
