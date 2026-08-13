"""Draft 2020-12 execution for the payload-agnostic COMMON-ENVELOPE."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.governance import ContractError, load_json


def validate_common_envelope_schema(repo_root: Path, envelope: dict[str, Any]) -> None:
    schema = load_json(repo_root / "contracts" / "common" / "common-envelope.schema.json")
    try:
        Draft202012Validator(schema).validate(envelope)
    except ValidationError as exc:
        raise ContractError("SCHEMA_VALIDATION_FAILED:COMMON_ENVELOPE") from exc
