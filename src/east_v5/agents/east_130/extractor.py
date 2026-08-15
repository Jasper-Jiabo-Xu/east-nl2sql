"""Deterministic contract boundary and retry orchestration for Agent 130.

Transported packages are always ``{"envelope": ..., "payload": ...}``.
The envelope is validated first and the payload schema second; 000 continues to
receive only the validated request payload, which keeps its frozen interface
unchanged while preserving the immutable package identity at the boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError

from east_v5.agents.east_000.extractor import validate_request_schema, validate_result_schema
from east_v5.agents.east_120.extractor import validate_fact_package
from east_v5.artifacts import artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, load_json


TRANSPORT_KEYS = {"envelope", "payload"}
ASSET_TYPES = ["data_element", "single_field", "within_table", "cross_table"]
REVIEW_ARTIFACTS = {"deepseek_review_result", "glm_review_result"}
MANIFEST_SCHEMA = "contracts/packages/east-observable-fact-manifest.schema.json"


def _fail(code: str) -> None:
    raise ContractError(code)


def _schema_validate(repo_root: Path, relative: str, payload: dict[str, Any], label: str) -> None:
    try:
        Draft202012Validator(load_json(repo_root / relative)).validate(payload)
    except ValidationError as exc:
        raise ContractError(f"SCHEMA_VALIDATION_FAILED:{label}") from exc


def _same_ref(actual: Any, expected: dict[str, Any], code: str) -> None:
    if actual != expected:
        _fail(code)


def _same_lineage(envelope: dict[str, Any], reference: dict[str, Any], code: str) -> None:
    for key in ("run_id", "qa_id", "trace_id"):
        if envelope[key] != reference[key]:
            _fail(code)


class ObservableFactMapper:
    """Implements the three Excel tasks for Agent 130 without inventing assets."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def _validate_transport(self, package: dict[str, Any], expected_type: str, schema_path: str | None = None) -> None:
        if not isinstance(package, dict) or set(package) != TRANSPORT_KEYS:
            _fail("TRANSPORT_PACKAGE_INVALID")
        envelope, payload = package["envelope"], package["payload"]
        # Phase 1: COMMON-ENVELOPE schema + immutable identity/hash/lineage.
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["artifact_type"] != expected_type:
            _fail("ARTIFACT_TYPE_MISMATCH")
        if not isinstance(payload, dict):
            _fail("PAYLOAD_NOT_OBJECT")
        # Phase 2: package-specific executable JSON Schema.
        if expected_type == "penalty_fact_package":
            validate_fact_package(self.repo_root, payload)
        elif expected_type == "constraint_query_request":
            validate_request_schema(self.repo_root, payload)
        elif expected_type == "constraint_asset_package":
            validate_result_schema(self.repo_root, payload)
        elif schema_path is not None:
            _schema_validate(self.repo_root, schema_path, payload, expected_type.upper())
        else:
            _fail("PACKAGE_SCHEMA_UNREGISTERED")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _wrap(
        self, artifact_type: str, artifact_id: str, payload: dict[str, Any], *, run_id: str,
        qa_id: str, version: int = 1, attempt_no: int = 1,
        parents: list[dict[str, Any]] | None = None, supersedes_ref: dict[str, Any] | None = None,
        status: str = "candidate", trace_id: str = "130-trace", created_at: str | None = None,
    ) -> dict[str, Any]:
        parent_refs = list(parents or [])
        envelope: dict[str, Any] = {
            "artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": run_id,
            "qa_id": qa_id, "version": version, "schema_version": "COMMON-ENVELOPE/v1",
            "content_hash": "0" * 64, "supersedes_ref": supersedes_ref,
            "attempt_no": attempt_no, "producer_id": "130", "parent_artifact_refs": parent_refs,
            "input_hashes": [item["content_hash"] for item in parent_refs], "status": status,
            "mode": "question_sql", "created_at": created_at or self._now(),
            "trace_id": trace_id, "storage_locator": None,
        }
        envelope["content_hash"] = content_hash(envelope, payload)
        package = {"envelope": envelope, "payload": payload}
        self._validate_transport(
            package, artifact_type,
            {
                "east_observable_fact_package": "contracts/packages/east-observable-fact-package.schema.json",
                "deepseek_review_result": "contracts/packages/deepseek-review-result.schema.json",
                "glm_review_result": "contracts/packages/glm-review-result.schema.json",
            }.get(artifact_type),
        )
        return package

    def validate_penalty(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "penalty_fact_package")

    def validate_request(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "constraint_query_request")

    def validate_assets(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "constraint_asset_package")

    def validate_observable(self, package: dict[str, Any]) -> None:
        self._validate_transport(package, "east_observable_fact_package", "contracts/packages/east-observable-fact-package.schema.json")

    def validate_review(self, package: dict[str, Any]) -> None:
        artifact_type = package.get("envelope", {}).get("artifact_type") if isinstance(package, dict) else None
        if artifact_type not in REVIEW_ARTIFACTS:
            _fail("REVIEW_ARTIFACT_TYPE_INVALID")
        schema_paths = {
            "deepseek_review_result": "contracts/packages/deepseek-review-result.schema.json",
            "glm_review_result": "contracts/packages/glm-review-result.schema.json",
        }
        self._validate_transport(package, artifact_type, schema_paths[artifact_type])

    @staticmethod
    def _record_matches_fact(record: dict[str, Any], fact_id: str) -> bool:
        """Only an explicit fact-id plus source/constraint evidence is mappable."""
        data = record.get("data")
        if not isinstance(data, dict) or not record.get("source_refs"):
            return False
        fact_ids = data.get("penalty_fact_ids")
        if fact_ids is None:
            fact_ids = [data.get("penalty_fact_id")]
        return (
            isinstance(fact_ids, list)
            and fact_id in fact_ids
            and isinstance(data.get("constraint_evidence_ref"), str)
            and bool(data["constraint_evidence_ref"])
        )

    def _validate_manifest(self, manifest: dict[str, Any], observable: dict[str, Any]) -> None:
        _schema_validate(self.repo_root, MANIFEST_SCHEMA, manifest, "EAST_OBSERVABLE_FACT_MANIFEST")
        envelope = observable["envelope"]
        _same_ref(manifest["artifact_ref"], artifact_ref(envelope), "MANIFEST_ARTIFACT_REF_MISMATCH")
        if manifest["input_artifact_refs"] != envelope["parent_artifact_refs"]:
            _fail("MANIFEST_INPUT_REFS_MISMATCH")
        for key in ("run_id", "qa_id", "trace_id", "attempt_no", "status"):
            if manifest[key] != envelope[key]:
                _fail("MANIFEST_LINEAGE_MISMATCH")
        locator = PurePosixPath(manifest["runtime_locator"])
        if locator.is_absolute() or ".." in locator.parts:
            _fail("MANIFEST_RUNTIME_BOUNDARY_VIOLATION")

    def build_manifest(self, observable: dict[str, Any]) -> dict[str, Any]:
        """Create a control-plane manifest instance without writing runtime data."""
        self.validate_observable(observable)
        envelope = observable["envelope"]
        manifest = {
            "manifest_schema_version": "east-observable-fact-manifest/v1",
            "artifact_ref": artifact_ref(envelope), "input_artifact_refs": envelope["parent_artifact_refs"],
            "run_id": envelope["run_id"], "qa_id": envelope["qa_id"], "trace_id": envelope["trace_id"],
            "attempt_no": envelope["attempt_no"], "status": envelope["status"],
            "runtime_locator": f"vnext/03_构建过程层/issues/EAS-22/{envelope['run_id']}/{envelope['attempt_no']}/manifest.json",
        }
        self._validate_manifest(manifest, observable)
        return manifest

    def _validate_remap_output(self, output: dict[str, Any], previous: dict[str, Any], assets: dict[str, Any], attempt_no: int) -> None:
        self.validate_observable(output)
        envelope = output["envelope"]
        prior = previous["envelope"]
        _same_lineage(envelope, prior, "REMAP_OUTPUT_LINEAGE_MISMATCH")
        if envelope["version"] != prior["version"] + 1:
            _fail("REMAP_OUTPUT_VERSION_MISMATCH")
        _same_ref(envelope["supersedes_ref"], artifact_ref(prior), "REMAP_OUTPUT_SUPERSEDES_MISMATCH")
        if envelope["attempt_no"] != attempt_no:
            _fail("REMAP_OUTPUT_ATTEMPT_MISMATCH")
        if artifact_ref(assets["envelope"]) not in envelope["parent_artifact_refs"]:
            _fail("REMAP_OUTPUT_ASSET_PARENT_MISSING")

    def plan_constraint_query(
        self, penalty: dict[str, Any], *, run_id: str, qa_id: str, previous_request_refs: list[dict[str, Any]] | None = None,
        expansion: bool = False, attempt_no: int = 1, created_at: str | None = None,
    ) -> dict[str, Any]:
        """Task 1: form a schema-valid, bounded request for Agent 000."""
        self.validate_penalty(penalty)
        facts = penalty["payload"]["source_facts"]
        if not facts:
            _fail("PENALTY_FACTS_EMPTY")
        request_id = f"130-query-{run_id}-{attempt_no}"
        refs = list(previous_request_refs or [])
        payload = {
            "request_id": request_id, "caller_agent_id": "130", "caller_stage": "observable_fact",
            "query_purpose": "closure_expansion" if expansion else "constraint_lookup",
            "natural_language_intent": "扩大EAST约束资产范围以复核处罚事实可观察边界" if expansion else "检索处罚事实对应的EAST表、字段、表内和跨表约束",
            "target_asset_types": ASSET_TYPES,
            "table_scope": [], "field_scope": [], "relationship_scope": [],
            "required_output_fields": ["table_id", "field_id", "source_refs", "constraint_summary"],
            "previous_request_refs": refs, "max_rows": 100,
        }
        return self._wrap("constraint_query_request", request_id, payload, run_id=run_id, qa_id=qa_id,
                          attempt_no=attempt_no, parents=[artifact_ref(penalty["envelope"])],
                          trace_id=penalty["envelope"]["trace_id"], created_at=created_at)

    @staticmethod
    def _asset_location(record: dict[str, Any]) -> tuple[str, str, str]:
        data = record.get("data", {})
        table_id = str(data.get("table_id") or "EAST_ASSET_UNKNOWN")
        field_id = str(data.get("field_id") or "ASSET_FIELD_UNKNOWN")
        source = (record.get("source_refs") or [{"source_id": "CA-V0.3.0"}])[0].get("source_id", "CA-V0.3.0")
        return table_id, field_id, str(source)

    def build_observable_facts(
        self, penalty: dict[str, Any], assets: dict[str, Any], *, run_id: str, qa_id: str,
        version: int = 1, attempt_no: int = 1, supersedes_ref: dict[str, Any] | None = None,
        status: str = "candidate", created_at: str | None = None,
    ) -> dict[str, Any]:
        """Task 2: map only returned assets; emit explicit, consumable unknowns."""
        self.validate_penalty(penalty)
        self.validate_assets(assets)
        records = assets["payload"]["matched_records"]
        observable: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for fact in penalty["payload"]["source_facts"]:
            fid = fact["penalty_fact_id"]
            record = next((item for item in records if self._record_matches_fact(item, fid)), None)
            if record is not None:
                table_id, field_id, source = self._asset_location(record)
                evidence = record["data"]["constraint_evidence_ref"]
                direct = record.get("record_type") in {"data_element", "single_field"}
                observation = "direct" if direct else "indirect"
                proxy = f"以 {table_id}.{field_id} 筛查与处罚事实 {fid} 相关记录"
                matrix = {"penalty_fact_id": fid, "proxy_expression": proxy, "table_field_path": f"{table_id}.{field_id}", "asset_evidence_ref": f"{source}:{evidence}"}
                unobservable_parts: list[str] = []
                related = [{"table_id": table_id, "field_id": field_id, "purpose": "可观察代理字段"}]
                refs = [f"{source}:{evidence}"]
            else:
                # Sentinels are deliberately non-empty: they say no EAST asset was
                # found, rather than masquerading as an actual table/field mapping.
                observation, table_id, field_id = "unobservable", "NO_EAST_ASSET", "NO_EAST_FIELD"
                proxy = f"处罚事实 {fid} 当前无冻结EAST资产可表达，转人工审核"
                matrix = {"penalty_fact_id": fid, "proxy_expression": proxy, "table_field_path": "NO_EAST_ASSET.NO_EAST_FIELD", "asset_evidence_ref": "000:unmatched"}
                unobservable_parts, related, refs = [fact["original_text"]], [], ["000:unmatched"]
                unresolved.append({"item": fid, "reason": "000未命中可观察资产", "needs_human_review": True})
            observable.append({
                "observable_fact_id": f"observable-{fid}", "penalty_fact_refs": [fid], "topic": "监管处罚风险筛查",
                "main_object": fact["structured_fact"]["subject"], "query_grain": "一条EAST业务记录或聚合事件",
                "entry_table": table_id, "related_tables_fields": related, "within_table_relations": [], "cross_table_relations": [],
                "time_amount_conditions": ["仅使用冻结资产中可表达的时间或金额条件"], "observable_proxy": proxy,
                "observability_type": observation, "unobservable_parts": unobservable_parts,
                "risk_screening_boundary": "仅用于风险筛查，不直接认定监管违法或替代人工结论。",
                "mapping_matrix": [matrix], "constraint_asset_refs": refs,
            })
        coverage = "complete" if records and not unresolved else "partial"
        if status == "blocked_manual":
            coverage = "blocked"
            if not unresolved:
                unresolved.append({"item": "review-remap", "reason": "第3次审核回退仍无法稳定完成映射", "needs_human_review": True})
        payload = {"observable_facts": observable, "coverage_status": coverage,
                   "asset_version": assets["payload"]["asset_version"], "unresolved_items": unresolved}
        parents = [artifact_ref(penalty["envelope"]), artifact_ref(assets["envelope"])]
        return self._wrap("east_observable_fact_package", f"130-observable-{run_id}", payload,
                          run_id=run_id, qa_id=qa_id, version=version, attempt_no=attempt_no,
                          parents=parents, supersedes_ref=supersedes_ref, status=status,
                          trace_id=penalty["envelope"]["trace_id"], created_at=created_at)

    def handle_review_feedback(
        self, penalty: dict[str, Any], review: dict[str, Any], previous_observable: dict[str, Any],
        previous_request: dict[str, Any], query_000: Callable[[dict[str, Any]], dict[str, Any]], *,
        run_id: str, qa_id: str, attempt_no: int, created_at: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Task 3: consume 170/180, expand 000 lookup, then supersede or block."""
        self.validate_penalty(penalty); self.validate_review(review)
        self.validate_observable(previous_observable); self.validate_request(previous_request)
        previous_envelope = previous_observable["envelope"]
        previous_request_envelope = previous_request["envelope"]
        penalty_envelope = penalty["envelope"]
        _same_lineage(previous_request_envelope, previous_envelope, "PREVIOUS_REQUEST_LINEAGE_MISMATCH")
        _same_lineage(penalty_envelope, previous_envelope, "PENALTY_LINEAGE_MISMATCH")
        _same_lineage(review["envelope"], previous_envelope, "REVIEW_LINEAGE_MISMATCH")
        _same_ref(review["payload"]["reviewed_package_ref"], artifact_ref(previous_envelope), "REVIEWED_PACKAGE_REF_MISMATCH")
        if (previous_envelope["version"] == 1 and previous_envelope["supersedes_ref"] is not None) or (previous_envelope["version"] > 1 and previous_envelope["supersedes_ref"] is None):
            _fail("PREVIOUS_SUPERSEDES_INVALID")
        report = review["payload"]["semantic_review_report"]
        if report["decision"] != "no" or "OBSERVABLE_MAPPING_ERROR" not in report["error_types"] or report["route_suggestion"] != "130":
            _fail("REVIEW_NOT_ROUTED_TO_130")
        if attempt_no not in (1, 2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        if attempt_no != previous_envelope["attempt_no"] + 1 or attempt_no != previous_request_envelope["attempt_no"] + 1:
            _fail("ATTEMPT_LINEAGE_MISMATCH")
        if run_id != previous_envelope["run_id"] or qa_id != previous_envelope["qa_id"]:
            _fail("RUN_OR_QA_MISMATCH")
        request = self.plan_constraint_query(
            penalty, run_id=run_id, qa_id=qa_id, expansion=True, attempt_no=attempt_no,
            previous_request_refs=[artifact_ref(previous_request["envelope"])], created_at=created_at,
        )
        assets = query_000(request)
        self.validate_assets(assets)
        asset_envelope = assets["envelope"]
        _same_lineage(asset_envelope, request["envelope"], "ASSET_LINEAGE_MISMATCH")
        if asset_envelope["attempt_no"] != request["envelope"]["attempt_no"]:
            _fail("ASSET_ATTEMPT_MISMATCH")
        if assets["payload"]["request_id"] != request["payload"]["request_id"]:
            _fail("ASSET_REQUEST_ID_MISMATCH")
        if artifact_ref(request["envelope"]) not in asset_envelope["parent_artifact_refs"]:
            _fail("ASSET_REQUEST_PARENT_MISSING")
        no_match = not assets["payload"]["matched_records"]
        terminal = attempt_no == 3 and no_match
        observable = self.build_observable_facts(
            penalty, assets, run_id=run_id, qa_id=qa_id,
            version=previous_observable["envelope"]["version"] + 1, attempt_no=attempt_no,
            supersedes_ref=artifact_ref(previous_observable["envelope"]),
            status="blocked_manual" if terminal else "candidate", created_at=created_at,
        )
        self._validate_remap_output(observable, previous_observable, assets, attempt_no)
        return {"request": request, "assets": assets, "observable": observable}
