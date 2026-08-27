from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class _FakeChatEndpoint(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers["Content-Length"])
        _FakeChatEndpoint.requests.append(json.loads(self.rfile.read(length)))
        body = self._response(len(_FakeChatEndpoint.requests))
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _response(number: int) -> dict[str, object]:
        if number == 1:
            name, args, call_id = "run_sql_query", {"sql": "SELECT name FROM db1.main.items"}, "fixture-run"
        else:
            name, args, call_id = "submit_result", {"query_id": "2-0", "result_description": "fixture", "visualization_prompt": ""}, "fixture-submit"
        return {
            "id": f"fixture-{number}",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def log_message(self, _format: str, *_args: object) -> None:
        return


class DatabaoNativeLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = os.environ.get("EAST_DATABAO_RUNTIME_PYTHON")
        self.source = os.environ.get("EAST_DATABAO_UPSTREAM_PATH")
        if not self.runtime or not self.source:
            self.skipTest("requires prepared Databao Python 3.11 runtime and pinned source path")

    def _child_env(self, endpoint: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "EAST_DATABAO_UPSTREAM_PATH": self.source or "",
                "EAST_DATABAO_FAKE_ENDPOINT": endpoint,
                "DEEPSEEK_API_KEY": "fixture-key-not-a-secret",
                "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), self.source or ""]),
            }
        )
        return env

    def test_native_thread_consumes_fake_endpoint_and_returns_thread_code(self) -> None:
        _FakeChatEndpoint.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeChatEndpoint)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        endpoint = f"http://127.0.0.1:{server.server_port}/v1"
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                db = root / "fixture.duckdb"
                subprocess.run([self.runtime or "", "-c", "import duckdb,sys; c=duckdb.connect(sys.argv[1]); c.execute('create table items(name varchar)'); c.execute(\"insert into items values ('alpha')\"); c.close()", str(db)], check=True)
                request = root / "request.json"
                output = root / "prediction.json"
                request.write_text(json.dumps({"qa_id": "fixture-1", "question": "List item names", "public_schema": {"tables": ["items"]}, "read_only_db_path": str(db)}), encoding="utf-8")
                completed = subprocess.run([self.runtime or "", "-m", "east_v5.experiments.databao_launcher", "--request", str(request), "--output", str(output)], env=self._child_env(endpoint), text=True, capture_output=True, timeout=90, check=False)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                prediction = json.loads(output.read_text(encoding="utf-8"))
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
        self.assertEqual(prediction["sql"], "SELECT name FROM db1.main.items")
        self.assertEqual(prediction["native_method"], "bao.agent.thread.ask.thread.code")
        self.assertEqual(prediction["provider_endpoint"], "fixture")
        self.assertEqual(len(_FakeChatEndpoint.requests), 2)
        self.assertEqual(_FakeChatEndpoint.requests[0]["model"], "deepseek-v4-flash")
        self.assertIn("tools", _FakeChatEndpoint.requests[0])
        self.assertNotIn("fixture-key-not-a-secret", json.dumps(_FakeChatEndpoint.requests))

    def test_missing_auth_fails_before_loading_runtime(self) -> None:
        env = os.environ.copy()
        env.pop("DEEPSEEK_API_KEY", None)
        env["EAST_DATABAO_UPSTREAM_PATH"] = self.source or ""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "request.json"
            request.write_text(json.dumps({"qa_id": "fixture-1", "question": "x", "public_schema": {}, "read_only_db_path": str(root / "absent.duckdb")}), encoding="utf-8")
            completed = subprocess.run([self.runtime or "", "-m", "east_v5.experiments.databao_launcher", "--request", str(request), "--output", str(root / "output.json")], env=env, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("DATABAO_READ_ONLY_DB_REQUIRED", completed.stdout)

    def test_upstream_commit_drift_fails_before_native_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "placeholder.duckdb"
            db.write_bytes(b"not opened because source attestation fails first")
            request = root / "request.json"
            request.write_text(json.dumps({"qa_id": "fixture-1", "question": "x", "public_schema": {}, "read_only_db_path": str(db)}), encoding="utf-8")
            env = os.environ.copy()
            env.update({"EAST_DATABAO_UPSTREAM_PATH": str(ROOT), "DEEPSEEK_API_KEY": "fixture-key-not-a-secret", "PYTHONPATH": str(ROOT / "src")})
            completed = subprocess.run([self.runtime or "", "-m", "east_v5.experiments.databao_launcher", "--request", str(request), "--output", str(root / "output.json")], env=env, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("DATABAO_UPSTREAM_COMMIT_DRIFT", completed.stdout)


if __name__ == "__main__":
    unittest.main()
