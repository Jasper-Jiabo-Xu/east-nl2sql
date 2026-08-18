"""Small standard-library-only immutable registry for the v4 controller."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
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
    def __init__(self, runtime_root: Path, issue_id: str, run_id: str, attempt: int):
        if not runtime_root.is_absolute() or attempt not in {1, 2, 3}:
            raise ContractError("RUNTIME_REGISTRY_ROOT_INVALID")
        self.directory = runtime_root / "east-v5-runtime" / "issues" / issue_id / run_id / str(attempt)
        self.path = self.directory / "artifact-registry.json"
        self.lock_path = self.directory / ".artifact-registry.lock"

    @contextmanager
    def _state(self) -> Iterator[dict[str, Any]]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"schema_version": "EAST-V5-RUNTIME-REGISTRY/v4", "records": []}
            yield state
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _save(self, state: dict[str, Any]) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".registry-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(canonical_bytes(state)); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ContractError("RUNTIME_REGISTRY_WRITE_FAILED") from exc

    def _validate(self, package: dict[str, Any]) -> None:
        if set(package) != {"envelope", "payload"} or not isinstance(package["envelope"], dict):
            raise ContractError("RUNTIME_OUTPUT_TRANSPORT_INVALID")
        envelope = package["envelope"]
        required = {"artifact_id", "artifact_type", "run_id", "qa_id", "version", "schema_version", "content_hash", "attempt_no", "producer_id", "status", "trace_id"}
        if not required.issubset(envelope) or envelope.get("schema_version") != "COMMON-ENVELOPE/v1" or envelope.get("attempt_no") not in {1, 2, 3} or envelope.get("status") != "candidate":
            raise ContractError("RUNTIME_OUTPUT_ENVELOPE_REJECTED")
        if not isinstance(envelope["content_hash"], str) or len(envelope["content_hash"]) != 64 or envelope["content_hash"] != content_hash(envelope, package["payload"]):
            raise ContractError("RUNTIME_OUTPUT_HASH_DRIFT")

    def register(self, package: dict[str, Any]) -> dict[str, Any]:
        self._validate(package)
        reference = artifact_ref(package["envelope"])
        with self._state() as state:
            existing = next((record for record in state["records"] if artifact_ref(record["envelope"]) == reference), None)
            same_id_version = next((record for record in state["records"] if (record["envelope"]["artifact_id"], record["envelope"]["version"]) == (reference["artifact_id"], reference["version"])), None)
            if existing is not None:
                if existing != package:
                    raise ContractError("RUNTIME_REGISTRY_CONFLICT")
                return reference
            if same_id_version is not None:
                raise ContractError("RUNTIME_REGISTRY_CONFLICT")
            state["records"].append(package)
            self._save(state)
        return reference

    def resolve(self, reference: dict[str, Any]) -> dict[str, Any]:
        if set(reference) != {"artifact_id", "version", "content_hash"}:
            raise ContractError("RUNTIME_INPUT_REF_INVALID")
        with self._state() as state:
            record = next((item for item in state["records"] if artifact_ref(item["envelope"]) == reference), None)
            if record is None:
                raise ContractError("RUNTIME_INPUT_RESOLUTION_REJECTED")
            return json.loads(json.dumps(record))
