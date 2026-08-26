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
_MATERIALIZER_CONFIG_SHA256 = "f0f8d19dcd1a8cfe505b687e24dc9d320902caf0ab8f02f9186c79c1a7a31cf4"
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
        required = {"schema_version", "container_schema_version", "attachments"}
        keys = {"id", "filename", "byte_sha256", "semantic_sha256"}
        if set(value) != required or value.get("schema_version") != "foundation-parent-chain-materializer-config/v1" or value.get("container_schema_version") != "foundation-parent-chain-container-manifest/v1" or not isinstance(attachments, list) or len(attachments) != 3 or any(not isinstance(item, dict) or set(item) != keys or not all(isinstance(item.get(key), str) and item[key] for key in keys) or not _is_sha(item["byte_sha256"], 64) or not _is_sha(item["semantic_sha256"], 64) for item in attachments) or len({item["id"] for item in attachments}) != 3 or len({item["filename"] for item in attachments}) != 3:
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
                manifest = {"schema_version": config["container_schema_version"], "root_binding_id": evidence.root_binding_id, "config_sha256": config_hash, "attachments": config["attachments"]}
                manifest["container_sha256"] = sha256(manifest)
                self._atomic_json(staging / _MATERIALIZER_MANIFEST, manifest)
                if set(path.name for path in staging.iterdir()) != {item["filename"] for item in config["attachments"]} | {_MATERIALIZER_MANIFEST}:
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
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeBootstrapError("FOUNDATION_PARENT_MATERIALIZER_CONTAINER_DRIFT") from exc
        body = {"schema_version": "foundation-parent-chain-materialization-receipt/v1", "root_binding_id": evidence.root_binding_id, "bootstrap": evidence.redacted(), "skill_manifest_sha256": self.declaration["skill_bundle"]["skill_manifest_sha256"], "config_sha256": config_hash, "attachments": config["attachments"], "container_sha256": manifest["container_sha256"]}
        body["receipt_sha256"] = sha256(body)
        body["attestation"] = hmac.new(self._materializer_key(root), canonical_bytes(body), hashlib.sha256).hexdigest()
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
