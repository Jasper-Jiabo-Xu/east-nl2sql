from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


class ContractError(ValueError):
    """Stable rejection where mutation must not have happened."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_exact_keys(value: dict[str, Any], keys: set[str], name: str) -> None:
    if set(value) != keys:
        raise ContractError(f"UNKNOWN_FIELD:{name}")


def _resolved(path: str) -> Path:
    if not os.path.isabs(path):
        raise ContractError("ROOT_BOUNDARY_VIOLATION:root must be absolute")
    return Path(path).resolve(strict=False)


def validate_roots(roots: dict[str, Any]) -> tuple[Path, Path, Path]:
    require_exact_keys(roots, {"repo_root", "runtime_root", "reference_root", "reference_read_only"}, "roots")
    if roots["reference_read_only"] is not True:
        raise ContractError("ROOT_BOUNDARY_VIOLATION:reference root must be read-only")
    values = tuple(_resolved(roots[x]) for x in ("repo_root", "runtime_root", "reference_root"))
    for index, first in enumerate(values):
        for second in values[index + 1:]:
            if first == second or first in second.parents or second in first.parents:
                raise ContractError("ROOT_BOUNDARY_VIOLATION:roots overlap")
    return values


def attempt_path(roots: dict[str, Any], issue_id: str, run_id: str, attempt: int) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", run_id):
        raise ContractError("RUN_ID_INVALID")
    if attempt not in (1, 2, 3):
        raise ContractError("ATTEMPT_OUT_OF_RANGE")
    _, runtime, _ = validate_roots(roots)
    return runtime / "vnext" / "03_构建过程层" / "issues" / issue_id / run_id / str(attempt)


def assert_reference_unchanged(path: Path, expected_sha256: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ContractError("INPUT_VERSION_DRIFT")


def governed_manifest(repo_root: Path) -> dict[str, Any]:
    locations = {
        "input_lock": "config/input-lock.json", "root_contract": "config/root-contract.json",
        "artifact_layout": "config/artifact-layout.json", "workflow_policy": "config/workflow-policy.json",
        "toolchain_contract": "config/toolchain-contract.json", "migration_map": "config/migration-map.json",
        "downstream_contract": "config/downstream-contract.json",
    }
    manifest: dict[str, Any] = {"schema_version": "v5.governance-manifest/v1", **locations}
    manifest["content_sha256"] = sha256(manifest)
    return manifest


def verify_governed_manifest(repo_root: Path) -> dict[str, Any]:
    manifest = load_json(repo_root / "governance-manifest.json")
    supplied = manifest.pop("content_sha256", None)
    if supplied != sha256(manifest):
        raise ContractError("CONTENT_HASH_DRIFT")
    for key, locator in manifest.items():
        if key in {"schema_version"}:
            continue
        if not isinstance(locator, str) or locator.startswith("/") or not (repo_root / locator).is_file():
            raise ContractError("LOCATOR_INVALID")
    manifest["content_sha256"] = supplied
    return manifest
