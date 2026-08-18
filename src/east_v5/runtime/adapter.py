"""Strict runtime-owned registry hand-off used by EAS-70 probe tasks.

This module deliberately has no model invocation and does not create business
packages.  A business task supplies a complete package; the adapter validates,
atomically registers, reads it back, and only then returns a receipt that a
platform launcher may use to create the next task.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from east_v5.artifacts import ArtifactRegistry, artifact_ref, content_hash, validate_envelope
from east_v5.governance import ContractError, canonical_bytes

_INPUT_KEYS = {"schema_version", "adapter_version", "issue_id", "run_id", "trace_id", "qa_id", "attempt", "target_agent_id", "target_agent_uuid", "root_binding_id", "input_ref", "expected_output"}
_REF_KEYS = {"artifact_id", "version", "content_hash"}


class RuntimeAdapterError(ContractError):
    """A runtime edge is rejected before a downstream task is created."""


def _fail(code: str) -> None:
    raise RuntimeAdapterError(code)


def _ref(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _REF_KEYS:
        _fail(f"RUNTIME_{label}_REF_INVALID")
    return value


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def task_execution_receipt(*, task_id: str, issue_id: str, agent_id: str, runtime_id: str, input_ref: dict[str, Any] | None, output_ref: dict[str, Any], run_id: str, trace_id: str, qa_id: str, attempt: int, route_target: str | None) -> dict[str, Any]:
    """Return an immutable, hash-addressed receipt after registry read-back."""
    if not all(isinstance(value, str) and value for value in (task_id, issue_id, agent_id, runtime_id, run_id, trace_id, qa_id)):
        _fail("RUNTIME_RECEIPT_ID_INVALID")
    if attempt not in (1, 2, 3):
        _fail("RUNTIME_RECEIPT_ATTEMPT_INVALID")
    receipt = {"schema_version": "task_execution_receipt/v1", "task_id": task_id, "issue_id": issue_id, "agent_id": agent_id, "runtime_id": runtime_id, "input_ref": input_ref, "output_ref": output_ref, "run_id": run_id, "trace_id": trace_id, "qa_id": qa_id, "attempt": attempt, "route_target": route_target, "content_hash": "0" * 64}
    receipt["content_hash"] = _sha({key: value for key, value in receipt.items() if key != "content_hash"})
    return receipt


class RuntimeAdapter:
    """Adapter-owned edge gate: register -> read back -> receipt -> dispatch intent."""

    def __init__(self, repo_root: Path, roots: dict[str, Any], envelope: dict[str, Any]):
        if not isinstance(envelope, dict) or set(envelope) != _INPUT_KEYS:
            _fail("RUNTIME_TASK_INPUT_UNKNOWN_FIELD")
        if envelope["schema_version"] != "task_input_envelope/v1" or envelope["adapter_version"] != "east-v5-runtime-adapter/v1":
            _fail("RUNTIME_TASK_INPUT_VERSION_INVALID")
        if not isinstance(envelope["attempt"], int) or envelope["attempt"] not in (1, 2, 3):
            _fail("RUNTIME_TASK_INPUT_ATTEMPT_INVALID")
        if not all(isinstance(envelope[key], str) and envelope[key] for key in ("issue_id", "run_id", "trace_id", "qa_id", "target_agent_id", "target_agent_uuid", "root_binding_id")):
            _fail("RUNTIME_TASK_INPUT_VALUE_INVALID")
        expected = envelope["expected_output"]
        if not isinstance(expected, dict) or set(expected) != {"artifact_type", "producer_id", "route_target"} or not all(isinstance(expected[key], str) and expected[key] for key in expected):
            _fail("RUNTIME_EXPECTED_OUTPUT_INVALID")
        initial = envelope["target_agent_id"] == "010" and envelope["input_ref"] is None
        if initial:
            if (expected["artifact_type"], expected["producer_id"]) != ("penalty_source_package", "010"):
                _fail("RUNTIME_INITIAL_OUTPUT_CONTRACT_INVALID")
        elif envelope["input_ref"] is None:
            _fail("RUNTIME_INPUT_REF_REQUIRED")
        else:
            _ref(envelope["input_ref"], "INPUT")
        self.repo_root, self.envelope = repo_root.resolve(), envelope.copy()
        self.registry = ArtifactRegistry(self.repo_root, roots, envelope["issue_id"], envelope["run_id"], envelope["attempt"])

    def register_output(self, package: dict[str, Any], *, task_id: str, runtime_id: str) -> dict[str, Any]:
        if not isinstance(package, dict) or set(package) != {"envelope", "payload"}:
            _fail("RUNTIME_OUTPUT_TRANSPORT_INVALID")
        output, payload = package["envelope"], package["payload"]
        try:
            validate_envelope(self.repo_root, output, payload)
        except ContractError as exc:
            raise RuntimeAdapterError("RUNTIME_OUTPUT_ENVELOPE_REJECTED") from exc
        expected = self.envelope["expected_output"]
        if (output["artifact_type"], output["producer_id"]) != (expected["artifact_type"], expected["producer_id"]):
            _fail("RUNTIME_OUTPUT_CONTRACT_DRIFT")
        if (output["run_id"], output["trace_id"], output["qa_id"], output["attempt_no"]) != (self.envelope["run_id"], self.envelope["trace_id"], self.envelope["qa_id"], self.envelope["attempt"]):
            _fail("RUNTIME_OUTPUT_CONTEXT_DRIFT")
        registered = self.registry.register(output, payload)
        reference = artifact_ref(registered)
        read_back = self.registry.resolve(reference)
        if read_back != {"envelope": output, "payload": payload}:
            _fail("RUNTIME_REGISTRY_READBACK_DRIFT")
        receipt = task_execution_receipt(task_id=task_id, issue_id=self.envelope["issue_id"], agent_id=self.envelope["target_agent_uuid"], runtime_id=runtime_id, input_ref=self.envelope["input_ref"], output_ref=reference, run_id=self.envelope["run_id"], trace_id=self.envelope["trace_id"], qa_id=self.envelope["qa_id"], attempt=self.envelope["attempt"], route_target=expected["route_target"])
        return {"output_ref": reference, "receipt": receipt, "next_dispatch": {"target": expected["route_target"], "input_ref": reference, "receipt_hash": receipt["content_hash"]}}

    def consume_input(self, *, task_id: str, runtime_id: str) -> dict[str, Any]:
        """Verify the prior edge and issue a receipt for a pure routing task.

        110 does not manufacture a second copy of the 010 source package.  Its
        only valid success result is a verified consumption receipt; the adapter
        keeps ownership of the following task creation.
        """
        reference = _ref(self.envelope["input_ref"], "INPUT")
        try:
            record = self.registry.resolve(reference)
            validate_envelope(self.repo_root, record["envelope"], record["payload"])
        except ContractError as exc:
            raise RuntimeAdapterError("RUNTIME_INPUT_RESOLUTION_REJECTED") from exc
        receipt = task_execution_receipt(task_id=task_id, issue_id=self.envelope["issue_id"], agent_id=self.envelope["target_agent_uuid"], runtime_id=runtime_id, input_ref=reference, output_ref=reference, run_id=self.envelope["run_id"], trace_id=self.envelope["trace_id"], qa_id=self.envelope["qa_id"], attempt=self.envelope["attempt"], route_target=self.envelope["expected_output"]["route_target"])
        return {"input_package": record, "receipt": receipt, "next_dispatch": {"target": self.envelope["expected_output"]["route_target"], "input_ref": reference, "receipt_hash": receipt["content_hash"]}}
