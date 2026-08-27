"""Fail-closed verification of the six read-only EAS-125 upstream trees."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .s0_harness import HarnessError


ROOT = Path(__file__).resolve().parents[3]
AUDIT_PATH = ROOT / "contracts" / "experiments" / "upstream_source_audit.json"


def load_upstream_audit(path: Path = AUDIT_PATH) -> dict[str, Any]:
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError("UPSTREAM_AUDIT_INVALID") from exc
    routes = audit.get("routes") if isinstance(audit, dict) else None
    if audit.get("schema_version") != "eas125-upstream-source-audit-v1" or not isinstance(routes, list) or len(routes) != 6:
        raise HarnessError("UPSTREAM_AUDIT_INVALID")
    return audit


def verify_upstream_mount(root: Path, audit: dict[str, Any] | None = None) -> dict[str, str]:
    audit = audit or load_upstream_audit()
    observed: dict[str, str] = {}
    for route in audit["routes"]:
        if not isinstance(route, dict) or set(route) - {"id", "repo_url", "commit", "license", "entry", "provider", "output", "status"}:
            raise HarnessError("UPSTREAM_AUDIT_INVALID")
        tree = root / route["id"]
        try:
            commit = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise HarnessError("UPSTREAM_TREE_UNAVAILABLE", route["id"]) from exc
        if commit != route["commit"]:
            raise HarnessError("UPSTREAM_COMMIT_DRIFT", route["id"])
        for role in ("license", "entry", "provider", "output"):
            item = route[role]
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                raise HarnessError("UPSTREAM_AUDIT_INVALID", route["id"])
            target = tree / item["path"]
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                raise HarnessError("UPSTREAM_SOURCE_DRIFT", f"{route['id']}:{role}")
        observed[route["id"]] = commit
    return observed
