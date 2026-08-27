"""Pinned-worktree adapters for the six frozen Text-to-SQL baselines.

Native mode never calls the former synthetic executable.  It requires a
caller-supplied, attested worktree for the exact upstream revision and passes a
generated DeepSeek configuration to that upstream entrypoint.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .s0_harness import HarnessError

ADAPTER_IDS = ("deepeye_sql_native_v1", "datagallery_text2sql_native_v1", "joydataagent_sql_native_v1", "databao_agent_native_v1", "reforce_native_v1", "autolink_native_v1")


def _text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise HarnessError(code)
    return value


def _integer(value: Any, code: str) -> int:
    if isinstance(value, bool): raise HarnessError(code)
    try: result = int(value)
    except (TypeError, ValueError) as exc: raise HarnessError(code) from exc
    if result < 0: raise HarnessError(code)
    return result


def _nested(row: dict[str, Any], outer: str, sql: str, calls: str, tokens: str, code: str) -> tuple[str, int, int]:
    value = row.get(outer)
    if not isinstance(value, dict): raise HarnessError(code)
    metrics = value.get("usage") or value.get("metrics") or value.get("telemetry") or value.get("model_cost") or value.get("refinement") or value.get("exploration")
    if not isinstance(metrics, dict): raise HarnessError(code)
    return _text(value.get(sql), code), _integer(metrics.get(calls), code), _integer(metrics.get(tokens), code)


def _deepeye(row: dict[str, Any]) -> tuple[str, int, int]: return _nested(row, "deepeye_result", "final_sql", "calls", "total_tokens", "INVALID_DEEPEYE_SQL_RESPONSE")
def _datagallery(row: dict[str, Any]) -> tuple[str, int, int]: return _nested(row, "dataagent_result", "generated_sql", "llm_calls", "token_count", "INVALID_DATAGALLERY_RESPONSE")
def _joydata(row: dict[str, Any]) -> tuple[str, int, int]: return _nested(row, "joydata_answer", "sql_text", "requests", "tokens", "INVALID_JOYDATAAGENT_RESPONSE")
def _databao(row: dict[str, Any]) -> tuple[str, int, int]: return _nested(row, "databao_thread", "sql", "llm_calls", "tokens", "INVALID_DATABAO_RESPONSE")
def _reforce(row: dict[str, Any]) -> tuple[str, int, int]: return _nested(row, "reforce_result", "selected_sql", "generation_calls", "token_total", "INVALID_REFORCE_RESPONSE")
def _autolink(row: dict[str, Any]) -> tuple[str, int, int]: return _nested(row, "autolink_result", "final_query", "model_calls", "tokens", "INVALID_AUTOLINK_RESPONSE")

PARSERS: dict[str, Callable[[dict[str, Any]], tuple[str, int, int]]] = dict(zip(ADAPTER_IDS, (_deepeye, _datagallery, _joydata, _databao, _reforce, _autolink)))


def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class NativeAdapter:
    adapter_id: str
    command: list[str]
    transport_mode: str
    worktree_id: str
    evidence: dict[str, Any]
    repo_url: str
    commit: str

    @classmethod
    def from_manifest(cls, baseline: dict[str, Any]) -> "NativeAdapter":
        adapter_id = baseline.get("native_adapter_id")
        command, mode, evidence = baseline.get("command"), baseline.get("transport_mode"), baseline.get("upstream_evidence")
        if adapter_id not in PARSERS: raise HarnessError("UNKNOWN_NATIVE_ADAPTER")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command): raise HarnessError("INVALID_BASELINE_COMMAND")
        if mode not in {"native", "fixture_stub"}: raise HarnessError("INVALID_TRANSPORT_MODE")
        if not isinstance(evidence, dict) or set(evidence) != {"worktree_id", "license_path", "entrypoint_path", "provider_config_path", "source_hashes"}: raise HarnessError("INVALID_UPSTREAM_EVIDENCE")
        worktree_id = evidence["worktree_id"]
        hashes = evidence["source_hashes"]
        if not isinstance(worktree_id, str) or not worktree_id or "/" in worktree_id or not isinstance(hashes, dict) or set(hashes) != {"license", "entrypoint", "provider_config"} or not all(isinstance(v, str) and len(v) == 64 for v in hashes.values()): raise HarnessError("INVALID_UPSTREAM_EVIDENCE")
        if any(not isinstance(evidence[key], str) or not evidence[key] or evidence[key].startswith("/") or ".." in Path(evidence[key]).parts for key in ("license_path", "entrypoint_path", "provider_config_path")): raise HarnessError("INVALID_UPSTREAM_EVIDENCE")
        rendered = " ".join(command)
        if "fixture_baseline" in rendered or "fake_native_transport" in rendered or "{upstream_worktree}" not in rendered or "{provider_config}" not in rendered: raise HarnessError("GENERIC_WRAPPER_PROHIBITED")
        repo_url, commit = baseline.get("repo_url"), baseline.get("commit")
        if not isinstance(repo_url, str) or not isinstance(commit, str): raise HarnessError("INVALID_UPSTREAM_EVIDENCE")
        return cls(adapter_id, command, mode, worktree_id, evidence, repo_url, commit)

    def require_credentials(self, model_contract: dict[str, Any]) -> None:
        if self.transport_mode == "fixture_stub": return
        if model_contract.get("api_key_env") != "DEEPSEEK_API_KEY" or not os.environ.get("DEEPSEEK_API_KEY"): raise HarnessError("DEEPSEEK_AUTH_MISSING")

    def resolve_worktree(self, root: Path | None) -> Path:
        if root is None: raise HarnessError("NATIVE_WORKTREE_ROOT_REQUIRED")
        try: worktree = (root.resolve(strict=True) / self.worktree_id).resolve(strict=True)
        except OSError as exc: raise HarnessError("NATIVE_WORKTREE_MISSING") from exc
        if worktree.parent != root.resolve() or not worktree.is_dir() or worktree.is_symlink(): raise HarnessError("NATIVE_WORKTREE_UNSAFE")
        lock = worktree / "east-upstream-lock.json"
        try: locked = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc: raise HarnessError("NATIVE_WORKTREE_LOCK_MISSING") from exc
        if locked != {"repo_url": self.repo_url, "commit": self.commit}: raise HarnessError("NATIVE_WORKTREE_LOCK_DRIFT")
        for label, key in (("license", "license_path"), ("entrypoint", "entrypoint_path"), ("provider_config", "provider_config_path")):
            path = worktree / self.evidence[key]
            if path.is_symlink() or not path.is_file() or _sha(path) != self.evidence["source_hashes"][label]: raise HarnessError("NATIVE_WORKTREE_SOURCE_DRIFT")
        return worktree

    def parse(self, row: dict[str, Any]) -> tuple[str, int, int]: return PARSERS[self.adapter_id](row)
