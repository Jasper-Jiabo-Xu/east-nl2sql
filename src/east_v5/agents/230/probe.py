"""Sanitized, input-driven real-run probe for Agent 230."""
from __future__ import annotations

import json
import copy
from pathlib import Path
from typing import Any

from east_v5.artifacts import content_hash
from east_v5.governance import ContractError

from .builder import OperationClosureBuilder


ROOT = Path(__file__).resolve().parents[4]


def _structure() -> dict[str, Any]:
    payload = {"schema_version": "v5.structure-closure/v1", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "tables": ["FIXTURE_ACCOUNT", "FIXTURE_CUSTOMER"], "fields": ["FIXTURE_ACCOUNT.CUSTOMER_ID", "FIXTURE_ACCOUNT.STATUS", "FIXTURE_CUSTOMER.ID"], "references": [{"type": "cross_table", "data": {"from": "FIXTURE_ACCOUNT.CUSTOMER_ID", "to": "FIXTURE_CUSTOMER.ID"}}, {"type": "object_detail_state", "data": {"object": "FIXTURE_ACCOUNT", "state_field": "FIXTURE_ACCOUNT.STATUS"}}]}
    envelope = {"artifact_id": "eas30-sanitized-structure", "artifact_type": "structure_closure", "run_id": "eas30-sanitized-run", "qa_id": "QA-EAS30", "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1, "producer_id": "220", "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "event_data", "created_at": "2026-08-16T00:00:00+00:00", "trace_id": "eas30-sanitized-trace", "storage_locator": None}
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def run_sanitized_probe() -> dict[str, Any]:
    builder, source = OperationClosureBuilder(ROOT), _structure()
    package = builder.build(source)
    drifted = copy.deepcopy(source)
    drifted["payload"]["tables"] = ["FIXTURE_ACCOUNT"]
    cycle = copy.deepcopy(package)
    cycle["payload"]["operations"][0]["dependencies"] = [cycle["payload"]["operations"][-1]["operation_step_id"]]
    cycle["envelope"]["content_hash"] = content_hash(cycle["envelope"], cycle["payload"])
    def rejected(callback):
        try:
            callback()
        except ContractError:
            return True
        return False
    return {"transport": package, "summary": {"consumers": [builder.consume_downstream_stub(item, package)["consumer"] for item in ("241", "251")], "foundation_rejected": rejected(lambda: builder.build({**source, "envelope": {**source["envelope"], "mode": "foundation"}})), "hash_drift_rejected": rejected(lambda: builder.build(drifted)), "dependency_cycle_rejected": rejected(lambda: builder.validate_operation_closure_package(cycle)), "third_attempt_blocked_manual": builder.retry_status(3) == "blocked_manual"}}


if __name__ == "__main__":
    print(json.dumps(run_sanitized_probe()["summary"], ensure_ascii=False, sort_keys=True))
