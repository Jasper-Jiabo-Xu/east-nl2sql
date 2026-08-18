"""Input-driven, sanitized runtime probe for the 220 closure contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref, content_hash

from .closure import _ref_for_request, build_closure, build_event_closure, consume_downstream_stub, event_query_rounds, retry_status


def _asset(source: dict[str, Any], request: dict[str, Any], records: list[dict[str, Any]], parent: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "artifact_id": f"probe:{request['request_id']}", "artifact_type": "constraint_asset_package",
        "run_id": source["run_id"], "qa_id": source["qa_id"], "version": 1,
        "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64,
        "supersedes_ref": None, "attempt_no": source["attempt_no"], "producer_id": "000",
        "parent_artifact_refs": [parent], "input_hashes": [parent["content_hash"]],
        "status": "candidate", "mode": source["mode"], "created_at": source["created_at"],
        "trace_id": source["trace_id"], "storage_locator": None,
    }
    payload = {
        "request_id": request["request_id"], "asset_version": "CA-V0.3.0", "executed_queries": [],
        "matched_records": records,
        "constraint_summary": {"total_matched": len(records), "asset_types_covered": [item["record_type"] for item in records]},
        "unmatched_items": [], "query_trace": [],
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _event_closure(event: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    requests = event_query_rounds(event, context)
    first_field = requests[0]["field_scope"][0]
    first = _asset(event["envelope"], requests[0], [{
        "record_type": "single_field", "data": {"table_id": first_field.split(".", 1)[0], "field_id": first_field.split(".", 1)[1]},
        "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": [],
    }], _ref_for_request(requests[0]))
    relation = requests[1]["relationship_scope"][0]
    second = _asset(event["envelope"], requests[1], [{
        "record_type": "cross_table", "data": {"from": relation.split("->", 1)[0], "to": relation.split("->", 1)[1]},
        "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": [],
    }], artifact_ref(first["envelope"]))
    return build_event_closure(event, context, first, second)


def _foundation_closure(profile: dict[str, Any]) -> dict[str, Any]:
    request = {"request_id": f"{profile['envelope']['run_id']}:220:1"}
    asset = _asset(profile["envelope"], request, [], artifact_ref(profile["envelope"]))
    return build_closure(profile, [asset])


def run_sanitized_probe(event_packages: list[dict[str, Any]], foundation_package: dict[str, Any]) -> dict[str, Any]:
    """Recompute complete registered packages from caller-supplied sanitized inputs."""
    if len(event_packages) < 2:
        raise ValueError("two reviewed/context event pairs are required")
    if any(set(package) != {"reviewed_question_sql", "event_query_context"} for package in event_packages):
        raise ValueError("each event input must contain reviewed_question_sql and event_query_context")
    event_closures = [_event_closure(package["reviewed_question_sql"], package["event_query_context"]) for package in event_packages]
    if event_closures[0]["envelope"]["content_hash"] == event_closures[1]["envelope"]["content_hash"]:
        raise ValueError("event inputs did not produce distinct closure identities")
    foundation_closure = _foundation_closure(foundation_package)
    consumers = [consume_downstream_stub(consumer, event_closures[0])["consumer"] for consumer in ("230", "241", "251", "252", "260")]
    foundation_consumers = [consume_downstream_stub(consumer, foundation_closure)["consumer"] for consumer in ("241", "260")]
    return {
        "transport": {"event": event_closures, "foundation": foundation_closure},
        "summary": {
            "event_refs": [artifact_ref(package["envelope"]) for package in event_closures],
            "foundation_ref": artifact_ref(foundation_closure["envelope"]),
            "event_consumers": consumers, "foundation_consumers": foundation_consumers,
            "third_attempt_blocked_manual": retry_status(3) == "blocked_manual",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-fixture", action="append", required=True)
    parser.add_argument("--event-context-fixture", action="append", required=True)
    parser.add_argument("--foundation-fixture", required=True)
    args = parser.parse_args()
    if len(args.event_fixture) != len(args.event_context_fixture):
        raise ValueError("event fixtures and context fixtures must be paired")
    events = [{"reviewed_question_sql": json.loads(Path(event).read_text(encoding="utf-8")), "event_query_context": json.loads(Path(context).read_text(encoding="utf-8"))} for event, context in zip(args.event_fixture, args.event_context_fixture)]
    foundation = json.loads(Path(args.foundation_fixture).read_text(encoding="utf-8"))
    print(json.dumps(run_sanitized_probe(events, foundation)["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
