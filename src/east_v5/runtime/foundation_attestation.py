"""Controlled-data-plane attestation for real Foundation 241 invocations.

The service is constructed only from a bootstrapped :class:`RuntimeAdapter`.
It keeps a per-runtime HMAC key and opaque invocation evidence in the 0700
runtime root; neither is a Git artifact nor part of a business package.  241
can ask this service to attest its already chosen output.  242 receives only
the verifier capability and can never alter either data or evidence.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any

from east_v5.artifacts import artifact_ref
from east_v5.governance import ContractError, canonical_bytes, sha256
from east_v5.agents.foundation_contract import APPROVED_241_AGENT_UUID, APPROVED_241_RUNTIME_ID, APPROVED_242_AGENT_UUID


_LEDGER_DIR = "foundation-attestation-v1"
_KEY_NAME = "issuer.key"
_EVIDENCE_DIR = "receipts"
_RECEIPT_KEYS = {
    "schema_version", "agent_uuid", "runtime_id", "invocation_id", "task_ref", "run_id", "qa_id",
    "trace_id", "attempt_no", "input_context_ref", "output_hash", "runtime_attestation",
}


def _fail(code: str) -> None:
    raise ContractError(code)


def _canonical(value: Any) -> bytes:
    return canonical_bytes(value)


class FoundationRuntimeAttestationService:
    """Mint/verify Foundation receipts backed by the controlled runtime root.

    The constructor receives the already verified task identity from
    ``RuntimeAdapter``.  Direct callers cannot mint a 241 receipt because only
    the adapter has completed the bootstrap and task-envelope gates.
    """

    def __init__(
        self, runtime_root: Path, *, task_id: str, issue_id: str, target_agent_id: str,
        target_agent_uuid: str, runtime_id: str, run_id: str, qa_id: str | None,
        trace_id: str, attempt_no: int, mode: str,
    ) -> None:
        if not all(isinstance(value, str) and value for value in (task_id, issue_id, target_agent_id, target_agent_uuid, runtime_id, run_id, trace_id)):
            _fail("FOUNDATION_RUNTIME_IDENTITY_INVALID")
        if qa_id is not None and not isinstance(qa_id, str):
            _fail("FOUNDATION_RUNTIME_IDENTITY_INVALID")
        if mode != "foundation" or attempt_no not in (1, 2, 3):
            _fail("FOUNDATION_RUNTIME_CONTEXT_INVALID")
        if target_agent_id not in {"241", "242"} or runtime_id != APPROVED_241_RUNTIME_ID:
            _fail("FOUNDATION_RUNTIME_CALLER_FORBIDDEN")
        if (target_agent_id, target_agent_uuid) not in {
            ("241", APPROVED_241_AGENT_UUID), ("242", APPROVED_242_AGENT_UUID),
        }:
            _fail("FOUNDATION_RUNTIME_CALLER_FORBIDDEN")
        self.runtime_root = Path(runtime_root).resolve()
        self.task_id, self.issue_id, self.target_agent_id = task_id, issue_id, target_agent_id
        self.target_agent_uuid, self.runtime_id = target_agent_uuid, runtime_id
        self.run_id, self.qa_id, self.trace_id, self.attempt_no = run_id, qa_id, trace_id, attempt_no
        self._ledger = self._prepare_ledger()

    def _prepare_ledger(self) -> Path:
        try:
            if not self.runtime_root.is_dir() or self.runtime_root.is_symlink() or stat.S_IMODE(self.runtime_root.stat().st_mode) != 0o700:
                _fail("FOUNDATION_RUNTIME_ROOT_UNSAFE")
            ledger = self.runtime_root / _LEDGER_DIR
            ledger.mkdir(mode=0o700, exist_ok=True)
            if ledger.is_symlink() or stat.S_IMODE(ledger.stat().st_mode) != 0o700:
                _fail("FOUNDATION_RUNTIME_LEDGER_UNSAFE")
            evidence = ledger / _EVIDENCE_DIR
            evidence.mkdir(mode=0o700, exist_ok=True)
            if evidence.is_symlink() or stat.S_IMODE(evidence.stat().st_mode) != 0o700:
                _fail("FOUNDATION_RUNTIME_LEDGER_UNSAFE")
        except OSError as exc:
            raise ContractError("FOUNDATION_RUNTIME_LEDGER_UNAVAILABLE") from exc
        self._ensure_key(ledger / _KEY_NAME)
        return ledger

    @staticmethod
    def _ensure_key(path: Path) -> None:
        if path.exists():
            if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600 or len(path.read_bytes()) != 32:
                _fail("FOUNDATION_RUNTIME_KEY_INVALID")
            return
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(os.urandom(32))
        except FileExistsError:
            FoundationRuntimeAttestationService._ensure_key(path)
        except OSError as exc:
            raise ContractError("FOUNDATION_RUNTIME_KEY_UNAVAILABLE") from exc

    def _key(self) -> bytes:
        path = self._ledger / _KEY_NAME
        try:
            key = path.read_bytes()
            if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600 or len(key) != 32:
                _fail("FOUNDATION_RUNTIME_KEY_INVALID")
            return key
        except OSError as exc:
            raise ContractError("FOUNDATION_RUNTIME_KEY_UNAVAILABLE") from exc

    @staticmethod
    def _signature(key: bytes, evidence: dict[str, Any]) -> str:
        return hmac.new(key, _canonical(evidence), hashlib.sha256).hexdigest()

    def _expected(self, task: dict[str, Any], context: dict[str, Any], groups: list[dict[str, Any]], traces: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": "v5.foundation-241-invocation-receipt/v1",
            "agent_uuid": APPROVED_241_AGENT_UUID,
            "runtime_id": APPROVED_241_RUNTIME_ID,
            "task_ref": artifact_ref(task["envelope"]), "run_id": self.run_id, "qa_id": self.qa_id,
            "trace_id": self.trace_id, "attempt_no": self.attempt_no,
            "input_context_ref": artifact_ref(context["envelope"]),
            "output_hash": sha256({"data_groups": groups, "selection_traces": traces}),
        }

    def mint_241_receipt(
        self, task: dict[str, Any], context: dict[str, Any], groups: list[dict[str, Any]], traces: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Attest a real 241 output after its business choices are complete."""
        if self.target_agent_id != "241":
            _fail("FOUNDATION_RUNTIME_MINT_FORBIDDEN")
        expected = self._expected(task, context, groups, traces)
        evidence = {
            "schema_version": "v5.foundation-runtime-attestation/v1", "task_id": self.task_id,
            "issue_id": self.issue_id, **expected,
        }
        evidence_id = sha256(evidence)
        signed = {**evidence, "evidence_id": evidence_id, "signature": self._signature(self._key(), evidence)}
        target = self._ledger / _EVIDENCE_DIR / f"{evidence_id}.json"
        try:
            if target.exists():
                existing = json.loads(target.read_text(encoding="utf-8"))
                if existing != signed:
                    _fail("FOUNDATION_RUNTIME_EVIDENCE_DRIFT")
            else:
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(_canonical(signed))
        except FileExistsError:
            return self.mint_241_receipt(task, context, groups, traces)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContractError("FOUNDATION_RUNTIME_EVIDENCE_UNAVAILABLE") from exc
        return {**expected, "invocation_id": evidence_id, "runtime_attestation": evidence_id}

    def verify(self, receipt: dict[str, Any], expected: dict[str, Any]) -> None:
        """Verify a receipt against an immutable data-plane evidence record."""
        if self.target_agent_id not in {"241", "242"} or not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
            _fail("FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED")
        if any(receipt.get(key) != value for key, value in expected.items()):
            _fail("FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED")
        evidence_id = receipt.get("runtime_attestation")
        if not isinstance(evidence_id, str) or receipt.get("invocation_id") != evidence_id or len(evidence_id) != 64:
            _fail("FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED")
        target = self._ledger / _EVIDENCE_DIR / f"{evidence_id}.json"
        try:
            if target.is_symlink() or stat.S_IMODE(target.stat().st_mode) != 0o600:
                _fail("FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED")
            evidence = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContractError("FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED") from exc
        evidence_body = {key: value for key, value in evidence.items() if key not in {"evidence_id", "signature"}}
        if evidence.get("evidence_id") != evidence_id or evidence_id != sha256(evidence_body) or evidence.get("signature") != self._signature(self._key(), evidence_body):
            _fail("FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED")
        if any(evidence.get(key) != value for key, value in expected.items()):
            _fail("FOUNDATION_241_INVOCATION_RECEIPT_UNTRUSTED")
