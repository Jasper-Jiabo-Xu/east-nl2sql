"""Self-contained v6 controller with cwd-confined launch descriptors."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash
from east_v5.governance import ContractError, canonical_bytes, file_sha256, root_binding_id, sha256

SKILL_NAME = "east-v5-runtime-bootstrap-v6"
SKILL_VERSION = "v6"
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
    """The only business/control path; it never imports a project checkout."""

    def __init__(self, skill_root: Path, envelope: dict[str, Any], claim: dict[str, Any]):
        self.root = skill_root.resolve()
        self.manifest_path = self.root / "manifest.json"
        self.manifest = _load(self.manifest_path, "RUNTIME_SKILL_MANIFEST_UNREADABLE")
        self.manifest_hash = file_sha256(self.manifest_path)
        self.envelope, self.claim = envelope, claim
        self.control_mode = envelope.get("execution_mode") == "launcher-control-preflight/v1"
        self._validate_manifest()
        (self._validate_control_envelope if self.control_mode else self._validate_envelope)()
        self._validate_claim()

    def _validate_manifest(self) -> None:
        needed = {"schema_version", "skill_name", "skill_version", "source_candidate_head", "root_binding_algorithm", "allowed_targets", "prohibited_transports", "files", "source_hashes", "fixture_hashes", "instruction_hashes"}
        if set(self.manifest) != needed or self.manifest.get("schema_version") != "workspace_skill_bundle_manifest/v6" or (self.manifest.get("skill_name"), self.manifest.get("skill_version")) != (SKILL_NAME, SKILL_VERSION):
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
        instruction_hashes = self.manifest.get("instruction_hashes")
        if not isinstance(instruction_hashes, dict) or set(instruction_hashes) != set(_TARGETS) or not all(_HEX.fullmatch(str(value)) for value in instruction_hashes.values()):
            _fail("RUNTIME_SKILL_INSTRUCTIONS_HASH_INVALID")

    def _validate_envelope(self) -> None:
        required = {"schema_version", "adapter_version", "issue_id", "platform_parent_issue_id", "project_id", "run_id", "trace_id", "qa_id", "attempt", "target_agent_id", "target_agent_uuid", "root_binding_id", "input_ref", "input_receipt", "expected_output", "execution_bootstrap", "recovery_of", "supersedes"}
        keys = set(self.envelope)
        if keys != required and keys != required | {"launch_idempotency_key"} or self.envelope.get("schema_version") != "task_input_envelope/v6" or self.envelope.get("adapter_version") != "east-v5-runtime-adapter/v6" or self.envelope.get("attempt") != 2:
            _fail("RUNTIME_TASK_INPUT_INVALID")
        if "launch_idempotency_key" in self.envelope and not _HEX.fullmatch(str(self.envelope["launch_idempotency_key"])):
            _fail("RUNTIME_LAUNCH_IDEMPOTENCY_KEY_INVALID")
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
        elif self.envelope["input_receipt"].get("output_ref") != self.envelope["input_ref"] or self.envelope["input_receipt"].get("route_target") != self.envelope["target_agent_id"] or self.envelope["input_receipt"].get("run_id") != self.envelope["run_id"] or self.envelope["input_receipt"].get("trace_id") != self.envelope["trace_id"] or self.envelope["input_receipt"].get("qa_id") != self.envelope["qa_id"] or self.envelope["input_receipt"].get("attempt") != self.envelope["attempt"]:
            _fail("RUNTIME_RECEIPT_SEQUENCE_INVALID")
        elif self.envelope["input_receipt"].get("content_hash") != sha256({key: value for key, value in self.envelope["input_receipt"].items() if key != "content_hash"}):
            _fail("RUNTIME_RECEIPT_HASH_DRIFT")
        bootstrap = self.envelope["execution_bootstrap"]
        keys = {"bootstrap_version", "candidate_base_sha", "candidate_head_sha", "controller_sha256", "adapter_sha256", "runtime_context", "skill_bundle"}
        if not isinstance(bootstrap, dict) or set(bootstrap) != keys or bootstrap.get("bootstrap_version") != "east-v5-runtime-bootstrap/v6":
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
        recovery = self.envelope["recovery_of"]
        if not isinstance(recovery, dict) or set(recovery) != {"old_run_id", "old_issue_id", "old_task_id", "decision_comment_id", "run_id", "trace_id", "qa_id"}:
            _fail("RUNTIME_RECOVERY_BINDING_INVALID")
        if {key: recovery[key] for key in ("old_run_id", "old_issue_id", "old_task_id", "decision_comment_id")} != {"old_run_id": "real-probe-010-110-120-v2", "old_issue_id": "0081927a-f1e7-42fe-a9e7-0a080857c082", "old_task_id": "c73f6821-0c29-4941-9214-12986df0d389", "decision_comment_id": "7397c95e-15b6-4b60-9d2d-5693d1e2659c"}:
            _fail("RUNTIME_RECOVERY_BINDING_INVALID")
        if any(recovery[key] != self.envelope[key] for key in ("run_id", "trace_id", "qa_id")):
            _fail("RUNTIME_RECOVERY_LINEAGE_DRIFT")
        if self.envelope["supersedes"] != ["124a3e9d-fff7-4492-8c7b-9991d018795d", "a7d894d2-1c82-4bf2-a66a-5035404a6b4c"]:
            _fail("RUNTIME_RECOVERY_SUPERSEDES_INVALID")

    def _validate_control_envelope(self) -> None:
        """Reject every business transport before a control task can be created."""
        required = {"schema_version", "execution_mode", "issue_id", "platform_parent_issue_id", "project_id", "run_id", "trace_id", "qa_id", "target_agent_id", "target_agent_uuid", "root_binding_id", "launch_idempotency_key", "execution_bootstrap", "callback"}
        if set(self.envelope) != required or self.envelope.get("schema_version") != "launcher_control_envelope/v1" or self.envelope.get("execution_mode") != "launcher-control-preflight/v1":
            _fail("RUNTIME_LAUNCH_CONTROL_ENVELOPE_INVALID")
        if not all(isinstance(self.envelope.get(key), str) and self.envelope[key] for key in ("issue_id", "platform_parent_issue_id", "project_id", "run_id", "trace_id", "qa_id", "root_binding_id", "launch_idempotency_key")) or not _HEX.fullmatch(self.envelope["launch_idempotency_key"]):
            _fail("RUNTIME_LAUNCH_CONTROL_ENVELOPE_INVALID")
        target = self.envelope.get("target_agent_id")
        if target not in {"010", "110"} or self.envelope.get("target_agent_uuid") != _TARGETS[target]["uuid"]:
            _fail("RUNTIME_LAUNCH_CONTROL_TARGET_DRIFT")
        callback = self.envelope.get("callback")
        if not isinstance(callback, dict) or set(callback) != {"callback_agent_id", "callback_issue_id", "callback_condition"} or not all(isinstance(value, str) and value for value in callback.values()):
            _fail("RUNTIME_LAUNCH_CONTROL_CALLBACK_INVALID")
        bootstrap = self.envelope["execution_bootstrap"]
        keys = {"bootstrap_version", "candidate_base_sha", "candidate_head_sha", "controller_sha256", "adapter_sha256", "runtime_context", "skill_bundle"}
        if not isinstance(bootstrap, dict) or set(bootstrap) != keys or bootstrap.get("bootstrap_version") != "east-v5-runtime-bootstrap/v6" or bootstrap.get("candidate_head_sha") != self.manifest["source_candidate_head"] or not _GIT.fullmatch(str(bootstrap.get("candidate_base_sha"))):
            _fail("RUNTIME_LAUNCH_CONTROL_BOOTSTRAP_INVALID")
        if bootstrap.get("controller_sha256") != self.manifest["source_hashes"]["scripts/controller.py"] or bootstrap.get("adapter_sha256") != self.manifest["source_hashes"]["east_v5/runtime/controller_core.py"]:
            _fail("RUNTIME_LAUNCH_CONTROL_BOOTSTRAP_INVALID")
        bundle = bootstrap.get("skill_bundle")
        if not isinstance(bundle, dict) or set(bundle) != {"skill_name", "skill_version", "skill_id", "skill_manifest_sha256", "archive_sha256"} or (bundle.get("skill_name"), bundle.get("skill_version"), bundle.get("skill_manifest_sha256")) != (SKILL_NAME, SKILL_VERSION, self.manifest_hash) or not _UUID.fullmatch(str(bundle.get("skill_id"))) or not _HEX.fullmatch(str(bundle.get("archive_sha256"))):
            _fail("RUNTIME_LAUNCH_CONTROL_BOOTSTRAP_INVALID")
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
        if self.claim["instructions_sha256"] != self.manifest["instruction_hashes"][self.envelope["target_agent_id"]]:
            _fail("RUNTIME_SKILL_CLAIM_INSTRUCTIONS_DRIFT")

    def launcher_control_preflight(self, runtime_root: Path, *, task_id: str, runtime_id: str, launcher_record: Path | None = None, launcher_fail: bool = False) -> dict[str, Any]:
        """Use the exact production outbox/create/observe path without business writes."""
        if not self.control_mode or self.envelope["target_agent_id"] != "010" or not _UUID.fullmatch(task_id) or not _UUID.fullmatch(runtime_id):
            _fail("RUNTIME_LAUNCH_CONTROL_ARGUMENT_INVALID")
        registry = ArtifactRegistry(runtime_root.resolve(), self.envelope["issue_id"], self.envelope["run_id"], 1)
        control = {**self.envelope, "target_agent_id": "110", "target_agent_uuid": _TARGETS["110"]["uuid"]}
        launched = self._launch(registry, control, record=launcher_record, fail=launcher_fail)
        phase = self._outbox_state(self._outbox(registry))["entries"][launched["launch_idempotency_key"]]["state"]
        return {"status": "launcher_control_launched", "preflight_task_id": task_id, "preflight_runtime_id": runtime_id, "control_issue_id": launched["issue_id"], "control_task_id": launched["task_id"], "control_agent_uuid": _TARGETS["110"]["uuid"], "launch_idempotency_key": launched["launch_idempotency_key"], "outbox_phase": phase, "business_artifact_count": 0, "business_receipt_count": 0, "downstream_120_task_count": 0}

    def launcher_control_consume(self, *, task_id: str, runtime_id: str) -> dict[str, Any]:
        if not self.control_mode or self.envelope["target_agent_id"] != "110" or not _UUID.fullmatch(task_id) or not _UUID.fullmatch(runtime_id):
            _fail("RUNTIME_LAUNCH_CONTROL_ARGUMENT_INVALID")
        return {"status": "launcher_control_consumed", "task_id": task_id, "runtime_id": runtime_id, "launch_idempotency_key": self.envelope["launch_idempotency_key"], "business_artifact_count": 0, "business_receipt_count": 0, "downstream_120_task_count": 0}

    def preflight(self, *, business: bool) -> dict[str, str]:
        after_configuration_hash = sha256({"agent_uuid": self.claim["agent_uuid"], "runtime_uuid": self.claim["runtime_uuid"], "instructions_sha256": self.claim["instructions_sha256"], "enabled_skill_ids": sorted(self.claim["enabled_skill_ids"]), "skill_manifest_sha256": self.manifest_hash, "archive_sha256": self.claim["archive_sha256"]})
        result = {"claim_status": "accepted", "mode": "business-preflight" if business else "claim-preflight", "skill_name": SKILL_NAME, "skill_version": SKILL_VERSION, "skill_manifest_sha256": self.manifest_hash, "candidate_head_sha": self.manifest["source_candidate_head"], "controller_sha256": self.manifest["source_hashes"]["scripts/controller.py"], "adapter_sha256": self.manifest["source_hashes"]["east_v5/runtime/controller_core.py"], "root_binding_id": self.envelope["root_binding_id"], "after_configuration_hash": after_configuration_hash}
        if business:
            result["business_operation"] = "not_started"
        return result

    def _package(self, artifact_type: str, producer: str, payload: Any, artifact_id: str) -> dict[str, Any]:
        envelope = {"artifact_id": artifact_id, "artifact_type": artifact_type, "run_id": self.envelope["run_id"], "qa_id": self.envelope["qa_id"], "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "attempt_no": self.envelope["attempt"], "producer_id": producer, "status": "candidate", "trace_id": self.envelope["trace_id"], "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, payload)
        return {"envelope": envelope, "payload": payload}

    def _source_package(self) -> dict[str, Any]:
        fixture = _load(self.root / "fixtures" / "penalty" / "matched.json", "RUNTIME_SKILL_FIXTURE_UNREADABLE")
        fixture_hash = file_sha256(self.root / "fixtures" / "penalty" / "matched.json")
        return self._package("penalty_source_package", "010", {"source_package_schema_version": fixture.get("source_package_schema_version"), "sanitized_fixture_sha256": fixture_hash, "source_document_id": fixture.get("source_document_id"), "join_status": fixture.get("join_status"), "penalty_summary": fixture.get("penalty_decision_raw")}, f"eas70-010-{self.envelope['run_id']}")

    def _fact_package(self, reference: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        return self._package("penalty_fact_package", "120", {"penalty_fact_package_schema_version": "penalty-fact-v1", "source_ref": reference, "source_content_hash": source["envelope"]["content_hash"], "fact_status": "sanitized-controller-output"}, f"eas70-120-{self.envelope['run_id']}")

    def _receipt(self, task_id: str, runtime_id: str, input_ref: dict[str, Any] | None, output_ref: dict[str, Any], route_target: str) -> dict[str, Any]:
        value = {"schema_version": "task_execution_receipt/v6", "task_id": task_id, "runtime_id": runtime_id, "issue_id": self.envelope["issue_id"], "agent_id": self.envelope["target_agent_uuid"], "run_id": self.envelope["run_id"], "trace_id": self.envelope["trace_id"], "qa_id": self.envelope["qa_id"], "attempt": self.envelope["attempt"], "input_ref": input_ref, "output_ref": output_ref, "route_target": route_target, "recovery_of": self.envelope["recovery_of"]}
        value["content_hash"] = sha256(value)
        return value

    def _next_envelope(self, reference: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        current = self.envelope["target_agent_id"]
        next_target = _TARGETS[current]["route"]
        if next_target == "complete":
            _fail("RUNTIME_TERMINAL_LAUNCH_FORBIDDEN")
        target = _TARGETS[next_target]
        return {**self.envelope, "target_agent_id": next_target, "target_agent_uuid": target["uuid"], "input_ref": reference, "input_receipt": receipt, "expected_output": {"artifact_type": target["artifact_type"], "producer_id": target["producer"], "route_target": target["route"]}}

    def _outbox(self, registry: ArtifactRegistry) -> Path:
        return registry.directory / "launch-outbox.json"

    def _outbox_state(self, path: Path) -> dict[str, Any]:
        state = _load(path, "RUNTIME_LAUNCH_CREATE_OUTPUT_INVALID") if path.exists() else {"schema_version": "EAST-V5-LAUNCH-OUTBOX/v6", "entries": {}}
        if set(state) != {"schema_version", "entries"} or state["schema_version"] != "EAST-V5-LAUNCH-OUTBOX/v6" or not isinstance(state["entries"], dict):
            _fail("RUNTIME_LAUNCH_OUTBOX_VERSION_DRIFT")
        return state

    def _descriptor(self, key: str, payload: dict[str, Any]) -> tuple[Path, Path]:
        """Create one private descriptor under this controller's real cwd only."""
        cwd = Path.cwd().resolve(strict=True)
        if not cwd.is_dir():
            _fail("RUNTIME_LAUNCH_DESCRIPTOR_ROOT_INVALID")
        fd, raw = tempfile.mkstemp(prefix=f".east-launch-{key[:12]}-", suffix=".json", dir=str(cwd))
        descriptor = Path(raw)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            resolved = descriptor.resolve(strict=True)
            if resolved.parent != cwd or not resolved.is_file() or (resolved.stat().st_mode & 0o777) != 0o600:
                _fail("RUNTIME_LAUNCH_DESCRIPTOR_PATH_INVALID")
            return cwd, resolved
        except BaseException:
            descriptor.unlink(missing_ok=True)
            raise

    def _launch(self, registry: ArtifactRegistry, next_envelope: dict[str, Any], *, record: Path | None, fail: bool) -> dict[str, Any]:
        """Create once, persist the issue id, then observe the same issue only."""
        target = next_envelope["target_agent_id"]
        key = self.envelope["launch_idempotency_key"] if self.control_mode else hashlib.sha256(canonical_bytes({"parent": self.envelope["platform_parent_issue_id"], "run": self.envelope["run_id"], "attempt": self.envelope["attempt"], "from": self.envelope["target_agent_id"], "to": target, "input": next_envelope["input_ref"]})).hexdigest()
        next_envelope = {**next_envelope, "launch_idempotency_key": key}
        outbox_path = self._outbox(registry)
        outbox_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        outbox = self._outbox_state(outbox_path)
        binding = {"envelope_sha256": sha256(next_envelope), "candidate_head_sha": self.manifest["source_candidate_head"], "skill_id": self.envelope["execution_bootstrap"]["skill_bundle"]["skill_id"]}
        entry = outbox["entries"].setdefault(key, {"state": "intent", "target": target, **binding})
        if not isinstance(entry, dict) or any(entry.get(name) != value for name, value in {"target": target, **binding}.items()):
            _fail("RUNTIME_LAUNCH_OUTBOX_BINDING_DRIFT")
        outbox_path.write_bytes(canonical_bytes(outbox))
        if fail:
            _fail("RUNTIME_LAUNCH_CREATE_FAILED")
        if record is not None:
            state = _load(record, "RUNTIME_LAUNCH_CREATE_OUTPUT_INVALID") if record.exists() else {"launches": []}
            if set(state) != {"launches"} or not isinstance(state["launches"], list):
                _fail("RUNTIME_LAUNCH_CREATE_OUTPUT_INVALID")
            nonce = hashlib.sha256(canonical_bytes(next_envelope)).hexdigest()[:16]
            result = next((item for item in state["launches"] if item.get("launch_idempotency_key") == key), None)
            if result is None:
                result = {"issue_id": f"test-issue-{target}-{nonce}", "task_id": f"test-task-{target}-{nonce}", "target_agent_uuid": next_envelope["target_agent_uuid"], "envelope": next_envelope, "launch_idempotency_key": key}
                state["launches"].append(result)
            record.write_bytes(canonical_bytes(state))
            entry.update({"state": "task_id", "issue_id": result["issue_id"], "task_id": result["task_id"]})
            outbox_path.write_bytes(canonical_bytes(outbox))
            return result
        if entry.get("issue_id") is None:
            cwd, descriptor = self._descriptor(key, next_envelope)
            title = f"EAS launcher-control {self.envelope['run_id']} {target}" if self.control_mode else f"EAS runtime {self.envelope['run_id']} {target} attempt {self.envelope['attempt']}"
            try:
                relative_descriptor = descriptor.relative_to(cwd)
                command = ["multica", "issue", "create", "--title", title, "--description-file", str(relative_descriptor), "--parent", self.envelope["platform_parent_issue_id"], "--assignee-id", next_envelope["target_agent_uuid"], "--status", "todo", "--project", self.envelope["project_id"], "--allow-duplicate", "--output", "json"]
                created = subprocess.run(command, check=True, capture_output=True, text=True, cwd=str(cwd))
                parsed = json.loads(created.stdout)
                issue_id = parsed.get("id")
                if not isinstance(issue_id, str) or not issue_id:
                    _fail("RUNTIME_LAUNCH_CREATE_OUTPUT_INVALID")
                entry.update({"state": "issue_id", "issue_id": issue_id})
                outbox_path.write_bytes(canonical_bytes(outbox))
            except ControllerError:
                raise
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                raise ControllerError("RUNTIME_LAUNCH_CREATE_FAILED") from exc
            finally:
                descriptor.unlink(missing_ok=True)
        try:
            for _ in range(3):
                runs = json.loads(subprocess.run(["multica", "issue", "runs", entry["issue_id"], "--output", "json"], check=True, capture_output=True, text=True).stdout)
                task_id = next((item.get("id") for item in runs if item.get("issue_id") == entry["issue_id"] and item.get("agent_id") == next_envelope["target_agent_uuid"]), None)
                if isinstance(task_id, str) and task_id:
                    entry.update({"state": "task_id", "task_id": task_id})
                    outbox_path.write_bytes(canonical_bytes(outbox))
                    return {"issue_id": entry["issue_id"], "task_id": task_id, "target_agent_uuid": next_envelope["target_agent_uuid"], "envelope": next_envelope, "launch_idempotency_key": key}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise ControllerError("RUNTIME_LAUNCH_TASK_OBSERVE_TIMEOUT") from exc
        raise ControllerError("RUNTIME_LAUNCH_TASK_OBSERVE_TIMEOUT")

    def run_task(self, runtime_root: Path, *, task_id: str, runtime_id: str, launcher_record: Path | None = None, launcher_fail: bool = False) -> dict[str, Any]:
        if not _UUID.fullmatch(task_id) or not _UUID.fullmatch(runtime_id):
            _fail("RUNTIME_TASK_ID_INVALID")
        registry = ArtifactRegistry(runtime_root.resolve(), self.envelope["issue_id"], self.envelope["run_id"], self.envelope["attempt"])
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
        else:
            source = registry.resolve(input_ref)
            if source["envelope"].get("artifact_type") != "penalty_source_package" or source["envelope"].get("producer_id") != "010":
                _fail("RUNTIME_INPUT_CONTRACT_DRIFT")
            if target == "120":
                package = self._fact_package(input_ref, source)
            else:
                output_ref = input_ref
        output_ref = artifact_ref(package["envelope"]) if target in {"010", "120"} else input_ref
        stage = registry.directory / ("staged-" + task_id + ".json")
        stage.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stage.write_bytes(canonical_bytes({"state": "staged", "package": package if target in {"010", "120"} else None, "output_ref": output_ref}))
        result: dict[str, Any] = {"output_ref": output_ref, "receipt": None, "next_task": None, "stage": "staged"}
        if _TARGETS[target]["route"] != "complete":
            provisional = self._receipt(task_id, runtime_id, input_ref, output_ref, _TARGETS[target]["route"])
            next_envelope = self._next_envelope(output_ref, provisional)
            result["next_task"] = self._launch(registry, next_envelope, record=launcher_record, fail=launcher_fail)
        if target in {"010", "120"}:
            output_ref = registry.register(package)
            if registry.resolve(output_ref) != package:
                _fail("RUNTIME_REGISTRY_READBACK_DRIFT")
        receipt = self._receipt(task_id, runtime_id, input_ref, output_ref, _TARGETS[target]["route"])
        result.update({"output_ref": output_ref, "receipt": receipt, "stage": "committed"})
        stage.unlink(missing_ok=True)
        journal_state = _load(journal, "RUNTIME_EXECUTION_JOURNAL_INVALID") if journal.exists() else {}
        journal_state[task_id] = result
        journal.write_bytes(canonical_bytes(journal_state))
        if result["next_task"] is not None:
            outbox_path = self._outbox(registry)
            outbox = self._outbox_state(outbox_path)
            key = result["next_task"]["launch_idempotency_key"]
            outbox["entries"][key]["state"] = "committed"
            outbox_path.write_bytes(canonical_bytes(outbox))
        return result
