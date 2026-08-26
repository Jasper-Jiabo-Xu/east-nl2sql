"""Controlled, local-only launcher for the Foundation v12 241 chain.

The launcher is intentionally a narrow control-plane seam.  It does not
construct business data, query a formal database, or accept caller-provided
paths, claims, graph routes, or component receipts.  Those inputs are derived
from the bound runtime root after the EAS-19 entrypoint has sealed its output.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from east_v5.governance import ContractError, canonical_bytes, sha256
from east_v5.runtime.bootstrap import BootstrapEvidence, RuntimeBootstrap, RuntimeBootstrapError


_V12_SOURCE_HEAD = "9bd88e5fc8587695ae65d37fffae69b39c7f4c31"
_V12_MANIFEST_SHA256 = "0de0623e2a3922e22dfe25703eb76e4ccd38bb96b6b010a78fcc6c15da59746f"
_V12_SKILL_ID = "f42ba062-5a2d-430f-812e-c147322cc79e"
_AGENTS = ("010", "110", "120", "130", "140", "150", "160", "170", "180", "210", "220", "230", "241", "242", "251", "252", "260")
_ROUTES = {"241": "242", "242": "260"}
_BUNDLE = "foundation-v12-bundle"
_INPUTS = "foundation-launch-inputs-v1.json"
_KEY = ".foundation-parent-chain-materializer-v1.key"
_LEDGER = "foundation-repo-launcher-v1"
_HEX = set("0123456789abcdef")


class FoundationRepoLauncherError(ContractError):
    """A launcher input was not independently reproducible."""


def _fail(code: str) -> None:
    raise FoundationRepoLauncherError(code)


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoundationRepoLauncherError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _private(path: Path, mode: int, code: str) -> None:
    try:
        if path.is_symlink() or not path.exists() or stat.S_IMODE(path.stat().st_mode) != mode:
            _fail(code)
    except OSError as exc:
        raise FoundationRepoLauncherError(code) from exc


def _ref(value: Any, code: str) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"artifact_id", "version", "content_hash"}
            or not isinstance(value["artifact_id"], str) or not value["artifact_id"]
            or not isinstance(value["version"], int) or isinstance(value["version"], bool) or value["version"] < 1
            or not isinstance(value["content_hash"], str) or len(value["content_hash"]) != 64
            or set(value["content_hash"]) - _HEX):
        _fail(code)
    return dict(value)


class FoundationRepoLauncher:
    """Derive the four v12 launch inputs from the sealed local data plane."""

    def __init__(self, bootstrap: RuntimeBootstrap, *, agent_role: str = "241") -> None:
        if not isinstance(bootstrap, RuntimeBootstrap):
            _fail("FOUNDATION_REPO_LAUNCHER_BOOTSTRAP_REQUIRED")
        if agent_role not in {"000", "241", "242", "260"}:
            _fail("FOUNDATION_REPO_LAUNCHER_ROLE_FORBIDDEN")
        self.bootstrap = bootstrap
        self.checkout = bootstrap.checkout
        self.agent_role = agent_role

    def _root(self, evidence: BootstrapEvidence) -> Path:
        if "V5_RUNTIME_ROOT" in self.bootstrap.environ:
            _fail("FOUNDATION_REPO_LAUNCHER_ENV_OVERRIDE_FORBIDDEN")
        root = self.bootstrap.resolve_runtime_root()
        _private(root, 0o700, "FOUNDATION_REPO_LAUNCHER_ROOT_UNSAFE")
        marker = _load(root / "daemon-root-binding-v12.json", "FOUNDATION_REPO_LAUNCHER_ROOT_MARKER_INVALID")
        if marker != {"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": evidence.root_binding_id}:
            _fail("FOUNDATION_REPO_LAUNCHER_ROOT_MARKER_INVALID")
        key = root / _KEY
        _private(key, 0o600, "FOUNDATION_REPO_LAUNCHER_KEY_UNSAFE")
        if len(key.read_bytes()) != 32:
            _fail("FOUNDATION_REPO_LAUNCHER_KEY_UNSAFE")
        return root

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any], code: str) -> None:
        fd, raw = tempfile.mkstemp(prefix=".foundation-launch-", dir=path.parent)
        temporary = Path(raw)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical_bytes(value)); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise FoundationRepoLauncherError(code) from exc

    def _sealed_inputs(self, root: Path, evidence: BootstrapEvidence) -> dict[str, Any]:
        path = root / _INPUTS
        _private(path, 0o600, "FOUNDATION_REPO_LAUNCHER_INPUTS_REQUIRED")
        value = _load(path, "FOUNDATION_REPO_LAUNCHER_INPUTS_INVALID")
        required = {"schema_version", "root_binding_id", "issue_id", "run_id", "attempt", "resolver_universe_hash", "foundation_task_ref", "structure_closure_ref", "database_snapshot_ref", "generation_context_ref", "inputs_sha256", "attestation"}
        body = {key: value[key] for key in value if key not in {"inputs_sha256", "attestation"}}
        if (set(value) != required or body.get("schema_version") != "foundation-launch-inputs/v1"
                or body.get("root_binding_id") != evidence.root_binding_id or body.get("issue_id") != "EAS-114"
                or not isinstance(body.get("run_id"), str) or not body["run_id"]
                or body.get("attempt") not in {1, 2, 3}
                or not isinstance(body.get("resolver_universe_hash"), str) or len(body["resolver_universe_hash"]) != 64
                or any(_ref(body.get(name), "FOUNDATION_REPO_LAUNCHER_INPUTS_INVALID") != body[name] for name in ("foundation_task_ref", "structure_closure_ref", "database_snapshot_ref", "generation_context_ref"))
                or value.get("inputs_sha256") != sha256(body)):
            _fail("FOUNDATION_REPO_LAUNCHER_INPUTS_INVALID")
        key = (root / _KEY).read_bytes()
        signature = hmac.new(key, canonical_bytes(body), hashlib.sha256).hexdigest()
        if not isinstance(value.get("attestation"), str) or not hmac.compare_digest(value["attestation"], signature):
            _fail("FOUNDATION_REPO_LAUNCHER_INPUTS_FORGED")
        return value

    def _install_bundle(self, root: Path) -> Path:
        """Materialize only the frozen, historical v12 bundle under this root."""
        final = root / _BUNDLE
        manifest_path = final / "manifest.json"
        if final.exists() or final.is_symlink():
            _private(final, 0o700, "FOUNDATION_REPO_LAUNCHER_BUNDLE_UNSAFE")
            _private(manifest_path, 0o600, "FOUNDATION_REPO_LAUNCHER_BUNDLE_UNSAFE")
            if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != _V12_MANIFEST_SHA256:
                _fail("FOUNDATION_REPO_LAUNCHER_BUNDLE_DRIFT")
            return final
        staging = root / f".foundation-v12-staging-{os.urandom(12).hex()}"
        source = staging / "source"
        archive = staging / "bundle.zip"
        try:
            staging.mkdir(mode=0o700); _private(staging, 0o700, "FOUNDATION_REPO_LAUNCHER_STAGING_UNSAFE")
            # The commit is a code constant, not an argument or a remote fetch.
            subprocess.run(["git", "-C", str(self.checkout), "worktree", "add", "--detach", str(source), _V12_SOURCE_HEAD], check=True, capture_output=True, text=True)
            subprocess.run([sys.executable, str(source / "skills/east-v5-runtime-bootstrap-v12/scripts/pack_skill.py"), "--repo-root", str(source), "--head", _V12_SOURCE_HEAD, "--output", str(archive)], check=True, capture_output=True, text=True)
            install = staging / "installed"; install.mkdir(mode=0o700)
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                if not names or any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                    _fail("FOUNDATION_REPO_LAUNCHER_BUNDLE_INVALID")
                package.extractall(install)
            for path in install.rglob("*"):
                if path.is_symlink():
                    _fail("FOUNDATION_REPO_LAUNCHER_BUNDLE_INVALID")
                path.chmod(0o700 if path.is_dir() else 0o600)
            _private(install / "manifest.json", 0o600, "FOUNDATION_REPO_LAUNCHER_BUNDLE_INVALID")
            if hashlib.sha256((install / "manifest.json").read_bytes()).hexdigest() != _V12_MANIFEST_SHA256:
                _fail("FOUNDATION_REPO_LAUNCHER_BUNDLE_DRIFT")
            os.replace(install, final); _private(final, 0o700, "FOUNDATION_REPO_LAUNCHER_BUNDLE_UNSAFE")
            return final
        except FoundationRepoLauncherError:
            raise
        except (OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
            raise FoundationRepoLauncherError("FOUNDATION_REPO_LAUNCHER_BUNDLE_UNAVAILABLE") from exc
        finally:
            if source.exists():
                subprocess.run(["git", "-C", str(self.checkout), "worktree", "remove", "--force", str(source)], capture_output=True, text=True)
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _controller(bundle: Path) -> Any:
        module_path = bundle / "east_v5/runtime/graph_controller.py"
        spec = importlib.util.spec_from_file_location("east_v5_runtime_graph_controller_launch", module_path)
        if spec is None or spec.loader is None:
            _fail("FOUNDATION_REPO_LAUNCHER_GRAPH_UNAVAILABLE")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            return module.GraphController(bundle, bundle.parent)
        except Exception as exc:
            raise FoundationRepoLauncherError("FOUNDATION_REPO_LAUNCHER_GRAPH_UNAVAILABLE") from exc

    def _claims(self, bundle: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        manifest = _load(bundle / "manifest.json", "FOUNDATION_REPO_LAUNCHER_BUNDLE_INVALID")
        graph = _load(bundle / "config/full-runtime-graph.json", "FOUNDATION_REPO_LAUNCHER_GRAPH_INVALID")
        authority = _load(bundle / "config/authority-matrix-v2.json", "FOUNDATION_REPO_LAUNCHER_AUTHORITY_INVALID")
        resolver = _load(bundle / "config/skill-identity-resolver-v1.json", "FOUNDATION_REPO_LAUNCHER_AUTHORITY_INVALID")
        if (hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest() != _V12_MANIFEST_SHA256
                or manifest.get("source_candidate_head") != _V12_SOURCE_HEAD
                or graph.get("schema_version") != "east-v5-full-runtime-graph/v12"
                or authority.get("matrix_version") != "authority-matrix-v2"
                or authority.get("row_count") != 17
                or not isinstance(graph.get("real_agents"), dict)
                or set(graph["real_agents"]) != set(_AGENTS)
                or not isinstance(authority.get("rows"), list) or len(authority["rows"]) != 17
                or not isinstance(resolver.get("workspace_skill_ids"), dict)):
            _fail("FOUNDATION_REPO_LAUNCHER_AUTHORITY_INVALID")
        rows = {row.get("agent_id"): row for row in authority["rows"] if isinstance(row, dict)}
        if set(rows) != set(_AGENTS):
            _fail("FOUNDATION_REPO_LAUNCHER_AUTHORITY_INVALID")
        claims = {"schema_version": "east-v5-full-claims/v12", "skill_id": _V12_SKILL_ID, "skill_manifest_sha256": _V12_MANIFEST_SHA256, "config_sha256": hashlib.sha256((bundle / "config/full-runtime-graph.json").read_bytes()).hexdigest(), "agents": {}}
        for agent in _AGENTS:
            item, row = graph["real_agents"][agent], rows[agent]
            names = row.get("approved_skill_bindings")
            if (not isinstance(item, dict) or not isinstance(names, list)
                    or row.get("uuid") != item.get("uuid")
                    or row.get("approved_runtime_id") != item.get("runtime_id")
                    or row.get("approved_instruction_sha256") != manifest.get("instruction_hashes", {}).get(agent)):
                _fail("FOUNDATION_REPO_LAUNCHER_AUTHORITY_INVALID")
            try:
                enabled = sorted([_V12_SKILL_ID, *(resolver["workspace_skill_ids"][name] for name in names)])
            except (KeyError, TypeError) as exc:
                raise FoundationRepoLauncherError("FOUNDATION_REPO_LAUNCHER_AUTHORITY_INVALID") from exc
            claims["agents"][agent] = {"agent_uuid": item["uuid"], "runtime_id": item["runtime_id"], "instructions_sha256": manifest["instruction_hashes"][agent], "enabled_skill_ids": enabled}
        return claims, graph, manifest

    def _identity(self, evidence: BootstrapEvidence, graph: dict[str, Any], inputs: dict[str, Any], *, agent_id: str) -> dict[str, Any]:
        # Identity is an already signed root-registry record, derived by the
        # repo-side Bootstrap entrypoint.  In particular, the task envelope
        # has no identity override field that a caller can simply move here.
        try:
            value = self.bootstrap.foundation_task_identity_issuer().load(agent_id)
        except ContractError as exc:
            raise FoundationRepoLauncherError(str(exc)) from exc
        needed = {"schema_version", "workspace_id", "project_id", "issue_id", "task_id", "agent_id", "agent_uuid", "runtime_id", "run_id", "attempt", "root_binding_id", "inputs_sha256", "git_head", "content_hash", "attestation"}
        if not isinstance(value, dict) or set(value) != needed:
            _fail("FOUNDATION_REPO_LAUNCHER_TASK_IDENTITY_INVALID")
        context = self.bootstrap.declaration["runtime_context"]
        identity_body = {key: item for key, item in value.items() if key not in {"content_hash", "attestation"}}
        if (value.get("schema_version") != "foundation-task-identity/v1"
                or value.get("content_hash") != sha256(identity_body)
                or not all(isinstance(value[key], str) and value[key] for key in ("workspace_id", "project_id", "issue_id", "task_id", "agent_id", "agent_uuid", "runtime_id", "run_id", "root_binding_id", "inputs_sha256", "git_head"))
                or agent_id not in {"241", "242", "260"} or value["agent_id"] != agent_id or value["issue_id"] != "EAS-114"
                or value["agent_uuid"] != graph["real_agents"][agent_id]["uuid"]
                or value["runtime_id"] != graph["real_agents"][agent_id]["runtime_id"]
                or value["run_id"] != inputs["run_id"] or value["attempt"] != inputs["attempt"]
                or value["root_binding_id"] != evidence.root_binding_id or value["inputs_sha256"] != inputs["inputs_sha256"]
                or value["git_head"] != evidence.candidate_head_sha):
            _fail("FOUNDATION_REPO_LAUNCHER_TASK_IDENTITY_INVALID")
        # Context identifiers are checked against the bootstrap declaration,
        # not trusted merely because the task supplied text.
        if (value["workspace_id"], value["project_id"]) != (context["workspace_id"], context["project_id"]):
            _fail("FOUNDATION_REPO_LAUNCHER_TASK_IDENTITY_INVALID")
        return {key: value[key] for key in ("workspace_id", "project_id", "issue_id", "task_id", "agent_uuid", "runtime_id", "run_id", "attempt")}

    def launch(self) -> dict[str, Any]:
        """Prepare one idempotent 241 launch from sealed Foundation inputs."""
        try:
            evidence = self.bootstrap.preflight()
        except RuntimeBootstrapError as exc:
            raise FoundationRepoLauncherError(str(exc)) from exc
        root = self._root(evidence)
        inputs = self._sealed_inputs(root, evidence)
        bundle = self._install_bundle(root)
        claims, graph, manifest = self._claims(bundle)
        identity = self._identity(evidence, graph, inputs, agent_id="241")
        controller = self._controller(bundle)
        # 000 is issued during controlled bootstrap provisioning.  241 only
        # read-backs its sealed production record; it has no receipt minting
        # capability of its own.
        issued = self.bootstrap.foundation_fixed_component_issuer().load()
        component = issued["receipt"]
        component_proof = issued
        preflight = controller.full_preflight(claims, component)
        envelope = {"schema_version": "runtime_graph_envelope/v12", "run_id": identity["run_id"], "mode": "foundation", "attempt": identity["attempt"], "target_agent_id": "241", "target_agent_uuid": identity["agent_uuid"], "root_binding_id": evidence.root_binding_id, "preflight_token": preflight["preflight_token"], "input_receipt_hashes": [inputs["structure_closure_ref"]["content_hash"]], "outcome": "success"}
        replay_key = sha256({"head": evidence.candidate_head_sha, "root_binding_id": evidence.root_binding_id, "task_id": identity["task_id"], "agent_uuid": identity["agent_uuid"], "runtime_id": identity["runtime_id"], "run_id": identity["run_id"], "attempt": identity["attempt"], "inputs_sha256": inputs["inputs_sha256"], "manifest": _V12_MANIFEST_SHA256})
        body = {"schema_version": "foundation-repo-launch-receipt/v1", "git_head": evidence.candidate_head_sha, "skill_manifest_sha256": _V12_MANIFEST_SHA256, "root_binding_id": evidence.root_binding_id, "task_identity": identity, "claims_sha256": sha256(claims), "component_receipt_sha256": component["receipt_sha256"], "component_proof_sha256": sha256(component_proof), "inputs_sha256": inputs["inputs_sha256"], "runtime_graph_envelope": envelope, "route": _ROUTES, "replay_key": replay_key}
        receipt = {**body, "attestation": hmac.new((root / _KEY).read_bytes(), canonical_bytes(body), hashlib.sha256).hexdigest()}
        ledger = root / _LEDGER; ledger.mkdir(mode=0o700, exist_ok=True); _private(ledger, 0o700, "FOUNDATION_REPO_LAUNCHER_LEDGER_UNSAFE")
        path = ledger / f"{replay_key}.json"
        if path.exists() or path.is_symlink():
            _private(path, 0o600, "FOUNDATION_REPO_LAUNCHER_REPLAY_UNSAFE")
            if _load(path, "FOUNDATION_REPO_LAUNCHER_REPLAY_UNSAFE") != receipt:
                _fail("FOUNDATION_REPO_LAUNCHER_REPLAY_DRIFT")
        else:
            self._atomic_json(path, receipt, "FOUNDATION_REPO_LAUNCHER_WRITE_FAILED")
            _private(path, 0o600, "FOUNDATION_REPO_LAUNCHER_REPLAY_UNSAFE")
        return receipt

    def verify_downstream(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """Consume exactly one upstream edge under the current task identity.

        242 and 260 never nominate their own task/runtime in parameters.  They
        re-open all root evidence and accept only 241 -> 242 -> 260 state.
        """
        evidence = self.bootstrap.preflight(); root = self._root(evidence)
        inputs = self._sealed_inputs(root, evidence)
        bundle = self._install_bundle(root)
        claims, graph, _manifest = self._claims(bundle)
        # The caller cannot nominate its agent identity.  Downstream adapters
        # carry a fixed node role selected by their production entrypoint.
        target_agent_id = self.agent_role
        if target_agent_id not in {"242", "260"}:
            _fail("FOUNDATION_REPO_LAUNCHER_TASK_IDENTITY_INVALID")
        identity = self._identity(evidence, graph, inputs, agent_id=target_agent_id)
        # Re-read all 000 inputs rather than trusting its outer launch HMAC.
        component = self.bootstrap.foundation_fixed_component_issuer().load()
        controller = self._controller(bundle)
        controller.full_preflight(claims, component["receipt"])
        if not isinstance(receipt, dict):
            _fail("FOUNDATION_REPO_LAUNCHER_RECEIPT_INVALID")
        body = {key: value for key, value in receipt.items() if key != "attestation"}
        signature = hmac.new((root / _KEY).read_bytes(), canonical_bytes(body), hashlib.sha256).hexdigest()
        if not isinstance(receipt.get("attestation"), str) or not hmac.compare_digest(receipt["attestation"], signature):
            _fail("FOUNDATION_REPO_LAUNCHER_RECEIPT_FORGED")
        expected_schema = "foundation-repo-launch-receipt/v1" if target_agent_id == "242" else "foundation-repo-downstream-receipt/v1"
        expected_upstream = "241" if target_agent_id == "242" else "242"
        if (receipt.get("schema_version") != expected_schema or receipt.get("root_binding_id") != evidence.root_binding_id
                or receipt.get("inputs_sha256") != inputs["inputs_sha256"]):
            _fail("FOUNDATION_REPO_LAUNCHER_DOWNSTREAM_DRIFT")
        upstream_identity = receipt.get("task_identity")
        if not isinstance(upstream_identity, dict) or upstream_identity.get("agent_uuid") != graph["real_agents"][expected_upstream]["uuid"]:
            _fail("FOUNDATION_REPO_LAUNCHER_DOWNSTREAM_DRIFT")
        if target_agent_id == "242" and receipt.get("route") != _ROUTES:
            _fail("FOUNDATION_REPO_LAUNCHER_DOWNSTREAM_DRIFT")
        upstream_hash = sha256(receipt)
        accepted_body = {"schema_version": "foundation-repo-downstream-receipt/v1", "status": "accepted", "root_binding_id": evidence.root_binding_id, "task_identity": identity, "upstream_agent_id": expected_upstream, "upstream_receipt_sha256": upstream_hash, "inputs_sha256": inputs["inputs_sha256"], "component_production_record_sha256": sha256(component), "replay_key": sha256({"target": target_agent_id, "task": identity["task_id"], "upstream": upstream_hash})}
        accepted = {**accepted_body, "attestation": hmac.new((root / _KEY).read_bytes(), canonical_bytes(accepted_body), hashlib.sha256).hexdigest()}
        ledger = root / _LEDGER; ledger.mkdir(mode=0o700, exist_ok=True); _private(ledger, 0o700, "FOUNDATION_REPO_LAUNCHER_LEDGER_UNSAFE")
        path = ledger / f"{accepted_body['replay_key']}.json"
        if path.exists() or path.is_symlink():
            _private(path, 0o600, "FOUNDATION_REPO_LAUNCHER_REPLAY_UNSAFE")
            if _load(path, "FOUNDATION_REPO_LAUNCHER_REPLAY_UNSAFE") != accepted:
                _fail("FOUNDATION_REPO_LAUNCHER_REPLAY_DRIFT")
        else:
            self._atomic_json(path, accepted, "FOUNDATION_REPO_LAUNCHER_WRITE_FAILED")
            _private(path, 0o600, "FOUNDATION_REPO_LAUNCHER_REPLAY_UNSAFE")
        return accepted
