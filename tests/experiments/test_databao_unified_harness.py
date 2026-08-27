from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.experiments.s0_harness import load_json, run_harness


FIXTURE_ROOT = ROOT / "fixtures" / "experiments" / "s0_synthetic"
CONTRACT = FIXTURE_ROOT / "experiment_contract.json"
RUN_MANIFEST = FIXTURE_ROOT / "baseline_run_manifest.template.json"


class _Endpoint(BaseHTTPRequestHandler):
    mode = "success"
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers["Content-Length"])
        _Endpoint.requests.append(json.loads(self.rfile.read(size)))
        if self.mode == "timeout":
            time.sleep(6)
            return
        if self.mode == "transport":
            self._write(503, {"error": {"message": "temporary transport failure"}})
            return
        if self.mode == "first_transport_then_success" and len(self.requests) == 1:
            self._write(503, {"error": {"message": "temporary transport failure"}})
            return
        if self.mode == "model":
            self._write(404, {"error": {"message": "model not found"}})
            return
        if self.mode == "empty":
            self._write(200, self._completion("", None))
            return
        number = len(self.requests) - (1 if self.mode == "first_transport_then_success" else 0)
        if number % 2:
            self._write(200, self._completion("run_sql_query", {"sql": "SELECT name FROM db1.main.items"}))
        else:
            self._write(200, self._completion("submit_result", {"query_id": "2-0", "result_description": "fixture", "visualization_prompt": ""}))

    @staticmethod
    def _completion(name: str, arguments: dict[str, object] | None) -> dict[str, object]:
        message: dict[str, object] = {"role": "assistant", "content": None}
        finish = "stop"
        if name:
            message["tool_calls"] = [{"id": "fixture-call", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]
            finish = "tool_calls"
        return {"id": "fixture", "object": "chat.completion", "created": 0, "model": "deepseek-v4-flash", "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    def _write(self, status: int, value: dict[str, object]) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class DatabaoUnifiedHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = os.environ.get("EAST_DATABAO_RUNTIME_PYTHON")
        self.source = os.environ.get("EAST_DATABAO_UPSTREAM_PATH")
        if not self.runtime or not self.source:
            self.skipTest("requires prepared Databao Python 3.11 runtime and pinned source path")

    def _run(self, endpoint_mode: str, harness_mode: str = "serial", question_count: int = 1, timeout: int = 60) -> tuple[dict[str, object], list[dict[str, object]], dict[str, str], set[str], int]:
        _Endpoint.mode, _Endpoint.requests = endpoint_mode, []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Endpoint)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                db = root / "fixture.duckdb"
                subprocess.run([self.runtime or "", "-c", "import duckdb,sys; c=duckdb.connect(sys.argv[1]); c.execute('create table items(name varchar)'); c.execute(\"insert into items values ('alpha')\"); c.close()", str(db)], check=True)
                dataset = root / "dataset.json"
                questions = [{"qa_id": f"fixture-{index}", "clear_question": f"List item names {index}", "public_semantic_schema": {"tables": ["items"]}} for index in range(1, question_count + 1)]
                dataset.write_text(json.dumps({"s0_boundary": "qa_id_clear_question_public_schema", "questions": questions}), encoding="utf-8")
                manifest = copy.deepcopy(load_json(RUN_MANIFEST))
                manifest["baselines"][3]["timeout_seconds"] = timeout
                manifest_path = root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                env = {"EAST_DATABAO_RUNTIME_PYTHON": self.runtime or "", "EAST_DATABAO_UPSTREAM_PATH": self.source or "", "EAST_DATABAO_READ_ONLY_DB_PATH": str(db), "EAST_DATABAO_FAKE_ENDPOINT": f"http://127.0.0.1:{server.server_port}/v1", "DEEPSEEK_API_KEY": "fixture-key-not-a-secret"}
                with patch.dict(os.environ, env, clear=False):
                    summary = run_harness(CONTRACT, dataset, manifest_path, root / "out", harness_mode)
                rows = [json.loads(line) for line in (root / "out" / "predictions.jsonl").read_text().splitlines()]
                trace_paths = {row["qa_id"]: root / "out" / row["trace_ref"] for row in rows if row["baseline_id"] == "Databao Agent"}
                traces = {qa_id: path.read_text(encoding="utf-8") for qa_id, path in trace_paths.items()}
                present_paths = {str(path.relative_to(root / "out")) for path in (root / "out" / "runs" / "databao_agent").rglob("*")}
                request_count = len(_Endpoint.requests)
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
        return summary, rows, traces, present_paths, request_count

    def test_common_prediction_consumes_actual_databao_launcher(self) -> None:
        summary, rows, traces, _, request_count = self._run("success")
        databao = next(row for row in rows if row["baseline_id"] == "Databao Agent")
        self.assertEqual((summary["candidate_count"], summary["failure_count"]), (1, 5))
        self.assertEqual(databao["execution_status"], "ok")
        self.assertEqual(databao["token_calls"], 2)
        self.assertEqual(databao["token_total"], 4)
        self.assertNotIn("fixture-key-not-a-secret", traces["fixture-1"])
        self.assertEqual(request_count, 2)

    def test_fake_endpoint_failure_matrix_is_secret_safe(self) -> None:
        for mode, expected, harness_attempt, endpoint_requests in (("model", "MODEL_UNAVAILABLE", 1, 1), ("empty", "INVALID_BASELINE_OUTPUT", 1, 1), ("timeout", "TIMEOUT", 1, 1)):
            with self.subTest(mode=mode):
                _, rows, traces, _, request_count = self._run(mode, timeout=5 if mode == "timeout" else 60)
                databao = next(row for row in rows if row["baseline_id"] == "Databao Agent")
                self.assertEqual((databao["failure_code"], databao["attempt"]), (expected, harness_attempt))
                self.assertNotIn("fixture-key-not-a-secret", traces["fixture-1"])
                self.assertEqual(request_count, endpoint_requests)

    def test_two_questions_have_unique_safe_attempt_namespaces_in_serial_and_concurrent_modes(self) -> None:
        for harness_mode in ("serial", "concurrent"):
            with self.subTest(harness_mode=harness_mode):
                summary, rows, traces, present_paths, request_count = self._run("success", harness_mode=harness_mode, question_count=2)
                databao = [row for row in rows if row["baseline_id"] == "Databao Agent"]
                self.assertEqual((summary["candidate_count"], summary["failure_count"]), (2, 10))
                self.assertEqual([row["qa_id"] for row in databao], ["fixture-1", "fixture-2"])
                self.assertEqual({row["attempt"] for row in databao}, {1})
                self.assertEqual(len({row["trace_ref"] for row in databao}), 2)
                self.assertEqual(request_count, 4)
                for row in databao:
                    self.assertRegex(row["trace_ref"], r"^runs/databao_agent/qa-[0-9a-f]{20}/attempt-01/trace\.json$")
                    self.assertNotIn("List item names", row["trace_ref"])
                    self.assertNotIn("fixture-key-not-a-secret", traces[row["qa_id"]])
                    self.assertIn(row["trace_ref"], present_paths)

    def test_transport_retry_for_first_question_does_not_consume_second_question_attempt_one(self) -> None:
        for harness_mode in ("serial", "concurrent"):
            with self.subTest(harness_mode=harness_mode):
                summary, rows, _, present_paths, request_count = self._run("first_transport_then_success", harness_mode=harness_mode, question_count=2)
                databao = {row["qa_id"]: row for row in rows if row["baseline_id"] == "Databao Agent"}
                self.assertEqual((summary["candidate_count"], summary["failure_count"]), (2, 10))
                self.assertEqual((databao["fixture-1"]["execution_status"], databao["fixture-1"]["attempt"]), ("ok", 2))
                self.assertEqual((databao["fixture-2"]["execution_status"], databao["fixture-2"]["attempt"]), ("ok", 1))
                self.assertEqual(request_count, 5)
                first_namespace = Path(databao["fixture-1"]["trace_ref"]).parents[1]
                second_namespace = Path(databao["fixture-2"]["trace_ref"]).parents[1]
                self.assertNotEqual(first_namespace, second_namespace)
                self.assertIn(str(first_namespace / "attempt-01"), present_paths)
                self.assertIn(str(first_namespace / "attempt-02"), present_paths)
                self.assertIn(str(second_namespace / "attempt-01"), present_paths)

    def test_only_transport_error_retries_three_times(self) -> None:
        from east_v5.experiments import s0_harness

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "placeholder.duckdb"
            db.write_bytes(b"fixture")
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"s0_boundary": "qa_id_clear_question_public_schema", "questions": [{"qa_id": "fixture-1", "clear_question": "x", "public_semantic_schema": {}}]}), encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(load_json(RUN_MANIFEST)), encoding="utf-8")
            env = {"EAST_DATABAO_RUNTIME_PYTHON": self.runtime or "", "EAST_DATABAO_UPSTREAM_PATH": self.source or "", "EAST_DATABAO_READ_ONLY_DB_PATH": str(db), "DEEPSEEK_API_KEY": "fixture-key-not-a-secret"}
            completed = subprocess.CompletedProcess(args=[], returncode=2, stdout="DATABAO_TRANSPORT_ERROR\n", stderr="")
            with patch.dict(os.environ, env, clear=False), patch.object(s0_harness.subprocess, "run", return_value=completed) as run:
                summary = run_harness(CONTRACT, dataset, manifest_path, root / "out", "serial")
                databao = next(row for row in [json.loads(line) for line in (root / "out" / "predictions.jsonl").read_text().splitlines()] if row["baseline_id"] == "Databao Agent")
        self.assertEqual((databao["failure_code"], databao["attempt"], run.call_count), ("TRANSPORT_ERROR", 3, 3))
        self.assertEqual(summary["failure_count"], 6)


if __name__ == "__main__":
    unittest.main()
