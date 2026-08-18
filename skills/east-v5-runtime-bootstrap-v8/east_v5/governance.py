from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class ContractError(ValueError):
    """Stable fail-closed execution error."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


ROOT_RESOLVER_VERSION = "v5_runtime_root_env/v8"


def root_binding_id(context: dict[str, Any]) -> str:
    if set(context) != {"resolver_version", "workspace_id", "project_id", "daemon_id", "runtime_root_resolver_version", "runtime_root_fingerprint"}:
        raise ContractError("RUNTIME_ROOT_BINDING_DRIFT")
    if context.get("resolver_version") != "daemon_local_platform_data_resolver_v1" or context.get("runtime_root_resolver_version") != ROOT_RESOLVER_VERSION or not all(isinstance(context.get(key), str) and context[key] for key in ("workspace_id", "project_id", "daemon_id", "runtime_root_fingerprint")):
        raise ContractError("RUNTIME_ROOT_BINDING_DRIFT")
    return sha256(context)


def resolve_runtime_root(context: dict[str, Any]) -> tuple[Path, str]:
    """Resolve only the governed local data root; never accept a CLI path."""
    raw = os.environ.get("V5_RUNTIME_ROOT")
    if not raw:
        raise ContractError("RUNTIME_ROOT_NOT_SET")
    requested = Path(raw)
    if not requested.is_absolute():
        raise ContractError("RUNTIME_ROOT_NOT_ABSOLUTE")
    try:
        root = requested.resolve(strict=True)
        stat = root.stat()
    except OSError as exc:
        raise ContractError("RUNTIME_ROOT_UNAVAILABLE") from exc
    if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        raise ContractError("RUNTIME_ROOT_UNAVAILABLE")
    configured_daemon = os.environ.get("V5_RUNTIME_DAEMON_ID")
    if configured_daemon is not None and configured_daemon != context.get("daemon_id"):
        raise ContractError("RUNTIME_ROOT_DAEMON_UNREACHABLE")
    fingerprint = sha256({"resolver_version": ROOT_RESOLVER_VERSION, "workspace_id": context.get("workspace_id"), "project_id": context.get("project_id"), "daemon_id": context.get("daemon_id"), "device": stat.st_dev, "inode": stat.st_ino})
    if context.get("runtime_root_fingerprint") != fingerprint:
        raise ContractError("RUNTIME_ROOT_FINGERPRINT_DRIFT")
    return root, fingerprint
