"""Fail-closed tests for the no-argument Multica Foundation task reader."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from east_v5.runtime import foundation_task_context as context_module
from east_v5.runtime.foundation_task_context import FoundationTaskContextError
from east_v5.runtime.bootstrap import RuntimeBootstrap, RuntimeBootstrapError


AGENTS = {
    "241": "7df640f9-973f-4c46-8302-df1256f60146",
    "242": "4e801c18-7048-4227-a5c7-515f51a5e5ba",
    "260": "f89e7039-e213-4e1e-9204-64f7ce69ac1c",
}
RUNTIME = "0e5e9dd9-5135-4937-bb03-92b77adb8395"
WORKSPACE = "82db0426-715a-47e1-a66f-15b479c47654"
JIA_BO = "18b0369f-00c7-4db0-a313-e1fb4383cb08"


class FoundationTaskContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(__file__).resolve().parents[2].parent / f".foundation-task-context-{os.urandom(8).hex()}"
        self.directory.mkdir(mode=0o700)
        self.work_dir = self.directory / "task-work"; self.work_dir.mkdir(mode=0o700)
        self.checkout = self.work_dir / "checkout"; self.checkout.mkdir(mode=0o700)
        self.config = self.directory / "config"; self.config.mkdir(mode=0o700)
        self.role = "241"
        self.task_id = "real-multica-task-241"
        self.environ = {
            "MULTICA_TASK_ID": self.task_id,
            "MULTICA_AGENT_ID": AGENTS[self.role],
            "MULTICA_WORKSPACE_ID": WORKSPACE,
            "MULTICA_TASK_CONFIG_ROOT": str(self.config),
            "MULTICA_DAEMON_PORT": "48123",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def _task(self) -> dict[str, object]:
        return {
            "id": self.task_id,
            "issue_id": "01a0389c-5fe5-7a27-a214-574cd66d9a2e",
            "agent_id": AGENTS[self.role],
            "runtime_id": RUNTIME,
            "status": "running",
            "attempt": 1,
            "trigger_comment_id": "human-trigger-comment",
            "work_dir": str(self.work_dir),
            "attribution": {
                "source": "delegation", "precise": True,
                "initiator": {"id": JIA_BO}, "originator": {"id": JIA_BO},
            },
        }

    def _run(self, records: object | None = None):
        payload = [self._task()] if records is None else records
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload))
        return patch.object(context_module.subprocess, "run", return_value=completed)

    def _current(self, records: object | None = None):
        with patch.dict(os.environ, self.environ, clear=True), self._run(records) as runner, patch.object(context_module, "_checkout", return_value=self.checkout):
            value = context_module.current_foundation_task()
        runner.assert_called_once_with(
            ["multica", "agent", "tasks", AGENTS[self.role], "--output", "json"],
            check=True, capture_output=True, text=True,
        )
        return value

    def test_each_fixed_role_is_derived_from_the_real_task_not_a_caller_argument(self) -> None:
        for role in AGENTS:
            self.role = role
            self.task_id = f"real-multica-task-{role}"
            self.environ["MULTICA_AGENT_ID"] = AGENTS[role]
            self.environ["MULTICA_TASK_ID"] = self.task_id
            task = self._current()
            self.assertEqual((task.task_id, task.agent_id, task.role, task.runtime_id), (self.task_id, AGENTS[role], role, RUNTIME))
        with self.assertRaises(TypeError):
            context_module.current_foundation_task({})  # type: ignore[call-arg]

    def test_env_and_task_identity_drift_fail_before_any_bootstrap_state(self) -> None:
        cases: list[tuple[str, dict[str, object] | None, dict[str, str] | None, str]] = [
            ("missing-env", None, {"MULTICA_TASK_ID": ""}, "ENV_MISSING"),
            ("wrong-agent", {"agent_id": AGENTS["242"]}, None, "TASK_DRIFT"),
            ("wrong-issue", {"issue_id": "other"}, None, "TASK_DRIFT"),
            ("wrong-runtime", {"runtime_id": "other"}, None, "TASK_DRIFT"),
            ("terminal", {"status": "completed"}, None, "TASK_DRIFT"),
            ("bad-attempt", {"attempt": 0}, None, "TASK_DRIFT"),
            ("missing-trigger", {"trigger_comment_id": ""}, None, "TASK_INVALID"),
        ]
        for _name, change, environment_change, code in cases:
            with self.subTest(_name):
                task = self._task()
                if change:
                    task.update(change)
                environment = dict(self.environ)
                if environment_change:
                    environment.update(environment_change)
                completed = subprocess.CompletedProcess([], 0, stdout=json.dumps([task]))
                with patch.dict(os.environ, environment, clear=True), patch.object(context_module.subprocess, "run", return_value=completed), patch.object(context_module, "_checkout", return_value=self.checkout):
                    with self.assertRaisesRegex(FoundationTaskContextError, code):
                        context_module.current_foundation_task()

    def test_attribution_workdir_and_uniqueness_are_fail_closed(self) -> None:
        bad_attribution = self._task()
        bad_attribution["attribution"] = {"source": "delegation", "precise": True, "initiator": {"id": "other"}, "originator": {"id": JIA_BO}}
        outside = self._task(); outside["work_dir"] = "."
        duplicate = [self._task(), self._task()]
        for records, code in (([bad_attribution], "ATTRIBUTION_DRIFT"), ([outside], "WORKDIR_INVALID"), (duplicate, "TASK_MATCH_INVALID")):
            with self.subTest(code):
                completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(records))
                with patch.dict(os.environ, self.environ, clear=True), patch.object(context_module.subprocess, "run", return_value=completed), patch.object(context_module, "_checkout", return_value=self.checkout):
                    with self.assertRaisesRegex(FoundationTaskContextError, code):
                        context_module.current_foundation_task()

    def test_task_list_shape_is_strict(self) -> None:
        for records, code in (({"tasks": [self._task()]}, "TASK_LIST_INVALID"), ([], "TASK_MATCH_INVALID"), (["not-an-object"], "TASK_MATCH_INVALID")):
            with self.subTest(code):
                completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(records))
                with patch.dict(os.environ, self.environ, clear=True), patch.object(context_module.subprocess, "run", return_value=completed), patch.object(context_module, "_checkout", return_value=self.checkout):
                    with self.assertRaisesRegex(FoundationTaskContextError, code):
                        context_module.current_foundation_task()

    def test_production_bootstrap_has_no_envelope_or_root_override_parameter(self) -> None:
        task = context_module.VerifiedFoundationTask(
            task_id=self.task_id, issue_id="01a0389c-5fe5-7a27-a214-574cd66d9a2e", agent_id=AGENTS["241"], role="241", workspace_id=WORKSPACE,
            runtime_id=RUNTIME, attempt=1, trigger_comment_id="human-trigger-comment", work_dir=self.work_dir,
        )
        head = "a" * 40

        def git_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[-2:] in (["rev-parse", "HEAD"], ["rev-parse", "refs/remotes/origin/main"]):
                return subprocess.CompletedProcess(args, 0, stdout=head)
            if args[-2:] == ["rev-parse", "HEAD^"]:
                return subprocess.CompletedProcess(args, 0, stdout="b" * 40)
            if args[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(args, 0, stdout="")
            raise AssertionError(args)

        from east_v5.runtime import bootstrap as bootstrap_module
        with patch.object(context_module, "current_foundation_task", return_value=task), patch.object(bootstrap_module.subprocess, "run", side_effect=git_runner):
            # The factory obtains the task reader dynamically; only the Git
            # evidence runner is replaced, never an identity/envelope input.
            with patch.object(bootstrap_module, "_file_sha", return_value="c" * 64):
                bootstrap = RuntimeBootstrap.from_current_foundation_task()
        self.assertEqual(bootstrap.environ, {})
        self.assertEqual(bootstrap.declaration["runtime_context"]["workspace_id"], WORKSPACE)
        with self.assertRaises(TypeError):
            RuntimeBootstrap.from_current_foundation_task({})  # type: ignore[call-arg]

    def test_untracked_checkout_drift_is_rejected_before_root_provisioning(self) -> None:
        task = context_module.VerifiedFoundationTask(
            task_id=self.task_id, issue_id="01a0389c-5fe5-7a27-a214-574cd66d9a2e", agent_id=AGENTS["241"], role="241", workspace_id=WORKSPACE,
            runtime_id=RUNTIME, attempt=1, trigger_comment_id="human-trigger-comment", work_dir=self.work_dir,
        )
        from east_v5.runtime import bootstrap as bootstrap_module

        def git_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[-2:] in (["rev-parse", "HEAD"], ["rev-parse", "refs/remotes/origin/main"]):
                return subprocess.CompletedProcess(args, 0, stdout="a" * 40)
            if args[-2:] == ["rev-parse", "HEAD^"]:
                return subprocess.CompletedProcess(args, 0, stdout="b" * 40)
            if args[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(args, 0, stdout="?? attacker.py\n")
            raise AssertionError(args)

        with patch.object(context_module, "current_foundation_task", return_value=task), patch.object(bootstrap_module.subprocess, "run", side_effect=git_runner):
            with self.assertRaisesRegex(RuntimeBootstrapError, "FOUNDATION_TASK_CONTEXT_CHECKOUT_DRIFT"):
                RuntimeBootstrap.from_current_foundation_task()


if __name__ == "__main__":
    unittest.main()
