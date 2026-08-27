"""Fail-closed binding of a Foundation process to its real Multica task.

This is deliberately a *reader*, not a launcher: the daemon supplies the
task-scoped environment and CLI authentication, while the repository only
verifies the task record before it creates any runtime-root state.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from east_v5.governance import ContractError


class FoundationTaskContextError(ContractError):
    """The current process is not an authenticated Foundation task."""


def _fail(code: str) -> None:
    raise FoundationTaskContextError(code)


_ISSUE = "01a0389c-5fe5-7a27-a214-574cd66d9a2e"
_WORKSPACE = "82db0426-715a-47e1-a66f-15b479c47654"
_RUNTIME = "0e5e9dd9-5135-4937-bb03-92b77adb8395"
_JIA_BO = "18b0369f-00c7-4db0-a313-e1fb4383cb08"
_AGENTS = {
    "7df640f9-973f-4c46-8302-df1256f60146": "241",
    "4e801c18-7048-4227-a5c7-515f51a5e5ba": "242",
    "f89e7039-e213-4e1e-9204-64f7ce69ac1c": "260",
}
_REQUIRED_ENV = {
    "MULTICA_TASK_ID", "MULTICA_AGENT_ID", "MULTICA_WORKSPACE_ID",
    "MULTICA_TASK_CONFIG_ROOT", "MULTICA_DAEMON_PORT",
}


@dataclass(frozen=True)
class VerifiedFoundationTask:
    task_id: str
    issue_id: str
    agent_id: str
    role: str
    workspace_id: str
    runtime_id: str
    attempt: int
    trigger_comment_id: str
    work_dir: Path


def _checkout() -> Path:
    return Path(__file__).resolve().parents[3]


def _safe_work_dir(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        _fail("FOUNDATION_TASK_CONTEXT_WORKDIR_INVALID")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        _fail("FOUNDATION_TASK_CONTEXT_WORKDIR_INVALID")
    resolved = path.resolve(strict=True)
    if resolved != path or path.is_symlink():
        _fail("FOUNDATION_TASK_CONTEXT_WORKDIR_INVALID")
    return resolved


def _daemon_environment() -> dict[str, str]:
    if set(os.environ).isdisjoint(_REQUIRED_ENV):
        _fail("FOUNDATION_TASK_CONTEXT_ENV_MISSING")
    values = {key: os.environ.get(key) for key in _REQUIRED_ENV}
    if any(not isinstance(value, str) or not value for value in values.values()):
        _fail("FOUNDATION_TASK_CONTEXT_ENV_MISSING")
    if values["MULTICA_AGENT_ID"] not in _AGENTS or values["MULTICA_WORKSPACE_ID"] != _WORKSPACE:
        _fail("FOUNDATION_TASK_CONTEXT_ENV_INVALID")
    try:
        port = int(values["MULTICA_DAEMON_PORT"])
    except ValueError as exc:
        raise FoundationTaskContextError("FOUNDATION_TASK_CONTEXT_ENV_INVALID") from exc
    if not 1 <= port <= 65535:
        _fail("FOUNDATION_TASK_CONTEXT_ENV_INVALID")
    config_root = Path(values["MULTICA_TASK_CONFIG_ROOT"])
    if not config_root.is_absolute() or config_root.is_symlink() or not config_root.is_dir():
        _fail("FOUNDATION_TASK_CONTEXT_CONFIG_ROOT_INVALID")
    # Do not return config_root or any daemon token: neither belongs in a
    # bootstrap declaration, receipt, registry entry, or log.
    return {key: str(values[key]) for key in ("MULTICA_TASK_ID", "MULTICA_AGENT_ID", "MULTICA_WORKSPACE_ID")}


def _task_list(agent_id: str) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["multica", "agent", "tasks", agent_id, "--output", "json"],
            check=True, capture_output=True, text=True,
        )
        value = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoundationTaskContextError("FOUNDATION_TASK_CONTEXT_TASK_LIST_UNAVAILABLE") from exc
    if not isinstance(value, list):
        _fail("FOUNDATION_TASK_CONTEXT_TASK_LIST_INVALID")
    return value


def current_foundation_task() -> VerifiedFoundationTask:
    """Return the sole current task after platform and checkout verification.

    There are no parameters: callers cannot nominate an envelope, identity,
    path, task, agent, runtime, or token.
    """
    environment = _daemon_environment()
    task_id, agent_id = environment["MULTICA_TASK_ID"], environment["MULTICA_AGENT_ID"]
    matches = [item for item in _task_list(agent_id) if isinstance(item, dict) and item.get("id") == task_id]
    if len(matches) != 1:
        _fail("FOUNDATION_TASK_CONTEXT_TASK_MATCH_INVALID")
    task = matches[0]
    required_strings = ("id", "issue_id", "agent_id", "runtime_id", "status", "trigger_comment_id", "work_dir")
    if any(not isinstance(task.get(key), str) or not task[key] for key in required_strings):
        _fail("FOUNDATION_TASK_CONTEXT_TASK_INVALID")
    if (task["issue_id"], task["agent_id"], task["runtime_id"], task["status"]) != (_ISSUE, agent_id, _RUNTIME, "running"):
        _fail("FOUNDATION_TASK_CONTEXT_TASK_DRIFT")
    if not isinstance(task.get("attempt"), int) or task["attempt"] not in {1, 2, 3}:
        _fail("FOUNDATION_TASK_CONTEXT_TASK_DRIFT")
    attribution = task.get("attribution")
    if (not isinstance(attribution, dict) or attribution.get("source") != "delegation"
            or attribution.get("precise") is not True
            or not isinstance(attribution.get("initiator"), dict)
            or not isinstance(attribution.get("originator"), dict)
            or attribution["initiator"].get("id") != _JIA_BO
            or attribution["originator"].get("id") != _JIA_BO):
        _fail("FOUNDATION_TASK_CONTEXT_ATTRIBUTION_DRIFT")
    work_dir = _safe_work_dir(task["work_dir"])
    checkout = _checkout().resolve(strict=True)
    try:
        checkout.relative_to(work_dir)
    except ValueError as exc:
        raise FoundationTaskContextError("FOUNDATION_TASK_CONTEXT_CHECKOUT_DRIFT") from exc
    return VerifiedFoundationTask(
        task_id=task_id, issue_id=task["issue_id"], agent_id=agent_id, role=_AGENTS[agent_id],
        workspace_id=environment["MULTICA_WORKSPACE_ID"], runtime_id=task["runtime_id"],
        attempt=task["attempt"], trigger_comment_id=task["trigger_comment_id"], work_dir=work_dir,
    )
