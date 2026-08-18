#!/usr/bin/env python3
"""Self-contained, fail-closed claim verifier for workspace_skill_bundle_v1."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SKILL_NAME = "east-v5-runtime-bootstrap-v1"
SKILL_VERSION = "v1"
_HEX = set("0123456789abcdef")
_MANIFEST_KEYS = {"schema_version", "skill_name", "skill_version", "source_candidate_head", "root_binding_algorithm", "allowed_targets", "prohibited_transports", "files", "source_hashes"}
_CLAIM_KEYS = {"agent_uuid", "runtime_uuid", "provider_id", "instructions_sha256", "enabled_skill_ids"}


class ClaimError(ValueError):
    pass


def _fail(code: str) -> None:
    raise ClaimError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex(value: Any, size: int) -> bool:
    return isinstance(value, str) and len(value) == size and set(value) <= _HEX


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _validate_manifest(manifest: dict[str, Any], root: Path) -> None:
    if set(manifest) != _MANIFEST_KEYS or manifest["schema_version"] != "workspace_skill_bundle_manifest/v1":
        _fail("RUNTIME_SKILL_MANIFEST_INVALID")
    if manifest["skill_name"] != SKILL_NAME or manifest["skill_version"] != SKILL_VERSION or not _is_hex(manifest["source_candidate_head"], 40):
        _fail("RUNTIME_SKILL_MANIFEST_IDENTITY_DRIFT")
    if manifest["root_binding_algorithm"] != "sha256(canonical(runtime_context))":
        _fail("RUNTIME_SKILL_ROOT_ALGORITHM_DRIFT")
    if not isinstance(manifest["prohibited_transports"], list) or set(manifest["prohibited_transports"]) != {"business_payload", "physical_path", "issue_comment_route", "network_fetch", "project_local_directory"}:
        _fail("RUNTIME_SKILL_TRANSPORT_POLICY_DRIFT")
    targets = manifest["allowed_targets"]
    if not isinstance(targets, list) or len(targets) != 3 or any(not isinstance(item, dict) or set(item) != {"agent_id", "agent_uuid", "provider_id"} for item in targets):
        _fail("RUNTIME_SKILL_TARGETS_INVALID")
    if len({item["agent_uuid"] for item in targets}) != 3 or {item["agent_id"] for item in targets} != {"010", "110", "120"}:
        _fail("RUNTIME_SKILL_TARGETS_INVALID")
    for section in ("files", "source_hashes"):
        entries = manifest[section]
        if not isinstance(entries, dict) or not entries or not all(isinstance(name, str) and _is_hex(value, 64) for name, value in entries.items()):
            _fail("RUNTIME_SKILL_MANIFEST_HASHES_INVALID")
    for relative, expected in manifest["files"].items():
        candidate = (root / relative).resolve(strict=False)
        if root.resolve() not in candidate.parents or not candidate.is_file() or _file_sha(candidate) != expected:
            _fail("RUNTIME_SKILL_SUPPORT_FILE_HASH_DRIFT")


def claim(envelope: dict[str, Any], claim_input: dict[str, Any], manifest: dict[str, Any], manifest_hash: str) -> dict[str, str]:
    if set(claim_input) != _CLAIM_KEYS or not _is_hex(claim_input["instructions_sha256"], 64) or not all(isinstance(claim_input[key], str) and claim_input[key] for key in ("agent_uuid", "runtime_uuid", "provider_id")) or not isinstance(claim_input["enabled_skill_ids"], list) or any(not isinstance(item, str) or not item for item in claim_input["enabled_skill_ids"]):
        _fail("RUNTIME_SKILL_CLAIM_INVALID")
    bootstrap = envelope.get("execution_bootstrap") if isinstance(envelope, dict) else None
    if not isinstance(bootstrap, dict):
        _fail("RUNTIME_SKILL_BOOTSTRAP_MISSING")
    bundle = bootstrap.get("skill_bundle")
    if not isinstance(bundle, dict) or set(bundle) != {"skill_name", "skill_version", "skill_manifest_sha256"} or (bundle["skill_name"], bundle["skill_version"], bundle["skill_manifest_sha256"]) != (SKILL_NAME, SKILL_VERSION, manifest_hash):
        _fail("RUNTIME_SKILL_BUNDLE_DRIFT")
    if not _is_hex(bootstrap.get("candidate_head_sha"), 40) or bootstrap.get("adapter_sha256") != manifest["source_hashes"].get("src/east_v5/runtime/adapter.py"):
        _fail("RUNTIME_SKILL_SOURCE_DRIFT")
    context = bootstrap.get("runtime_context")
    if not isinstance(context, dict) or set(context) != {"resolver_version", "workspace_id", "project_id", "daemon_id"} or context["resolver_version"] != "daemon_local_platform_data_resolver_v1" or not all(isinstance(context[key], str) and context[key] for key in ("workspace_id", "project_id", "daemon_id")):
        _fail("RUNTIME_SKILL_ROOT_BINDING_DRIFT")
    if envelope.get("root_binding_id") != _sha(context):
        _fail("RUNTIME_SKILL_ROOT_BINDING_DRIFT")
    target = next((item for item in manifest["allowed_targets"] if item["agent_uuid"] == claim_input["agent_uuid"]), None)
    if target is None or envelope.get("target_agent_uuid") != claim_input["agent_uuid"] or claim_input["provider_id"] != target["provider_id"]:
        _fail("RUNTIME_SKILL_TARGET_OR_PROVIDER_DRIFT")
    if SKILL_NAME not in claim_input["enabled_skill_ids"] or len(set(claim_input["enabled_skill_ids"])) != len(claim_input["enabled_skill_ids"]):
        _fail("RUNTIME_SKILL_BINDING_MISSING")
    after_hash = _sha({"agent_uuid": claim_input["agent_uuid"], "runtime_uuid": claim_input["runtime_uuid"], "instructions_sha256": claim_input["instructions_sha256"], "enabled_skill_ids": sorted(claim_input["enabled_skill_ids"]), "skill_manifest_sha256": manifest_hash})
    return {"claim_status": "accepted", "skill_name": SKILL_NAME, "skill_version": SKILL_VERSION, "skill_manifest_sha256": manifest_hash, "candidate_head_sha": bootstrap["candidate_head_sha"], "adapter_sha256": bootstrap["adapter_sha256"], "root_binding_id": envelope["root_binding_id"], "after_configuration_hash": after_hash}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envelope-file", required=True)
    parser.add_argument("--claim-file", required=True)
    parser.add_argument("--manifest-file")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    manifest_path = Path(args.manifest_file) if args.manifest_file else root / "manifest.json"
    try:
        manifest = _load_json(manifest_path, "RUNTIME_SKILL_MANIFEST_UNREADABLE")
        _validate_manifest(manifest, root)
        manifest_hash = _file_sha(manifest_path)
        result = claim(_load_json(Path(args.envelope_file), "RUNTIME_SKILL_ENVELOPE_UNREADABLE"), _load_json(Path(args.claim_file), "RUNTIME_SKILL_CLAIM_UNREADABLE"), manifest, manifest_hash)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ClaimError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
