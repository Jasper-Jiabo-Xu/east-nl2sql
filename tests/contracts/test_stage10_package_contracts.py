from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import content_hash, validate_envelope
from east_v5.governance import ContractError


CONTRACTS = {
    "question_sql_dual_review_passed": {
        "producer": "110", "consumers": ["210"], "modes": ["event"],
        "payload_schema": "v5.question-sql-dual-review-passed/v1",
        "package_schema": "contracts/packages/question-sql-dual-review-passed-package.schema.json",
    },
    "reviewed_question_sql": {
        "producer": "210", "consumers": ["150", "220"], "modes": ["event"],
        "payload_schema": "v5.reviewed-question-sql/v1",
        "package_schema": "contracts/packages/reviewed-question-sql-package.schema.json",
    },
    "release_candidate": {
        "producer": "210", "consumers": ["010"], "modes": ["event", "foundation"],
        "payload_schema": "v5.release-candidate/v1",
        "package_schema": "contracts/packages/release-candidate-package.schema.json",
    },
}


def ref(name: str, value: str = "a") -> dict[str, object]:
    return {"artifact_id": name, "version": 1, "content_hash": value * 64}


def package(kind: str, payload: dict[str, object], *, producer: str, mode: str, status: str = "candidate") -> dict[str, object]:
    envelope = {
        "artifact_id": f"stage10-{kind}", "artifact_type": kind, "run_id": "stage10-contract",
        "qa_id": "QA-STAGE10", "version": 1, "schema_version": "COMMON-ENVELOPE/v1",
        "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1,
        "producer_id": producer, "parent_artifact_refs": [], "input_hashes": [], "status": status,
        "mode": mode, "created_at": "2026-08-17T00:00:00+00:00", "trace_id": "stage10-contract",
        "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def schema_validator(kind: str) -> Draft202012Validator:
    schema = json.loads((ROOT / CONTRACTS[kind]["package_schema"]).read_text(encoding="utf-8"))
    common = json.loads((ROOT / "contracts/common/common-envelope.schema.json").read_text(encoding="utf-8"))
    resources = [(schema["$id"], Resource.from_contents(schema)), (common["$id"], Resource.from_contents(common))]
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


def consume_stub(kind: str, consumer: str, value: dict[str, object]) -> str:
    """Independent catalog + Schema consumer; it never releases or writes a database."""
    try:
        validate_envelope(ROOT, value["envelope"], value["payload"])
        schema_validator(kind).validate(value)
    except (ContractError, ValidationError) as exc:
        raise ContractError(f"{kind.upper()}_STUB_REJECTED") from exc
    catalog = json.loads((ROOT / "config/v5-package-catalog.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["packages"] if item["id"] == kind)
    if entry["producer"] != value["envelope"]["producer_id"] or consumer not in entry["consumers"]:
        raise ContractError("CATALOG_ROUTE_REJECTED")
    required_mode = "foundation" if value["envelope"]["mode"] == "foundation" else "event"
    if required_mode not in entry["modes"]:
        raise ContractError("CATALOG_MODE_REJECTED")
    return value["envelope"]["content_hash"]


def dual_review_passed() -> dict[str, object]:
    payload = {
        "schema_version": "v5.question-sql-dual-review-passed/v1", "candidate_ref": ref("candidate"),
        "candidate_content": {
            "clear_question": "脱敏问题", "sql_gold": "SELECT F1 FROM T1", "sql_explanation": {
                "select": "F1", "from_join": "T1", "where": "无", "aggregation": "无", "sort": "无", "business_meaning": "脱敏说明"},
            "business_event_candidates": [{"event_name": "开户", "objective": "开户", "objects": ["客户"], "state_changes": []}],
            "specification_mapping": [{"spec_item": "S1", "question_fragment": "问题", "sql_fragment": "F1"}],
        },
        "query_specification_package": ref("query-spec", "b"), "penalty_fact_package": ref("penalty", "c"),
        "observable_fact_package": ref("observable", "d"),
        "constraint_evidence_summary": {"tables": ["T1"], "fields": ["T1.F1"], "data_elements": [], "relationships": [], "source_refs": ["CA-V0.3.0"]},
        "precheck_report": {"decision": "pass", "report_hash": "e" * 64},
        "deepseek_review": {"decision": "pass", "issue_level": "none", "reason": "通过", "review_hash": "f" * 64},
        "glm_review": {"decision": "pass", "issue_level": "none", "reason": "通过", "review_hash": "0" * 64},
        "adjudication": {"decision": "pass", "report_hash": "1" * 64}, "review_round": 1,
        "package_hash": "2" * 64,
    }
    return package("question_sql_dual_review_passed", payload, producer="110", mode="event_data", status="validated")


def reviewed_question_sql() -> dict[str, object]:
    fixture = json.loads((ROOT / "tests/agents/220/fixtures/event-data-dual-review.json").read_text(encoding="utf-8"))
    return fixture


def release_candidate(mode: str = "event_data", *, resume_qa_ref: dict[str, object] | None = None) -> dict[str, object]:
    event = mode == "event_data"
    payload = {
        "release_candidate_id": f"release-{mode}", "release_mode": mode,
        "approved_question_sql_ref": ref("approved-question", "3") if event else None,
        "event_regression_passed_ref": ref("event-regression", "4") if event else None,
        "foundation_regression_report_ref": None if event else ref("foundation-regression", "5"),
        "target_database_version": "fixture-db-v1", "target_question_dataset_version": "fixture-question-v1" if event else None,
        "idempotency_key": f"stage10-{mode}-1", "expected_write_summary": {"T1": {"insert": 1, "update": 0}},
        "package_hashes": ({"question_sql": "6" * 64, "data": "7" * 64, "orm": "8" * 64, "regression": "9" * 64} if event else {"foundation_task": "a" * 64, "data": "b" * 64, "write_batch": "c" * 64, "regression_report": "d" * 64}),
        "resume_qa_ref": resume_qa_ref,
    }
    return package("release_candidate", payload, producer="210", mode=mode)


class Stage10PackageContractTests(unittest.TestCase):
    def test_catalog_and_stage10_schemas_have_one_to_one_frozen_routes(self) -> None:
        catalog = json.loads((ROOT / "config/v5-package-catalog.json").read_text(encoding="utf-8"))
        catalog_schema = json.loads((ROOT / "contracts/v5-package-catalog.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(catalog_schema).validate(catalog)
        entries = {entry["id"]: entry for entry in catalog["packages"]}
        for kind, expected in CONTRACTS.items():
            with self.subTest(kind=kind):
                self.assertEqual(entries[kind], {"id": kind, **expected})
                self.assertTrue((ROOT / expected["package_schema"]).is_file())
        managed_paths = {value["package_schema"] for value in CONTRACTS.values()}
        self.assertEqual(len(managed_paths), len(CONTRACTS))

    def test_valid_packages_are_consumed_by_each_frozen_downstream_stub(self) -> None:
        self.assertTrue(consume_stub("question_sql_dual_review_passed", "210", dual_review_passed()))
        reviewed = reviewed_question_sql()
        self.assertEqual(consume_stub("reviewed_question_sql", "150", reviewed), consume_stub("reviewed_question_sql", "220", reviewed))
        self.assertTrue(consume_stub("release_candidate", "010", release_candidate()))
        self.assertTrue(consume_stub("release_candidate", "010", release_candidate("foundation")))

    def test_identity_route_mode_unknown_field_and_hash_drift_are_rejected(self) -> None:
        cases = [
            ("dual_producer", dual_review_passed(), lambda p: p["envelope"].update({"producer_id": "210"}), "210"),
            ("dual_mode", dual_review_passed(), lambda p: p["envelope"].update({"mode": "foundation", "qa_id": None}), "210"),
            ("review_unknown", reviewed_question_sql(), lambda p: p["payload"].update({"legacy_alias": True}), "220"),
            ("review_version", reviewed_question_sql(), lambda p: p["payload"]["candidate_ref"].update({"version": 0}), "150"),
            ("release_consumer", release_candidate(), lambda p: None, "999"),
            ("release_mode", release_candidate(), lambda p: p["envelope"].update({"mode": "foundation", "qa_id": None}), "010"),
            ("release_hash", release_candidate(), lambda p: p["payload"].update({"idempotency_key": "drift"}), "010"),
        ]
        for name, value, mutate, consumer in cases:
            with self.subTest(name=name):
                mutated = copy.deepcopy(value)
                mutate(mutated)
                kind = mutated["envelope"]["artifact_type"]
                with self.assertRaisesRegex(ContractError, "(STUB_REJECTED|CATALOG_ROUTE_REJECTED)"):
                    consume_stub(kind, consumer, mutated)

    def test_release_mode_keeps_event_and_foundation_responsibilities_disjoint(self) -> None:
        event = release_candidate()
        foundation = release_candidate("foundation")
        self.assertIsNotNone(event["payload"]["event_regression_passed_ref"])
        self.assertIsNone(event["payload"]["foundation_regression_report_ref"])
        self.assertIsNone(foundation["payload"]["event_regression_passed_ref"])
        self.assertIsNotNone(foundation["payload"]["foundation_regression_report_ref"])
        leaked = copy.deepcopy(foundation)
        leaked["payload"]["approved_question_sql_ref"] = ref("alias")
        leaked["envelope"]["content_hash"] = content_hash(leaked["envelope"], leaked["payload"])
        with self.assertRaisesRegex(ContractError, "RELEASE_CANDIDATE_STUB_REJECTED"):
            consume_stub("release_candidate", "010", leaked)

    def test_resume_qa_ref_is_a_strict_artifact_reference_or_null(self) -> None:
        expansion = release_candidate("foundation", resume_qa_ref=ref("resume-qa", "e"))
        self.assertEqual(consume_stub("release_candidate", "010", expansion), expansion["envelope"]["content_hash"])
        self.assertTrue(consume_stub("release_candidate", "010", release_candidate("foundation")))

        cases = [
            ("legacy_string", lambda payload: payload.update({"resume_qa_ref": "legacy-qa-id"})),
            ("missing_field", lambda payload: payload.pop("resume_qa_ref")),
            ("unknown_field", lambda payload: payload.update({"resume_qa_ref": {**ref("resume-qa", "e"), "storage_locator": "forbidden"}})),
            ("invalid_version", lambda payload: payload.update({"resume_qa_ref": {**ref("resume-qa", "e"), "version": 0}})),
            ("invalid_hash", lambda payload: payload.update({"resume_qa_ref": {**ref("resume-qa", "e"), "content_hash": "not-a-hash"}})),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                candidate = release_candidate("foundation", resume_qa_ref=ref("resume-qa", "e"))
                mutate(candidate["payload"])
                candidate["envelope"]["content_hash"] = content_hash(candidate["envelope"], candidate["payload"])
                with self.assertRaisesRegex(ContractError, "RELEASE_CANDIDATE_STUB_REJECTED"):
                    consume_stub("release_candidate", "010", candidate)

        event = release_candidate()
        event["payload"]["resume_qa_ref"] = ref("resume-qa", "e")
        event["envelope"]["content_hash"] = content_hash(event["envelope"], event["payload"])
        with self.assertRaisesRegex(ContractError, "RELEASE_CANDIDATE_STUB_REJECTED"):
            consume_stub("release_candidate", "010", event)


if __name__ == "__main__":
    unittest.main()
