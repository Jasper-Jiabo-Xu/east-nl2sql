"""EAS-39 data 业务事件双通路端到端联调（210→220→230→{241→242,251→252}→260→210）。

本测试使用冻结的批准 QS Fixture（110 双审核通过包）与查询规格（140 输出），
真实驱动 210 调度器、220 结构闭包、230 操作闭包、241/242 数据通路、
251/252 ORM 通路、260 数据库副本回归，并证明：

- 241 与 251 消费同一操作闭包且槽位可一一绑定；
- 260 仅在正式库 copy 上绑定/回归，正式库字节不变；
- 210 仅在回归通过后组装 FORMAL-RELEASE-CANDIDATE，不正式提交；
- DATA_VALUE_ERROR→241 / ORM_PLAN_ERROR→251 / SQL_EXECUTION_ERROR→010 /
  FOUNDATION_REQUIRED→210 / MANUAL_REVIEW_REQUIRED→manual 五类路由均以
  「真实 260 产包 → 真实 210 消费并确定性路由」为整链证据。
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

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures" / "integration" / "data"
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref, content_hash
from east_v5.governance import ContractError, canonical_bytes, sha256
from east_v5.agents.east_150 import MAPPED_SPEC_ITEMS, PendingPrecheckBuilder
from east_v5.agents.east_180.reviewer import GLMReviewerAgent

coordinator_mod = importlib.import_module("east_v5.agents.210.scheduler")
closure_mod = importlib.import_module("east_v5.agents.220.closure")
operation_mod = importlib.import_module("east_v5.agents.230.builder")
data_generator_mod = importlib.import_module("east_v5.agents.241.generator")
data_validator_mod = importlib.import_module("east_v5.agents.242.validator")
sanitized_fixture_mod = importlib.import_module("east_v5.agents.242.sanitized_fixture")
orm_generator_mod = importlib.import_module("east_v5.agents.251.generator")
orm_validator_mod = importlib.import_module("east_v5.agents.252.validator")
regression_mod = importlib.import_module("east_v5.agents.260.regression")
question_scheduler_mod = importlib.import_module("east_v5.agents.110.scheduler")
committer_mod = importlib.import_module("east_v5.agents.010.committer")
precheck_mod = importlib.import_module("east_v5.agents.160.precheck")
review_170_mod = importlib.import_module("east_v5.agents.170.review")

try:
    stub_210 = importlib.import_module("tests.agents.260.approved_210_stub")
except ModuleNotFoundError:
    stub_210 = importlib.import_module("agents.260.approved_210_stub")


def _test_module(package: str):
    for name in (f"tests.{package}", package):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name.split(".")[0]:
                raise
    raise AssertionError(f"test module unavailable: {package}")


TIME = "2026-08-17T00:00:00+00:00"


class _ScriptedGLM:
    def review(self, _request):
        return json.dumps({"reviewer_id": "180", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [{"kind": "fixture", "ref": "data-chain", "description": "脱敏"}], "route_suggestion": "150"})


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _rehash(package: dict) -> dict:
    package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])
    return package


def _asset(source: dict, request: dict, records: list[dict], parent: dict) -> dict:
    envelope = {
        "artifact_id": f"asset:{request['request_id']}", "artifact_type": "constraint_asset_package",
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
        "constraint_summary": {"total_matched": len(records), "asset_types_covered": [record["record_type"] for record in records]},
        "unmatched_items": [], "query_trace": [],
    }
    envelope["content_hash"] = content_hash(envelope, payload)
    return {"envelope": envelope, "payload": payload}


def _event_results(reviewed: dict, context: dict) -> tuple[dict, dict]:
    requests = closure_mod.event_query_rounds(reviewed, context)
    source = reviewed["envelope"]
    first = _asset(source, requests[0], [{
        "record_type": "single_field",
        "data": {"table_id": requests[0]["table_scope"][0], "field_id": requests[0]["field_scope"][0].split(".", 1)[1]},
        "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": [],
    }], closure_mod._ref_for_request(requests[0]))
    second = _asset(source, requests[1], [{
        "record_type": "cross_table",
        "data": {"from": requests[1]["relationship_scope"][0].split("->", 1)[0], "to": requests[1]["relationship_scope"][0].split("->", 1)[1]},
        "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": [],
    }], artifact_ref(first["envelope"]))
    return first, second


class EventDualPathIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dirs = []
        self._pending_by_version = {}
        self.coordinator = coordinator_mod.DataStageCoordinator(ROOT)
        self.spec = _rehash(_load("query-specification.json"))
        self.spec["payload"]["query_entry"]["entry_conditions"] = [{"field_id": "F001", "operator": "=", "value": "A"}]
        self.spec["envelope"]["mode"] = "question_sql"
        _rehash(self.spec)
        self.approved, self.binding = self._approved_via_real_question_sql_chain(self.spec)
        self.snapshot = self._bind_snapshot(_load("database-read-snapshot.json"))

    def _bind_snapshot(self, snapshot: dict) -> dict:
        snapshot["payload"]["snapshot_hash"] = sha256({key: value for key, value in snapshot["payload"].items() if key != "snapshot_hash"})
        snapshot["envelope"]["content_hash"] = content_hash(snapshot["envelope"], snapshot["payload"])
        return snapshot

    def _bind_approved(self, approved: dict, spec: dict) -> dict:
        approved["payload"]["query_specification_package"] = artifact_ref(spec["envelope"])
        candidate = approved["payload"]["candidate_content"]
        candidate["sql_gold"] = "SELECT T1.F001, T1.F002 FROM FIXTURE_T001 T1 JOIN FIXTURE_T002 T2 ON T1.F001 = T2.PK001 WHERE T1.F001 = :account_value"
        candidate["query_parameter_bindings"] = [{"name": "account_value", "source_pointer": "/query_entry/entry_conditions/0/value"}]
        approved["payload"]["package_hash"] = sha256({key: value for key, value in approved["payload"].items() if key != "package_hash"})
        approved["envelope"]["content_hash"] = content_hash(approved["envelope"], approved["payload"])
        return approved

    def _approved_via_real_question_sql_chain(self, spec: dict, *, source_pointer: str = "/query_entry/entry_conditions/0/value") -> tuple[dict, dict]:
        """Exercise 140→150→160→170/180→110 without hand-writing a 110 package."""
        candidate = _load("approved-question-sql.json")["payload"]["candidate_content"]
        candidate["sql_gold"] = "SELECT T1.F001, T1.F002 FROM FIXTURE_T001 T1 JOIN FIXTURE_T002 T2 ON T1.F001 = T2.PK001 WHERE T1.F001 = :account_value"
        candidate["query_parameter_bindings"] = [{"name": "account_value", "source_pointer": source_pointer}]
        fragments = ("T1.F001", "T1.F002", "T2.PK001")
        candidate["specification_mapping"] = [{"spec_item": item, "question_fragment": candidate["clear_question"], "sql_fragment": fragments[index % len(fragments)]} for index, item in enumerate(MAPPED_SPEC_ITEMS)]
        builder = PendingPrecheckBuilder(ROOT)
        version = spec["envelope"]["version"]
        pending_kwargs = {"version": version, "attempt_no": spec["envelope"]["attempt_no"]}
        if version > 1:
            pending_kwargs["artifact_id"] = self._pending_by_version[version - 1]["envelope"]["artifact_id"]
            pending_kwargs["supersedes_ref"] = artifact_ref(self._pending_by_version[version - 1]["envelope"])
        pending = builder.build_pending_precheck(spec, run_id=spec["envelope"]["run_id"], qa_id=spec["envelope"]["qa_id"], created_at=TIME, **pending_kwargs, **candidate)
        self._pending_by_version[version] = pending
        checker = precheck_mod.PrecheckAgent(ROOT)
        precheck = checker.precheck(pending, spec, checked_at=TIME)
        self.assertEqual(precheck["decision"], "pass")
        dual = checker.build_dual_review(pending, spec, precheck, created_at=TIME)
        review_170 = review_170_mod.DeepSeekReviewAgent(ROOT).review(dual, {"reviewer_id": "170", "decision": "yes", "error_types": [], "error_details": [], "evidence_refs": [], "route_suggestion": "150"}, created_at=TIME)
        review_180 = GLMReviewerAgent(ROOT, _ScriptedGLM()).review(dual, created_at=TIME)
        result = question_scheduler_mod.QuestionSqlStageScheduler(ROOT).collect_reviews(dual, [review_170, review_180], spec, created_at=TIME)
        self.assertEqual(result["target"], "210")
        return result["approved_package"], result["query_parameter_binding"]

    def _refresh_spec(self, mutate) -> dict:
        spec = copy.deepcopy(self.spec)
        mutate(spec["payload"])
        spec["envelope"]["content_hash"] = content_hash(spec["envelope"], spec["payload"])
        return spec

    def _versioned_spec(self, value: object) -> dict:
        """Create the next immutable 140 revision, retaining the same logical id."""
        spec = copy.deepcopy(self.spec)
        previous = artifact_ref(spec["envelope"])
        spec["envelope"]["version"] += 1
        spec["envelope"]["supersedes_ref"] = previous
        spec["payload"]["query_entry"]["entry_conditions"][0]["value"] = value
        for coverage in spec["payload"]["code_value_coverage"]:
            if coverage["field_id"] == "F001":
                coverage["target_code_values"] = [value]
        return _rehash(spec)

    def _formal_db(self, directory: Path) -> Path:
        db = directory / "formal.sqlite"
        connection = sqlite3.connect(db)
        connection.executescript("CREATE TABLE FIXTURE_T001 (F001 TEXT, F002 TEXT); CREATE TABLE FIXTURE_T002 (PK001 TEXT);")
        connection.commit()
        connection.close()
        return db

    def _formal_sql_error_file(self) -> Path:
        """A legal copy target whose join comparison fails only at SQL execution.

        The 140/110/210 source remains untouched: the frozen ORM can insert
        into these tables, then the approved join deterministically encounters
        the absent collation in the 260 copy's SELECT.
        """
        directory = tempfile.TemporaryDirectory()
        self._tmp_dirs.append(directory)
        db = Path(directory.name) / "formal-sql-error.sqlite"
        connection = sqlite3.connect(db)
        connection.create_collation("COPY_ONLY_COLLATION", lambda left, right: (left > right) - (left < right))
        connection.executescript("CREATE TABLE FIXTURE_T001 (F001 TEXT COLLATE COPY_ONLY_COLLATION, F002 TEXT); CREATE TABLE FIXTURE_T002 (PK001 TEXT);")
        connection.commit()
        connection.close()
        return db

    def _chain(self, *, approved: dict | None = None, spec: dict | None = None, binding: dict | None = None):
        """Build one same-source 210→220→230→{241→242,251→252} pair."""
        approved = self.approved if approved is None else approved
        spec = self.spec if spec is None else spec
        binding = binding or (self.binding if approved is self.approved and spec is self.spec else question_scheduler_mod.QuestionSqlStageScheduler(ROOT).build_query_parameter_binding(approved, spec, created_at=TIME))
        started = self.coordinator.begin_event(approved, spec, binding)
        reviewed = started["reviewed_question_sql"]
        context = started["event_query_context"]
        first_asset, second_asset = _event_results(reviewed, context)
        structure = closure_mod.build_event_closure(reviewed, context, first_asset, second_asset)
        operation = operation_mod.OperationClosureBuilder(ROOT).build(structure)

        data_builder = data_generator_mod.BoundDataGenerator(ROOT)
        draft = data_builder.build_bound_data(structure, operation_closure=operation, created_at=TIME)
        groups = copy.deepcopy(draft["payload"]["data_groups"])
        for record in groups[0]["records"]:
            record["case_role"] = "positive"
            for value in record["field_values"]:
                value["value"] = binding["payload"]["parameters"][0]["value"] if value["field_id"] in {"F001", "PK001"} else "B"
        for index in (0, 1):
            negative = copy.deepcopy(groups[0]["records"][index])
            negative["record_id"] += "-negative"
            negative["case_role"] = "hard_negative"
            for value in negative["field_values"]:
                value["value"] = "Z"
            groups[0]["records"].append(negative)
        groups[0]["group_summary"] = data_builder._summarize(groups[0]["records"])
        bound = data_builder.build_bound_data(structure, operation_closure=operation, proposed_data_groups=groups, created_at=TIME)

        runtime = sanitized_fixture_mod.SanitizedRuntime()
        try:
            verified = data_validator_mod.DataValidator(ROOT).freeze_bound_data(bound, structure, runtime.resolver())
        finally:
            runtime.close()

        restricted = orm_generator_mod.RestrictedOrmGenerator(ROOT).build(structure, operation)
        frozen = orm_validator_mod.OrmValidator(ROOT).freeze_orm(restricted, structure, operation)
        return approved, reviewed, context, binding, structure, operation, bound, verified, restricted, frozen

    def test_dual_path_chain_260_copy_regression_and_210_release_candidate(self):
        approved, reviewed, context, binding, structure, operation, bound, verified, restricted, frozen = self._chain()

        # 241 与 251 必须消费同一操作闭包，槽位一一绑定。
        self.assertEqual(bound["payload"]["operation_closure_ref"], artifact_ref(operation["envelope"]))
        self.assertEqual(restricted["payload"]["operation_closure_ref"], artifact_ref(operation["envelope"]))
        self.assertEqual(verified["payload"]["source_data_package_ref"], artifact_ref(bound["envelope"]))
        self.assertEqual(frozen["envelope"]["producer_id"], "252")
        self.assertEqual(
            {slot["data_placeholder_ref"] for slot in frozen["payload"]["validated_orm_plan"]["execution_contract"]["binding_slots"]},
            {placeholder for item in operation["payload"]["operations"] for placeholder in item["data_placeholders"] if item["operation_type"] == "INSERT"},
        )

        # 260 仅在正式库 copy 上绑定/回归，正式库字节不变。
        with tempfile.TemporaryDirectory() as directory:
            formal = self._formal_db(Path(directory))
            before = formal.read_bytes()
            dispatch = self.coordinator.join_event_validations(approved, reviewed, context, structure, operation, restricted, verified, frozen, binding)
            self.assertEqual(dispatch["target"], "260")
            regression = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, reviewed, context, binding, self.spec, formal)
            self.assertEqual(regression["payload"]["regression_status"], "passed")
            self.assertEqual(binding["payload"]["parameters"], [{"name": "account_value", "source_pointer": "/query_entry/entry_conditions/0/value", "field": "FIXTURE_T001.F001", "operator": "=", "value": self.spec["payload"]["query_entry"]["entry_conditions"][0]["value"], "sqlite_type": "text", "evidence_ref": None}])
            self.assertEqual(regression["payload"]["execution_instances"]["query_binding_names"], ["account_value"])
            self.assertEqual(regression["payload"]["sql_regression_report"]["row_count"], 1)
            self.assertEqual(binding["payload"]["sql_hash"], hashlib.sha256(approved["payload"]["candidate_content"]["sql_gold"].strip().encode("utf-8")).hexdigest())
            self.assertEqual(formal.read_bytes(), before)
            self.assertEqual(stub_210.consume(regression, ROOT)["decision"], "accepted")

        # 210 仅通过后组装 FORMAL-RELEASE-CANDIDATE，010 可消费；不正式提交。
        candidate = self.coordinator.build_event_release(approved, regression, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")
        self.assertEqual(candidate["envelope"]["artifact_type"], "release_candidate")
        self.assertEqual(candidate["payload"]["release_mode"], "event_data")
        self.assertIsNone(candidate["payload"]["foundation_regression_report_ref"])
        self.assertTrue(_test_module("contracts.test_stage10_package_contracts").consume_stub("release_candidate", "010", candidate))
        self.assertEqual(len(committer_mod.FormalReleaseCommitter(ROOT)._validate_event(candidate, approved, regression)), regression["payload"]["sandbox_execution_report"]["write_count"])

        replay = question_scheduler_mod.QuestionSqlStageScheduler(ROOT).build_query_parameter_binding(approved, self.spec, created_at=TIME)
        self.assertEqual(replay, binding)
        changed_spec = self._refresh_spec(lambda payload: payload["query_entry"]["entry_conditions"][0].update({"value": "B"}))
        changed_approved = self._bind_approved(copy.deepcopy(approved), changed_spec)
        changed_binding = question_scheduler_mod.QuestionSqlStageScheduler(ROOT).build_query_parameter_binding(changed_approved, changed_spec, created_at=TIME)
        with self.assertRaises(ContractError):
            regression_mod.DatabaseCopyRegression(ROOT).validate_event_inputs(verified, frozen, self.snapshot, reviewed, context, changed_binding, changed_spec)

    @staticmethod
    def _rehash_binding(package: dict, *, binding_hash: bool = True) -> dict:
        if binding_hash:
            package["payload"]["binding_hash"] = sha256({key: value for key, value in package["payload"].items() if key != "binding_hash"})
        return _rehash(package)

    def _assert_binding_rejected_before_220_and_copy(self, binding: dict, verified: dict, frozen: dict, reviewed: dict, context: dict, *, approved: dict | None = None, spec: dict | None = None) -> None:
        approved = self.approved if approved is None else approved
        spec = self.spec if spec is None else spec
        with self.assertRaises(ContractError):
            self.coordinator.begin_event(approved, spec, binding)
        unopened = Path(tempfile.mkdtemp()) / "must-not-open.sqlite"
        try:
            with self.assertRaises(ContractError):
                regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, reviewed, context, binding, spec, unopened)
            self.assertFalse(unopened.exists())
        finally:
            unopened.parent.rmdir()

    def test_real_110_binding_package_drift_is_rejected_before_210_dispatch_and_260_copy_open(self):
        """Every mutation starts from the real 110 output, not a lexer fixture."""
        _, reviewed, context, binding, _, _, _, verified, _, frozen = self._chain()

        def parameter(change, *, binding_hash=True):
            package = copy.deepcopy(binding)
            change(package["payload"]["parameters"][0])
            return self._rehash_binding(package, binding_hash=binding_hash)

        def envelope(change):
            package = copy.deepcopy(binding)
            change(package["envelope"])
            return self._rehash_binding(package)

        cases = {
            "illegal_name": parameter(lambda item: item.update({"name": "bad-name"})),
            "null_operator": parameter(lambda item: item.update({"value": None, "sqlite_type": "null", "operator": "="})),
            "value": parameter(lambda item: item.update({"value": "B"})),
            "operator": parameter(lambda item: item.update({"operator": "!="})),
            "sqlite_type": parameter(lambda item: item.update({"sqlite_type": "integer"})),
            "sql_hash": self._rehash_binding(copy.deepcopy(binding)),
            "binding_hash": parameter(lambda item: item.update({"value": "B"}), binding_hash=False),
            "unknown_parameter_field": parameter(lambda item: item.update({"unknown": "rejected"})),
            "version": envelope(lambda item: item.update({"version": item["version"] + 1})),
            "parent_refs": envelope(lambda item: item.update({"parent_artifact_refs": list(reversed(item["parent_artifact_refs"]))})),
            "input_hash_order": envelope(lambda item: item.update({"input_hashes": list(reversed(item["input_hashes"]))})),
            "run": envelope(lambda item: item.update({"run_id": "drift-run"})),
            "trace": envelope(lambda item: item.update({"trace_id": "drift-trace"})),
            "qa": envelope(lambda item: item.update({"qa_id": "drift-qa"})),
            "attempt": envelope(lambda item: item.update({"attempt_no": 2})),
        }
        cases["sql_hash"]["payload"]["sql_hash"] = "0" * 64
        self._rehash_binding(cases["sql_hash"])

        for label, drifted in cases.items():
            with self.subTest(label=label):
                self._assert_binding_rejected_before_220_and_copy(drifted, verified, frozen, reviewed, context)

        # A real 110 binding from a unique filter is then rebased onto a
        # malformed 140 source whose F001 exists in two allowed tables.  The
        # 210 public entry point must reach the resolver and route this to 140.
        filter_source = copy.deepcopy(self.spec)
        filter_source["envelope"]["artifact_id"] = "140-filter-query-spec"
        filter_source["payload"]["query_entry"]["entry_conditions"] = []
        filter_source["payload"]["filters_and_evidence"] = [{"field_id": "F001", "operator": "=", "value": "A", "evidence_ref": "fixture-filter"}]
        filter_source = _rehash(filter_source)
        filter_approved, filter_binding = self._approved_via_real_question_sql_chain(filter_source, source_pointer="/filters_and_evidence/0/value")
        _, filter_reviewed, filter_context, _, _, _, _, filter_verified, _, filter_frozen = self._chain(approved=filter_approved, spec=filter_source, binding=filter_binding)
        ambiguous_source = copy.deepcopy(filter_source)
        ambiguous_source["payload"]["sql_schema_scope"]["allowed_tables"][1]["allowed_fields"].append("F001")
        ambiguous_source = _rehash(ambiguous_source)
        ambiguous_approved = copy.deepcopy(filter_approved)
        ambiguous_approved["payload"]["query_specification_package"] = artifact_ref(ambiguous_source["envelope"])
        ambiguous_approved["payload"]["package_hash"] = sha256({key: value for key, value in ambiguous_approved["payload"].items() if key != "package_hash"})
        _rehash(ambiguous_approved)
        ambiguous_binding = copy.deepcopy(filter_binding)
        ambiguous_binding["payload"]["source_question_sql_ref"] = artifact_ref(ambiguous_approved["envelope"])
        ambiguous_binding["payload"]["source_query_spec_ref"] = artifact_ref(ambiguous_source["envelope"])
        ambiguous_binding["envelope"]["parent_artifact_refs"] = [artifact_ref(ambiguous_approved["envelope"]), artifact_ref(ambiguous_source["envelope"])]
        ambiguous_binding["envelope"]["input_hashes"] = [item["content_hash"] for item in ambiguous_binding["envelope"]["parent_artifact_refs"]]
        self._rehash_binding(ambiguous_binding)
        with self.assertRaisesRegex(ContractError, "QUERY_SPEC_ERROR:PARAMETER_FIELD_AMBIGUOUS"):
            self.coordinator.begin_event(ambiguous_approved, ambiguous_source, ambiguous_binding)
        self._assert_binding_rejected_before_220_and_copy(ambiguous_binding, filter_verified, filter_frozen, filter_reviewed, filter_context, approved=ambiguous_approved, spec=ambiguous_source)

    def test_injection_value_reaches_260_only_as_a_bound_sqlite_value(self):
        injection = "A' OR 1=1 --"
        source = self._versioned_spec(injection)
        approved, binding = self._approved_via_real_question_sql_chain(source)
        _, reviewed, context, _, _, _, _, verified, _, frozen = self._chain(approved=approved, spec=source, binding=binding)
        with tempfile.TemporaryDirectory() as directory:
            formal = self._formal_db(Path(directory))
            before = formal.read_bytes()
            regression = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, reviewed, context, binding, source, formal)
            self.assertEqual(regression["payload"]["regression_status"], "passed")
            self.assertEqual(regression["payload"]["sql_regression_report"]["row_count"], 1)
            self.assertEqual(regression["payload"]["execution_instances"]["query_binding_names"], ["account_value"])
            self.assertEqual(binding["payload"]["parameters"][0]["value"], injection)
            self.assertEqual(binding["payload"]["sql_hash"], hashlib.sha256(approved["payload"]["candidate_content"]["sql_gold"].strip().encode("utf-8")).hexdigest())
            self.assertNotIn(injection, approved["payload"]["candidate_content"]["sql_gold"])
            self.assertEqual(formal.read_bytes(), before)

    def test_replay_hashes_are_stable_and_a_new_source_binding_version_invalidates_old_consumers(self):
        first = self._chain()
        second = self._chain()

        def regress_and_release(chain):
            approved, reviewed, context, binding, _, _, _, verified, _, frozen = chain
            formal = self._formal_file()
            regression = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, reviewed, context, binding, self.spec, formal)
            release = self.coordinator.build_event_release(approved, regression, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")
            return regression, release

        first_regression, first_release = regress_and_release(first)
        second_regression, second_release = regress_and_release(second)
        self.assertEqual(first[2]["envelope"]["content_hash"], second[2]["envelope"]["content_hash"])
        self.assertEqual(first_regression["envelope"]["content_hash"], second_regression["envelope"]["content_hash"])
        self.assertEqual(first_regression["payload"]["executable_package_hash"], second_regression["payload"]["executable_package_hash"])
        self.assertEqual(first_release["envelope"]["content_hash"], second_release["envelope"]["content_hash"])
        self.assertEqual(first_release["payload"]["package_hashes"], second_release["payload"]["package_hashes"])

        changed_source = self._versioned_spec("B")
        changed_approved, changed_binding = self._approved_via_real_question_sql_chain(changed_source)
        changed = self._chain(approved=changed_approved, spec=changed_source, binding=changed_binding)
        changed_regression = regression_mod.DatabaseCopyRegression(ROOT).run_event(changed[7], changed[9], self.snapshot, changed[1], changed[2], changed_binding, changed_source, self._formal_file())
        changed_release = self.coordinator.build_event_release(changed_approved, changed_regression, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")
        self.assertEqual(changed_source["envelope"]["artifact_id"], self.spec["envelope"]["artifact_id"])
        self.assertEqual(changed_source["envelope"]["version"], self.spec["envelope"]["version"] + 1)
        self.assertEqual(changed_binding["envelope"]["artifact_id"], self.binding["envelope"]["artifact_id"])
        self.assertEqual(changed_binding["envelope"]["version"], self.binding["envelope"]["version"] + 1)
        self.assertNotEqual(changed_source["envelope"]["content_hash"], self.spec["envelope"]["content_hash"])
        self.assertNotEqual(changed_binding["envelope"]["content_hash"], self.binding["envelope"]["content_hash"])
        with self.assertRaises(ContractError):
            regression_mod.DatabaseCopyRegression(ROOT).validate_event_inputs(first[7], first[9], self.snapshot, first[1], first[2], changed_binding, changed_source)
        with self.assertRaises(ContractError):
            self.coordinator.build_event_release(changed_approved, first_regression, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")
        with self.assertRaises(ContractError):
            committer_mod.FormalReleaseCommitter(ROOT)._validate_event(first_release, changed_approved, changed_regression)
        self.assertNotEqual(changed_release["envelope"]["content_hash"], first_release["envelope"]["content_hash"])

    def _failures(self, verified, frozen, reviewed, context, binding):
        """用真实 260 产包覆盖五类失败路由，返回 {error_code: (feedback, target)}。"""
        spec = self._refresh_spec(lambda payload: payload.update({"minimum_negative_count": 99}))
        approved_dve = self._bind_approved(copy.deepcopy(self.approved), spec)
        worker = regression_mod.DatabaseCopyRegression(ROOT)

        dve_binding = question_scheduler_mod.QuestionSqlStageScheduler(ROOT).build_query_parameter_binding(approved_dve, spec, created_at=TIME)
        dve_started = self.coordinator.begin_event(approved_dve, spec, dve_binding)
        dve = worker.run_event(verified, frozen, self.snapshot, dve_started["reviewed_question_sql"], dve_started["event_query_context"], dve_binding, spec, self._formal_file())

        sql = worker.run_event(verified, frozen, self.snapshot, reviewed, context, binding, self.spec, self._formal_sql_error_file())

        orm_broken = copy.deepcopy(frozen)
        plan = orm_broken["payload"]["validated_orm_plan"]
        plan["orm_source_code"] = "def apply(context, params):\n    raise RuntimeError('boom')\n"
        code_hash = hashlib.sha256(canonical_bytes({"orm_source_code": plan["orm_source_code"], "execution_contract": plan["execution_contract"], "operations": plan["operations"]})).hexdigest()
        plan["code_hash"] = code_hash
        orm_broken["payload"]["validated_hash"] = code_hash
        orm_broken["envelope"]["content_hash"] = content_hash(orm_broken["envelope"], orm_broken["payload"])
        orm = worker.run_event(verified, orm_broken, self.snapshot, reviewed, context, binding, self.spec, self._formal_file())

        data_foundation = copy.deepcopy(verified)
        data_foundation["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["existing_record_refs"] = [{"table_id": "FIXTURE_CUSTOMER", "record_key": "not-in-snapshot"}]
        data_foundation["payload"]["validated_hash"] = sha256(data_foundation["payload"]["validated_data_package"])
        data_foundation["envelope"]["content_hash"] = content_hash(data_foundation["envelope"], data_foundation["payload"])
        foundation = worker.run_event(data_foundation, frozen, self.snapshot, reviewed, context, binding, self.spec, self._formal_file())

        manual = worker.run_event(verified, frozen, self.snapshot, dve_started["reviewed_question_sql"], dve_started["event_query_context"], dve_binding, spec, self._formal_file(), attempt_no=3)

        return {
            "DATA_VALUE_ERROR": (dve, "241"),
            "SQL_EXECUTION_ERROR": (sql, "010"),
            "ORM_PLAN_ERROR": (orm, "251"),
            "FOUNDATION_REQUIRED": (foundation, "210"),
            "MANUAL_REVIEW_REQUIRED": (manual, "manual"),
        }

    def test_release_candidate_requires_passed_regression(self):
        _, reviewed, context, binding, _, _, _, verified, _, frozen = self._chain()
        feedback, _ = self._failures(verified, frozen, reviewed, context, binding)["DATA_VALUE_ERROR"]
        with self.assertRaisesRegex(ContractError, "210_EVENT_REGRESSION_REJECTED"):
            self.coordinator.build_event_release(self.approved, feedback, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")

    def test_five_real_failure_routes_consumed_by_210_and_routed(self):
        _, reviewed, context, binding, _, _, _, verified, _, frozen = self._chain()
        for code, (feedback, target) in self._failures(verified, frozen, reviewed, context, binding).items():
            with self.subTest(code=code):
                self.assertEqual(feedback["payload"]["failure_details"]["error_code"], code)
                self.assertEqual(feedback["payload"]["route_target"], target)
                self.assertEqual(stub_210.consume(feedback, ROOT), {"decision": "accepted", "kind": "feedback"})
                route = self.coordinator.route_feedback(feedback)
                self.assertEqual(route["target"], target)
                self.assertEqual(route["reason"], code)
                self.assertEqual(route["requires_explicit_foundation_task"], code == "FOUNDATION_REQUIRED")
                self.assertEqual(route["kind"], "manual" if target == "manual" else "retry_or_rollback")

    def test_retry_escalation_manual_block_and_idempotent_replay(self):
        _, reviewed, context, binding, _, _, _, verified, _, frozen = self._chain()
        spec = self._refresh_spec(lambda payload: payload.update({"minimum_negative_count": 99}))
        approved_dve = self._bind_approved(copy.deepcopy(self.approved), spec)
        worker = regression_mod.DatabaseCopyRegression(ROOT)
        retry_binding = question_scheduler_mod.QuestionSqlStageScheduler(ROOT).build_query_parameter_binding(approved_dve, spec, created_at=TIME)
        started = self.coordinator.begin_event(approved_dve, spec, retry_binding)
        reviewed, context = started["reviewed_question_sql"], started["event_query_context"]
        attempt1 = worker.run_event(verified, frozen, self.snapshot, reviewed, context, retry_binding, spec, self._formal_file(), attempt_no=1)
        attempt2 = worker.run_event(verified, frozen, self.snapshot, reviewed, context, retry_binding, spec, self._formal_file(), attempt_no=2)
        attempt3 = worker.run_event(verified, frozen, self.snapshot, reviewed, context, retry_binding, spec, self._formal_file(), attempt_no=3)
        self.assertEqual((attempt1["payload"]["failure_details"]["error_code"], attempt1["payload"]["route_target"]), ("DATA_VALUE_ERROR", "241"))
        self.assertEqual((attempt2["payload"]["failure_details"]["error_code"], attempt2["payload"]["route_target"]), ("DATA_VALUE_ERROR", "241"))
        self.assertEqual((attempt3["payload"]["failure_details"]["error_code"], attempt3["payload"]["route_target"]), ("MANUAL_REVIEW_REQUIRED", "manual"))
        self.assertEqual(self.coordinator.route_feedback(attempt1), self.coordinator.route_feedback(attempt1))
        self.assertEqual(stub_210.consume(attempt1, ROOT), stub_210.consume(attempt1, ROOT))
        self.assertEqual(self.coordinator.route_feedback(attempt3)["kind"], "manual")

    def test_route_conflict_version_and_hash_drift_rejected(self):
        _, reviewed, context, binding, _, _, _, verified, _, frozen = self._chain()
        feedback, _ = self._failures(verified, frozen, reviewed, context, binding)["DATA_VALUE_ERROR"]
        conflict = copy.deepcopy(feedback)
        conflict["payload"]["route_target"] = "251"
        conflict["envelope"]["content_hash"] = content_hash(conflict["envelope"], conflict["payload"])
        with self.assertRaisesRegex(ContractError, "210_260_ROUTE_CONFLICT"):
            self.coordinator.route_feedback(conflict)
        retry_drift = copy.deepcopy(feedback)
        retry_drift["payload"]["retry_count"] = 2
        retry_drift["envelope"]["content_hash"] = content_hash(retry_drift["envelope"], retry_drift["payload"])
        with self.assertRaisesRegex(ContractError, "210_260_ROUTE_CONFLICT"):
            self.coordinator.route_feedback(retry_drift)
        hash_drift = copy.deepcopy(feedback)
        hash_drift["payload"]["actual_values"] = ["tampered"]
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(hash_drift, ROOT)
        bad_target = copy.deepcopy(feedback)
        bad_target["payload"]["route_target"] = "110"
        bad_target["envelope"]["content_hash"] = content_hash(bad_target["envelope"], bad_target["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_ROUTE_REJECTED"):
            stub_210.consume(bad_target, ROOT)
        # payload.schema_version 漂移（重算 content_hash，证明拒绝源于 Schema const 而非旧哈希）。
        schema_version_drift = copy.deepcopy(feedback)
        schema_version_drift["payload"]["schema_version"] = "v5.sql-regression-failed-feedback/v2"
        schema_version_drift["envelope"]["content_hash"] = content_hash(schema_version_drift["envelope"], schema_version_drift["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(schema_version_drift, ROOT)
        with self.assertRaisesRegex(ContractError, "210_260_FEEDBACK_REJECTED"):
            self.coordinator.route_feedback(schema_version_drift)
        # 未知 payload 字段（重算 content_hash，证明严格 Schema additionalProperties:false 拒绝）。
        unknown_field_drift = copy.deepcopy(feedback)
        unknown_field_drift["payload"]["unexpected_extra_field"] = "x"
        unknown_field_drift["envelope"]["content_hash"] = content_hash(unknown_field_drift["envelope"], unknown_field_drift["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(unknown_field_drift, ROOT)
        with self.assertRaisesRegex(ContractError, "210_260_FEEDBACK_REJECTED"):
            self.coordinator.route_feedback(unknown_field_drift)

    def test_210_stub_rejects_schema_title_impersonation_and_hash_drift(self):
        _, reviewed, context, binding, _, _, _, verified, _, frozen = self._chain()
        # 以 Schema title 冒充 artifact_type 必须被 210 拒绝。
        fake = {"envelope": {"artifact_type": "REGRESSION-PASSED-DATA-ORM", "mode": "event_data"}, "payload": {}}
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(fake, ROOT)
        # 错误 mode/schema 配对必须被拒绝。
        formal = self._formal_file()
        regression = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, reviewed, context, binding, self.spec, formal)
        wrong_mode = copy.deepcopy(regression)
        wrong_mode["envelope"]["mode"] = "foundation"
        wrong_mode["envelope"]["content_hash"] = content_hash(wrong_mode["envelope"], wrong_mode["payload"])
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(wrong_mode, ROOT)
        # 内容哈希漂移（未重算 content_hash）必须被拒绝。
        hash_drift = copy.deepcopy(regression)
        hash_drift["payload"]["executable_package_hash"] = "f" * 64
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(hash_drift, ROOT)

    def _formal_file(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self._tmp_dirs.append(directory)
        return self._formal_db(Path(directory.name))

    def tearDown(self):
        for directory in getattr(self, "_tmp_dirs", []):
            directory.cleanup()
        self._tmp_dirs = []


if __name__ == "__main__":
    unittest.main()
