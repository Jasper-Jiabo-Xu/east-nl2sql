"""Sanitized local 252 probe; it never executes the ORM against a real database."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError

from .validator import OrmValidator

ROOT = Path(__file__).resolve().parents[4]
_closure_probe = importlib.import_module("east_v5.agents.230.probe")
_closure_builder = importlib.import_module("east_v5.agents.230.builder")
_orm_generator = importlib.import_module("east_v5.agents.251.generator")


def run_sanitized_probe() -> dict[str, Any]:
    structure = _closure_probe._structure()
    operation = _closure_builder.OperationClosureBuilder(ROOT).build(structure)
    builder = _orm_generator.RestrictedOrmGenerator(ROOT)
    restricted_orm = builder.build(structure, operation)
    validator = OrmValidator(ROOT)
    before_freeze = copy.deepcopy((restricted_orm, structure, operation))
    frozen = validator.freeze_orm(restricted_orm, structure, operation)
    input_immutable = (restricted_orm, structure, operation) == before_freeze

    def rejected(callback) -> bool:
        try:
            callback()
        except ContractError:
            return True
        return False

    # A source-code drift (forbidden API) must be rejected with aggregated feedback.
    drifted = copy.deepcopy(restricted_orm)
    drifted["payload"]["orm_source_code"] = drifted["payload"]["orm_source_code"].replace("transaction.update(", "transaction.delete(")
    drifted["payload"]["code_hash"] = builder._code_hash(drifted["payload"]["orm_source_code"], drifted["payload"]["execution_contract"], drifted["payload"]["operations"])
    drifted["envelope"]["content_hash"] = content_hash(drifted["envelope"], drifted["payload"])
    feedback = validator.build_validation_feedback(drifted, structure, operation)

    return {
        "transport": frozen,
        "summary": {
            "frozen_artifact_ref": artifact_ref(frozen["envelope"]),
            "validated_hash": frozen["payload"]["validated_hash"],
            "code_hash_preserved": frozen["payload"]["validated_hash"] == restricted_orm["payload"]["code_hash"],
            "operation_count": len(frozen["payload"]["validated_orm_plan"]["operations"]),
            "input_immutable": input_immutable,
            "source_drift_rejected": rejected(lambda: validator.freeze_orm(drifted, structure, operation)),
            "feedback_validation_types": feedback["payload"]["validation_types"],
        },
    }


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe()["summary"], ensure_ascii=False, sort_keys=True))
