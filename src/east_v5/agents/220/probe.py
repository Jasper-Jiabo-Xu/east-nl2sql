"""Sanitized EAS-29 runtime probe for the eventual EAS-51 task gate."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from east_v5.artifacts import content_hash
from east_v5.governance import ContractError

from .closure import (
    build_event_closure,
    consume_downstream_stub,
    event_query_rounds,
    retry_status,
)


def run_sanitized_probe(repo_root: Path) -> dict[str, Any]:
    """Execute the event fixture end-to-end without a database or business data."""
    fixture = repo_root / "tests" / "agents" / "220" / "fixtures" / "event-data-dual-review.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    requests = event_query_rounds(data["event"], data["first_result"])
    closure = build_event_closure(data["event"], data["first_result"], data["second_result"])
    consumers = [consume_downstream_stub("event_data", consumer, closure)["consumer"] for consumer in ("230", "241", "251", "252", "260")]

    bad_hash = copy.deepcopy(data["event"])
    bad_hash["payload"]["sql_gold"] = "SELECT altered"
    try:
        event_query_rounds(bad_hash, data["first_result"])
    except ContractError as exc:
        bad_hash_rejected = str(exc) == "CONTENT_HASH_DRIFT"
    else:
        bad_hash_rejected = False

    unknown = copy.deepcopy(data["event"])
    unknown["payload"]["unexpected"] = True
    unknown["envelope"]["content_hash"] = content_hash(unknown["envelope"], unknown["payload"])
    try:
        event_query_rounds(unknown, data["first_result"])
    except ContractError as exc:
        unknown_field_rejected = str(exc) == "SCHEMA_VALIDATION_FAILED:QUESTION_SQL_DUAL_REVIEW_PASSED"
    else:
        unknown_field_rejected = False

    if not bad_hash_rejected or not unknown_field_rejected:
        raise ContractError("SANITIZED_PROBE_REJECTION_MISSING")
    return {
        "summary": {
            "first_request": requests[0]["request_id"],
            "second_request": requests[1]["request_id"],
            "closure_hash": hashlib.sha256(json.dumps(closure, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "event_consumers": consumers,
            "bad_hash_rejected": bad_hash_rejected,
            "unknown_field_rejected": unknown_field_rejected,
            "third_attempt_blocked_manual": retry_status(3) == "blocked_manual",
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-summary", action="store_true")
    parser.parse_args()
    result = run_sanitized_probe(Path(__file__).resolve().parents[4])
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
