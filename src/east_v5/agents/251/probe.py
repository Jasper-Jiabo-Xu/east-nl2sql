"""Sanitized local 251 probe; it never contacts a database or a runtime store."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

from .generator import RestrictedOrmGenerator

ROOT = Path(__file__).resolve().parents[4]
_closure_probe = importlib.import_module("east_v5.agents.230.probe")
_closure_builder = importlib.import_module("east_v5.agents.230.builder")


def run_sanitized_probe() -> dict[str, Any]:
    structure = _closure_probe._structure()
    operation = _closure_builder.OperationClosureBuilder(ROOT).build(structure)
    builder = RestrictedOrmGenerator(ROOT)
    package = builder.build(structure, operation)
    drifted = copy.deepcopy(package); drifted["payload"]["orm_source_code"] += "# forbidden\n"; drifted["envelope"]["content_hash"] = content_hash(drifted["envelope"], drifted["payload"])
    try: builder.validate_restricted_orm(drifted, structure, operation)
    except ContractError as exc: source_drift_rejected = str(exc) == "CODE_HASH_OR_API_DRIFT"
    else: source_drift_rejected = False
    return {"transport": package, "summary": {"artifact_ref": artifact_ref(package["envelope"]), "content_hash": package["envelope"]["content_hash"], "source_drift_rejected": source_drift_rejected, "transaction_id": package["payload"]["execution_contract"]["transaction_id"]}}


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe()["summary"], ensure_ascii=False, sort_keys=True))
