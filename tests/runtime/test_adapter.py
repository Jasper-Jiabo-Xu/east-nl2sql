from __future__ import annotations

import json
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref
from east_v5.runtime import RuntimeAdapter, RuntimeAdapterError

FormalReleaseCommitter = importlib.import_module("east_v5.agents.010.committer").FormalReleaseCommitter


class RuntimeAdapterTests(unittest.TestCase):
    def test_registers_then_reads_back_before_dispatch(self) -> None:
        payload = json.loads((ROOT / "fixtures" / "penalty" / "matched.json").read_text(encoding="utf-8"))
        package = FormalReleaseCommitter(ROOT).build_penalty_source_package(payload, run_id="probe-run", trace_id="probe-trace", created_at="2026-08-18T00:00:00+00:00", attempt_no=2)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            roots = {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}
            envelope = {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": "EAS-70", "run_id": "probe-run", "trace_id": "probe-trace", "qa_id": package["envelope"]["qa_id"], "attempt": 2, "target_agent_id": "010", "target_agent_uuid": "010-uuid", "root_binding_id": "b" * 64, "input_ref": artifact_ref(package["envelope"]), "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "110"}}
            result = RuntimeAdapter(ROOT, roots, envelope).register_output(package, task_id="task-010", runtime_id="runtime-010")
            self.assertEqual(result["next_dispatch"]["target"], "110")
            self.assertEqual(result["receipt"]["output_ref"], artifact_ref(package["envelope"]))
            consumer_envelope = {**envelope, "target_agent_id": "110", "target_agent_uuid": "110-uuid", "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "120"}}
            consumed = RuntimeAdapter(ROOT, roots, consumer_envelope).consume_input(task_id="task-110", runtime_id="runtime-110")
            self.assertEqual(consumed["next_dispatch"]["target"], "120")
            self.assertEqual(consumed["receipt"]["output_ref"], artifact_ref(package["envelope"]))

    def test_rejects_route_before_registry_write(self) -> None:
        envelope = {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": "EAS-70", "run_id": "probe-run", "trace_id": "probe-trace", "qa_id": "QA", "attempt": 2, "target_agent_id": "010", "target_agent_uuid": "010-uuid", "root_binding_id": "b" * 64, "input_ref": {"artifact_id": "source", "version": 1, "content_hash": "a" * 64}, "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "110"}}
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            adapter = RuntimeAdapter(ROOT, roots, envelope)
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_OUTPUT_TRANSPORT_INVALID"):
                adapter.register_output({}, task_id="task", runtime_id="runtime")


if __name__ == "__main__":
    unittest.main()
