from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.experiments.s0_harness import HarnessError, _fixture_worktrees, consume_s0_manifest, load_json, run_harness, validate_dataset_manifest, validate_run_manifest


FIXTURE_ROOT = ROOT / "fixtures" / "experiments" / "s0_synthetic"
CONTRACT = FIXTURE_ROOT / "experiment_contract.json"
DATASET = FIXTURE_ROOT / "dataset_manifest.json"
RUN_MANIFEST = FIXTURE_ROOT / "baseline_run_manifest.template.json"


def write_json(root: Path, name: str, value: object) -> Path:
    path = root / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class S0HarnessTest(unittest.TestCase):
    def _native_manifest(self, root: Path) -> tuple[dict[str, object], Path, Path]:
        """Create an attested six-worktree shape without cloning or contacting upstream."""
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        worktree_root = _fixture_worktrees(manifest, root / "pinned-worktrees")
        for baseline in manifest["baselines"]:
            baseline["transport_mode"] = "native"
        return manifest, write_json(root, "native-manifest.json", manifest), worktree_root

    def test_native_keyed_launch_consumes_six_attested_worktrees_and_unified_output(self) -> None:
        expected_outer = {
            "DeepEye-SQL": "deepeye_result",
            "DataGallery-Text2SQL": "dataagent_result",
            "JoyDataAgent-SQL": "joydata_answer",
            "Databao Agent": "databao_thread",
            "ReFoRCE": "reforce_result",
            "AutoLink": "autolink_result",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fixture-key-never-persist"}, clear=False):
            root = Path(tmp)
            manifest, manifest_path, worktree_root = self._native_manifest(root)
            summary = run_harness(CONTRACT, DATASET, manifest_path, root / "out", "serial", worktree_root)
            predictions = [json.loads(line) for line in (root / "out" / "predictions.jsonl").read_text().splitlines()]
            for baseline in manifest["baselines"]:
                safe = baseline["baseline_id"].lower().replace(" ", "_").replace("-", "_")
                raw = (root / "out" / "runs" / safe / "attempt-01" / "raw_predictions.jsonl").read_text()
                trace = (root / "out" / "runs" / safe / "attempt-01" / "trace.json").read_text()
                provider = json.loads((root / "out" / "runs" / safe / "attempt-01" / "deepseek-provider.json").read_text())
                self.assertIn(expected_outer[baseline["baseline_id"]], raw)
                self.assertIn(str(worktree_root / baseline["upstream_evidence"]["worktree_id"]), trace)
                self.assertNotIn("fixture-key-never-persist", trace)
                self.assertEqual(provider, baseline["model_contract"])
        self.assertEqual((summary["baseline_count"], summary["candidate_count"], summary["failure_count"]), (6, 12, 0))
        self.assertTrue(all(row["token_calls"] == 1 and row["attempt"] == 1 for row in predictions))

    def test_attested_native_worktree_rejects_lock_and_source_drift(self) -> None:
        for drift, expected in (("lock", "NATIVE_WORKTREE_LOCK_DRIFT"), ("source", "NATIVE_WORKTREE_SOURCE_DRIFT")):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fixture-key"}, clear=False):
                root = Path(tmp)
                manifest, manifest_path, worktree_root = self._native_manifest(root)
                evidence = manifest["baselines"][0]["upstream_evidence"]
                tree = worktree_root / evidence["worktree_id"]
                target = tree / ("east-upstream-lock.json" if drift == "lock" else evidence["entrypoint_path"])
                target.write_text(json.dumps({"repo_url": "https://invalid.example", "commit": "0" * 40}) if drift == "lock" else "drift", encoding="utf-8")
                with self.assertRaises(HarnessError) as raised:
                    run_harness(CONTRACT, DATASET, manifest_path, root / "out", "serial", worktree_root)
            self.assertEqual(raised.exception.code, expected)

    def test_provider_contract_is_closed_and_model_reasoning_is_pinned(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["model_contract"]["extra"] = True
        with self.assertRaises(HarnessError) as raised:
            validate_run_manifest(manifest)
        self.assertEqual(raised.exception.code, "INVALID_DEEPSEEK_PROVIDER")
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["model_contract"]["reasoning_enabled"] = True
        with self.assertRaises(HarnessError) as raised:
            validate_run_manifest(manifest)
        self.assertEqual(raised.exception.code, "DEEPSEEK_CONTRACT_DRIFT")

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

    def test_real_transport_requires_environment_only_deepseek_key(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["transport_mode"] = "native"
        manifest["baselines"][0]["command"] = ["{python}", "{upstream_worktree}/runner/run.py", "--dataset", "{dataset}", "--output-jsonl", "{output_jsonl}", "--provider-config", "{provider_config}"]
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            path = write_json(Path(tmp), "manifest.json", manifest)
            with self.assertRaises(HarnessError) as raised:
                run_harness(CONTRACT, DATASET, path, Path(tmp) / "out", "serial")
        self.assertEqual(raised.exception.code, "DEEPSEEK_AUTH_MISSING")

    def test_model_unavailable_and_invalid_native_response_do_not_retry(self) -> None:
        for mode, code in (("model-unavailable", "MODEL_UNAVAILABLE"), ("invalid-response", "INVALID_BASELINE_OUTPUT")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest = copy.deepcopy(load_json(RUN_MANIFEST))
                manifest["baselines"][0]["command"].extend(["--mode", mode])
                summary = run_harness(CONTRACT, DATASET, write_json(root, "manifest.json", manifest), root / "out", "serial")
                rows = (root / "out" / "predictions.jsonl").read_text(encoding="utf-8")
                self.assertEqual(summary["failure_count"], 1)
                self.assertIn('"failure_code": "' + code + '"', rows)
                self.assertFalse((root / "out" / "runs" / "deepeye_sql" / "attempt-02").exists())

    def test_trace_redacts_environment_key_and_all_six_adapters_stay_isolated(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["command"].extend(["--mode", "echo-secret"])
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-secret-never-persist"}, clear=False):
            root = Path(tmp)
            summary = run_harness(CONTRACT, DATASET, write_json(root, "manifest.json", manifest), root / "out", "serial")
            trace = (root / "out" / "runs" / "deepeye_sql" / "attempt-03" / "trace.json").read_text(encoding="utf-8")
        self.assertEqual(summary["failure_count"], 1)
        self.assertTrue(summary["trace_refs_by_baseline_isolated"])
        self.assertNotIn("test-secret-never-persist", trace)

    def test_missing_endpoint_fails_closed(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["model_contract"]["base_url"] = ""

        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(Path(tmp), "manifest.json", manifest)
            with self.assertRaises(HarnessError) as raised:
                validate_run_manifest(load_json(path))

        self.assertEqual(raised.exception.code, "MISSING_ENDPOINT")

    def test_native_mode_rejects_generic_or_unpinned_wrapper(self) -> None:
        manifest = copy.deepcopy(load_json(RUN_MANIFEST))
        manifest["baselines"][0]["transport_mode"] = "native"
        manifest["baselines"][0]["command"] = ["{python}", "-m", "east_v5.experiments.fixture_baseline"]
        with self.assertRaises(HarnessError) as raised:
            validate_run_manifest(manifest)
        self.assertEqual(raised.exception.code, "GENERIC_WRAPPER_PROHIBITED")

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
