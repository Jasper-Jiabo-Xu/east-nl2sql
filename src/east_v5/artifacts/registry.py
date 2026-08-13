"""Immutable local-only COMMON-ENVELOPE registry; it never writes Git or a formal database."""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from east_v5.governance import ContractError, attempt_path, canonical_bytes, load_json, verify_governed_manifest
from east_v5.artifacts.schema import validate_common_envelope_schema

SCHEMA_VERSION = "COMMON-ENVELOPE/v1"
STATUSES = {"candidate", "pending_validation", "validated", "pending_review", "approved", "rejected", "blocked_manual", "released"}
FORWARD = {"candidate": {"pending_validation", "rejected", "blocked_manual"}, "pending_validation": {"validated", "rejected", "blocked_manual"}, "validated": {"pending_review", "rejected", "blocked_manual"}, "pending_review": {"approved", "rejected", "blocked_manual"}, "approved": {"released", "rejected", "blocked_manual"}}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
REF_KEYS = {"artifact_id", "version", "content_hash"}
ENVELOPE_KEYS = {"artifact_id", "artifact_type", "run_id", "qa_id", "version", "schema_version", "content_hash", "supersedes_ref", "attempt_no", "producer_id", "parent_artifact_refs", "input_hashes", "status", "mode", "created_at", "trace_id", "storage_locator"}


def _fail(code: str) -> None:
    raise ContractError(code)


def artifact_ref(envelope: dict[str, Any]) -> dict[str, Any]:
    return {key: envelope[key] for key in ("artifact_id", "version", "content_hash")}


def content_hash(envelope: dict[str, Any], payload: Any) -> str:
    """Identity hash: canonical complete package excluding only self hash and locator."""
    identity_envelope = {key: value for key, value in envelope.items() if key not in {"content_hash", "storage_locator"}}
    return hashlib.sha256(canonical_bytes({"envelope": identity_envelope, "payload": payload})).hexdigest()


