"""EAS-39 data 业务事件双通路端到端联调（210→220→230→{241→242,251→252}→260→210）。

本测试使用冻结的批准 QS Fixture（110 双审核通过包）与查询规格（140 输出），
真实驱动 210 调度器、220 结构闭包、230 操作闭包、241/242 数据通路、
251/252 ORM 通路、260 数据库副本回归，并证明：

- 241 与 251 消费同一操作闭包且槽位可一一绑定；
- 260 仅在正式库 copy 上绑定/回归，正式库字节不变；
- 210 仅在回归通过后组装 FORMAL-RELEASE-CANDIDATE，不正式提交；
- DATA_VALUE_ERROR / ORM_PLAN_ERROR / SQL_EXECUTION_ERROR / FOUNDATION_REQUIRED /
  MANUAL_REVIEW_REQUIRED 五类路由均有真实证据。
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

coordinator_mod = importlib.import_module("east_v5.agents.210.scheduler")
closure_mod = importlib.import_module("east_v5.agents.220.closure")
operation_mod = importlib.import_module("east_v5.agents.230.builder")
data_generator_mod = importlib.import_module("east_v5.agents.241.generator")
data_validator_mod = importlib.import_module("east_v5.agents.242.validator")
sanitized_fixture_mod = importlib.import_module("east_v5.agents.242.sanitized_fixture")
orm_generator_mod = importlib.import_module("east_v5.agents.251.generator")
orm_validator_mod = importlib.import_module("east_v5.agents.252.validator")
regression_mod = importlib.import_module("east_v5.agents.260.regression")

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


def _event_results(reviewed: dict) -> tuple[dict, dict]:
    requests = closure_mod.event_query_rounds(reviewed)
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
        self.coordinator = coordinator_mod.DataStageCoordinator(ROOT)
        self.spec = _rehash(_load("query-specification.json"))
        self.approved = self._bind_approved(_load("approved-question-sql.json"), self.spec)
        self.snapshot = self._bind_snapshot(_load("database-read-snapshot.json"))

    def _bind_snapshot(self, snapshot: dict) -> dict:
        snapshot["payload"]["snapshot_hash"] = sha256({key: value for key, value in snapshot["payload"].items() if key != "snapshot_hash"})
        snapshot["envelope"]["content_hash"] = content_hash(snapshot["envelope"], snapshot["payload"])
        return snapshot

    def _bind_approved(self, approved: dict, spec: dict) -> dict:
        approved["payload"]["query_specification_package"] = artifact_ref(spec["envelope"])
        refs = approved["envelope"]["parent_artifact_refs"]
        if artifact_ref(spec["envelope"]) not in refs:
            refs.append(artifact_ref(spec["envelope"]))
        approved["envelope"]["input_hashes"] = [item["content_hash"] for item in refs]
        approved["payload"]["package_hash"] = sha256({key: value for key, value in approved["payload"].items() if key != "package_hash"})
        approved["envelope"]["content_hash"] = content_hash(approved["envelope"], approved["payload"])
        return approved

    def _refresh_spec(self, mutate) -> dict:
        spec = copy.deepcopy(self.spec)
        mutate(spec["payload"])
        spec["envelope"]["content_hash"] = content_hash(spec["envelope"], spec["payload"])
        return spec

    def _formal_db(self, directory: Path) -> Path:
        db = directory / "formal.sqlite"
        connection = sqlite3.connect(db)
        connection.executescript("CREATE TABLE FIXTURE_T001 (F001 TEXT, F002 TEXT); CREATE TABLE FIXTURE_T002 (PK001 TEXT);")
        connection.commit()
        connection.close()
        return db

    def _chain(self):
        """Build one same-source 210→220→230→{241→242,251→252} pair."""
        approved = self.approved
        started = self.coordinator.begin_event(approved)
        reviewed = started["reviewed_question_sql"]
        first_asset, second_asset = _event_results(reviewed)
        structure = closure_mod.build_event_closure(reviewed, first_asset, second_asset)
        operation = operation_mod.OperationClosureBuilder(ROOT).build(structure)

        data_builder = data_generator_mod.BoundDataGenerator(ROOT)
        draft = data_builder.build_bound_data(structure, operation_closure=operation, created_at=TIME)
        groups = copy.deepcopy(draft["payload"]["data_groups"])
        for record in groups[0]["records"]:
            record["case_role"] = "positive"
            for value in record["field_values"]:
                value["value"] = "A" if value["field_id"] in {"F001", "PK001"} else "B"
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
        return approved, reviewed, structure, operation, bound, verified, restricted, frozen

    def test_dual_path_chain_260_copy_regression_and_210_release_candidate(self):
        approved, reviewed, structure, operation, bound, verified, restricted, frozen = self._chain()

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
            dispatch = self.coordinator.join_event_validations(approved, reviewed, structure, operation, restricted, verified, frozen)
            self.assertEqual(dispatch["target"], "260")
            regression = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, approved, self.spec, formal)
            self.assertEqual(regression["payload"]["regression_status"], "passed")
            self.assertEqual(formal.read_bytes(), before)
            self.assertEqual(stub_210.consume(regression, ROOT)["decision"], "accepted")

        # 210 仅通过后组装 FORMAL-RELEASE-CANDIDATE，010 可消费；不正式提交。
        candidate = self.coordinator.build_event_release(approved, regression, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")
        self.assertEqual(candidate["envelope"]["artifact_type"], "release_candidate")
        self.assertEqual(candidate["payload"]["release_mode"], "event_data")
        self.assertIsNone(candidate["payload"]["foundation_regression_report_ref"])
        self.assertTrue(_test_module("contracts.test_stage10_package_contracts").consume_stub("release_candidate", "010", candidate))

    def test_release_candidate_requires_passed_regression(self):
        # 210 仅在回归通过后组装；失败反馈不得组装为发布候选。
        feedback = self._feedback("DATA_VALUE_ERROR", "241", attempt=1)
        with self.assertRaisesRegex(ContractError, "210_EVENT_REGRESSION_REJECTED"):
            self.coordinator.build_event_release(self.approved, feedback, target_database_version="fixture-db-v1", target_question_dataset_version="fixture-question-v1")

    def test_five_frozen_error_routes_and_conflicts_are_rejected(self):
        for code, target in (("DATA_VALUE_ERROR", "241"), ("ORM_PLAN_ERROR", "251"), ("SQL_EXECUTION_ERROR", "010"), ("FOUNDATION_REQUIRED", "210"), ("MANUAL_REVIEW_REQUIRED", "manual")):
            with self.subTest(code=code):
                result = self.coordinator.route_feedback(self._feedback(code, target, attempt=3 if code == "MANUAL_REVIEW_REQUIRED" else 1))
                self.assertEqual(result["target"], target)
                self.assertEqual(result["requires_explicit_foundation_task"], code == "FOUNDATION_REQUIRED")
        with self.assertRaisesRegex(ContractError, "210_260_ROUTE_CONFLICT"):
            self.coordinator.route_feedback(self._feedback("DATA_VALUE_ERROR", "251"))
        with self.assertRaisesRegex(ContractError, "210_THIRD_ATTEMPT_NOT_MANUAL"):
            self.coordinator.route_feedback(self._feedback("DATA_VALUE_ERROR", "241", attempt=3))
        with self.assertRaisesRegex(ContractError, "210_MANUAL_REVIEW_ATTEMPT_INVALID"):
            self.coordinator.route_feedback(self._feedback("MANUAL_REVIEW_REQUIRED", "manual", attempt=1))

    def test_realistic_260_failure_productions_route_correctly(self):
        approved, reviewed, structure, operation, bound, verified, restricted, frozen = self._chain()

        # DATA_VALUE_ERROR：反例数量不足 → 241。
        spec = self._refresh_spec(lambda payload: payload.update({"minimum_negative_count": 99}))
        approved_dve = self._bind_approved(copy.deepcopy(self.approved), spec)
        feedback = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, approved_dve, spec, self._formal_file())
        self.assertEqual((feedback["payload"]["failure_details"]["error_code"], feedback["payload"]["route_target"]), ("DATA_VALUE_ERROR", "241"))

        # SQL_EXECUTION_ERROR：Gold SQL 无法执行 → 210。
        approved_sql = copy.deepcopy(self.approved)
        approved_sql["payload"]["candidate_content"]["sql_gold"] = "SELECT missing FROM FIXTURE_T001"
        approved_sql["payload"]["package_hash"] = sha256({key: value for key, value in approved_sql["payload"].items() if key != "package_hash"})
        approved_sql["envelope"]["content_hash"] = content_hash(approved_sql["envelope"], approved_sql["payload"])
        feedback = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, approved_sql, self.spec, self._formal_file())
        self.assertEqual((feedback["payload"]["failure_details"]["error_code"], feedback["payload"]["route_target"]), ("SQL_EXECUTION_ERROR", "210"))

        # ORM_PLAN_ERROR：受限 ORM 执行异常 → 251。
        orm_broken = copy.deepcopy(frozen)
        plan = orm_broken["payload"]["validated_orm_plan"]
        plan["orm_source_code"] = "def apply(context, params):\n    raise RuntimeError('boom')\n"
        code_hash = hashlib.sha256(canonical_bytes({"orm_source_code": plan["orm_source_code"], "execution_contract": plan["execution_contract"], "operations": plan["operations"]})).hexdigest()
        plan["code_hash"] = code_hash
        orm_broken["payload"]["validated_hash"] = code_hash
        orm_broken["envelope"]["content_hash"] = content_hash(orm_broken["envelope"], orm_broken["payload"])
        feedback = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, orm_broken, self.snapshot, approved, self.spec, self._formal_file())
        self.assertEqual((feedback["payload"]["failure_details"]["error_code"], feedback["payload"]["route_target"]), ("ORM_PLAN_ERROR", "251"))

        # FOUNDATION_REQUIRED：existing_record_ref 不在快照 → 210。
        data_foundation = copy.deepcopy(verified)
        data_foundation["payload"]["validated_data_package"]["data_groups"][0]["records"][0]["existing_record_refs"] = [{"table_id": "FIXTURE_CUSTOMER", "record_key": "not-in-snapshot"}]
        data_foundation["payload"]["validated_hash"] = sha256(data_foundation["payload"]["validated_data_package"])
        data_foundation["envelope"]["content_hash"] = content_hash(data_foundation["envelope"], data_foundation["payload"])
        feedback = regression_mod.DatabaseCopyRegression(ROOT).run_event(data_foundation, frozen, self.snapshot, approved, self.spec, self._formal_file())
        self.assertEqual((feedback["payload"]["failure_details"]["error_code"], feedback["payload"]["route_target"]), ("FOUNDATION_REQUIRED", "210"))

        # MANUAL_REVIEW_REQUIRED：第三次尝试终止 → manual。
        feedback = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, approved_dve, spec, self._formal_file(), attempt_no=3)
        self.assertEqual((feedback["payload"]["failure_details"]["error_code"], feedback["payload"]["route_target"]), ("MANUAL_REVIEW_REQUIRED", "manual"))

    def test_210_stub_rejects_schema_title_impersonation_and_hash_drift(self):
        _, _, _, _, _, verified, _, frozen = self._chain()
        # 以 Schema title 冒充 artifact_type 必须被 210 拒绝。
        fake = {"envelope": {"artifact_type": "REGRESSION-PASSED-DATA-ORM", "mode": "event_data"}, "payload": {}}
        with self.assertRaisesRegex(ContractError, "210_STUB_SCHEMA_REJECTED"):
            stub_210.consume(fake, ROOT)
        # 错误 mode/schema 配对必须被拒绝。
        formal = self._formal_file()
        regression = regression_mod.DatabaseCopyRegression(ROOT).run_event(verified, frozen, self.snapshot, self.approved, self.spec, formal)
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

    def _feedback(self, code: str, target: str, *, attempt: int = 1) -> dict:
        payload = {
            "schema_version": "v5.sql-regression-failed-feedback/v1", "mode": "event_data",
            "input_data_refs": [{"artifact_id": "data", "version": 1, "content_hash": "a" * 64}],
            "input_orm_ref": {"artifact_id": "orm", "version": 1, "content_hash": "b" * 64},
            "sandbox_snapshot_id": "sanitized-copy",
            "failure_details": {
                "error_code": code, "error_stage": "regression_gate", "error_location": "sanitized",
                "expected_values": [], "actual_values": [], "sql_error_detail": None, "regression_metrics": {},
            },
            "route_target": target, "retry_count": attempt,
        }
        envelope = {
            "artifact_id": f"eas39-feedback-{code}", "artifact_type": "sql_regression_failed_feedback",
            "run_id": "eas39-run", "qa_id": "QA-EAS39", "version": 1, "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt, "producer_id": "260",
            "parent_artifact_refs": [], "input_hashes": [], "status": "rejected", "mode": "event_data",
            "created_at": TIME, "trace_id": "eas39-trace", "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}

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
