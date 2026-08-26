"""Governed, fail-closed bootstrap for a real EAS-70 task process.

The task envelope carries the candidate provenance, but a task never treats
that text as an instruction to load code.  This module is the executable gate
which verifies the already-mounted governed checkout before importing or using
the runtime adapter.  It intentionally has no business-package behaviour.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from east_v5.governance import ContractError, canonical_bytes, sha256

BOOTSTRAP_VERSION = "east-v5-runtime-bootstrap/v1"
RESOLVER_VERSION = "daemon_local_platform_data_resolver_v1"
_SHA = set("0123456789abcdef")
_BOOTSTRAP_KEYS = {"bootstrap_version", "candidate_base_sha", "candidate_head_sha", "adapter_sha256", "bootstrap_sha256", "runner_sha256", "runtime_context", "skill_bundle"}
_CONTEXT_KEYS = {"resolver_version", "workspace_id", "project_id", "daemon_id"}
_SKILL_BUNDLE_KEYS = {"skill_name", "skill_version", "skill_manifest_sha256"}
_MATERIALIZER_CONFIG = "config/foundation-parent-chain-materializer-v1.json"
_MATERIALIZER_CONFIG_SHA256 = "0a5eb66f040afa1e9b7630a2148c10b784f4c3ea7d165d14da15af4b6e2d44dc"
_MATERIALIZER_CONTAINER = "foundation-parent-chain-container-v1"
_MATERIALIZER_MANIFEST = "foundation-parent-chain-container-manifest.json"
_MATERIALIZER_KEY = ".foundation-parent-chain-materializer-v1.key"


class RuntimeBootstrapError(ContractError):
    """A task must not make a partial registry or control-plane mutation."""


def _fail(code: str) -> None:
    raise RuntimeBootstrapError(code)


def _is_sha(value: Any, size: int) -> bool:
    return isinstance(value, str) and len(value) == size and set(value) <= _SHA


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def root_binding_id(context: dict[str, Any]) -> str:
    if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS:
        _fail("RUNTIME_BOOTSTRAP_CONTEXT_INVALID")
    if context["resolver_version"] != RESOLVER_VERSION:
        _fail("RUNTIME_BOOTSTRAP_RESOLVER_VERSION_INVALID")
    if not all(isinstance(context[key], str) and context[key] for key in _CONTEXT_KEYS - {"resolver_version"}):
        _fail("RUNTIME_BOOTSTRAP_CONTEXT_INVALID")
    return hashlib.sha256(canonical_bytes(context)).hexdigest()


def validate_bootstrap_declaration(envelope: dict[str, Any]) -> dict[str, Any]:
    """Validate portable provenance before a task can call the adapter."""
    declaration = envelope.get("execution_bootstrap") if isinstance(envelope, dict) else None
    if not isinstance(declaration, dict):
        _fail("RUNTIME_BOOTSTRAP_MISSING")
    if set(declaration) != _BOOTSTRAP_KEYS:
        if "skill_bundle" not in declaration and set(declaration) == _BOOTSTRAP_KEYS - {"skill_bundle"}:
            _fail("RUNTIME_SKILL_BUNDLE_MISSING")
        _fail("RUNTIME_BOOTSTRAP_MISSING")
    if declaration["bootstrap_version"] != BOOTSTRAP_VERSION:
        _fail("RUNTIME_BOOTSTRAP_VERSION_INVALID")
    if not _is_sha(declaration["candidate_base_sha"], 40) or not _is_sha(declaration["candidate_head_sha"], 40):
        _fail("RUNTIME_BOOTSTRAP_CANDIDATE_INVALID")
    if not all(_is_sha(declaration[key], 64) for key in ("adapter_sha256", "bootstrap_sha256", "runner_sha256")):
        _fail("RUNTIME_BOOTSTRAP_CODE_HASH_INVALID")
    skill_bundle = declaration["skill_bundle"]
    if not isinstance(skill_bundle, dict) or set(skill_bundle) != _SKILL_BUNDLE_KEYS:
        _fail("RUNTIME_SKILL_BUNDLE_MISSING")
    if skill_bundle["skill_name"] != "east-v5-runtime-bootstrap-v1" or skill_bundle["skill_version"] != "v1" or not _is_sha(skill_bundle["skill_manifest_sha256"], 64):
        _fail("RUNTIME_SKILL_BUNDLE_DRIFT")
    if not isinstance(envelope.get("root_binding_id"), str) or envelope["root_binding_id"] != root_binding_id(declaration["runtime_context"]):
        _fail("RUNTIME_BOOTSTRAP_ROOT_BINDING_DRIFT")
    return declaration


def _platform_data_home(platform: str, environ: dict[str, str], home: Path) -> Path:
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Multica"
    if platform == "win32":
        raw = environ.get("LOCALAPPDATA")
        if not raw:
            _fail("RUNTIME_BOOTSTRAP_DATA_HOME_UNAVAILABLE")
        return Path(raw) / "Multica"
    raw = environ.get("XDG_DATA_HOME")
    return Path(raw) / "multica" if raw else home / ".local" / "share" / "multica"


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _contains_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


@dataclass(frozen=True)
class BootstrapEvidence:
    candidate_head_sha: str
    adapter_sha256: str
    bootstrap_sha256: str
    runner_sha256: str
    root_binding_id: str
    runner_entrypoint: str

    def redacted(self) -> dict[str, str]:
        return {"candidate_head_sha": self.candidate_head_sha, "adapter_sha256": self.adapter_sha256, "bootstrap_sha256": self.bootstrap_sha256, "runner_sha256": self.runner_sha256, "root_binding_id": self.root_binding_id, "runner_entrypoint": self.runner_entrypoint}


class RuntimeBootstrap:
    """Verify the candidate checkout and establish the governed local root."""

    def __init__(self, checkout: Path, envelope: dict[str, Any], *, environ: dict[str, str] | None = None, platform: str | None = None, home: Path | None = None, runner: Callable[..., Any] = subprocess.run):
        self.checkout = checkout.resolve()
        self.envelope = envelope
        self.declaration = validate_bootstrap_declaration(envelope)
        self.environ = dict(os.environ if environ is None else environ)
        self.platform = sys.platform if platform is None else platform
        self.home = Path.home() if home is None else home
        self.runner = runner

    def _git_output(self, *args: str) -> str:
        try:
            result = self.runner(["git", "-C", str(self.checkout), *args], check=True, capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeBootstrapError("RUNTIME_BOOTSTRAP_GIT_UNAVAILABLE") from exc
        return str(result.stdout).strip()

    def _verify_checkout(self) -> None:
        if self._git_output("rev-parse", "HEAD") != self.declaration["candidate_head_sha"]:
            _fail("RUNTIME_BOOTSTRAP_CANDIDATE_HEAD_DRIFT")
        if self._git_output("status", "--porcelain", "--untracked-files=no"):
            _fail("RUNTIME_BOOTSTRAP_CHECKOUT_DIRTY")
        adapter = self.checkout / "src" / "east_v5" / "runtime" / "adapter.py"
        bootstrap = self.checkout / "src" / "east_v5" / "runtime" / "bootstrap.py"
        runner = self.checkout / "scripts" / "runtime_bootstrap.py"
        if not adapter.is_file() or not bootstrap.is_file() or not runner.is_file():
            _fail("RUNTIME_BOOTSTRAP_ENTRYPOINT_MISSING")
        try:
            compile(runner.read_text(encoding="utf-8"), str(runner), "exec")
        except (OSError, SyntaxError) as exc:
            raise RuntimeBootstrapError("RUNTIME_BOOTSTRAP_ENTRYPOINT_NOT_EXECUTABLE") from exc
        if _file_sha(adapter) != self.declaration["adapter_sha256"] or _file_sha(bootstrap) != self.declaration["bootstrap_sha256"] or _file_sha(runner) != self.declaration["runner_sha256"]:
            _fail("RUNTIME_BOOTSTRAP_CODE_HASH_DRIFT")

    def resolve_runtime_root(self) -> Path:
        override = self.environ.get("V5_RUNTIME_ROOT")
        base = Path(override) if override else _platform_data_home(self.platform, self.environ, self.home)
        context = self.declaration["runtime_context"]
        root = base if override else base / "east-v5-runtime" / context["workspace_id"] / context["project_id"] / context["daemon_id"]
        if not root.is_absolute() or _contains_symlink(root):
            _fail("RUNTIME_BOOTSTRAP_ROOT_UNSAFE")
        prohibited = (self.checkout, Path(tempfile.gettempdir()))
        if any(_inside(root, value) or _inside(value, root) for value in prohibited):
            _fail("RUNTIME_BOOTSTRAP_ROOT_UNSAFE")
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt" and stat.S_IMODE(root.stat().st_mode) != 0o700:
                root.chmod(0o700)
                if stat.S_IMODE(root.stat().st_mode) != 0o700:
                    _fail("RUNTIME_BOOTSTRAP_ROOT_PERMISSION_INVALID")
        except OSError as exc:
            raise RuntimeBootstrapError("RUNTIME_BOOTSTRAP_ROOT_UNAVAILABLE") from exc
        return root

    def preflight(self) -> BootstrapEvidence:
        self._verify_checkout()
        self.resolve_runtime_root()
        return BootstrapEvidence(candidate_head_sha=self.declaration["candidate_head_sha"], adapter_sha256=self.declaration["adapter_sha256"], bootstrap_sha256=self.declaration["bootstrap_sha256"], runner_sha256=self.declaration["runner_sha256"], root_binding_id=self.envelope["root_binding_id"], runner_entrypoint="scripts/runtime_bootstrap.py")

    @staticmethod
    def _private(path: Path, mode: int, code: str) -> None:
        try:
            if path.is_symlink() or not path.exists() or stat.S_IMODE(path.stat().st_mode) != mode:
                _fail(code)
        except OSError as exc:
            raise RuntimeBootstrapError(code) from exc

    def _materializer_config(self) -> tuple[dict[str, Any], str]:
        path = self.checkout / _MATERIALIZER_CONFIG
        try:
            raw = path.read_bytes(); value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_CONFIG_INVALID") from exc
        attachments = value.get("attachments") if isinstance(value, dict) else None
        assets = value.get("assets") if isinstance(value, dict) else None
        required = {"schema_version", "container_schema_version", "attachments", "eas111_evidence", "assets", "runtime_manifest_template_byte_sha256", "runtime_manifest_identity_schema", "runtime_manifest_identity_bytes_length", "runtime_manifest_without_locators_sha256", "hierarchy_mapping_sha256"}
        keys = {"id", "filename", "byte_sha256", "semantic_sha256"}
        asset_keys = {"asset_version", "artifact_id", "artifact_type", "content_hash", "role", "logical_path", "filename", "byte_sha256"}
        evidence = value.get("eas111_evidence")
        if set(value) != required or value.get("schema_version") != "foundation-parent-chain-materializer-config/v2" or value.get("container_schema_version") != "foundation-parent-chain-container-manifest/v2" or value.get("runtime_manifest_identity_schema") != "constraint-assets-runtime-manifest-identity/v1" or value.get("runtime_manifest_identity_bytes_length") != 1231 or not _is_sha(value.get("runtime_manifest_template_byte_sha256"), 64) or not isinstance(attachments, list) or len(attachments) != 3 or any(not isinstance(item, dict) or set(item) != keys or not all(isinstance(item.get(key), str) and item[key] for key in keys) or not _is_sha(item["byte_sha256"], 64) or not _is_sha(item["semantic_sha256"], 64) for item in attachments) or len({item["id"] for item in attachments}) != 3 or len({item["filename"] for item in attachments}) != 3 or not isinstance(evidence, dict) or set(evidence) != {"id", "filename", "byte_sha256"} or not all(isinstance(evidence.get(key), str) and evidence[key] for key in evidence) or not _is_sha(evidence["byte_sha256"], 64) or not isinstance(assets, list) or len(assets) != 6 or any(not isinstance(item, dict) or set(item) != asset_keys or not all(isinstance(item.get(key), str) and item[key] for key in asset_keys) or not _is_sha(item["content_hash"], 64) or not _is_sha(item["byte_sha256"], 64) or Path(item["logical_path"]).is_absolute() or ".." in Path(item["logical_path"]).parts for item in assets) or len({item["filename"] for item in assets}) != len(assets) or not _is_sha(value.get("runtime_manifest_without_locators_sha256"), 64) or not _is_sha(value.get("hierarchy_mapping_sha256"), 64):
            _fail("FOUNDATION_PARENT_MATERIALIZER_CONFIG_INVALID")
        observed = hashlib.sha256(raw).hexdigest()
        if observed != _MATERIALIZER_CONFIG_SHA256:
            _fail("FOUNDATION_PARENT_MATERIALIZER_CONFIG_DRIFT")
        return value, observed

    @staticmethod
    def _semantic_attachment(item: dict[str, Any], path: Path) -> None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_SEMANTIC_INVALID") from exc
        expected = item["semantic_sha256"]
        if item["filename"] == "foundation_intent_package.json":
            observed = value.get("content_sha256") if isinstance(value, dict) else None
        else:
            observed = sha256(value)
        if observed != expected:
            _fail("FOUNDATION_PARENT_MATERIALIZER_SEMANTIC_DRIFT")

    @staticmethod
    def _without_locators(manifest: dict[str, Any]) -> dict[str, Any]:
        """Canonical public identity for a local-only runtime manifest."""
        expected_assets = (
            ("CA-FOUNDATION-20260805-002", {"single_field"}),
            ("CA-MULTIFIELD-20260812-003", {"sqlite"}),
            ("EAS-TYPED-GRAPH-20260812-001", {"nodes", "edges", "projections", "closures"}),
        )
        if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "assets"} or manifest.get("schema_version") != "v5.constraint-assets-runtime-manifest/v1" or not isinstance(manifest["assets"], list) or len(manifest["assets"]) != 3:
            _fail("FOUNDATION_PARENT_MATERIALIZER_RUNTIME_MANIFEST_IDENTITY_INVALID")
        for asset, (artifact_id, roles) in zip(manifest["assets"], expected_assets):
            if not isinstance(asset, dict) or set(asset) != {"artifact_id", "artifact_type", "asset_version", "content_hash", "payload"} or asset.get("artifact_id") != artifact_id or not isinstance(asset["payload"], dict) or set(asset["payload"]) != roles:
                _fail("FOUNDATION_PARENT_MATERIALIZER_RUNTIME_MANIFEST_IDENTITY_INVALID")
            for payload in asset["payload"].values():
                if not isinstance(payload, dict) or set(payload) != {"locator", "sha256"} or not isinstance(payload["locator"], str) or not payload["locator"] or not _is_sha(payload["sha256"], 64):
                    _fail("FOUNDATION_PARENT_MATERIALIZER_RUNTIME_MANIFEST_IDENTITY_INVALID")
        return {
            "schema_version": manifest["schema_version"],
            "assets": [
                {**{key: value for key, value in asset.items() if key != "payload"}, "payload": {
                    role: {"sha256": payload["sha256"]} for role, payload in asset["payload"].items()
                }} for asset in manifest["assets"]
            ],
        }

    @staticmethod
    def _runtime_manifest(assets: list[dict[str, Any]], asset_dir: Path) -> dict[str, Any]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in assets:
            record = grouped.setdefault(item["asset_version"], {
                "artifact_id": item["artifact_id"], "artifact_type": item["artifact_type"],
                "asset_version": item["asset_version"], "content_hash": item["content_hash"], "payload": {},
            })
            if any(record[key] != item[key] for key in ("artifact_id", "artifact_type", "asset_version", "content_hash")):
                _fail("FOUNDATION_PARENT_MATERIALIZER_CONFIG_INVALID")
            record["payload"][item["role"]] = {"locator": str((asset_dir / item["filename"]).resolve()), "sha256": item["byte_sha256"]}
        return {"schema_version": "v5.constraint-assets-runtime-manifest/v1", "assets": [grouped[key] for key in ("CA-V0.2.0", "CA-V0.3.0", "TRG-V1.0.0")]}

    def _verify_runtime_manifest(self, manifest: dict[str, Any], config: dict[str, Any], asset_dir: Path) -> None:
        """Validate the root-local manifest through the actual asset service."""
        projection = self._without_locators(manifest)
        identity_bytes = canonical_bytes(projection)
        observed = hashlib.sha256(identity_bytes).hexdigest()
        template = self.checkout / "fixtures" / "constraint_assets" / "runtime-manifest.template.json"
        if _file_sha(template) != config["runtime_manifest_template_byte_sha256"] or len(identity_bytes) != config["runtime_manifest_identity_bytes_length"] or observed != config["runtime_manifest_without_locators_sha256"]:
            _fail("FOUNDATION_PARENT_MATERIALIZER_RUNTIME_MANIFEST_DRIFT")
        try:
            # Import lazily: Bootstrap remains a control-plane module until
            # local files have passed all byte and permission checks.
            from east_v5.constraint_assets.service import validate_runtime_manifest
            roots = {"repo_root": str(self.checkout), "runtime_root": str(self.resolve_runtime_root()), "reference_root": str(self.checkout.parent / "approved-reference-root"), "reference_read_only": True}
            # validate_runtime_manifest needs only the manifest and root-local
            # payloads.  The reference root is deliberately never read here.
            validate_runtime_manifest(self.checkout, roots, asset_dir.parent / "constraint-assets-runtime-manifest.json")
        except RuntimeBootstrapError:
            raise
        except Exception as exc:
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_RUNTIME_MANIFEST_INVALID") from exc

    def _hierarchy_mapping(self, intent_path: Path, config: dict[str, Any]) -> dict[str, Any]:
        """Project only the approved intent's existing org-tree field mapping."""
        try:
            source = json.loads(intent_path.read_text(encoding="utf-8"))
            content_hash = source.get("content_sha256")
            payload = source.get("payload", source)
            refs = payload["hierarchy_asset_refs"]
            org_tree = next(item["org_tree"] for item in refs if isinstance(item, dict) and isinstance(item.get("org_tree"), dict))
            mapping = org_tree["field_mapping"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_HIERARCHY_MAPPING_INVALID") from exc
        if content_hash != config["attachments"][0]["semantic_sha256"] or not isinstance(mapping, dict) or not mapping:
            _fail("FOUNDATION_PARENT_MATERIALIZER_HIERARCHY_MAPPING_INVALID")
        result = {"schema_version": "foundation-hierarchy-endpoint-mapping/v1", "intent_content_hash": content_hash, "field_mapping": mapping}
        result["content_hash"] = sha256(result)
        if result["content_hash"] != config["hierarchy_mapping_sha256"]:
            _fail("FOUNDATION_PARENT_MATERIALIZER_HIERARCHY_MAPPING_DRIFT")
        return result

    def _materializer_key(self, root: Path) -> bytes:
        key = root / _MATERIALIZER_KEY
        try:
            if key.exists() or key.is_symlink():
                self._private(key, 0o600, "FOUNDATION_PARENT_MATERIALIZER_KEY_UNSAFE")
                value = key.read_bytes()
            else:
                descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(secrets.token_bytes(32))
                value = key.read_bytes()
            if len(value) != 32:
                _fail("FOUNDATION_PARENT_MATERIALIZER_KEY_UNSAFE")
            return value
        except OSError as exc:
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_KEY_UNAVAILABLE") from exc

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".foundation-parent-", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(canonical_bytes(value)); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            Path(temporary).unlink(missing_ok=True)
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_WRITE_FAILED") from exc

    def materialize_foundation_parent_chain(self) -> dict[str, Any]:
        """The sole provisioning-only route for the frozen Foundation parents.

        It has no parameters: attachment identifiers, target directory and
        hashes are all governed by the checked-in allowlist.
        """
        evidence = self.preflight()
        root = self.resolve_runtime_root(); self._private(root, 0o700, "FOUNDATION_PARENT_MATERIALIZER_ROOT_UNSAFE")
        config, config_hash = self._materializer_config()
        final = root / _MATERIALIZER_CONTAINER
        manifest_path = final / _MATERIALIZER_MANIFEST
        if final.exists() or final.is_symlink():
            self._private(final, 0o700, "FOUNDATION_PARENT_MATERIALIZER_CONTAINER_UNSAFE")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_CONTAINER_DRIFT") from exc
            if manifest.get("root_binding_id") != evidence.root_binding_id or manifest.get("config_sha256") != config_hash or manifest.get("attachments") != config["attachments"]:
                _fail("FOUNDATION_PARENT_MATERIALIZER_CONTAINER_DRIFT")
        else:
            staging = root / f".foundation-parent-chain-staging-{secrets.token_hex(16)}"
            try:
                staging.mkdir(mode=0o700); self._private(staging, 0o700, "FOUNDATION_PARENT_MATERIALIZER_STAGING_UNSAFE")
                for item in config["attachments"]:
                    self.runner(["multica", "attachment", "download", item["id"], "--output-dir", str(staging)], check=True, capture_output=True, text=True)
                    target = staging / item["filename"]
                    if target.is_symlink() or not target.is_file() or _file_sha(target) != item["byte_sha256"]:
                        _fail("FOUNDATION_PARENT_MATERIALIZER_ATTACHMENT_DRIFT")
                    target.chmod(0o600); self._private(target, 0o600, "FOUNDATION_PARENT_MATERIALIZER_ATTACHMENT_PERMISSION_DRIFT")
                    self._semantic_attachment(item, target)
                # The EAS-111 evidence is control-plane provenance only.  Its
                # fixed attachment id is never supplied by a business caller.
                evidence_item = config["eas111_evidence"]
                self.runner(["multica", "attachment", "download", evidence_item["id"], "--output-dir", str(staging)], check=True, capture_output=True, text=True)
                evidence_file = staging / evidence_item["filename"]
                if evidence_file.is_symlink() or not evidence_file.is_file() or _file_sha(evidence_file) != evidence_item["byte_sha256"]:
                    _fail("FOUNDATION_PARENT_MATERIALIZER_EAS111_EVIDENCE_DRIFT")
                evidence_file.chmod(0o600); self._private(evidence_file, 0o600, "FOUNDATION_PARENT_MATERIALIZER_ATTACHMENT_PERMISSION_DRIFT")
                asset_dir = staging / "approved-assets"; asset_dir.mkdir(mode=0o700); self._private(asset_dir, 0o700, "FOUNDATION_PARENT_MATERIALIZER_STAGING_UNSAFE")
                for asset in config["assets"]:
                    source = self.checkout / asset["logical_path"]
                    target = asset_dir / asset["filename"]
                    if source.is_symlink() or not source.is_file() or _file_sha(source) != asset["byte_sha256"]:
                        _fail("FOUNDATION_PARENT_MATERIALIZER_ASSET_DRIFT")
                    shutil.copyfile(source, target); target.chmod(0o600)
                    self._private(target, 0o600, "FOUNDATION_PARENT_MATERIALIZER_ASSET_PERMISSION_DRIFT")
                    if _file_sha(target) != asset["byte_sha256"]:
                        _fail("FOUNDATION_PARENT_MATERIALIZER_ASSET_DRIFT")
                runtime_manifest = self._runtime_manifest(config["assets"], asset_dir)
                manifest_file = staging / "constraint-assets-runtime-manifest.json"
                self._atomic_json(manifest_file, runtime_manifest); self._private(manifest_file, 0o600, "FOUNDATION_PARENT_MATERIALIZER_ATTACHMENT_PERMISSION_DRIFT")
                self._verify_runtime_manifest(runtime_manifest, config, asset_dir)
                hierarchy_mapping = self._hierarchy_mapping(staging / "foundation_intent_package.json", config)
                self._atomic_json(staging / "foundation-hierarchy-endpoint-mapping.json", hierarchy_mapping)
                manifest = {"schema_version": config["container_schema_version"], "root_binding_id": evidence.root_binding_id, "config_sha256": config_hash, "attachments": config["attachments"], "eas111_evidence": evidence_item, "assets": [{key: item[key] for key in ("asset_version", "artifact_id", "artifact_type", "content_hash", "role", "filename", "byte_sha256")} for item in config["assets"]], "runtime_manifest_sha256": sha256(self._without_locators(runtime_manifest)), "runtime_manifest_local_sha256": _file_sha(manifest_file), "hierarchy_mapping_sha256": hierarchy_mapping["content_hash"]}
                manifest["container_sha256"] = sha256(manifest)
                self._atomic_json(staging / _MATERIALIZER_MANIFEST, manifest)
                if set(path.name for path in staging.iterdir()) != {item["filename"] for item in config["attachments"]} | {evidence_item["filename"], "approved-assets", "constraint-assets-runtime-manifest.json", "foundation-hierarchy-endpoint-mapping.json", _MATERIALIZER_MANIFEST}:
                    _fail("FOUNDATION_PARENT_MATERIALIZER_PARTIAL_CONTAINER")
                os.replace(staging, final); self._private(final, 0o700, "FOUNDATION_PARENT_MATERIALIZER_CONTAINER_UNSAFE")
            except RuntimeBootstrapError:
                shutil.rmtree(staging, ignore_errors=True); raise
            except (OSError, subprocess.SubprocessError) as exc:
                shutil.rmtree(staging, ignore_errors=True)
                raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_DOWNLOAD_FAILED") from exc
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("container_sha256") != sha256({key: value for key, value in manifest.items() if key != "container_sha256"}):
                _fail("FOUNDATION_PARENT_MATERIALIZER_CONTAINER_DRIFT")
            for item in config["attachments"]:
                target = final / item["filename"]
                self._private(target, 0o600, "FOUNDATION_PARENT_MATERIALIZER_ATTACHMENT_PERMISSION_DRIFT")
                if _file_sha(target) != item["byte_sha256"]:
                    _fail("FOUNDATION_PARENT_MATERIALIZER_ATTACHMENT_DRIFT")
                self._semantic_attachment(item, target)
            if manifest.get("runtime_manifest_sha256") != config["runtime_manifest_without_locators_sha256"] or manifest.get("hierarchy_mapping_sha256") != config["hierarchy_mapping_sha256"]:
                _fail("FOUNDATION_PARENT_MATERIALIZER_V2_DRIFT")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_CONTAINER_DRIFT") from exc
        body = {"schema_version": "foundation-parent-chain-materialization-receipt/v2", "root_binding_id": evidence.root_binding_id, "bootstrap": evidence.redacted(), "skill_manifest_sha256": self.declaration["skill_bundle"]["skill_manifest_sha256"], "config_sha256": config_hash, "attachments": config["attachments"], "eas111_evidence": config["eas111_evidence"], "assets": manifest["assets"], "runtime_manifest_sha256": manifest["runtime_manifest_sha256"], "runtime_manifest_local_sha256": manifest["runtime_manifest_local_sha256"], "hierarchy_mapping_sha256": manifest["hierarchy_mapping_sha256"], "container_sha256": manifest["container_sha256"]}
        body["receipt_sha256"] = sha256(body)
        body["attestation"] = hmac.new(self._materializer_key(root), canonical_bytes(body), hashlib.sha256).hexdigest()
        receipt_path = root / "foundation-parent-chain-materialization-receipt-v2.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            self._private(receipt_path, 0o600, "FOUNDATION_PARENT_MATERIALIZER_RECEIPT_UNSAFE")
            try:
                if json.loads(receipt_path.read_text(encoding="utf-8")) != body:
                    _fail("FOUNDATION_PARENT_MATERIALIZER_RECEIPT_DRIFT")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_RECEIPT_DRIFT") from exc
        else:
            self._atomic_json(receipt_path, body)
            self._private(receipt_path, 0o600, "FOUNDATION_PARENT_MATERIALIZER_RECEIPT_UNSAFE")
        return body

    def build_adapter(self, roots: dict[str, Any]) -> Any:
        """Return an adapter only after the task-start gate has passed.

        Import is delayed to avoid a bootstrap/adapter import cycle.  The
        adapter additionally compares this immutable proof to the envelope, so
        a caller cannot accidentally start business work using only text from
        the task description.
        """
        evidence = self.preflight()
        if Path(roots.get("runtime_root", "")).resolve() != self.resolve_runtime_root():
            _fail("RUNTIME_BOOTSTRAP_RUNTIME_ROOT_DRIFT")
        from east_v5.runtime.adapter import RuntimeAdapter
        return RuntimeAdapter(self.checkout, roots, self.envelope, preflight=evidence)

    def foundation_repo_launcher(self) -> Any:
        """Return the parameterless, root-bound Foundation launch seam.

        The returned object accepts only the task's authenticated identity;
        claims, local paths, graph routes and component receipts are derived by
        the launcher from the verified runtime root.
        """
        self.preflight()
        from east_v5.runtime.foundation_repo_launcher import FoundationRepoLauncher
        return FoundationRepoLauncher(self, agent_role="241")

    def foundation_242_launcher(self) -> Any:
        """The only repository-side 242 entry into the Foundation edge gate."""
        self.preflight()
        from east_v5.runtime.foundation_repo_launcher import FoundationRepoLauncher
        return FoundationRepoLauncher(self, agent_role="242")

    def foundation_260_launcher(self) -> Any:
        """The only repository-side 260 entry into the Foundation edge gate."""
        self.preflight()
        from east_v5.runtime.foundation_repo_launcher import FoundationRepoLauncher
        return FoundationRepoLauncher(self, agent_role="260")

    def foundation_fixed_component_issuer(self) -> Any:
        """Return the sole, parameterless production issuer for fixed 000."""
        self.preflight()
        from east_v5.runtime.foundation_fixed_component_000 import FoundationFixedComponent000Issuer
        return FoundationFixedComponent000Issuer(self)

    def foundation_task_identity_issuer(self) -> Any:
        """Return the root-bound, repo-side Foundation task identity issuer."""
        self.preflight()
        from east_v5.runtime.foundation_task_identity import FoundationTaskIdentityIssuer
        return FoundationTaskIdentityIssuer(self)