def _check_ref(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != REF_KEYS:
        _fail(f"UNKNOWN_FIELD:{label}")
    if not isinstance(value["artifact_id"], str) or not ID.fullmatch(value["artifact_id"]):
        _fail("ARTIFACT_ID_INVALID")
    if not isinstance(value["version"], int) or isinstance(value["version"], bool) or value["version"] < 1:
        _fail("VERSION_INVALID")
    if not isinstance(value["content_hash"], str) or not HASH.fullmatch(value["content_hash"]):
        _fail("CONTENT_HASH_INVALID")


def allowed_artifact_types(repo_root: Path) -> set[str]:
    catalog = load_json(repo_root / "config" / "v5-package-catalog.json")
    return {entry["id"] for entry in catalog["packages"]}


def validate_envelope(repo_root: Path, envelope: dict[str, Any], payload: Any) -> None:
    """Strictly validate common fields without interpreting the payload's business semantics."""
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_KEYS:
        _fail("UNKNOWN_FIELD:COMMON_ENVELOPE")
    if not isinstance(envelope["artifact_id"], str) or not ID.fullmatch(envelope["artifact_id"]): _fail("ARTIFACT_ID_INVALID")
    if not isinstance(envelope["artifact_type"], str) or envelope["artifact_type"] not in allowed_artifact_types(repo_root): _fail("ARTIFACT_TYPE_INVALID")
    if not isinstance(envelope["run_id"], str) or not RUN.fullmatch(envelope["run_id"]): _fail("RUN_ID_INVALID")
    if envelope["schema_version"] != SCHEMA_VERSION: _fail("SCHEMA_VERSION_UNSUPPORTED")
    if not isinstance(envelope["version"], int) or isinstance(envelope["version"], bool) or envelope["version"] < 1: _fail("VERSION_INVALID")
    if not isinstance(envelope["attempt_no"], int) or isinstance(envelope["attempt_no"], bool) or envelope["attempt_no"] not in (1, 2, 3): _fail("ATTEMPT_OUT_OF_RANGE")
    if envelope["mode"] not in {"question_sql", "event_data", "foundation"}: _fail("MODE_INVALID")
    # An independent Foundation task has no QA identifier; a recovery Foundation
    # task carries one.  Both are valid.  Non-Foundation event/question flows
    # always require a QA identifier.
    if envelope["mode"] != "foundation" and (not isinstance(envelope["qa_id"], str) or not envelope["qa_id"]): _fail("QA_ID_REQUIRED")
    if not isinstance(envelope["producer_id"], str) or not envelope["producer_id"] or not isinstance(envelope["trace_id"], str) or not envelope["trace_id"]: _fail("REQUIRED_VALUE_MISSING")
    if envelope["status"] not in STATUSES: _fail("STATUS_INVALID")
    if envelope["status"] == "released": _fail("FORMAL_RELEASE_FORBIDDEN")
    if envelope["storage_locator"] is not None and (not isinstance(envelope["storage_locator"], str) or not envelope["storage_locator"]): _fail("LOCATOR_INVALID")
    if not isinstance(envelope["content_hash"], str) or not HASH.fullmatch(envelope["content_hash"]): _fail("CONTENT_HASH_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(envelope["created_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError): _fail("TIMESTAMP_INVALID")
    if parsed.tzinfo is None: _fail("TIMESTAMP_INVALID")
    if envelope["content_hash"] != content_hash(envelope, payload): _fail("CONTENT_HASH_DRIFT")
    if not isinstance(envelope["parent_artifact_refs"], list) or not isinstance(envelope["input_hashes"], list): _fail("SCHEMA_VALIDATION_FAILED")
    for item in envelope["parent_artifact_refs"]: _check_ref(item, "parent_artifact_ref")
    if envelope["input_hashes"] != [item["content_hash"] for item in envelope["parent_artifact_refs"]]: _fail("INPUT_HASH_ORDER_INVALID")
    if len({(x["artifact_id"], x["version"], x["content_hash"]) for x in envelope["parent_artifact_refs"]}) != len(envelope["parent_artifact_refs"]): _fail("PARENT_DUPLICATE")
    if envelope["supersedes_ref"] is not None: _check_ref(envelope["supersedes_ref"], "supersedes_ref")
    validate_common_envelope_schema(repo_root, envelope)


class ArtifactRegistry:
    """A small JSON registry whose single atomic state file contains its audit trail."""
    def __init__(self, repo_root: Path, roots: dict[str, Any], issue_id: str, run_id: str, attempt: int):
        verify_governed_manifest(repo_root)
        self.repo_root, self.roots = repo_root.resolve(), roots
        self.directory = attempt_path(roots, issue_id, run_id, attempt)
        self.path = self.directory / "artifact-registry.json"
        self.lock_path = self.directory / ".artifact-registry.lock"

    @property
    def run_directory(self) -> Path:
        return self.directory.parent

    def _locator_path(self, locator: str | None) -> Path | None:
        """A locator is an existing file owned by this issue/run/attempt only."""
        if locator is None:
            return None
        if not isinstance(locator, str) or not locator:
            _fail("LOCATOR_INVALID")
        path = Path(locator)
        if not path.is_absolute():
            _fail("LOCATOR_INVALID")
        resolved = path.resolve(strict=False)
        runtime = Path(self.roots["runtime_root"]).resolve()
        if runtime not in resolved.parents:
            _fail("LOCATOR_OUT_OF_RUNTIME_ROOT")
        # This exact scope excludes the Git control plane, the reference root,
        # every other issue/attempt, and runtime_root/vnext/05_新版本交付层.
        if self.directory.resolve() not in resolved.parents:
            _fail("LOCATOR_OUT_OF_ATTEMPT_SCOPE")
        if not resolved.is_file():
            _fail("LOCATOR_MISSING")
        return resolved

    def validate_locator(self, locator: str | None) -> str | None:
        resolved = self._locator_path(locator)
        return str(resolved) if resolved else None

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if self.path.exists():
                    state = load_json(self.path)
                else:
                    state = {"schema_version": "COMMON-ENVELOPE-REGISTRY/v1", "artifacts": [], "audit_events": []}
                yield state
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _save(self, state: dict[str, Any]) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".registry-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(canonical_bytes(state)); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except OSError:
            try: os.unlink(temporary)
            except FileNotFoundError: pass
            _fail("RUNTIME_TEMPORARY")

    @staticmethod
    def _append_audit(state: dict[str, Any], event: dict[str, Any]) -> None:
        state["audit_events"].append(event)

    def _audit(self, state: dict[str, Any], event: dict[str, Any]) -> None:
        try:
            self._append_audit(state, event)
        except OSError:
            _fail("RUNTIME_TEMPORARY")

    @staticmethod
    def _find(state: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any] | None:
        for record in state["artifacts"]:
            if artifact_ref(record["envelope"]) == reference: return record
        return None

    @classmethod
    def _parent_reaches_artifact(cls, state: dict[str, Any], reference: dict[str, Any], artifact_id: str, seen: set[tuple[str, int, str]] | None = None) -> bool:
        """Reject a direct or transitive parent edge back to the child artifact."""
        key = (reference["artifact_id"], reference["version"], reference["content_hash"])
        if seen is None: seen = set()
        if key in seen: return True
        seen.add(key)
        if reference["artifact_id"] == artifact_id: return True
        record = cls._find(state, reference)
        if record is None: return False
        return any(cls._parent_reaches_artifact(state, parent, artifact_id, seen.copy()) for parent in record["envelope"]["parent_artifact_refs"])

    def register(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        validate_envelope(self.repo_root, envelope, payload)
        if envelope["status"] != "candidate": _fail("INITIAL_STATUS_INVALID")
        self._locator_path(envelope["storage_locator"])
        with self._locked_state() as state:
            same_version = [r for r in state["artifacts"] if r["envelope"]["artifact_id"] == envelope["artifact_id"] and r["envelope"]["version"] == envelope["version"]]
            if same_version:
                existing = same_version[0]
                if existing["envelope"]["content_hash"] == envelope["content_hash"] and existing["payload"] == payload:
                    return existing["envelope"].copy()
                _fail("IDENTITY_CONTENT_CONFLICT")
            previous = [r for r in state["artifacts"] if r["envelope"]["artifact_id"] == envelope["artifact_id"]]
            expected = len(previous) + 1
            if envelope["version"] != expected: _fail("VERSION_NOT_CONTIGUOUS")
            if expected == 1:
                if envelope["supersedes_ref"] is not None: _fail("SUPERSEDES_FIRST_VERSION")
            else:
                predecessor = next(r for r in previous if r["envelope"]["version"] == expected - 1)
                if envelope["supersedes_ref"] != artifact_ref(predecessor["envelope"]): _fail("SUPERSEDES_INVALID")
            reference = artifact_ref(envelope)
            for parent in envelope["parent_artifact_refs"]:
                if self._find(state, parent) is None: _fail("PARENT_ORPHAN")
                if self._parent_reaches_artifact(state, parent, envelope["artifact_id"]): _fail("PARENT_CYCLE")
            record = {"envelope": envelope.copy(), "payload": payload}
            state["artifacts"].append(record)
            self._audit(state, {"event": "registered", "ref": reference, "attempt_no": envelope["attempt_no"]})
            self._save(state)
            return envelope.copy()

    def resolve(self, reference: dict[str, Any]) -> dict[str, Any]:
        _check_ref(reference, "artifact_ref")
        with self._locked_state() as state:
            record = self._find(state, reference)
            if record is None: _fail("ARTIFACT_NOT_FOUND")
            self._locator_path(record["envelope"]["storage_locator"])
            return {"envelope": record["envelope"].copy(), "payload": record["payload"]}

    def transition(self, reference: dict[str, Any], target: str) -> dict[str, Any]:
        if target == "released": _fail("FORMAL_RELEASE_FORBIDDEN")
        with self._locked_state() as state:
            record = self._find(state, reference)
            if record is None: _fail("ARTIFACT_NOT_FOUND")
            old = record.get("registry_status", record["envelope"]["status"])
            if target not in FORWARD.get(old, set()): _fail("STATUS_TRANSITION_INVALID")
            # Status is operational registry metadata.  It deliberately does not
            # alter the immutable stored package whose content_hash includes the
            # candidate status at registration time.
            record["registry_status"] = target
            self._audit(state, {"event": "status_changed", "ref": reference, "from": old, "to": target})
            self._save(state)
            return {**record["envelope"], "status": target}

    def migrate_locator(self, reference: dict[str, Any], locator: str | None) -> dict[str, Any]:
        self._locator_path(locator)
        with self._locked_state() as state:
            record = self._find(state, reference)
            if record is None: _fail("ARTIFACT_NOT_FOUND")
            record["envelope"]["storage_locator"] = locator
            self._audit(state, {"event": "locator_migrated", "ref": reference, "locator": locator})
            self._save(state)
            return record["envelope"].copy()

    def audit(self, reference: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._locked_state() as state:
            events = state["audit_events"]
            if reference is not None:
                _check_ref(reference, "artifact_ref")
                events = [event for event in events if event.get("ref") == reference]
            return [event.copy() for event in events]

    def record_transient_failure(self, operation: str, error_code: str) -> dict[str, Any]:
        """Record only retryable runtime failures, isolated by attempt directory.

        The third allowed attempt is terminal and deliberately cannot be retried.
        """
        policy = load_json(self.repo_root / "config" / "workflow-policy.json")
        if error_code not in policy["retryable_failure_codes"]:
            _fail("RETRY_ERROR_NOT_TRANSIENT")
        retry_path = self.run_directory / "retry-state.json"
        retry_path.parent.mkdir(parents=True, exist_ok=True)
        prior: dict[str, Any] = load_json(retry_path) if retry_path.exists() else {"failures": []}
        failures = prior["failures"]
        attempt = int(self.directory.name)
        numbers = [item.get("attempt") for item in failures]
        if len(numbers) != len(set(numbers)) or any(not isinstance(item, str) or not item.isdigit() for item in numbers):
            _fail("ATTEMPT_SEQUENCE_INVALID")
        numbers = [int(item) for item in numbers]
        if attempt in numbers:
            _fail("ATTEMPT_REPLAY_FORBIDDEN")
        if attempt in (1, 2) and numbers != list(range(1, attempt)):
            _fail("ATTEMPT_SEQUENCE_INVALID")
        if attempt == 3 and numbers and numbers != [1, 2]:
            _fail("ATTEMPT_SEQUENCE_INVALID")
        state = {"operation": operation, "error_code": error_code, "attempt": self.directory.name,
                 "status": "blocked_manual" if attempt == policy["max_attempts"] else "retryable"}
        failures.append(state)
        try:
            fd, temporary = tempfile.mkstemp(prefix=".retry-", dir=retry_path.parent)
            with os.fdopen(fd, "wb") as output:
                output.write(canonical_bytes({"failures": failures})); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, retry_path)
        except OSError as exc:
            _fail("STORAGE_FAILURE")
        return {"failures": failures}

    def lineage(self, reference: dict[str, Any]) -> dict[str, Any]:
        record = self.resolve(reference)
        return {"supersedes_ref": record["envelope"]["supersedes_ref"], "parent_artifact_refs": record["envelope"]["parent_artifact_refs"]}
