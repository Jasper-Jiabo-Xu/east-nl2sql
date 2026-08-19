#!/usr/bin/env python3
"""Build the immutable v11 full-graph installation archive from a committed head."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _bytes(root: Path, head: str, path: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), "show", f"{head}:{path}"], stderr=subprocess.DEVNULL)


def _canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _skill_metadata(skill_md: bytes) -> dict[str, str]:
    """Freeze the exact record metadata needed before first workspace use."""
    lines = skill_md.decode("utf-8").splitlines()
    if len(lines) < 4 or lines[0] != "---":
        raise ValueError("RUNTIME_SKILL_PACK_METADATA_INVALID")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("RUNTIME_SKILL_PACK_METADATA_INVALID") from exc
    fields = dict(line.split(": ", 1) for line in lines[1:closing] if ": " in line)
    fields = {key: value.strip().strip('"') for key, value in fields.items()}
    if set(fields) != {"name", "description"} or not all(fields.values()):
        raise ValueError("RUNTIME_SKILL_PACK_METADATA_INVALID")
    return {"name": fields["name"], "description": fields["description"], "skill_md_sha256": hashlib.sha256(skill_md).hexdigest()}


MATERIALIZER_VERSION = "multica_daemon_frontmatter_normalizer/v11"
_SOURCE_DESCRIPTION = b"description: Execute the EAST V5 v11 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight.\n"
_RUNTIME_DESCRIPTION = b'description: "Execute the EAST V5 v11 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight."\n'


def materialize_skill(skill_md: bytes) -> bytes:
    """Apply the only approved daemon normalization byte-for-byte."""
    if skill_md.count(_SOURCE_DESCRIPTION) != 1 or skill_md.count(b"---\n\n# EAST V5 Runtime Bootstrap V11") != 1:
        raise ValueError("RUNTIME_SKILL_MATERIALIZER_INPUT_INVALID")
    result = skill_md.replace(_SOURCE_DESCRIPTION, _RUNTIME_DESCRIPTION, 1).replace(b"---\n\n# EAST V5 Runtime Bootstrap V11", b"---\n\n\n# EAST V5 Runtime Bootstrap V11", 1)
    if result.count(_RUNTIME_DESCRIPTION) != 1 or result.count(b"---\n\n\n# EAST V5 Runtime Bootstrap V11") != 1:
        raise ValueError("RUNTIME_SKILL_MATERIALIZATION_DRIFT")
    return result


def _materializer_contract() -> dict[str, Any]:
    contract = {"version": MATERIALIZER_VERSION, "approved_byte_diff": {"description_yaml_double_quoted": True, "frontmatter_blank_lines_after_closing": {"source": 1, "runtime": 2}}}
    return {**contract, "fingerprint": hashlib.sha256(_canon(contract)).hexdigest()}


def build(repo_root: Path, head: str, output: Path) -> dict[str, Any]:
    if _git(repo_root, "rev-parse", "HEAD") != head or _git(repo_root, "status", "--porcelain"):
        raise ValueError("RUNTIME_SKILL_PACK_SOURCE_NOT_FINAL_HEAD")
    skill_prefix = "skills/east-v5-runtime-bootstrap-v11/"
    names = _git(repo_root, "ls-tree", "-r", "--name-only", head, "--", skill_prefix).splitlines()
    relative = sorted(name[len(skill_prefix):] for name in names if name.startswith(skill_prefix) and name[len(skill_prefix):] != "manifest.template.json")
    required = {"SKILL.md", "scripts/controller.py", "scripts/pack_skill.py", "east_v5/governance.py", "east_v5/runtime/graph_controller.py", "config/full-runtime-graph.json"}
    if not required.issubset(relative):
        raise ValueError("RUNTIME_SKILL_PACK_CLOSURE_MISSING")
    template = json.loads(_bytes(repo_root, head, skill_prefix + "manifest.template.json").decode("utf-8"))
    if template.get("source_candidate_head") != "__FINAL_HEAD__":
        raise ValueError("RUNTIME_SKILL_PACK_TEMPLATE_INVALID")
    derivation_contract = {"version": "east-v5-launch-idempotency/v11", "canonical_fields": ["skill_name", "skill_version", "skill_manifest_sha256", "root_binding_id", "run_id", "attempt", "target_agent_id", "input_receipt_hashes"]}
    if template.get("key_derivation") != {**derivation_contract, "fingerprint": "__KEY_DERIVATION_FINGERPRINT__"}:
        raise ValueError("RUNTIME_KEY_DERIVATION_TEMPLATE_INVALID")
    template["key_derivation"] = {**derivation_contract, "fingerprint": hashlib.sha256(_canon(derivation_contract)).hexdigest()}
    payloads = {name: _bytes(repo_root, head, skill_prefix + name) for name in relative}
    instruction_paths = {target: f"agents/{target}/prompt.md" for target in ("010", "110", "120", "130", "140", "150", "160", "170", "180", "210", "220", "230", "241", "242", "251", "252", "260")}
    instruction_hashes = {target: hashlib.sha256(_bytes(repo_root, head, path)).hexdigest() for target, path in instruction_paths.items()}
    skill_metadata = _skill_metadata(payloads["SKILL.md"])
    source_hash = hashlib.sha256(payloads["SKILL.md"]).hexdigest()
    runtime_hash = hashlib.sha256(materialize_skill(payloads["SKILL.md"])).hexdigest()
    skill_identity = {"source_skill_sha256": source_hash, "archive_skill_sha256": source_hash, "runtime_materialized_skill_sha256": runtime_hash, "materializer_contract": _materializer_contract()}
    manifest = {**template, "source_candidate_head": head, "files": {name: hashlib.sha256(payloads[name]).hexdigest() for name in relative}, "source_hashes": {"scripts/controller.py": hashlib.sha256(payloads["scripts/controller.py"]).hexdigest(), "east_v5/runtime/graph_controller.py": hashlib.sha256(payloads["east_v5/runtime/graph_controller.py"]).hexdigest()}, "fixture_hashes": {}, "instruction_hashes": instruction_hashes, "skill_metadata": skill_metadata, "skill_identity": skill_identity}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in relative:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payloads[name])
        info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, _canon(manifest))
    archive_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest_hash = hashlib.sha256(_canon(manifest)).hexdigest()
    return {"schema_version": "workspace_skill_bundle_build_receipt/v11", "skill_name": manifest["skill_name"], "skill_version": manifest["skill_version"], "source_candidate_head": head, "archive_sha256": archive_hash, "manifest_sha256": manifest_hash, "supporting_files": manifest["files"], "skill_metadata": manifest["skill_metadata"], "skill_identity": skill_identity, "key_derivation": manifest["key_derivation"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[3]
    try:
        if len(args.head) != 40 or any(char not in "0123456789abcdef" for char in args.head):
            raise ValueError("RUNTIME_SKILL_PACK_HEAD_INVALID")
        print(json.dumps(build(root, args.head, Path(args.output).resolve()), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
