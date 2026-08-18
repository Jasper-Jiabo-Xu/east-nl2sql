from __future__ import annotations

import hashlib
import json
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


def root_binding_id(context: dict[str, Any]) -> str:
    if set(context) != {"resolver_version", "workspace_id", "project_id", "daemon_id"}:
        raise ContractError("RUNTIME_ROOT_BINDING_DRIFT")
    if context.get("resolver_version") != "daemon_local_platform_data_resolver_v1" or not all(isinstance(context.get(key), str) and context[key] for key in ("workspace_id", "project_id", "daemon_id")):
        raise ContractError("RUNTIME_ROOT_BINDING_DRIFT")
    return sha256(context)
