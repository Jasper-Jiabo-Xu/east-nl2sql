#!/usr/bin/env python3
"""Build the immutable v6 installation archive from an already-committed head."""
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


def build(repo_root: Path, head: str, output: Path) -> dict[str, Any]:
    if _git(repo_root, "rev-parse", "HEAD") != head or _git(repo_root, "status", "--porcelain"):
        raise ValueError("RUNTIME_SKILL_PACK_SOURCE_NOT_FINAL_HEAD")
    skill_prefix = "skills/east-v5-runtime-bootstrap-v6/"
    names = _git(repo_root, "ls-tree", "-r", "--name-only", head, "--", skill_prefix).splitlines()
    relative = sorted(name[len(skill_prefix):] for name in names if name.startswith(skill_prefix) and name[len(skill_prefix):] != "manifest.template.json")
    required = {"SKILL.md", "scripts/controller.py", "scripts/pack_skill.py", "east_v5/governance.py", "east_v5/artifacts/registry.py", "east_v5/runtime/controller_core.py", "fixtures/penalty/matched.json", "config/runtime-bootstrap.json", "schemas/task-input-envelope.schema.json"}
    if not required.issubset(relative):
        raise ValueError("RUNTIME_SKILL_PACK_CLOSURE_MISSING")
    template = json.loads(_bytes(repo_root, head, skill_prefix + "manifest.template.json").decode("utf-8"))
    if template.get("source_candidate_head") != "__FINAL_HEAD__":
        raise ValueError("RUNTIME_SKILL_PACK_TEMPLATE_INVALID")
    payloads = {name: _bytes(repo_root, head, skill_prefix + name) for name in relative}
    instruction_paths = {"010": "agents/010/prompt.md", "110": "agents/110/prompt.md", "120": "agents/120/prompt.md"}
    instruction_hashes = {target: hashlib.sha256(_bytes(repo_root, head, path)).hexdigest() for target, path in instruction_paths.items()}
    manifest = {**template, "source_candidate_head": head, "files": {name: hashlib.sha256(payloads[name]).hexdigest() for name in relative}, "source_hashes": {"scripts/controller.py": hashlib.sha256(payloads["scripts/controller.py"]).hexdigest(), "east_v5/runtime/controller_core.py": hashlib.sha256(payloads["east_v5/runtime/controller_core.py"]).hexdigest()}, "fixture_hashes": {"fixtures/penalty/matched.json": hashlib.sha256(payloads["fixtures/penalty/matched.json"]).hexdigest()}, "instruction_hashes": instruction_hashes}
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
    return {"schema_version": "workspace_skill_bundle_build_receipt/v6", "skill_name": manifest["skill_name"], "skill_version": manifest["skill_version"], "source_candidate_head": head, "archive_sha256": archive_hash, "manifest_sha256": manifest_hash, "supporting_files": manifest["files"]}


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
