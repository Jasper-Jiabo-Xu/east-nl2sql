"""Fail-closed provider-only overlay for the pinned AutoLink source tree.

The original checkout is never modified.  This module applies the declared
provider call substitutions only to a disposable copy and refuses any source,
overlay, route, or patched-tree drift.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from .s0_harness import HarnessError, stable_hash


AUTOLINK_STAGES = ("complete_schema", "sql_generation", "sql_revise", "sql_selection")
AUTOLINK_FILES = {
    "complete_schema": "run/complete_schema.py",
    "sql_generation": "run/sql_generation.py",
    "sql_revise": "run/sql_revise.py",
    "sql_selection": "run/sql_selection.py",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(root: Path) -> str:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and ".git" not in item.parts):
        rows.append({"path": path.relative_to(root).as_posix(), "sha256": _sha(path)})
    return stable_hash(rows)


def load_overlay(path: Path) -> dict[str, Any]:
    try:
        overlay = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError("AUTOLINK_OVERLAY_INVALID") from exc
    if set(overlay) != {"overlay_version", "upstream_commit", "routes"} or overlay["overlay_version"] != "eas125-autolink-provider-v4-v1":
        raise HarnessError("AUTOLINK_OVERLAY_INVALID")
    routes = overlay["routes"]
    if not isinstance(routes, list) or [item.get("stage") for item in routes if isinstance(item, dict)] != list(AUTOLINK_STAGES):
        raise HarnessError("AUTOLINK_OVERLAY_ROUTE_DRIFT")
    for item in routes:
        if set(item) != {"stage", "path", "original_sha256", "source_model", "route"} or item["path"] != AUTOLINK_FILES[item["stage"]]:
            raise HarnessError("AUTOLINK_OVERLAY_INVALID")
    return overlay


def validate_model_routes(routes: Any) -> list[dict[str, Any]]:
    if not isinstance(routes, list) or [item.get("stage") for item in routes if isinstance(item, dict)] != list(AUTOLINK_STAGES):
        raise HarnessError("AUTOLINK_MODEL_ROUTE_DRIFT")
    result: list[dict[str, Any]] = []
    for item in routes:
        required = {"stage", "model", "thinking", "reasoning_effort", "temperature", "max_tokens", "timeout_seconds", "retry_count", "call_budget", "token_budget"}
        if not isinstance(item, dict) or set(item) != required or item["model"] != "deepseek-v4-flash" or not isinstance(item["max_tokens"], int) or item["max_tokens"] < 1 or not isinstance(item["timeout_seconds"], int) or item["timeout_seconds"] < 1 or item["retry_count"] != 3 or item["call_budget"] < 1 or item["token_budget"] < item["max_tokens"]:
            raise HarnessError("AUTOLINK_MODEL_ROUTE_INVALID")
        thinking = item["thinking"]
        if thinking:
            if item["reasoning_effort"] != "high" or item["temperature"] is not None:
                raise HarnessError("AUTOLINK_MODEL_ROUTE_INVALID")
        elif item["reasoning_effort"] is not None or item["temperature"] != 0:
            raise HarnessError("AUTOLINK_MODEL_ROUTE_INVALID")
        result.append(item)
    return result


def child_provider_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if parent is None else parent
    key = source.get("DEEPSEEK_API_KEY")
    if not key:
        raise HarnessError("DEEPSEEK_AUTH_MISSING")
    result = dict(source)
    result["OPENAI_API_KEY"] = key
    result["OPENAI_BASE_URL"] = "https://api.deepseek.com"
    return result


def validate_captured_request_bodies(captured: Any, model_routes: Any) -> None:
    """Validate fake-endpoint captures without retaining prompts or credentials."""
    routes = {route["stage"]: route for route in validate_model_routes(model_routes)}
    if not isinstance(captured, list) or [row.get("stage") for row in captured if isinstance(row, dict)] != list(AUTOLINK_STAGES):
        raise HarnessError("AUTOLINK_REQUEST_CAPTURE_DRIFT")
    for row in captured:
        route = routes[row["stage"]]
        expected = {"stage", "model", "max_tokens", "timeout", "thinking", "reasoning_effort", "temperature"}
        if not isinstance(row, dict) or set(row) != expected or row["model"] != route["model"] or row["max_tokens"] != route["max_tokens"] or row["timeout"] != route["timeout_seconds"] or row["thinking"] != route["thinking"] or row["reasoning_effort"] != route["reasoning_effort"] or row["temperature"] != route["temperature"]:
            raise HarnessError("AUTOLINK_REQUEST_CAPTURE_DRIFT")


def _request_arguments(route: dict[str, Any]) -> str:
    args = f'model="deepseek-v4-flash",\n                    max_tokens={route["max_tokens"]},\n                    timeout={route["timeout_seconds"]},'
    if route["thinking"]:
        return args + '\n                    extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},'
    return args + "\n                    temperature=0,"


def apply_hashed_provider_overlay(source: Path, destination: Path, overlay_path: Path, model_routes: Any) -> dict[str, str]:
    overlay = load_overlay(overlay_path)
    routes = {item["stage"]: item for item in validate_model_routes(model_routes)}
    if not source.is_dir() or (source / ".git").exists() and subprocess_check_clean(source) is False:
        raise HarnessError("AUTOLINK_SOURCE_NOT_CLEAN")
    if destination.exists():
        raise HarnessError("AUTOLINK_OVERLAY_DESTINATION_EXISTS")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
    for copied in destination.rglob("*"):
        mode = copied.stat().st_mode
        copied.chmod(mode | (stat.S_IWUSR | stat.S_IXUSR if copied.is_dir() else stat.S_IWUSR))
    for item in overlay["routes"]:
        path = source / item["path"]
        if _sha(path) != item["original_sha256"]:
            raise HarnessError("AUTOLINK_OVERLAY_SOURCE_DRIFT")
        target = destination / item["path"]
        text = target.read_text(encoding="utf-8")
        needle = f'model="{item["source_model"]}",'
        if text.count(needle) != 1:
            raise HarnessError("AUTOLINK_OVERLAY_CALLSITE_DRIFT")
        target.chmod(target.stat().st_mode | stat.S_IWUSR)
        target.write_text(text.replace(needle, _request_arguments(routes[item["stage"]]), 1), encoding="utf-8")
    return {"overlay_sha256": _sha(overlay_path), "patched_tree_sha256": _tree_hash(destination)}


def subprocess_check_clean(source: Path) -> bool:
    import subprocess
    result = subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "--", "."], capture_output=True, text=True, check=False)
    return result.returncode == 0
