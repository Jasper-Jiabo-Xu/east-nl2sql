"""The sole production issuer for Foundation's fixed component 000."""
from __future__ import annotations

import hashlib
import hmac
import importlib
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
_TASK_HASH = "899ac6cdea3a9e08fa01d77102850952f9b8c83db3bf94672fe5ae8a31982fbe"
_CLOSURE_HASH = "168aab095660895c36eaece21e9f4de6ea3a8874939dd1d195950fc70a57dfce"
_RESOLVER_HASH = "5dd7a81e22c68199f212e1e37aa1ad8dd989eb34de33098c14fe6da656fcaa26"
_RECOVERY_SOURCE_ISSUE = "01a0386f-c03f-7be9-b2d6-45841f1a4a74"
_RECOVERY_SOURCE_TASK = "01a03882-8b63-7480-9c58-acf2213573a6"
_RECOVERY_SOURCE_RUNTIME = "0e5e9dd9-5135-4937-bb03-92b77adb8395"
_RECOVERY_SOURCE_COMMENT = "01a0388e-729c-7289-9c99-b94d075e984e"
_RECOVERY_CLOSURE_BYTES = "15f1e12e0fce75ac99fec764896dd7acadc055ee2c0771897affb67ab1990f8a"
_RECOVERY_EVIDENCE_BYTES = "0bd3ea76bf7f11f6b127bc3e6f6c88fb477d185547d1c963a2bf3f6c248f90a7"
_RECOVERY_ANCESTOR_000 = "664ccab637a68e01a6ac93ab7356f90f79c75b09b3a597fdab66d8db208c92f0"


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

    def _verify_parent_chain(self, root: Path, evidence: Any, inputs: dict[str, Any], materialization: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Re-authenticate every immutable parent, not just receipt labels."""
        container = root / "foundation-parent-chain-container-v1"
        self._private(container, 0o700, "FOUNDATION_000_MATERIALIZER_UNAVAILABLE")
        manifest = _load(container / "foundation-parent-chain-container-manifest.json", "FOUNDATION_000_MATERIALIZER_UNAVAILABLE")
        recovery_dir = root / "foundation-parent-chain-recovery-v1"
        self._private(recovery_dir, 0o700, "FOUNDATION_000_RECOVERY_UNAVAILABLE")
        recovery = _load(recovery_dir / "recovery-receipt.json", "FOUNDATION_000_RECOVERY_UNAVAILABLE")
        recovery_body = {key: value for key, value in recovery.items() if key != "receipt_sha256"}
        required_recovery = {"schema_version", "source_issue_id", "source_task_id", "source_runtime_id", "source_comment_id", "closure_sha256", "evidence_sha256", "foundation_task_hash", "closure_hash", "ancestor_000_hash", "root_binding_id", "registry_task_ref", "rehydrated_from_accepted_projection", "receipt_sha256"}
        if (set(recovery) != required_recovery or recovery.get("schema_version") != "foundation_parent_chain_recovery_receipt/v1"
                or recovery.get("receipt_sha256") != sha256(recovery_body) or recovery.get("root_binding_id") != evidence.root_binding_id
                or recovery.get("foundation_task_hash") != _TASK_HASH or recovery.get("closure_hash") != _CLOSURE_HASH
                or recovery.get("source_issue_id") != _RECOVERY_SOURCE_ISSUE or recovery.get("source_task_id") != _RECOVERY_SOURCE_TASK
                or recovery.get("source_runtime_id") != _RECOVERY_SOURCE_RUNTIME or recovery.get("source_comment_id") != _RECOVERY_SOURCE_COMMENT
                or recovery.get("closure_sha256") != _RECOVERY_CLOSURE_BYTES or recovery.get("evidence_sha256") != _RECOVERY_EVIDENCE_BYTES
                or recovery.get("ancestor_000_hash") != _RECOVERY_ANCESTOR_000 or recovery.get("rehydrated_from_accepted_projection") is not True
                or inputs.get("resolver_universe_hash") != _RESOLVER_HASH):
            _fail("FOUNDATION_000_RECOVERY_INVALID")
        closure_path, evidence_path = recovery_dir / "structure_closure.json", recovery_dir / "evidence.json"
        self._private(closure_path, 0o600, "FOUNDATION_000_RECOVERY_INVALID")
        self._private(evidence_path, 0o600, "FOUNDATION_000_RECOVERY_INVALID")
        if hashlib.sha256(closure_path.read_bytes()).hexdigest() != recovery["closure_sha256"] or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != recovery["evidence_sha256"]:
            _fail("FOUNDATION_000_RECOVERY_INVALID")
        closure = _load(closure_path, "FOUNDATION_000_RECOVERY_INVALID")
        try:
            importlib.import_module("east_v5.agents.220.closure").validate_structure_closure_package(closure)
        except Exception as exc:
            raise FoundationFixedComponent000Error("FOUNDATION_000_RECOVERY_INVALID") from exc
        envelope, payload = closure["envelope"], closure["payload"]
        if (envelope.get("content_hash") != _CLOSURE_HASH
                or payload.get("foundation_task_ref", {}).get("content_hash") != _TASK_HASH
                or recovery.get("registry_task_ref", {}).get("content_hash") != _TASK_HASH):
            _fail("FOUNDATION_000_RECOVERY_INVALID")
        config, config_hash = self.bootstrap._materializer_config()
        if (manifest.get("container_sha256") != sha256({key: value for key, value in manifest.items() if key != "container_sha256"})
                or manifest.get("root_binding_id") != evidence.root_binding_id or manifest.get("config_sha256") != config_hash
                or manifest.get("attachments") != config["attachments"] or manifest.get("assets") != materialization.get("assets")
                or manifest.get("container_sha256") != materialization.get("container_sha256")
                or manifest.get("runtime_manifest_sha256") != materialization.get("runtime_manifest_sha256")
                or manifest.get("hierarchy_mapping_sha256") != materialization.get("hierarchy_mapping_sha256")):
            _fail("FOUNDATION_000_MATERIALIZER_DRIFT")
        for item in config["attachments"]:
            path = container / item["filename"]
            self._private(path, 0o600, "FOUNDATION_000_MATERIALIZER_DRIFT")
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["byte_sha256"]:
                _fail("FOUNDATION_000_MATERIALIZER_DRIFT")
            self.bootstrap._semantic_attachment(item, path)
        for item in config["assets"]:
            path = container / "approved-assets" / item["filename"]
            self._private(path, 0o600, "FOUNDATION_000_MATERIALIZER_DRIFT")
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["byte_sha256"]:
                _fail("FOUNDATION_000_MATERIALIZER_DRIFT")
        runtime_manifest_path = container / "constraint-assets-runtime-manifest.json"
        mapping_path = container / "foundation-hierarchy-endpoint-mapping.json"
        self._private(runtime_manifest_path, 0o600, "FOUNDATION_000_MATERIALIZER_DRIFT")
        self._private(mapping_path, 0o600, "FOUNDATION_000_MATERIALIZER_DRIFT")
        runtime_manifest = _load(runtime_manifest_path, "FOUNDATION_000_MATERIALIZER_DRIFT")
        if sha256(self.bootstrap._without_locators(runtime_manifest)) != config["runtime_manifest_without_locators_sha256"]:
            _fail("FOUNDATION_000_MATERIALIZER_DRIFT")
        mapping = _load(mapping_path, "FOUNDATION_000_MATERIALIZER_DRIFT")
        try:
            expected_mapping = self.bootstrap._hierarchy_mapping(container / "foundation_intent_package.json", config)
        except RuntimeBootstrapError as exc:
            raise FoundationFixedComponent000Error(str(exc)) from exc
        mapping_body = {key: value for key, value in mapping.items() if key != "content_hash"}
        if (set(mapping) != {"schema_version", "intent_content_hash", "field_mapping", "content_hash"}
                or mapping.get("schema_version") != "foundation-hierarchy-endpoint-mapping/v1"
                or not isinstance(mapping.get("field_mapping"), dict) or not mapping["field_mapping"]
                or mapping.get("intent_content_hash") != config["attachments"][0]["semantic_sha256"]
                or mapping.get("content_hash") != sha256(mapping_body)
                or mapping.get("content_hash") != config["hierarchy_mapping_sha256"]
                or mapping != expected_mapping):
            _fail("FOUNDATION_000_MATERIALIZER_DRIFT")
        return manifest, recovery

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
        try:
            manifest, recovery = self._verify_parent_chain(root, evidence, inputs, materialization)
        except RuntimeBootstrapError as exc:
            raise FoundationFixedComponent000Error(str(exc)) from exc
        from east_v5.runtime.foundation_repo_launcher import FoundationRepoLauncher
        launcher = FoundationRepoLauncher(self.bootstrap, agent_role="000")
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
