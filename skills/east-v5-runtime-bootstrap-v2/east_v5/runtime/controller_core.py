"""Self-contained v2 controller: preflight, registry edge, receipt, launcher."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash
from east_v5.governance import ContractError, canonical_bytes, file_sha256, root_binding_id, sha256

SKILL_NAME = "east-v5-runtime-bootstrap-v2"
SKILL_VERSION = "v2"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TARGETS = {
    "010": {"uuid": "1d9153fc-4386-42e9-b2e5-56eb38f671af", "provider": "codex", "artifact_type": "penalty_source_package", "producer": "010", "route": "110"},
    "110": {"uuid": "67f9cf29-cd45-4ef3-8c87-963fd3ff5898", "provider": "codex", "artifact_type": "penalty_source_package", "producer": "010", "route": "120"},
    "120": {"uuid": "22533152-db59-4a1b-8d01-5f251c618e6b", "provider": "claude", "artifact_type": "penalty_fact_package", "producer": "120", "route": "complete"},
}


class ControllerError(ContractError):
    pass


def _fail(code: str) -> None:
    raise ControllerError(code)


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControllerError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


class RuntimeController:
    """The only v2 business path; it never imports a project checkout."""

    def __init__(self, skill_root: Path, envelope: dict[str, Any], claim: dict[str, Any]):
        self.root = skill_root.resolve()
        self.manifest_path = self.root / "manifest.json"
        self.manifest = _load(self.manifest_path, "RUNTIME_SKILL_MANIFEST_UNREADABLE")
        self.manifest_hash = file_sha256(self.manifest_path)
        self.envelope, self.claim = envelope, claim
        self._validate_manifest()
        self._validate_envelope()
        self._validate_claim()

    def _validate_manifest(self) -> None:
        needed = {"schema_version", "skill_name", "skill_version", "source_candidate_head", "root_binding_algorithm", "allowed_targets", "prohibited_transports", "files", "source_hashes", "fixture_hashes"}
        if set(self.manifest) != needed or self.manifest.get("schema_version") != "workspace_skill_bundle_manifest/v2" or (self.manifest.get("skill_name"), self.manifest.get("skill_version")) != (SKILL_NAME, SKILL_VERSION):
            _fail("RUNTIME_SKILL_MANIFEST_INVALID")
        if not isinstance(self.manifest.get("source_candidate_head"), str) or not _GIT.fullmatch(self.manifest["source_candidate_head"]):
            _fail("RUNTIME_SKILL_MANIFEST_HEAD_INVALID")
        if self.manifest.get("root_binding_algorithm") != "sha256(canonical(runtime_context))":
            _fail("RUNTIME_SKILL_ROOT_ALGORITHM_DRIFT")
        targets = self.manifest.get("allowed_targets")
        if targets != [{"agent_id": key, "agent_uuid": value["uuid"], "provider_id": value["provider"]} for key, value in _TARGETS.items()]:
            _fail("RUNTIME_SKILL_TARGETS_INVALID")
        if set(self.manifest.get("prohibited_transports", [])) != {"business_payload_in_issue", "physical_path_in_issue", "issue_comment_route", "network_fetch", "project_local_directory"}:
            _fail("RUNTIME_SKILL_TRANSPORT_POLICY_DRIFT")
        files = self.manifest.get("files")
        if not isinstance(files, dict) or not files:
            _fail("RUNTIME_SKILL_MANIFEST_HASHES_INVALID")
        for relative, expected in files.items():
            candidate = (self.root / relative).resolve(strict=False)
            if not isinstance(relative, str) or not _HEX.fullmatch(str(expected)) or self.root not in candidate.parents or not candidate.is_file() or file_sha256(candidate) != expected:
                _fail("RUNTIME_SKILL_SUPPORT_FILE_HASH_DRIFT")
        sources = self.manifest.get("source_hashes")
        if not isinstance(sources, dict) or sources.get("scripts/controller.py") != files.get("scripts/controller.py") or sources.get("east_v5/runtime/controller_core.py") != files.get("east_v5/runtime/controller_core.py"):
            _fail("RUNTIME_SKILL_SOURCE_HASH_DRIFT")
        fixtures = self.manifest.get("fixture_hashes")
        if not isinstance(fixtures, dict) or fixtures.get("fixtures/penalty/matched.json") != files.get("fixtures/penalty/matched.json"):
            _fail("RUNTIME_SKILL_FIXTURE_HASH_DRIFT")

    def _validate_envelope(self) -> None:
        required = {"schema_version", "adapter_version", "issue_id", "platform_parent_issue_id", "project_id", "run_id", "trace_id", "qa_id", "attempt", "target_agent_id", "target_agent_uuid", "root_binding_id", "input_ref", "input_receipt", "expected_output", "execution_bootstrap"}
        if set(self.envelope) != required or self.envelope.get("schema_version") != "task_input_envelope/v2" or self.envelope.get("adapter_version") != "east-v5-runtime-adapter/v2" or self.envelope.get("attempt") != 3:
            _fail("RUNTIME_TASK_INPUT_INVALID")
        if not all(isinstance(self.envelope.get(key), str) and self.envelope[key] for key in ("issue_id", "platform_parent_issue_id", "project_id", "run_id", "trace_id", "qa_id", "target_agent_id", "target_agent_uuid", "root_binding_id")):
            _fail("RUNTIME_TASK_INPUT_INVALID")
        target = _TARGETS.get(self.envelope["target_agent_id"])
        if target is None or self.envelope["target_agent_uuid"] != target["uuid"]:
            _fail("RUNTIME_TARGET_DRIFT")
        expected = self.envelope["expected_output"]
        if expected != {"artifact_type": target["artifact_type"], "producer_id": target["producer"], "route_target": target["route"]}:
            _fail("RUNTIME_OUTPUT_CONTRACT_DRIFT")
        if self.envelope["target_agent_id"] == "010":
            if self.envelope["input_ref"] is not None or self.envelope["input_receipt"] is not None:
                _fail("RUNTIME_INITIAL_INPUT_DRIFT")
        elif not isinstance(self.envelope["input_ref"], dict) or set(self.envelope["input_ref"]) != {"artifact_id", "version", "content_hash"}:
            _fail("RUNTIME_INPUT_REF_INVALID")
        elif not isinstance(self.envelope["input_receipt"], dict):
            _fail("RUNTIME_RECEIPT_SEQUENCE_INVALID")
        elif self.envelope["input_receipt"].get("output_ref") != self.envelope["input_ref"] or self.envelope["input_receipt"].get("route_target") != self.envelope["target_agent_id"] or self.envelope["input_receipt"].get("run_id") != self.envelope["run_id"] or self.envelope["input_receipt"].get("trace_id") != self.envelope["trace_id"] or self.envelope["input_receipt"].get("qa_id") != self.envelope["qa_id"] or self.envelope["input_receipt"].get("attempt") != 3:
            _fail("RUNTIME_RECEIPT_SEQUENCE_INVALID")
        elif self.envelope["input_receipt"].get("content_hash") != sha256({key: value for key, value in self.envelope["input_receipt"].items() if key != "content_hash"}):
            _fail("RUNTIME_RECEIPT_HASH_DRIFT")
        bootstrap = self.envelope["execution_bootstrap"]
        keys = {"bootstrap_version", "candidate_base_sha", "candidate_head_sha", "controller_sha256", "adapter_sha256", "runtime_context", "skill_bundle"}
        if not isinstance(bootstrap, dict) or set(bootstrap) != keys or bootstrap.get("bootstrap_version") != "east-v5-runtime-bootstrap/v2":
            _fail("RUNTIME_BOOTSTRAP_INVALID")
        if not _GIT.fullmatch(str(bootstrap.get("candidate_base_sha"))) or bootstrap.get("candidate_head_sha") != self.manifest["source_candidate_head"]:
            _fail("RUNTIME_SKILL_CANDIDATE_HEAD_DRIFT")
        if bootstrap.get("controller_sha256") != self.manifest["source_hashes"]["scripts/controller.py"] or bootstrap.get("adapter_sha256") != self.manifest["source_hashes"]["east_v5/runtime/controller_core.py"]:
            _fail("RUNTIME_SKILL_SOURCE_DRIFT")
        bundle = bootstrap.get("skill_bundle")
        bundle_keys = {"skill_name", "skill_version", "skill_id", "skill_manifest_sha256", "archive_sha256"}
        if not isinstance(bundle, dict) or set(bundle) != bundle_keys or (bundle.get("skill_name"), bundle.get("skill_version"), bundle.get("skill_manifest_sha256")) != (SKILL_NAME, SKILL_VERSION, self.manifest_hash) or not _UUID.fullmatch(str(bundle.get("skill_id"))) or not _HEX.fullmatch(str(bundle.get("archive_sha256"))):
            _fail("RUNTIME_SKILL_BUNDLE_DRIFT")
        try:
            computed = root_binding_id(bootstrap["runtime_context"])
        except ContractError as exc:
            raise ControllerError(str(exc)) from exc
        if self.envelope["root_binding_id"] != computed:
            _fail("RUNTIME_ROOT_BINDING_DRIFT")

    def _validate_claim(self) -> None:
        keys = {"agent_uuid", "runtime_uuid", "provider_id", "instructions_sha256", "enabled_skill_ids", "archive_sha256"}
        if set(self.claim) != keys or not all(isinstance(self.claim.get(key), str) and self.claim[key] for key in ("agent_uuid", "runtime_uuid", "provider_id", "instructions_sha256", "archive_sha256")) or not isinstance(self.claim.get("enabled_skill_ids"), list):
            _fail("RUNTIME_SKILL_CLAIM_INVALID")
        target = _TARGETS[self.envelope["target_agent_id"]]
        bundle = self.envelope["execution_bootstrap"]["skill_bundle"]
        if self.claim["agent_uuid"] != target["uuid"] or self.claim["provider_id"] != target["provider"] or bundle["skill_id"] not in self.claim["enabled_skill_ids"] or self.claim["archive_sha256"] != bundle["archive_sha256"]:
            _fail("RUNTIME_SKILL_CLAIM_DRIFT")

    def preflight(self, *, business: bool) -> dict[str, str]:
        after_configuration_hash = sha256({"agent_uuid": self.claim["agent_uuid"], "runtime_uuid": self.claim["runtime_uuid"], "instructions_sha256": self.claim["instructions_sha256"], "enabled_skill_ids": sorted(self.claim["enabled_skill_ids"]), "skill_manifest_sha256": self.manifest_hash, "archive_sha256": self.claim["archive_sha256"]})
        result = {"claim_status": "accepted", "mode": "business-preflight" if business else "claim-preflight", "skill_name": SKILL_NAME, "skill_version": SKILL_VERSION, "skill_manifest_sha256": self.manifest_hash, "candidate_head_sha": self.manifest["source_candidate_head"], "controller_sha256": self.manifest["source_hashes"]["scripts/controller.py"], "adapter_sha256": self.manifest["source_hashes"]["east_v5/runtime/controller_core.py"], "root_binding_id": self.envelope["root_binding_id"], "after_configuration_hash": after_configuration_hash}
        if business:
            result["business_operation"] = "not_started"
        return result

    def _package(self, artifact_type: str, producer: str, payload: Any, artifact_id: str) -> dict[str, Any]:
        envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": self.envelope["run_id"], "qa_id": self.envelope["qa_id"], "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "attempt_no": 3, "producer_id": producer, "status": "candidate", "trace_id": self.envelope["trace_id"], "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}

    def _source_package(self) -> dict[str, Any]:
        fixture = _load(self.root / "fixtures" / "penalty" / "matched.json", "RUNTIME_SKILL_FIXTURE_UNREADABLE")
        fixture_hash = file_sha256(self.root / "fixtures" / "penalty" / "matched.json")
        return self._package("penalty_source_package", "010", {"source_package_schema_version": fixture.get("source_package_schema_version"), "sanitized_fixture_sha256": fixture_hash, "source_document_id": fixture.get("source_document_id"), "join_status": fixture.get("join_status"), "penalty_summary": fixture.get("penalty_decision_raw")}, f"eas70-010-{self.envelope['run_id']}")

    def _fact_package(self, reference: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        return self._package("penalty_fact_package", "120", {"penalty_fact_package_schema_version": "penalty-fact-v1", "source_ref": reference, "source_content_hash": source["envelope"]["content_hash"], "fact_status": "sanitized-controller-output"}, f"eas70-120-{self.envelope['run_id']}")

    def _receipt(self, task_id: str, runtime_id: str, input_ref: dict[str, Any] | None, output_ref: dict[str, Any], route_target: str) -> dict[str, Any]:
        value = {"schema_version": "task_execution_receipt/v2", "task_id": task_id, "runtime_id": runtime_id, "issue_id": self.envelope["issue_id"], "agent_id": self.envelope["target_agent_uuid"], "run_id": self.envelope["run_id"], "trace_id": self.envelope["trace_id"], "qa_id": self.envelope["qa_id"], "attempt": 3, "input_ref": input_ref, "output_ref": output_ref, "route_target": route_target}
        value["content_hash"] = sha256(value)
        return value

    def _next_envelope(self, reference: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        current = self.envelope["target_agent_id"]
        next_target = _TARGETS[current]["route"]
        if next_target == "complete":
            _fail("RUNTIME_TERMINAL_LAUNCH_FORBIDDEN")
        target = _TARGETS[next_target]
        return {**self.envelope, "target_agent_id": next_target, "target_agent_uuid": target["uuid"], "input_ref": reference, "input_receipt": receipt, "expected_output": {"artifact_type": target["artifact_type"], "producer_id": target["producer"], "route_target": target["route"]}}

    def _launch(self, next_envelope: dict[str, Any], *, record: Path | None, fail: bool) -> dict[str, Any]:
        if fail:
            _fail("RUNTIME_LAUNCH_FAILED")
        target = next_envelope["target_agent_id"]
        if record is not None:
            state = _load(record, "RUNTIME_LAUNCH_RECORD_INVALID") if record.exists() else {"launches": []}
            if set(state) != {"launches"} or not isinstance(state["launches"], list):
                _fail("RUNTIME_LAUNCH_RECORD_INVALID")
            nonce = hashlib.sha256(canonical_bytes(next_envelope)).hexdigest()[:16]
            result = {"issue_id": f"test-issue-{target}-{nonce}", "task_id": f"test-task-{target}-{nonce}", "target_agent_uuid": next_envelope["target_agent_uuid"], "envelope": next_envelope}
            state["launches"].append(result)
            record.write_bytes(canonical_bytes(state))
            return result
        command = ["multica", "issue", "create", "--title", f"EAS runtime {self.envelope['run_id']} {target} attempt 3", "--description", json.dumps(next_envelope, ensure_ascii=False, sort_keys=True), "--parent", self.envelope["platform_parent_issue_id"], "--assignee-id", next_envelope["target_agent_uuid"], "--status", "todo", "--project", self.envelope["project_id"], "--output", "json"]
        try:
            created = subprocess.run(command, check=True, capture_output=True, text=True)
            issue_id = json.loads(created.stdout)["id"]
            runs = json.loads(subprocess.run(["multica", "issue", "runs", issue_id, "--output", "json"], check=True, capture_output=True, text=True).stdout)
            task_id = next(item["id"] for item in runs if item.get("issue_id") == issue_id and item.get("agent_id") == next_envelope["target_agent_uuid"])
        except (OSError, subprocess.SubprocessError, KeyError, StopIteration, json.JSONDecodeError) as exc:
            raise ControllerError("RUNTIME_LAUNCH_FAILED") from exc
        return {"issue_id": issue_id, "task_id": task_id, "target_agent_uuid": next_envelope["target_agent_uuid"], "envelope": next_envelope}

    def run_task(self, runtime_root: Path, *, task_id: str, runtime_id: str, launcher_record: Path | None = None, launcher_fail: bool = False) -> dict[str, Any]:
        if not _UUID.fullmatch(task_id) or not _UUID.fullmatch(runtime_id):
            _fail("RUNTIME_TASK_ID_INVALID")
        registry = ArtifactRegistry(runtime_root.resolve(), self.envelope["issue_id"], self.envelope["run_id"], 3)
        journal = registry.directory / "execution-journal.json"
        if journal.exists():
            state = _load(journal, "RUNTIME_EXECUTION_JOURNAL_INVALID")
            prior = state.get(task_id)
            if prior is not None:
                return prior
        target = self.envelope["target_agent_id"]
        input_ref = self.envelope["input_ref"]
        if target == "010":
            package = self._source_package()
            output_ref = registry.register(package)
            if registry.resolve(output_ref) != package:
                _fail("RUNTIME_REGISTRY_READBACK_DRIFT")
        else:
            source = registry.resolve(input_ref)
            if source["envelope"].get("artifact_type") != "penalty_source_package" or source["envelope"].get("producer_id") != "010":
                _fail("RUNTIME_INPUT_CONTRACT_DRIFT")
            if target == "120":
                package = self._fact_package(input_ref, source)
                output_ref = registry.register(package)
                if registry.resolve(output_ref) != package:
                    _fail("RUNTIME_REGISTRY_READBACK_DRIFT")
            else:
                output_ref = input_ref
        receipt = self._receipt(task_id, runtime_id, input_ref, output_ref, _TARGETS[target]["route"])
        result: dict[str, Any] = {"output_ref": output_ref, "receipt": receipt, "next_task": None}
        if _TARGETS[target]["route"] != "complete":
            next_envelope = self._next_envelope(output_ref, receipt)
            result["next_task"] = self._launch(next_envelope, record=launcher_record, fail=launcher_fail)
        journal_state = _load(journal, "RUNTIME_EXECUTION_JOURNAL_INVALID") if journal.exists() else {}
        journal_state[task_id] = result
        journal.write_bytes(canonical_bytes(journal_state))
        return result
