"""The sole production issuer for Foundation's fixed component 000."""
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

_PRODUCER = "east-v5-foundation-fixed-000-issuer/v1"
_KEY = ".foundation-fixed-component-000-v1.key"
_DIR = "foundation-fixed-component-000-v1"
_INPUTS = "foundation-launch-inputs-v1.json"


class FoundationFixedComponent000Error(ContractError):
    pass


def _fail(code: str) -> None:
    raise FoundationFixedComponent000Error(code)


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoundationFixedComponent000Error(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


class FoundationFixedComponent000Issuer:
    """No-argument issuer rooted in RuntimeBootstrap's verified context."""

    def __init__(self, bootstrap: RuntimeBootstrap) -> None:
        if not isinstance(bootstrap, RuntimeBootstrap):
            _fail("FOUNDATION_000_BOOTSTRAP_REQUIRED")
        self.bootstrap = bootstrap

    @staticmethod
    def _private(path: Path, mode: int, code: str) -> None:
        try:
            if path.is_symlink() or not path.exists() or stat.S_IMODE(path.stat().st_mode) != mode:
                _fail(code)
        except OSError as exc:
            raise FoundationFixedComponent000Error(code) from exc

    def _key(self, root: Path) -> bytes:
        path = root / _KEY
        try:
            if path.exists() or path.is_symlink():
                self._private(path, 0o600, "FOUNDATION_000_ISSUER_KEY_UNSAFE")
                key = path.read_bytes()
            else:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(os.urandom(32))
                key = path.read_bytes()
            if len(key) != 32:
                _fail("FOUNDATION_000_ISSUER_KEY_UNSAFE")
            return key
        except FileExistsError:
            return self._key(root)
        except OSError as exc:
            raise FoundationFixedComponent000Error("FOUNDATION_000_ISSUER_KEY_UNAVAILABLE") from exc

    def _expected_record(self, *, create_key: bool) -> tuple[Path, Path, dict[str, Any]]:
        """Recompute the only record this root may contain for the run.

        This routine intentionally consumes no caller supplied value.  ``issue``
        is the sole mutator; ``load`` below only uses this reconstruction to
        reject a substituted registry entry before handing it to 241.
        """
        try:
            evidence = self.bootstrap.preflight()
        except RuntimeBootstrapError as exc:
            raise FoundationFixedComponent000Error(str(exc)) from exc
        if "V5_RUNTIME_ROOT" in self.bootstrap.environ:
            _fail("FOUNDATION_000_ENV_OVERRIDE_FORBIDDEN")
        root = self.bootstrap.resolve_runtime_root(); self._private(root, 0o700, "FOUNDATION_000_ROOT_UNSAFE")
        marker = _load(root / "daemon-root-binding-v12.json", "FOUNDATION_000_ROOT_MARKER_INVALID")
        if marker != {"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": evidence.root_binding_id}:
            _fail("FOUNDATION_000_ROOT_MARKER_INVALID")
        inputs = _load(root / _INPUTS, "FOUNDATION_000_INPUTS_REQUIRED")
        bare_inputs = {key: value for key, value in inputs.items() if key not in {"inputs_sha256", "attestation"}}
        materializer_key = root / ".foundation-parent-chain-materializer-v1.key"
        self._private(materializer_key, 0o600, "FOUNDATION_000_PARENT_CHAIN_UNAVAILABLE")
        if inputs.get("inputs_sha256") != sha256(bare_inputs) or not hmac.compare_digest(str(inputs.get("attestation", "")), hmac.new(materializer_key.read_bytes(), canonical_bytes(bare_inputs), hashlib.sha256).hexdigest()):
            _fail("FOUNDATION_000_INPUTS_INVALID")
        materialization = _load(root / "foundation-parent-chain-materialization-receipt-v2.json", "FOUNDATION_000_MATERIALIZER_UNAVAILABLE")
        materialization_bare = {key: value for key, value in materialization.items() if key not in {"receipt_sha256", "attestation"}}
        if (materialization.get("schema_version") != "foundation-parent-chain-materialization-receipt/v2"
                or materialization.get("root_binding_id") != evidence.root_binding_id
                or materialization.get("receipt_sha256") != sha256(materialization_bare)
                or not hmac.compare_digest(str(materialization.get("attestation", "")), hmac.new(materializer_key.read_bytes(), canonical_bytes({**materialization_bare, "receipt_sha256": materialization["receipt_sha256"]}), hashlib.sha256).hexdigest())):
            _fail("FOUNDATION_000_MATERIALIZER_INVALID")
        container = root / "foundation-parent-chain-container-v1"
        self._private(container, 0o700, "FOUNDATION_000_MATERIALIZER_UNAVAILABLE")
        manifest = _load(container / "foundation-parent-chain-container-manifest.json", "FOUNDATION_000_MATERIALIZER_UNAVAILABLE")
        recovery = _load(root / "foundation-parent-chain-recovery-v1/recovery-receipt.json", "FOUNDATION_000_RECOVERY_UNAVAILABLE")
        if (manifest.get("root_binding_id") != evidence.root_binding_id or recovery.get("root_binding_id") != evidence.root_binding_id
                or materialization.get("container_sha256") != manifest.get("container_sha256")):
            _fail("FOUNDATION_000_PARENT_CHAIN_DRIFT")
        from east_v5.runtime.foundation_repo_launcher import FoundationRepoLauncher
        launcher = FoundationRepoLauncher(self.bootstrap)
        bundle = launcher._install_bundle(root)
        _claims, graph, _manifest = launcher._claims(bundle)
        fixed = graph.get("fixed_components")
        if fixed != {"000": {"receipt_schema": "east-v5-fixed-component-receipt/v1", "capability": "constraint_asset_query"}}:
            _fail("FOUNDATION_000_GRAPH_DRIFT")
        config_sha = hashlib.sha256((bundle / "config/full-runtime-graph.json").read_bytes()).hexdigest()
        receipt = {"schema_version": "east-v5-fixed-component-receipt/v1", "component_id": "000", "root_binding_id": evidence.root_binding_id, "config_sha256": config_sha}
        receipt["receipt_sha256"] = sha256(receipt)
        replay = sha256({"producer": _PRODUCER, "root": evidence.root_binding_id, "inputs": inputs["inputs_sha256"], "recovery": recovery.get("receipt_sha256"), "manifest": manifest.get("container_sha256"), "graph": config_sha, "head": evidence.candidate_head_sha})
        body = {"schema_version": "east-v5-fixed-component-production-record/v1", "producer_id": _PRODUCER, "receipt": receipt, "materializer_receipt_hash": materialization.get("receipt_sha256"), "materializer_manifest_hash": manifest.get("container_sha256"), "recovery_receipt_hash": recovery.get("receipt_sha256"), "inputs_sha256": inputs["inputs_sha256"], "resolver_universe_hash": inputs.get("resolver_universe_hash"), "git_head": evidence.candidate_head_sha, "skill_manifest_sha256": hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest(), "root_binding_id": evidence.root_binding_id, "run_id": inputs.get("run_id"), "attempt": inputs.get("attempt"), "issued_replay_key": replay}
        body["content_hash"] = sha256(body)
        key_path = root / _KEY
        if create_key:
            key = self._key(root)
        else:
            self._private(key_path, 0o600, "FOUNDATION_000_ISSUER_KEY_UNSAFE")
            key = key_path.read_bytes()
            if len(key) != 32:
                _fail("FOUNDATION_000_ISSUER_KEY_UNSAFE")
        record = {**body, "attestation": hmac.new(key, canonical_bytes(body), hashlib.sha256).hexdigest()}
        directory = root / _DIR
        path = directory / f"{replay}.json"
        return directory, path, record

    def issue(self) -> dict[str, Any]:
        """Atomically register the fixed 000 receipt and production proof."""
        directory, path, record = self._expected_record(create_key=True)
        directory.mkdir(mode=0o700, exist_ok=True); self._private(directory, 0o700, "FOUNDATION_000_REGISTRY_UNSAFE")
        if path.exists() or path.is_symlink():
            self._private(path, 0o600, "FOUNDATION_000_REGISTRY_UNSAFE")
            if _load(path, "FOUNDATION_000_REGISTRY_INVALID") != record:
                _fail("FOUNDATION_000_REPLAY_DRIFT")
        else:
            tmp = path.with_suffix(".tmp")
            with tmp.open("wb") as out:
                os.chmod(tmp, 0o600); out.write(canonical_bytes(record)); out.flush(); os.fsync(out.fileno())
            os.replace(tmp, path); self._private(path, 0o600, "FOUNDATION_000_REGISTRY_UNSAFE")
        # Registry read-back is part of signing, not a best-effort diagnostic.
        if _load(path, "FOUNDATION_000_REGISTRY_INVALID") != record:
            _fail("FOUNDATION_000_REGISTRY_READBACK_DRIFT")
        return record

    def load(self) -> dict[str, Any]:
        """Read and verify the already sealed 000 production record.

        A 241 launcher reaches this method only through Bootstrap.  It never
        gets a receipt-construction API and it cannot create a missing record.
        """
        directory, path, expected = self._expected_record(create_key=False)
        self._private(directory, 0o700, "FOUNDATION_000_REGISTRY_REQUIRED")
        self._private(path, 0o600, "FOUNDATION_000_REGISTRY_REQUIRED")
        actual = _load(path, "FOUNDATION_000_REGISTRY_INVALID")
        if actual != expected:
            _fail("FOUNDATION_000_PRODUCTION_RECORD_DRIFT")
        return actual
