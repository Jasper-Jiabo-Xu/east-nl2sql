"""Deterministic 210 production of Foundation intent and compatibility projection."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

_closure = importlib.import_module("east_v5.agents.220.closure")


def _wrap(payload: dict[str, Any], *, artifact_type: str, artifact_id: str, run_id: str, trace_id: str, created_at: str, parents: list[dict[str, Any]]) -> dict[str, Any]:
    envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": run_id, "qa_id": None, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "210", "parent_artifact_refs": parents, "input_hashes": [item["content_hash"] for item in parents], "status": "candidate", "mode": "foundation", "created_at": created_at, "trace_id": trace_id, "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def build_foundation_task_package(payload: dict[str, Any], *, run_id: str, trace_id: str, created_at: str, parents: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce one immutable, schema-valid complete Foundation intent package."""
    if not isinstance(payload, dict) or "foundation_task_id" not in payload:
        raise ContractError("FOUNDATION_TASK_PACKAGE_INVALID")
    package = _wrap(payload, artifact_type="foundation_task_package", artifact_id=payload["foundation_task_id"], run_id=run_id, trace_id=trace_id, created_at=created_at, parents=parents)
    _closure.validate_foundation_task_package(package)
    return package


def build_foundation_profile(task_package: dict[str, Any]) -> dict[str, Any]:
    """Produce the only supported profile projection from complete 210 intent."""
    envelope, payload = _closure.validate_foundation_task_package(task_package)
    profile = _closure.project_foundation_profile(task_package)
    package = _wrap(profile, artifact_type="foundation_profile", artifact_id=f"{envelope['artifact_id']}:profile", run_id=envelope["run_id"], trace_id=envelope["trace_id"], created_at=envelope["created_at"], parents=[artifact_ref(envelope)])
    _closure.validate_foundation_profile_projection(package, task_package)
    return package
