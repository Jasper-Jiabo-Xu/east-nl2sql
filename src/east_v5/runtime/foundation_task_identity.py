"""Root-bound task identities for the three Foundation production nodes.

The task runner never receives an identity dictionary from a caller.  This
module derives the complete identity from root-local sealed launch inputs and
the frozen v12 agent matrix, then signs an immutable registry entry with a
dedicated local key.  It is deliberately unavailable to generic callers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any

from east_v5.governance import ContractError, canonical_bytes, sha256
from east_v5.runtime.bootstrap import RuntimeBootstrap, RuntimeBootstrapError
from east_v5.runtime.foundation_task_context import current_foundation_task

_KEY = ".foundation-task-identity-v1.key"
_DIR = "foundation-task-identities-v1"
_INPUTS = "foundation-launch-inputs-v1.json"
_AGENTS = {
    "241": ("7df640f9-973f-4c46-8302-df1256f60146", "0e5e9dd9-5135-4937-bb03-92b77adb8395"),
    "242": ("4e801c18-7048-4227-a5c7-515f51a5e5ba", "0e5e9dd9-5135-4937-bb03-92b77adb8395"),
    "260": ("f89e7039-e213-4e1e-9204-64f7ce69ac1c", "0e5e9dd9-5135-4937-bb03-92b77adb8395"),
}


class FoundationTaskIdentityError(ContractError):
    pass


def _fail(code: str) -> None:
    raise FoundationTaskIdentityError(code)


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoundationTaskIdentityError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


class FoundationTaskIdentityIssuer:
    def __init__(self, bootstrap: RuntimeBootstrap) -> None:
        if not isinstance(bootstrap, RuntimeBootstrap):
            _fail("FOUNDATION_TASK_IDENTITY_BOOTSTRAP_REQUIRED")
        self.bootstrap = bootstrap

    @staticmethod
    def _private(path: Path, mode: int, code: str) -> None:
        try:
            if path.is_symlink() or not path.exists() or stat.S_IMODE(path.stat().st_mode) != mode:
                _fail(code)
        except OSError as exc:
            raise FoundationTaskIdentityError(code) from exc

    def _key(self, root: Path, *, create: bool) -> bytes:
        path = root / _KEY
        if path.exists() or path.is_symlink():
            self._private(path, 0o600, "FOUNDATION_TASK_IDENTITY_KEY_UNSAFE")
            value = path.read_bytes()
            if len(value) != 32:
                _fail("FOUNDATION_TASK_IDENTITY_KEY_UNSAFE")
            return value
        if not create:
            _fail("FOUNDATION_TASK_IDENTITY_REQUIRED")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(os.urandom(32))
            return self._key(root, create=False)
        except FileExistsError:
            return self._key(root, create=False)
        except OSError as exc:
            raise FoundationTaskIdentityError("FOUNDATION_TASK_IDENTITY_KEY_UNAVAILABLE") from exc

    def _expected(self, *, create_key: bool) -> tuple[Path, Path, dict[str, Any]]:
        """Reconstruct this process's sole, verified Multica task identity."""
        task = current_foundation_task()
        agent_id = task.role
        try:
            evidence = self.bootstrap.preflight()
        except RuntimeBootstrapError as exc:
            raise FoundationTaskIdentityError(str(exc)) from exc
        root = self.bootstrap.resolve_runtime_root(); self._private(root, 0o700, "FOUNDATION_TASK_IDENTITY_ROOT_UNSAFE")
        marker = _load(root / "daemon-root-binding-v12.json", "FOUNDATION_TASK_IDENTITY_MARKER_INVALID")
        if marker != {"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": evidence.root_binding_id}:
            _fail("FOUNDATION_TASK_IDENTITY_MARKER_INVALID")
        inputs = _load(root / _INPUTS, "FOUNDATION_TASK_IDENTITY_INPUTS_REQUIRED")
        bare = {key: value for key, value in inputs.items() if key not in {"inputs_sha256", "attestation"}}
        parent_key = root / ".foundation-parent-chain-materializer-v1.key"
        self._private(parent_key, 0o600, "FOUNDATION_TASK_IDENTITY_INPUTS_INVALID")
        if (inputs.get("inputs_sha256") != sha256(bare)
                or not hmac.compare_digest(str(inputs.get("attestation", "")), hmac.new(parent_key.read_bytes(), canonical_bytes(bare), hashlib.sha256).hexdigest())):
            _fail("FOUNDATION_TASK_IDENTITY_INPUTS_INVALID")
        uuid, runtime = _AGENTS[agent_id]
        if (task.agent_id, task.runtime_id) != (uuid, runtime):
            _fail("FOUNDATION_TASK_IDENTITY_TASK_DRIFT")
        context = self.bootstrap.declaration["runtime_context"]
        # The signed identity's issue/run/attempt are the *current Multica
        # task* coordinates.  The EAS-19 package coordinates are retained
        # separately solely to bind the sealed input, never to impersonate a
        # task or make a cross-task receipt reusable.
        body = {
            "schema_version": "foundation-task-identity/v1",
            "workspace_id": context["workspace_id"], "project_id": context["project_id"],
            "issue_id": task.issue_id, "task_id": task.task_id,
            "agent_id": agent_id, "agent_uuid": uuid, "runtime_id": runtime,
            "run_id": task.task_id, "attempt": task.attempt,
            "input_issue_key": inputs.get("issue_id"), "input_run_id": inputs.get("run_id"),
            "input_attempt": inputs.get("attempt"),
            "root_binding_id": evidence.root_binding_id, "inputs_sha256": inputs.get("inputs_sha256"),
            "git_head": evidence.candidate_head_sha,
        }
        body["content_hash"] = sha256(body)
        key = self._key(root, create=create_key)
        record = {**body, "attestation": hmac.new(key, canonical_bytes(body), hashlib.sha256).hexdigest()}
        directory = root / _DIR
        return directory, directory / f"{agent_id}-{task.task_id}.json", record

    def issue(self) -> dict[str, Any]:
        directory, path, record = self._expected(create_key=True)
        directory.mkdir(mode=0o700, exist_ok=True); self._private(directory, 0o700, "FOUNDATION_TASK_IDENTITY_REGISTRY_UNSAFE")
        if path.exists() or path.is_symlink():
            self._private(path, 0o600, "FOUNDATION_TASK_IDENTITY_REGISTRY_UNSAFE")
            if _load(path, "FOUNDATION_TASK_IDENTITY_REGISTRY_INVALID") != record:
                _fail("FOUNDATION_TASK_IDENTITY_REPLAY_DRIFT")
        else:
            temporary = path.with_suffix(".tmp")
            with temporary.open("wb") as output:
                os.chmod(temporary, 0o600); output.write(canonical_bytes(record)); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, path)
        if _load(path, "FOUNDATION_TASK_IDENTITY_REGISTRY_INVALID") != record:
            _fail("FOUNDATION_TASK_IDENTITY_READBACK_DRIFT")
        return record

    def load(self) -> dict[str, Any]:
        directory, path, expected = self._expected(create_key=False)
        self._private(directory, 0o700, "FOUNDATION_TASK_IDENTITY_REQUIRED")
        self._private(path, 0o600, "FOUNDATION_TASK_IDENTITY_REQUIRED")
        actual = _load(path, "FOUNDATION_TASK_IDENTITY_REGISTRY_INVALID")
        if actual != expected:
            _fail("FOUNDATION_TASK_IDENTITY_DRIFT")
        return actual
