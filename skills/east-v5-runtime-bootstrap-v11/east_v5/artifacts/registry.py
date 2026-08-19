"""Daemon-root-bound, atomic publication store for the v10 controller."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from east_v5.governance import ContractError, canonical_bytes


def artifact_ref(envelope: dict[str, Any]) -> dict[str, Any]:
    return {key: envelope[key] for key in ("artifact_id", "version", "content_hash")}


def content_hash(envelope: dict[str, Any], payload: Any) -> str:
    identity = {key: value for key, value in envelope.items() if key not in {"content_hash", "storage_locator"}}
    return hashlib.sha256(canonical_bytes({"envelope": identity, "payload": payload})).hexdigest()


class ArtifactRegistry:
    """One fsync+replace transaction owns records, receipts, outbox and journal."""

    MARKER = "daemon-root-binding-v10.json"
    SCHEMA = "EAST-V5-DAEMON-ARTIFACT-PUBLICATION/v10"

    def __init__(self, runtime_root: Path, root_binding_id: str, issue_id: str, run_id: str, attempt: int):
        if not runtime_root.is_absolute() or attempt not in {1, 2, 3} or len(root_binding_id) != 64:
            raise ContractError("RUNTIME_REGISTRY_ROOT_INVALID")
        if runtime_root.is_symlink() or not runtime_root.is_dir() or runtime_root.name in {"tmp", "temp", "reference", "references"} or (runtime_root / ".git").exists() or stat.S_IMODE(runtime_root.stat().st_mode) != 0o700:
            raise ContractError("RUNTIME_DAEMON_ROOT_REJECTED")
        marker = runtime_root / self.MARKER
        if marker.is_symlink() or not marker.is_file() or stat.S_IMODE(marker.stat().st_mode) != 0o600:
            raise ContractError("RUNTIME_DAEMON_ROOT_REJECTED")
        try:
            binding = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("RUNTIME_DAEMON_ROOT_REJECTED") from exc
        if binding != {"schema_version": "east-v5-daemon-root-binding/v10", "root_binding_id": root_binding_id}:
            raise ContractError("RUNTIME_DAEMON_ROOT_BINDING_DRIFT")
        self.directory = runtime_root / "east-v5-daemon-artifacts-v10" / root_binding_id / "issues" / issue_id / run_id / str(attempt)
        self.path = self.directory / "artifact-publication-v10.json"
        self.lock_path = self.directory / ".artifact-publication-v10.lock"

    @staticmethod
    def bind_test_root(runtime_root: Path, root_binding_id: str) -> None:
        runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(runtime_root, 0o700)
        marker = runtime_root / ArtifactRegistry.MARKER
        marker.write_bytes(canonical_bytes({"schema_version": "east-v5-daemon-root-binding/v10", "root_binding_id": root_binding_id}))
        os.chmod(marker, 0o600)

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA, "records": [], "receipts": {}, "outbox": {}, "journal": {}}

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else self._empty()
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("RUNTIME_PUBLICATION_UNREADABLE") from exc
        shapes = (("records", list), ("receipts", dict), ("outbox", dict), ("journal", dict))
        if not isinstance(value, dict) or set(value) != {"schema_version", "records", "receipts", "outbox", "journal"} or value.get("schema_version") != self.SCHEMA or not all(isinstance(value[key], expected) for key, expected in shapes):
            raise ContractError("RUNTIME_PUBLICATION_SCHEMA_DRIFT")
        return value

    @contextmanager
    def _state(self) -> Iterator[dict[str, Any]]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield self._read()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _save(self, state: dict[str, Any]) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".publication-", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(canonical_bytes(state)); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise ContractError("RUNTIME_PUBLICATION_WRITE_FAILED") from exc
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _validate(self, package: dict[str, Any]) -> None:
        if set(package) != {"envelope", "payload"} or not isinstance(package["envelope"], dict):
            raise ContractError("RUNTIME_OUTPUT_TRANSPORT_INVALID")
        envelope = package["envelope"]
        required = {"artifact_id", "artifact_type", "run_id", "qa_id", "version", "schema_version", "content_hash", "attempt_no", "producer_id", "status", "trace_id"}
        if not required.issubset(envelope) or envelope.get("schema_version") != "COMMON-ENVELOPE/v1" or envelope.get("attempt_no") not in {1, 2, 3} or envelope.get("status") != "candidate":
            raise ContractError("RUNTIME_OUTPUT_ENVELOPE_REJECTED")
        if not isinstance(envelope["content_hash"], str) or len(envelope["content_hash"]) != 64 or envelope["content_hash"] != content_hash(envelope, package["payload"]):
            raise ContractError("RUNTIME_OUTPUT_HASH_DRIFT")

    def publish(self, *, package: dict[str, Any] | None, receipt: dict[str, Any], task_id: str, outbox_key: str | None, outbox_entry: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any]:
        if package is not None:
            self._validate(package)
        if not isinstance(receipt, dict) or not isinstance(task_id, str) or not isinstance(result, dict) or (outbox_key is None) != (outbox_entry is None):
            raise ContractError("RUNTIME_PUBLICATION_INVALID")
        with self._state() as state:
            prior = state["journal"].get(task_id)
            if prior is not None:
                if prior != result:
                    raise ContractError("RUNTIME_EXECUTION_JOURNAL_CONFLICT")
                return json.loads(json.dumps(prior))
            if package is not None:
                reference = artifact_ref(package["envelope"])
                existing = next((record for record in state["records"] if artifact_ref(record["envelope"]) == reference), None)
                same_id_version = next((record for record in state["records"] if (record["envelope"]["artifact_id"], record["envelope"]["version"]) == (reference["artifact_id"], reference["version"])), None)
                if existing is not None and existing != package or same_id_version is not None and existing is None:
                    raise ContractError("RUNTIME_REGISTRY_CONFLICT")
                if existing is None:
                    state["records"].append(package)
            if task_id in state["receipts"]:
                raise ContractError("RUNTIME_RECEIPT_CONFLICT")
            state["receipts"][task_id] = receipt
            if outbox_key is not None:
                prior_entry = state["outbox"].get(outbox_key)
                if prior_entry is not None and prior_entry != outbox_entry:
                    raise ContractError("RUNTIME_LAUNCH_OUTBOX_BINDING_DRIFT")
                state["outbox"][outbox_key] = outbox_entry
            state["journal"][task_id] = result
            self._save(state)
        return json.loads(json.dumps(result))

    def resolve(self, reference: dict[str, Any]) -> dict[str, Any]:
        if set(reference) != {"artifact_id", "version", "content_hash"}:
            raise ContractError("RUNTIME_INPUT_REF_INVALID")
        state = self._read()
        record = next((item for item in state["records"] if artifact_ref(item["envelope"]) == reference), None)
        if record is None:
            raise ContractError("RUNTIME_INPUT_RESOLUTION_REJECTED")
        self._validate(record)
        return json.loads(json.dumps(record))

    def receipt(self, task_id: str) -> dict[str, Any]:
        value = self._read()["receipts"].get(task_id)
        if not isinstance(value, dict):
            raise ContractError("RUNTIME_RECEIPT_RESOLUTION_REJECTED")
        return json.loads(json.dumps(value))

    def outbox_entry(self, key: str) -> dict[str, Any]:
        value = self._read()["outbox"].get(key)
        if not isinstance(value, dict):
            raise ContractError("RUNTIME_LAUNCH_OUTBOX_MISSING")
        return json.loads(json.dumps(value))

    def journal_entry(self, task_id: str) -> dict[str, Any] | None:
        value = self._read()["journal"].get(task_id)
        if value is not None and not isinstance(value, dict):
            raise ContractError("RUNTIME_EXECUTION_JOURNAL_INVALID")
        return json.loads(json.dumps(value)) if value is not None else None

    def finalize(self, task_id: str, result: dict[str, Any]) -> dict[str, Any]:
        with self._state() as state:
            previous = state["journal"].get(task_id)
            if not isinstance(previous, dict) or previous.get("stage") != "published":
                raise ContractError("RUNTIME_EXECUTION_JOURNAL_INVALID")
            state["journal"][task_id] = result
            self._save(state)
        return json.loads(json.dumps(result))

    def prepare_outbox(self, key: str, entry: dict[str, Any]) -> None:
        with self._state() as state:
            previous = state["outbox"].get(key)
            binding = {name: value for name, value in entry.items() if name != "state"}
            if previous is not None and (previous.get("state") not in {"published", "issue_id", "task_id"} or any(previous.get(name) != value for name, value in binding.items())):
                raise ContractError("RUNTIME_LAUNCH_OUTBOX_BINDING_DRIFT")
            if previous is None:
                state["outbox"][key] = entry
                self._save(state)

    def update_outbox(self, key: str, **updates: Any) -> dict[str, Any]:
        with self._state() as state:
            value = state["outbox"].get(key)
            if not isinstance(value, dict):
                raise ContractError("RUNTIME_LAUNCH_OUTBOX_MISSING")
            value.update(updates)
            self._save(state)
            return json.loads(json.dumps(value))

    def abort_outbox(self, key: str) -> None:
        with self._state() as state:
            if key in state["outbox"]:
                del state["outbox"][key]
                self._save(state)
