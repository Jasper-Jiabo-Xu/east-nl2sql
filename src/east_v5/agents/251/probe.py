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


def consume_252_stub(package: dict[str, Any], structure: dict[str, Any], operation: dict[str, Any], builder: RestrictedOrmGenerator) -> dict[str, Any]:
    """252 contract stub: production code validates the strict empty-run contract only."""
    builder.validate_restricted_orm(package, structure, operation)
    empty = package["payload"]["execution_contract"]["empty_run_contract"]
    if empty != {"input": {}, "write_count": 0, "database_side_effect": False, "return_shape": "execution_report/v1"}:
        raise ContractError("EMPTY_RUN_ZERO_WRITE_VIOLATION")
    return {"decision": "pass", "code_hash": package["payload"]["code_hash"], "empty_write_count": 0}


def run_sanitized_probe() -> dict[str, Any]:
    structure = _closure_probe._structure()
    operation = _closure_builder.OperationClosureBuilder(ROOT).build(structure)
    builder = RestrictedOrmGenerator(ROOT)
    package = builder.build(structure, operation)
    stub = consume_252_stub(package, structure, operation, builder)
    drifted = copy.deepcopy(package); drifted["payload"]["orm_source_code"] += "# forbidden\n"; drifted["envelope"]["content_hash"] = content_hash(drifted["envelope"], drifted["payload"])
    try: builder.validate_restricted_orm(drifted, structure, operation)
    except ContractError as exc: source_drift_rejected = str(exc) == "CODE_HASH_OR_API_DRIFT"
    else: source_drift_rejected = False
    feedback = {"orm_plan_ref": artifact_ref(package["envelope"]), "decision": "fail", "validation_types": ["static_ast"], "failed_items": [{"failed_rule_ids": ["fixture"]}]}
    revision = builder.apply_252_feedback(package, feedback, structure, operation)
    return {"transport": package, "summary": {"artifact_ref": artifact_ref(package["envelope"]), "content_hash": package["envelope"]["content_hash"], "empty_zero_write": stub["empty_write_count"] == 0, "stub_252_consumed": stub["decision"] == "pass", "source_drift_rejected": source_drift_rejected, "feedback_revision": revision["envelope"]["version"] == 2 and revision["envelope"]["attempt_no"] == 2}}


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe()["summary"], ensure_ascii=False, sort_keys=True))
