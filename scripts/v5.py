#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from east_v5.governance import governed_manifest, load_json, verify_governed_manifest
from east_v5.architecture import scan_active_contracts, verify_architecture
from east_v5.artifacts.schema import validate_common_envelope_schema

CONFIGS = ["input-lock", "root-contract", "artifact-layout", "workflow-policy", "toolchain-contract", "migration-map", "downstream-contract", "v5-architecture", "v5-package-catalog"]


def validate(value: object, spec: dict[str, object], label: str) -> None:
    if "const" in spec and value != spec["const"]:
        raise ValueError(f"SCHEMA_VALIDATION_FAILED:{label}")
    type_map = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    expected = spec.get("type")
    if expected and (not isinstance(value, type_map[expected]) or expected == "integer" and isinstance(value, bool)):
        raise ValueError(f"SCHEMA_VALIDATION_FAILED:{label}")
    if isinstance(value, dict):
        properties = spec.get("properties", {})
        required = set(spec.get("required", []))
        if not required.issubset(value) or spec.get("additionalProperties") is False and not set(value).issubset(properties):
            raise ValueError(f"UNKNOWN_FIELD:{label}")
        for key, child in properties.items():
            if key in value:
                validate(value[key], child, f"{label}.{key}")
    if isinstance(value, list) and len(value) < spec.get("minItems", 0):
        raise ValueError(f"SCHEMA_VALIDATION_FAILED:{label}")


def schema() -> None:
    for name in CONFIGS:
        value = load_json(ROOT / "config" / f"{name}.json")
        schema_doc = load_json(ROOT / "contracts" / f"{name}.schema.json")
        validate(value, schema_doc, name)
    # Execute the actual Draft 2020-12 schema, instead of merely treating it as
    # documentation.  The committed fixture has its runtime locator rebased only
    # for semantic registry tests; JSON Schema validates its portable shape here.
    validate_common_envelope_schema(ROOT, load_json(ROOT / "fixtures" / "artifacts" / "common-envelope-valid.json")["envelope"])
    manifest = governed_manifest(ROOT)
    (ROOT / "governance-manifest.json").write_bytes(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n")
    verify_governed_manifest(ROOT)
    verify_architecture(ROOT)
    scan_active_contracts(ROOT)


def lint() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), str(path))


def type_check() -> None:
    prohibited = ("FieldGenerator", "FieldPolicy", "TableAssembler", "old registry")
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(term in text for term in prohibited):
            raise ValueError("LEGACY_GENERATOR_REFERENCE")


def security() -> None:
    forbidden_suffixes = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".log"}
    forbidden_names = {".env", "id_rsa"}
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    bad = [p for p in tracked if Path(p).suffix.lower() in forbidden_suffixes or Path(p).name in forbidden_names or "_raw" in Path(p).name.lower()]
    if bad:
        raise ValueError("SECURITY_TRACKED_ARTIFACT:" + ",".join(bad))


def tests() -> None:
    result = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    if not unittest.TextTestRunner(verbosity=2).run(result).wasSuccessful():
        raise RuntimeError("TEST_FAILED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["lint", "type", "schema", "security", "test", "check"])
    command = parser.parse_args().command
    actions = {"lint": lint, "type": type_check, "schema": schema, "security": security, "test": tests}
    try:
        if command == "check":
            for action in (lint, type_check, schema, security, tests): action()
        else:
            actions[command]()
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
