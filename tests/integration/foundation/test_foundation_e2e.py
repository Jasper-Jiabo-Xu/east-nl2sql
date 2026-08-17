"""EAS-40 Foundation 铺底端到端联调 — integration gate.

Exercises the frozen Foundation pipeline ``210 → 220 → 241 → 242 → 260 → 210``
(and the 010 release boundary) as one deterministic, sanitized run, proving:

1. ``initial_seed`` and ``expansion`` entry points share the same machine path;
2. the event ``missing_object_state`` entry routes ``FOUNDATION_REQUIRED`` back to
   210 as an explicit Foundation task;
3. 230 / 251 / 252 and the legacy generators/assemblers are invoked zero times;
4. prohibited record types are rejected;
5. 260 serializes verified data into a deterministic parameterized SQL batch and
   regresses it inside a physically separate formal-database copy (the formal
   store is byte-identical before and after);
6. 210 assembles a Foundation release candidate, retains ``resume_qa_ref`` (a
   ``{artifact_id, version, content_hash}`` reference, or null) by value, and is
   strictly consumed by the frozen 010 Stub — never writing the formal store.

All fixtures are desensitized constants; no real database, business data, ORM,
model response or log is read or produced.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref, content_hash  # noqa: E402
from east_v5.governance import ContractError, sha256  # noqa: E402

producer = importlib.import_module("east_v5.agents.210.foundation")
coordinator_mod = importlib.import_module("east_v5.agents.210.scheduler")
closure_mod = importlib.import_module("east_v5.agents.220.closure")
generator_mod = importlib.import_module("east_v5.agents.241.generator")
validator_mod = importlib.import_module("east_v5.agents.242.validator")
fixture_mod = importlib.import_module("east_v5.agents.242.sanitized_fixture")
regression = importlib.import_module("east_v5.agents.260.regression")
compiler = importlib.import_module("east_v5.foundation.compiler")
architecture = importlib.import_module("east_v5.architecture")

FIXTURE_DIR = ROOT / "fixtures" / "integration" / "foundation"
TIME = "2026-08-15T00:00:00+00:00"
HIERARCHY_REF = {"artifact_id": "TRG-V1.0.0", "version": 1, "content_hash": "b" * 64}
CA_REF = {"artifact_id": "CA-V0.3.0", "version": 1, "content_hash": "a" * 64}
RESUME_REF = {"artifact_id": "eas40-resume-qa", "version": 1, "content_hash": "d" * 64}
FORBIDDEN_AGENT_PREFIXES = ("east_v5.agents.230", "east_v5.agents.251", "east_v5.agents.252")


def _consume_stub_010(release: dict[str, Any]) -> str:
    """The frozen 010 consumer stub (schema + catalog route + mode)."""
    for name in ("tests.contracts.test_stage10_package_contracts", "contracts.test_stage10_package_contracts"):
        try:
            return importlib.import_module(name).consume_stub("release_candidate", "010", release)
        except ModuleNotFoundError:
            continue
    raise AssertionError("010 consumer stub unavailable")


def _load_task_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _wrap(artifact_type: str, artifact_id: str, payload: dict[str, Any], *, producer_id: str, mode: str, status: str = "candidate", qa_id: str | None = None, parents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parents = list(parents or [])
    envelope = {
        "artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": "eas40-e2e",
        "qa_id": qa_id, "version": 1, "schema_version": "COMMON-ENVELOPE/v1",
        "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": 1,
        "producer_id": producer_id, "parent_artifact_refs": parents,
        "input_hashes": [item["content_hash"] for item in parents], "status": status,
        "mode": mode, "created_at": TIME, "trace_id": "eas40-e2e", "storage_locator": None,
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _build_snapshot(base_database_version: str, object_state_records: list[dict[str, Any]], *, mode: str = "foundation", snapshot_id: str = "eas40-snapshot", qa_id: str | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": "v5.database-read-snapshot/v1", "snapshot_id": snapshot_id,
        "base_database_version": base_database_version, "query_time": TIME,
        "query_scope": "sanitized", "executed_queries": ["SELECT 1"],
        "object_state_records": object_state_records, "snapshot_hash": "",
    }
    payload["snapshot_hash"] = sha256({key: value for key, value in payload.items() if key != "snapshot_hash"})
    return _wrap("database_read_snapshot", snapshot_id, payload, producer_id="EAS-19", mode=mode, status="validated", qa_id=qa_id)


def _build_task(task_payload: dict[str, Any]) -> dict[str, Any]:
    return producer.build_foundation_task_package(
        task_payload, run_id="eas40-e2e", trace_id="eas40-e2e", created_at=TIME, parents=[CA_REF, HIERARCHY_REF]
    )


def _seed_closure(task: dict[str, Any]) -> dict[str, Any]:
    """Build the minimal Foundation closure and seed the frozen field + hierarchy scope."""
    closure = closure_mod.build_closure(task, [])
    closure["payload"]["fields"] = ["FIXTURE_CUSTOMER.C001", "FIXTURE_CUSTOMER.C002"]
    closure["payload"]["references"] = [{"type": "hierarchy_asset", "artifact_ref": HIERARCHY_REF}]
    closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"])
    return closure


def _run_pipeline(task: dict[str, Any], resolver: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Run 210→220→241→242→260→(210 report) and return every stage plus the databases."""
    profile = producer.build_foundation_profile(task)
    closure = _seed_closure(task)
    bound = generator_mod.BoundDataGenerator(ROOT).build_bound_data(
        closure, foundation_task_package=task, foundation_profile=profile, snapshot=snapshot, created_at=TIME
    )
    verified = validator_mod.DataValidator(ROOT).freeze_bound_data(bound, closure, resolver)
    copy_db, formal_db = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    for connection in (copy_db, formal_db):
        connection.execute("CREATE TABLE FIXTURE_CUSTOMER (C001 TEXT PRIMARY KEY, C002 TEXT)")
        connection.commit()
    # The copy mirrors the authenticated snapshot baseline; the formal store stays empty.
    for item in snapshot["payload"]["object_state_records"]:
        data = item["data"]
        columns = sorted(data)
        quoted_columns = ", ".join('"' + column + '"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        table = item["record_keys"]["table_id"]
        copy_db.execute(f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})', [data[column] for column in columns])
    copy_db.commit()
    package = regression.run_foundation_regression(ROOT, task, closure, verified, snapshot, copy_db, formal_db, set())
    return {
        "task": task, "profile": profile, "closure": closure, "bound": bound, "verified": verified,
        "snapshot": snapshot, "regression": package, "copy_db": copy_db, "formal_db": formal_db,
    }


def _trace_forbidden(fn: Callable[[], Any]) -> tuple[Any, list[str]]:
    """Return ``(result, calls)``; ``calls`` lists any invocation from 230/251/252."""
    calls: list[str] = []

    def tracer(frame: Any, event: str, arg: Any) -> Any:
        if event == "call":
            module = frame.f_globals.get("__name__", "")
            if module.startswith(FORBIDDEN_AGENT_PREFIXES):
                calls.append(f"{module}.{frame.f_code.co_name}")
        return tracer

    previous = sys.getprofile()
    sys.setprofile(tracer)
    try:
        return fn(), calls
    finally:
        sys.setprofile(previous)


class FoundationE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = fixture_mod.SanitizedRuntime()
        self.resolver = self.runtime.resolver()
        self.initial_task = _build_task(_load_task_fixture("foundation-task-initial.json"))
        self.expansion_task = _build_task(_load_task_fixture("foundation-task-expansion.json"))

    def tearDown(self) -> None:
        self.runtime.close()

    def test_initial_seed_and_expansion_share_machine_path(self) -> None:
        initial = _run_pipeline(self.initial_task, self.resolver, _build_snapshot("fixture-db-v1", []))
        self.assertEqual(initial["regression"]["payload"]["regression_status"], "passed")
        self.assertEqual(initial["regression"]["payload"]["database_state_delta"]["FIXTURE_CUSTOMER"]["after"], 1)
        self.assertEqual(initial["formal_db"].execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)

        snapshot = _build_snapshot("fixture-db-v1", [{"record_keys": {"table_id": "FIXTURE_CUSTOMER", "primary_key": "preexisting"}, "data": {"C001": "preexisting", "C002": "legal"}}])
        expansion = _run_pipeline(self.expansion_task, self.resolver, snapshot)
        self.assertEqual(expansion["regression"]["payload"]["database_state_delta"]["FIXTURE_CUSTOMER"], {"before": 1, "after": 2, "delta": 1, "passed": True})
        self.assertEqual(expansion["regression"]["payload"]["target_count_validation"]["FIXTURE_CUSTOMER"]["target"], 2)

    def test_event_missing_object_state_routes_to_explicit_foundation_task(self) -> None:
        data, orm, snapshot, review, spec, formal_db = self._event_context_with_missing_state()
        worker = regression.DatabaseCopyRegression(ROOT)
        feedback = worker.run_event(data, orm, snapshot, review, spec, formal_db)
        self.assertEqual(feedback["payload"]["failure_details"]["error_code"], "FOUNDATION_REQUIRED")
        self.assertEqual(feedback["payload"]["route_target"], "210")

        coordinator = coordinator_mod.DataStageCoordinator(ROOT)
        routed = coordinator.route_feedback(feedback)
        self.assertEqual(routed["target"], "210")
        self.assertEqual(routed["reason"], "FOUNDATION_REQUIRED")
        self.assertTrue(routed["requires_explicit_foundation_task"])

        # Close the loop with the real production-consumption edge: the routed
        # feedback triple becomes the expansion task's resume_qa_ref AND a direct
        # upstream in the task lineage, then runs 210→220→241→242→260→210→010.
        feedback_ref = routed["feedback_ref"]
        expansion_payload = {
            "schema_version": "v5.foundation-task-package/v1",
            "foundation_task_id": "eas40-foundation-expansion-event",
            "foundation_mode": "expansion",
            "trigger_reason": "sanitized expansion from FOUNDATION_REQUIRED feedback",
            "target_database_version": "fixture-db-v1",
            "target_object_types": ["FIXTURE_CUSTOMER"],
            "target_table_field_scope": {"FIXTURE_CUSTOMER": ["C001", "C002"]},
            "target_counts": {"FIXTURE_CUSTOMER": 1},
            "distribution_targets": {"FIXTURE_CUSTOMER": {"default": 1}},
            "hierarchy_asset_refs": [HIERARCHY_REF],
            "prohibited_record_types": ["EVENT_OWNED", "transaction", "contract"],
            "resume_qa_ref": feedback_ref,
            "constraint_asset_version": "CA-V0.3.0",
            "graph_version": "TRG-V1.0.0",
        }
        expansion_task = producer.build_foundation_task_package(
            expansion_payload, run_id="eas40-e2e", trace_id="eas40-e2e", created_at=TIME,
            parents=[CA_REF, HIERARCHY_REF, feedback_ref],
        )
        self.assertEqual(expansion_task["payload"]["resume_qa_ref"], feedback_ref)
        self.assertIn(feedback_ref, expansion_task["envelope"]["parent_artifact_refs"])
        self.assertIn(feedback_ref["content_hash"], expansion_task["envelope"]["input_hashes"])

        result = _run_pipeline(expansion_task, self.resolver, _build_snapshot("fixture-db-v1", []))
        self.assertEqual(result["regression"]["payload"]["regression_status"], "passed")

        release = coordinator.build_foundation_release(expansion_task, result["regression"])
        self.assertEqual(release["payload"]["resume_qa_ref"], feedback_ref)
        self.assertEqual(release["payload"]["resume_qa_ref"], routed["feedback_ref"])
        self.assertEqual(_consume_stub_010(release), release["envelope"]["content_hash"])
        self.assertEqual(result["formal_db"].execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)

    def test_forbidden_agents_and_legacy_components_zero_invocation(self) -> None:
        verified_arch = architecture.verify_architecture(ROOT)
        self.assertEqual(verified_arch["architecture"]["foundation"]["forbidden_agents"], ["230", "251", "252"])
        for legacy_name in verified_arch["architecture"]["prohibited_runtime_components"]:
            hits = [path for path in (ROOT / "src" / "east_v5").rglob("*.py") if legacy_name in path.read_text(encoding="utf-8")]
            self.assertEqual(hits, [], f"legacy component present in control plane: {legacy_name}")

        _, calls = _trace_forbidden(lambda: _run_pipeline(self.initial_task, self.resolver, _build_snapshot("fixture-db-v1", [])))
        self.assertEqual(calls, [])

    def test_prohibited_record_types_rejected(self) -> None:
        verified = _run_pipeline(self.initial_task, self.resolver, _build_snapshot("fixture-db-v1", []))["verified"]
        candidate = copy.deepcopy(verified)
        candidate["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["record_type"] = "transaction"
        candidate["payload"]["validated_hash"] = sha256(candidate["payload"]["validated_data_package"])
        candidate["envelope"]["content_hash"] = content_hash(candidate["envelope"], candidate["payload"])
        with self.assertRaisesRegex(ContractError, "FOUNDATION_PROHIBITED_TYPE_HIT"):
            regression.validate_foundation_regression_inputs(ROOT, self.initial_task, _seed_closure(self.initial_task), candidate, _build_snapshot("fixture-db-v1", []))

        with self.assertRaisesRegex(ContractError, "FOUNDATION_EVENT_OWNED_REJECTED"):
            compiler.compile_insert_batch(
                {"schema_version": "v5.foundation-verified-data/v1", "mode": "foundation", "base_database_version": "d", "constraint_asset_version": "CA-V0.3.0", "graph_version": "TRG-V1.0.0", "records": [{"record_id": "r1", "table": "EVENT_TABLE", "values": {"F": 1}, "depends_on": []}]},
                {"EVENT_TABLE"},
            )

    def test_260_deterministic_parameterized_batch_in_isolated_copy(self) -> None:
        first = _run_pipeline(self.initial_task, self.resolver, _build_snapshot("fixture-db-v1", []))
        second = _run_pipeline(self.initial_task, self.resolver, _build_snapshot("fixture-db-v1", []))
        report = first["regression"]["payload"]
        batch = report["foundation_write_batch"]

        for statement in batch["sql_statements"]:
            self.assertIn("?", statement["sql"])
        for rendered in batch["rendered_sql_for_audit"]:
            self.assertNotIn("?", rendered)
        self.assertEqual(report["sandbox_execution_report"]["committed"], True)
        self.assertEqual(report["sandbox_execution_report"]["compiler"], "east-foundation-insert-compiler/v1")

        # Byte-level determinism: identical inputs reproduce an identical frozen
        # write batch (the report envelope carries wall-clock step timestamps).
        self.assertEqual(report["foundation_write_batch"], second["regression"]["payload"]["foundation_write_batch"])
        self.assertEqual(report["foundation_write_batch_hash"], second["regression"]["payload"]["foundation_write_batch_hash"])

        # The formal store is untouched; only the isolated copy received the delta.
        self.assertEqual(first["formal_db"].execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)
        self.assertEqual(first["copy_db"].execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 1)

    def test_210_assembles_release_candidate_retaining_null_resume_qa_ref(self) -> None:
        result = _run_pipeline(self.initial_task, self.resolver, _build_snapshot("fixture-db-v1", []))
        coordinator = coordinator_mod.DataStageCoordinator(ROOT)
        release = coordinator.build_foundation_release(result["task"], result["regression"])
        self.assertEqual(release["payload"]["release_mode"], "foundation")
        self.assertIsNone(release["payload"]["resume_qa_ref"])
        self.assertIsNone(release["payload"]["approved_question_sql_ref"])
        self.assertIsNone(release["payload"]["event_regression_passed_ref"])
        self.assertEqual(release["payload"]["foundation_regression_report_ref"], artifact_ref(result["regression"]["envelope"]))
        self.assertEqual(_consume_stub_010(release), release["envelope"]["content_hash"])
        # No formal commit: the release candidate is a candidate; the formal store stayed empty.
        self.assertEqual(result["formal_db"].execute("SELECT COUNT(*) FROM FIXTURE_CUSTOMER").fetchone()[0], 0)

    def test_non_null_resume_qa_ref_retained_by_value_and_strictly_consumed_by_010(self) -> None:
        # The approved expansion fixture (foundation_mode=expansion) carries a
        # non-null resume_qa_ref triple; it must never be an initial_seed impostor.
        task = self.expansion_task
        self.assertEqual(task["payload"]["foundation_mode"], "expansion")
        snapshot = _build_snapshot("fixture-db-v1", [{"record_keys": {"table_id": "FIXTURE_CUSTOMER", "primary_key": "preexisting"}, "data": {"C001": "preexisting", "C002": "legal"}}])
        result = _run_pipeline(task, self.resolver, snapshot)
        coordinator = coordinator_mod.DataStageCoordinator(ROOT)
        release = coordinator.build_foundation_release(task, result["regression"])

        # The non-null triple is retained by identity/value — no serialization or id extraction.
        self.assertIs(release["payload"]["resume_qa_ref"], task["payload"]["resume_qa_ref"])
        self.assertEqual(release["payload"]["resume_qa_ref"], RESUME_REF)
        self.assertEqual(release["payload"]["resume_qa_ref"], {"artifact_id": "eas40-resume-qa", "version": 1, "content_hash": "d" * 64})
        # Idempotent re-computation.
        self.assertEqual(coordinator.build_foundation_release(task, result["regression"]), release)
        # The frozen 010 Stub strictly consumes it.
        self.assertEqual(_consume_stub_010(release), release["envelope"]["content_hash"])

        # A malformed resume_qa_ref (bare string) is strictly rejected by the 010 Stub.
        mutated = copy.deepcopy(release)
        mutated["payload"]["resume_qa_ref"] = "legacy-qa-id"
        mutated["envelope"]["content_hash"] = content_hash(mutated["envelope"], mutated["payload"])
        with self.assertRaisesRegex(ContractError, "RELEASE_CANDIDATE_STUB_REJECTED"):
            _consume_stub_010(mutated)

    def test_manifest_is_recomputable_from_frozen_fixtures(self) -> None:
        """The machine-readable manifest is reproducible, not hand-authored.

        Every fixture SHA-256 and every deterministic_outputs entry must be
        recomputed from the frozen inputs on this exact head; any drift fails.
        """
        manifest_path = ROOT / "docs" / "reports" / "integration" / "foundation" / "EAS-40-运行清单.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        for name, expected in manifest["frozen_inputs"]["fixtures"].items():
            actual = hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"fixture sha256 drift: {name}")

        cases = [
            ("initial_seed", "foundation-task-initial.json", False),
            ("expansion", "foundation-task-expansion.json", True),
        ]
        for mode, fixture_name, preexisting in cases:
            task = _build_task(_load_task_fixture(fixture_name))
            records = [{"record_keys": {"table_id": "FIXTURE_CUSTOMER", "primary_key": "preexisting"}, "data": {"C001": "preexisting", "C002": "legal"}}] if preexisting else []
            result = _run_pipeline(task, self.resolver, _build_snapshot("fixture-db-v1", records))
            expected = manifest["deterministic_outputs"][mode]
            self.assertEqual(artifact_ref(result["task"]["envelope"]), expected["foundation_task_ref"])
            self.assertEqual(artifact_ref(result["profile"]["envelope"]), expected["foundation_profile_ref"])
            self.assertEqual(artifact_ref(result["closure"]["envelope"]), expected["structure_closure_ref"])
            self.assertEqual(artifact_ref(result["bound"]["envelope"]), expected["bound_data_ref"])
            self.assertEqual(artifact_ref(result["verified"]["envelope"]), expected["verified_bound_data_ref"])
            self.assertEqual(result["regression"]["payload"]["foundation_write_batch_hash"], expected["foundation_write_batch_hash"])

    # ------------------------------------------------------------------ helpers

    def _event_context_with_missing_state(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], Path]:
        data = copy.deepcopy(importlib.import_module("east_v5.agents.242.probe").run_sanitized_probe(ROOT)["transport"])
        data["envelope"].update({"mode": "event_data", "qa_id": "QA-260"})
        record = data["payload"]["validated_data_package"]["data_groups"][0]["records"][0]
        record["existing_record_refs"] = [{"table_id": "FIXTURE_CUSTOMER", "record_key": "missing-in-snapshot"}]
        data["payload"]["validated_hash"] = sha256(data["payload"]["validated_data_package"])
        data["envelope"]["content_hash"] = content_hash(data["envelope"], data["payload"])

        orm = copy.deepcopy(importlib.import_module("east_v5.agents.252.probe").run_sanitized_probe()["transport"])
        orm["envelope"].update({"mode": "event_data", "qa_id": "QA-260"})
        orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"])

        snapshot = _build_snapshot("db-v1", [], mode="event_data", snapshot_id="event-snapshot", qa_id="QA-260")

        spec = _wrap(
            "query_specification_package", "qspec-260",
            {"query_spec_id": "qspec-260", "penalty_fact_package_ref": {"artifact_id": "penalty", "version": 1, "content_hash": "1" * 64}, "observable_fact_package_ref": {"artifact_id": "observable", "version": 1, "content_hash": "2" * 64}, "query_goal": "open account", "must_preserve_fact_refs": ["fact"], "main_object_and_grain": {"main_object": "account", "grain": "FIXTURE_ACCOUNT.CUSTOMER_ID"}, "query_entry": {"entry_table": "FIXTURE_ACCOUNT", "entry_conditions": []}, "related_objects_and_path": [], "filters_and_evidence": [], "return_fields": [{"field_id": "CUSTOMER_ID", "display_name": "customer", "source_table": "FIXTURE_ACCOUNT"}, {"field_id": "STATUS", "display_name": "status", "source_table": "FIXTURE_ACCOUNT"}], "aggregation_dedup_sort_time": {"group_by_fields": ["CUSTOMER_ID"], "distinct_required": True, "order_by": [], "time_window": {"field_id": "STATUS", "window_type": "point"}}, "observability_boundary": {"answerable": ["open"], "unanswerable": []}, "expected_result_shape": {"row_grain": "account", "column_set": ["CUSTOMER_ID", "STATUS"], "aggregation_shape": "none"}, "sql_schema_scope": {"allowed_tables": [{"table_id": "FIXTURE_ACCOUNT", "allowed_fields": ["CUSTOMER_ID", "STATUS"]}]}, "minimum_positive_count": 2, "minimum_negative_count": 1, "condition_coverage": [{"predicate": "open", "positive_types": ["positive"], "negative_types": ["hard_negative"]}], "code_value_coverage": [{"field_id": "STATUS", "target_code_values": ["OPEN"]}], "expected_row_group_count": {"minimum": 1, "target": 1, "tolerance_range": {"low": 0, "high": 0}}, "join_expansion_limit": {"max_multiplier": 1, "max_result_rows": 1}, "query_specification_package_schema_version": "query-specification-v1"},
            producer_id="140", mode="event_data", status="validated", qa_id="QA-260",
        )

        review_payload = {"schema_version": "v5.question-sql-dual-review-passed/v1", "candidate_ref": {"artifact_id": "candidate", "version": 1, "content_hash": "3" * 64}, "candidate_content": {"clear_question": "open accounts", "sql_gold": "SELECT CUSTOMER_ID, STATUS FROM FIXTURE_ACCOUNT WHERE STATUS = 'OPEN'", "sql_explanation": {"select": "s", "from_join": "f", "where": "w", "aggregation": "", "sort": "", "business_meaning": "b"}, "business_event_candidates": [{"event_name": "open", "objective": "o", "objects": ["account"], "state_changes": []}], "specification_mapping": [{"spec_item": "open", "question_fragment": "open", "sql_fragment": "STATUS"}]}, "query_specification_package": artifact_ref(spec["envelope"]), "penalty_fact_package": {"artifact_id": "penalty", "version": 1, "content_hash": "1" * 64}, "observable_fact_package": {"artifact_id": "observable", "version": 1, "content_hash": "2" * 64}, "constraint_evidence_summary": {"tables": ["FIXTURE_ACCOUNT"], "fields": ["STATUS"], "data_elements": [], "relationships": [], "source_refs": ["CA-V0.3.0"]}, "precheck_report": {"decision": "pass", "report_hash": "4" * 64}, "deepseek_review": {"decision": "pass", "issue_level": "none", "reason": "ok", "review_hash": "5" * 64}, "glm_review": {"decision": "pass", "issue_level": "none", "reason": "ok", "review_hash": "6" * 64}, "adjudication": {"decision": "pass", "report_hash": "7" * 64}, "review_round": 1, "package_hash": ""}
        review_payload["package_hash"] = sha256({key: value for key, value in review_payload.items() if key != "package_hash"})
        review = _wrap("question_sql_dual_review_passed", "review-260", review_payload, producer_id="110", mode="event_data", status="validated", qa_id="QA-260", parents=[artifact_ref(spec["envelope"])])

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        formal_db = Path(tmp.name) / "formal.sqlite"
        connection = sqlite3.connect(formal_db)
        connection.execute("CREATE TABLE FIXTURE_ACCOUNT (CUSTOMER_ID TEXT, STATUS TEXT)")
        connection.commit()
        connection.close()
        return data, orm, snapshot, review, spec, formal_db


if __name__ == "__main__":
    unittest.main()
