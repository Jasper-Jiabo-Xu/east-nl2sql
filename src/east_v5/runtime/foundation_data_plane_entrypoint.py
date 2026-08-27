"""Production-only Foundation local data-plane root and source gate.

The module deliberately has no model invocation, business-value selection, or
formal-database write path.  It owns the small mutable boundary that the v12
graph controller requires before EAS-19 can register runtime-only artifacts.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from east_v5.governance import ContractError, canonical_bytes, sha256


class FoundationDataPlaneError(ContractError):
    """A production data-plane precondition failed before any business work."""


def _fail(code: str) -> None:
    raise FoundationDataPlaneError(code)


@dataclass(frozen=True)
class ProvisionedRuntime:
    runtime_root: Path
    root_binding_id: str


@dataclass(frozen=True)
class FormalBaseline:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class RecoveredParentChain:
    """Accepted 210/220 lineage restored without recreating a 000 package."""

    task: dict[str, Any]
    closure: dict[str, Any]
    evidence_sha256: str
    recovery_receipt: dict[str, Any]


_CONTEXT_KEYS = {"resolver_version", "workspace_id", "project_id", "daemon_id"}
_MARKER = "daemon-root-binding-v12.json"
_STATE = "east-v5-full-runtime-v12-state.json"
_LAUNCH_INPUTS = "foundation-launch-inputs-v1.json"
_RECOVERY_DIR = "foundation-parent-chain-recovery-v1"
_CONTAINER_DIR = "foundation-parent-chain-container-v1"
_CONTAINER_MANIFEST = "foundation-parent-chain-container-manifest.json"
_MATERIALIZER_KEY = ".foundation-parent-chain-materializer-v1.key"
_MATERIALIZER_CONFIG = "config/foundation-parent-chain-materializer-v1.json"
_PARENT_CONTAINER_BYTES = {
    "foundation_intent_package.json": "09b94bcb79d279d1e13620aa3c406db047fbc2b6b941443b7aeb865de0899038",
    "foundation_task_package_projection.json": "5b4d582f20cb87ec30eb657a3f61edaf65d721d0293a701af3d9ae10def5580d",
    "manifest.json": "8329b2cb0e8277d2af8c735c44852234cf3c2e8d3acd544d2575f2ff074eff8b",
}
_INTENT_HASH = "f354921c06e3f1d1c8abdcc4cd6e1dd40c82c263e776c3571eb3db5893b27b20"
_PROJECTION_HASH = "1947f7f8942ed8bd9aaf8e9856d3c042706dc3b4200704f64bae651f45703192"
_RECOVERED_CLOSURE_HASH = "168aab095660895c36eaece21e9f4de6ea3a8874939dd1d195950fc70a57dfce"
_RECOVERY_SOURCE_ISSUE = "01a0386f-c03f-7be9-b2d6-45841f1a4a74"
_RECOVERY_SOURCE_TASK = "01a03882-8b63-7480-9c58-acf2213573a6"
_RECOVERY_SOURCE_AGENT = "1cdd93d3-b5fa-4dae-9b09-8320055c3072"
_RECOVERY_SOURCE_RUNTIME = "0e5e9dd9-5135-4937-bb03-92b77adb8395"
_RECOVERY_SOURCE_TRIGGER_COMMENT = "01a03882-8b49-7777-8e22-e6221a70917e"
_RECOVERY_SOURCE_COMMENT = "01a0388e-729c-7289-9c99-b94d075e984e"
_RECOVERED_CLOSURE_BYTES = "15f1e12e0fce75ac99fec764896dd7acadc055ee2c0771897affb67ab1990f8a"
_RECOVERED_EVIDENCE_BYTES = "0bd3ea76bf7f11f6b127bc3e6f6c88fb477d185547d1c963a2bf3f6c248f90a7"
_RECOVERED_TASK_HASH = "899ac6cdea3a9e08fa01d77102850952f9b8c83db3bf94672fe5ae8a31982fbe"
_RECOVERED_000_ANCESTOR_HASH = "664ccab637a68e01a6ac93ab7356f90f79c75b09b3a597fdab66d8db208c92f0"
_RECOVERED_RESOLVER_UNIVERSE_HASH = "5dd7a81e22c68199f212e1e37aa1ad8dd989eb34de33098c14fe6da656fcaa26"


def _platform_data_home(platform: str, home: Path) -> Path:
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Multica"
    if platform == "win32":
        _fail("FOUNDATION_DATA_PLANE_PLATFORM_UNSUPPORTED")
    return home / ".local" / "share" / "multica"


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class FoundationDataPlaneEntrypoint:
    """The sole production provisioner for a Foundation v12 data-plane root."""

    def __init__(self, reference_root: Path, *, home: Path | None = None, platform: str | None = None) -> None:
        self.reference_root = Path(reference_root).resolve()
        self.home = Path.home() if home is None else Path(home).resolve()
        self.platform = sys.platform if platform is None else platform
        if not self.reference_root.is_dir() or self.reference_root.is_symlink():
            _fail("FOUNDATION_DATA_PLANE_REFERENCE_ROOT_INVALID")

    @staticmethod
    def _context(context: dict[str, Any]) -> tuple[dict[str, str], str]:
        if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS:
            _fail("FOUNDATION_DATA_PLANE_CONTEXT_INVALID")
        if context.get("resolver_version") != "daemon_local_platform_data_resolver_v1" or not all(isinstance(context.get(key), str) and context[key] for key in _CONTEXT_KEYS - {"resolver_version"}):
            _fail("FOUNDATION_DATA_PLANE_CONTEXT_INVALID")
        clean = {key: str(context[key]) for key in sorted(_CONTEXT_KEYS)}
        return clean, sha256(clean)

    @staticmethod
    def _assert_private(path: Path, mode: int, code: str) -> None:
        try:
            if path.is_symlink() or not path.exists() or stat.S_IMODE(path.stat().st_mode) != mode:
                _fail(code)
        except OSError as exc:
            raise FoundationDataPlaneError(code) from exc

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        fd, raw = tempfile.mkstemp(prefix=".foundation-data-plane-", dir=path.parent)
        temporary = Path(raw)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical_bytes(value)); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise FoundationDataPlaneError("FOUNDATION_DATA_PLANE_ROOT_WRITE_FAILED") from exc

    def provision(self, context: dict[str, Any]) -> ProvisionedRuntime:
        """Resolve and atomically bind one daemon-specific 0700 v12 root."""
        clean, binding = self._context(context)
        root = _platform_data_home(self.platform, self.home) / "east-v5-runtime" / clean["workspace_id"] / clean["project_id"] / clean["daemon_id"]
        if root.is_absolute() is False or any(part in {"", ".", ".."} for part in root.parts):
            _fail("FOUNDATION_DATA_PLANE_ROOT_INVALID")
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.is_symlink():
                _fail("FOUNDATION_DATA_PLANE_ROOT_SYMLINK")
            if stat.S_IMODE(root.stat().st_mode) != 0o700:
                root.chmod(0o700)
            self._assert_private(root, 0o700, "FOUNDATION_DATA_PLANE_ROOT_PERMISSION_DRIFT")
        except OSError as exc:
            raise FoundationDataPlaneError("FOUNDATION_DATA_PLANE_ROOT_UNAVAILABLE") from exc

        marker = root / _MARKER
        expected_marker = {"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": binding}
        if marker.exists() or marker.is_symlink():
            self._assert_private(marker, 0o600, "FOUNDATION_DATA_PLANE_ROOT_MARKER_DRIFT")
            try:
                if json.loads(marker.read_text(encoding="utf-8")) != expected_marker:
                    _fail("FOUNDATION_DATA_PLANE_ROOT_MARKER_DRIFT")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise FoundationDataPlaneError("FOUNDATION_DATA_PLANE_ROOT_MARKER_DRIFT") from exc
        else:
            self._atomic_json(marker, expected_marker)
            self._assert_private(marker, 0o600, "FOUNDATION_DATA_PLANE_ROOT_MARKER_DRIFT")

        state = root / _STATE
        expected_state = {"schema_version": "east-v5-full-runtime-state/v12", "preflights": {}, "runs": {}}
        if state.exists() or state.is_symlink():
            self._assert_private(state, 0o600, "FOUNDATION_DATA_PLANE_STATE_DRIFT")
            try:
                loaded = json.loads(state.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise FoundationDataPlaneError("FOUNDATION_DATA_PLANE_STATE_DRIFT") from exc
            if not isinstance(loaded, dict) or set(loaded) != set(expected_state) or loaded.get("schema_version") != expected_state["schema_version"] or not isinstance(loaded.get("preflights"), dict) or not isinstance(loaded.get("runs"), dict):
                _fail("FOUNDATION_DATA_PLANE_STATE_DRIFT")
        else:
            self._atomic_json(state, expected_state)
            self._assert_private(state, 0o600, "FOUNDATION_DATA_PLANE_STATE_DRIFT")
        return ProvisionedRuntime(root, binding)

    def resolve_formal_baseline(self, lock_path: Path) -> FormalBaseline:
        """Resolve exactly one EAS-106 locked source without path probing."""
        try:
            lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))["lock"]
            raw, expected_hash, expected_size = (lock["formal_db_baseline_path"], lock["formal_db_baseline_sha256"], lock["formal_db_baseline_size_bytes"])
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise FoundationDataPlaneError("FOUNDATION_DATA_PLANE_INPUT_LOCK_INVALID") from exc
        relative = Path(raw) if isinstance(raw, str) else Path("")
        if not isinstance(raw, str) or relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            _fail("FOUNDATION_DATA_PLANE_LOCATOR_INVALID")
        if relative.parts[0] == self.reference_root.name:
            relative = Path(*relative.parts[1:])
        if not relative.parts:
            _fail("FOUNDATION_DATA_PLANE_LOCATOR_INVALID")
        candidate = (self.reference_root / relative).resolve(strict=False)
        if self.reference_root not in candidate.parents or candidate.is_symlink() or not candidate.is_file():
            _fail("FOUNDATION_DATA_PLANE_SOURCE_MISSING")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or not isinstance(expected_size, int) or expected_size < 0:
            _fail("FOUNDATION_DATA_PLANE_INPUT_LOCK_INVALID")
        observed = _sha_file(candidate)
        if candidate.stat().st_size != expected_size or observed != expected_hash:
            _fail("FOUNDATION_DATA_PLANE_SOURCE_DRIFT")
        return FormalBaseline(candidate, observed, expected_size)

    @staticmethod
    def _platform_run_record() -> dict[str, Any]:
        """Project the single frozen source run and verify its provenance.

        Every run only needs a parseable, unique task id.  The frozen
        ``source_task_id`` must match exactly one run, and only that run is held
        to the full provenance contract; sibling direct runs legitimately carry
        ``trigger_comment_id=null`` and ``delivered_comment_ids=[]``.
        """
        try:
            completed = subprocess.run(
                ["multica", "issue", "runs", _RECOVERY_SOURCE_ISSUE, "--output", "json"],
                check=True, capture_output=True, text=True,
            )
            raw = json.loads(completed.stdout)
        except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_RUN_RECORD_UNAVAILABLE") from exc
        if not isinstance(raw, list) or not raw:
            _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
        matches: list[dict[str, Any]] = []
        task_ids: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
            task_id = item.get("id")
            if not isinstance(task_id, str) or not task_id:
                _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
            if task_id in task_ids:
                _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
            task_ids.add(task_id)
            if task_id == _RECOVERY_SOURCE_TASK:
                matches.append(item)
        if len(matches) != 1:
            _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
        source = matches[0]
        for key in ("status", "runtime_id", "work_dir", "agent_id", "issue_id", "kind"):
            field = source.get(key)
            if not isinstance(field, str) or not field:
                _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
        trigger = source.get("trigger_comment_id")
        if not isinstance(trigger, str) or not trigger:
            _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
        delivered = source.get("delivered_comment_ids")
        if (not isinstance(delivered, list) or not delivered
                or any(not isinstance(comment, str) or not comment for comment in delivered)):
            _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
        if (source["issue_id"] != _RECOVERY_SOURCE_ISSUE
                or source["status"] != "completed"
                or source["agent_id"] != _RECOVERY_SOURCE_AGENT
                or source["runtime_id"] != _RECOVERY_SOURCE_RUNTIME
                or source["kind"] != "comment"
                or trigger != _RECOVERY_SOURCE_TRIGGER_COMMENT
                or delivered != [_RECOVERY_SOURCE_TRIGGER_COMMENT]):
            _fail("FOUNDATION_PARENT_CHAIN_RUN_RECORD_INVALID")
        return {
            "id": source["id"], "status": source["status"], "runtime_id": source["runtime_id"],
            "delivered_comment_ids": delivered, "work_dir": source["work_dir"], "trigger_comment_id": trigger,
        }

    @staticmethod
    def _private_directory(path: Path, code: str) -> None:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o700:
                _fail(code)
        except OSError as exc:
            raise FoundationDataPlaneError(code) from exc

    @staticmethod
    def _copy_verified(source: Path, target: Path, expected_sha256: str) -> None:
        if source.is_symlink() or not source.is_file() or _sha_file(source) != expected_sha256:
            _fail("FOUNDATION_PARENT_CHAIN_BYTE_DRIFT")
        try:
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file() or _sha_file(target) != expected_sha256:
                    _fail("FOUNDATION_PARENT_CHAIN_RECOVERY_DRIFT")
                FoundationDataPlaneEntrypoint._assert_private(target, 0o600, "FOUNDATION_PARENT_CHAIN_RECOVERY_DRIFT")
                return
            temporary = target.with_name(f".{target.name}.tmp")
            shutil.copyfile(source, temporary)
            temporary.chmod(0o600)
            if _sha_file(temporary) != expected_sha256:
                _fail("FOUNDATION_PARENT_CHAIN_BYTE_DRIFT")
            os.replace(temporary, target)
            FoundationDataPlaneEntrypoint._assert_private(target, 0o600, "FOUNDATION_PARENT_CHAIN_RECOVERY_DRIFT")
        except OSError as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_COPY_FAILED") from exc

    @staticmethod
    def _rehydrate_task(payload: dict[str, Any], transport: dict[str, str]) -> dict[str, Any]:
        """Canonical 210 projection rehydration; it never claims an Agent call."""
        required = {"run_id", "trace_id", "created_at"}
        if set(transport) != required or not all(isinstance(transport[key], str) and transport[key] for key in required):
            _fail("EAS114_210_REHYDRATION_DRIFT")
        try:
            build = importlib.import_module("east_v5.agents.210.foundation").build_foundation_task_package
            return build(payload, run_id=transport["run_id"], trace_id=transport["trace_id"], created_at=transport["created_at"], parents=[])
        except Exception as exc:
            raise FoundationDataPlaneError("EAS114_210_REHYDRATION_DRIFT") from exc

    def _verify_materialization(self, runtime: ProvisionedRuntime, receipt: dict[str, Any]) -> None:
        """Accept only the RuntimeBootstrap domain-separated container receipt."""
        if not isinstance(receipt, dict):
            _fail("FOUNDATION_PARENT_CHAIN_MATERIALIZATION_RECEIPT_REQUIRED")
        required = {"schema_version", "root_binding_id", "bootstrap", "skill_manifest_sha256", "config_sha256", "attachments", "eas111_evidence", "assets", "runtime_manifest_sha256", "runtime_manifest_local_sha256", "hierarchy_mapping_sha256", "container_sha256", "receipt_sha256", "attestation"}
        body = {key: value for key, value in receipt.items() if key not in {"receipt_sha256", "attestation"}}
        if set(receipt) != required or receipt.get("schema_version") != "foundation-parent-chain-materialization-receipt/v2" or receipt.get("root_binding_id") != runtime.root_binding_id or receipt.get("receipt_sha256") != sha256(body):
            _fail("FOUNDATION_PARENT_CHAIN_MATERIALIZATION_RECEIPT_INVALID")
        key_path = runtime.runtime_root / _MATERIALIZER_KEY
        try:
            self._assert_private(key_path, 0o600, "FOUNDATION_PARENT_CHAIN_MATERIALIZATION_KEY_UNSAFE")
            key = key_path.read_bytes()
        except OSError as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_MATERIALIZATION_KEY_UNAVAILABLE") from exc
        if len(key) != 32 or not isinstance(receipt.get("attestation"), str) or not hmac.compare_digest(receipt["attestation"], hmac.new(key, canonical_bytes({**body, "receipt_sha256": receipt["receipt_sha256"]}), hashlib.sha256).hexdigest()):
            _fail("FOUNDATION_PARENT_CHAIN_MATERIALIZATION_RECEIPT_INVALID")
        try:
            config_path = Path(__file__).resolve().parents[3] / _MATERIALIZER_CONFIG
            if _sha_file(config_path) != receipt["config_sha256"]:
                _fail("FOUNDATION_PARENT_CHAIN_MATERIALIZATION_CONFIG_DRIFT")
            container = runtime.runtime_root / _CONTAINER_DIR
            manifest = json.loads((container / _CONTAINER_MANIFEST).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_MATERIALIZATION_CONTAINER_INVALID") from exc
        if container.is_symlink() or manifest.get("root_binding_id") != runtime.root_binding_id or manifest.get("config_sha256") != receipt["config_sha256"] or manifest.get("attachments") != receipt["attachments"] or manifest.get("eas111_evidence") != receipt["eas111_evidence"] or manifest.get("assets") != receipt["assets"] or manifest.get("runtime_manifest_sha256") != receipt["runtime_manifest_sha256"] or manifest.get("runtime_manifest_local_sha256") != receipt["runtime_manifest_local_sha256"] or manifest.get("hierarchy_mapping_sha256") != receipt["hierarchy_mapping_sha256"] or manifest.get("container_sha256") != receipt["container_sha256"]:
            _fail("FOUNDATION_PARENT_CHAIN_MATERIALIZATION_CONTAINER_INVALID")

    def foundation_resolver_handle(self, runtime: ProvisionedRuntime, materialization_receipt: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """Return the only v2-materialized resolver and hierarchy mapping.

        Neither a business caller nor EAS-19 may supply a manifest path, asset
        package, mapping, or locator.  The returned resolver is read-only and
        all physical paths remain inside the runtime root.
        """
        self._verify_materialization(runtime, materialization_receipt)
        container = runtime.runtime_root / _CONTAINER_DIR
        manifest_path = container / "constraint-assets-runtime-manifest.json"
        mapping_path = container / "foundation-hierarchy-endpoint-mapping.json"
        try:
            if any(path.is_symlink() or not path.is_file() for path in (manifest_path, mapping_path)):
                _fail("FOUNDATION_PARENT_CHAIN_RESOLVER_HANDLE_INVALID")
            self._assert_private(manifest_path, 0o600, "FOUNDATION_PARENT_CHAIN_RESOLVER_HANDLE_INVALID")
            self._assert_private(mapping_path, 0o600, "FOUNDATION_PARENT_CHAIN_RESOLVER_HANDLE_INVALID")
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            if mapping.get("schema_version") != "foundation-hierarchy-endpoint-mapping/v1" or mapping.get("content_hash") != materialization_receipt["hierarchy_mapping_sha256"]:
                _fail("FOUNDATION_PARENT_CHAIN_HIERARCHY_MAPPING_DRIFT")
            roots = {"repo_root": str(Path(__file__).resolve().parents[3]), "runtime_root": str(runtime.runtime_root), "reference_root": str(self.reference_root), "reference_read_only": True}
            resolver = importlib.import_module("east_v5.agents.242.resolver").build_constraint_asset_resolver(Path(__file__).resolve().parents[3], roots, manifest_path)
        except FoundationDataPlaneError:
            raise
        except Exception as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_RESOLVER_HANDLE_INVALID") from exc
        return resolver, {"schema_version": mapping["schema_version"], "content_hash": mapping["content_hash"]}

    def build_foundation_eas19_inputs(
        self, runtime: ProvisionedRuntime, materialization_receipt: dict[str, Any], baseline: FormalBaseline,
    ) -> Any:
        """Run the only production EAS-19 Foundation assembly route.

        There are intentionally no caller parameters for paths, task/closure
        packages, hierarchy mappings, resolver output, SQL, or tuples.  They
        are recovered or materialized under the same bound runtime root.
        """
        if (not isinstance(baseline, FormalBaseline) or baseline.path.is_symlink()
                or self.reference_root not in baseline.path.resolve().parents
                or _sha_file(baseline.path) != baseline.sha256
                or baseline.path.stat().st_size != baseline.size_bytes):
            _fail("EAS19_FOUNDATION_SELECTOR_BASELINE_DRIFT")
        recovered = self.recover_parent_chain(runtime, materialization_receipt)
        resolver, _ = self.foundation_resolver_handle(runtime, materialization_receipt)
        universe = resolver.enumerate(recovered.closure)
        if universe.get("content_sha256") != _RECOVERED_RESOLVER_UNIVERSE_HASH:
            _fail("EAS19_FOUNDATION_SELECTOR_RESOLVER_UNIVERSE_DRIFT")
        container = runtime.runtime_root / _CONTAINER_DIR
        try:
            mapping = json.loads((container / "foundation-hierarchy-endpoint-mapping.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FoundationDataPlaneError("EAS19_FOUNDATION_SELECTOR_HIERARCHY_MAPPING_DRIFT") from exc
        try:
            selector = importlib.import_module("east_v5.runtime.eas19_foundation_selector").Eas19FoundationParentSnapshotSelector
            selection = selector(baseline.path, baseline.sha256, baseline.size_bytes).select(
                recovered.task, recovered.closure, hierarchy_mapping=mapping, resolver_universe=universe,
            )
            validate_context = importlib.import_module("east_v5.agents.foundation_contract").validate_context
            validate_context(selection.generation_context, recovered.task, recovered.closure, selection.database_snapshot)
            ArtifactRegistry = importlib.import_module("east_v5.artifacts").ArtifactRegistry
            repo_root = Path(__file__).resolve().parents[3]
            registry = ArtifactRegistry(repo_root, {
                "repo_root": str(repo_root), "runtime_root": str(runtime.runtime_root),
                "reference_root": str(self.reference_root), "reference_read_only": True,
            }, "EAS-114", recovered.task["envelope"]["run_id"], recovered.task["envelope"]["attempt_no"])
            # Register in lineage order; replay is idempotent, while any
            # content drift is rejected by the immutable local registry.
            for package in (recovered.task, recovered.closure, selection.database_snapshot, selection.generation_context):
                registered = registry.register(package["envelope"], package["payload"])
                if registry.resolve({key: registered[key] for key in ("artifact_id", "version", "content_hash")}) != package:
                    _fail("EAS19_FOUNDATION_SELECTOR_REGISTRY_READBACK_DRIFT")
            # This is the only hand-off from EAS-19 to the repo-side launcher.
            # It contains refs and hashes only; physical locators and tuple
            # values remain in the private registry/snapshot files.
            self._seal_launch_inputs(runtime, recovered, selection)
            return selection
        except FoundationDataPlaneError:
            raise
        except ContractError as exc:
            raise FoundationDataPlaneError(str(exc)) from exc

    def _seal_launch_inputs(self, runtime: ProvisionedRuntime, recovered: RecoveredParentChain, selection: Any) -> dict[str, Any]:
        """Atomically seal the four verified Foundation inputs for launch.

        This method is deliberately private to the production data-plane
        entrypoint.  A caller cannot supply refs, a path, a component receipt,
        or a claim set: each value is derived from packages which have just
        passed the EAS-19 validator and immutable registry read-back.
        """
        try:
            from east_v5.artifacts import artifact_ref
            snapshot = selection.database_snapshot
            context = selection.generation_context
            refs = {
                "foundation_task_ref": artifact_ref(recovered.task["envelope"]),
                "structure_closure_ref": artifact_ref(recovered.closure["envelope"]),
                "database_snapshot_ref": artifact_ref(snapshot["envelope"]),
                "generation_context_ref": artifact_ref(context["envelope"]),
            }
            task_env = recovered.task["envelope"]
            if (not isinstance(selection.resolver_universe_ref, dict)
                    or not isinstance(selection.resolver_universe_ref.get("content_hash"), str)):
                _fail("FOUNDATION_LAUNCH_INPUTS_INVALID")
            body = {
                "schema_version": "foundation-launch-inputs/v1",
                "root_binding_id": runtime.root_binding_id,
                "issue_id": "EAS-114",
                "run_id": task_env["run_id"],
                "attempt": task_env["attempt_no"],
                "resolver_universe_hash": selection.resolver_universe_ref["content_hash"],
                **refs,
            }
            if (not isinstance(body["run_id"], str) or not body["run_id"]
                    or body["attempt"] not in {1, 2, 3}):
                _fail("FOUNDATION_LAUNCH_INPUTS_INVALID")
            key_path = runtime.runtime_root / _MATERIALIZER_KEY
            self._assert_private(key_path, 0o600, "FOUNDATION_LAUNCH_INPUTS_KEY_UNSAFE")
            key = key_path.read_bytes()
            if len(key) != 32:
                _fail("FOUNDATION_LAUNCH_INPUTS_KEY_UNSAFE")
            receipt = {
                **body,
                "inputs_sha256": sha256(body),
                "attestation": hmac.new(key, canonical_bytes(body), hashlib.sha256).hexdigest(),
            }
            path = runtime.runtime_root / _LAUNCH_INPUTS
            if path.exists() or path.is_symlink():
                self._assert_private(path, 0o600, "FOUNDATION_LAUNCH_INPUTS_DRIFT")
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != receipt:
                    _fail("FOUNDATION_LAUNCH_INPUTS_DRIFT")
            else:
                self._atomic_json(path, receipt)
                self._assert_private(path, 0o600, "FOUNDATION_LAUNCH_INPUTS_DRIFT")
            return receipt
        except FoundationDataPlaneError:
            raise
        except (KeyError, OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise FoundationDataPlaneError("FOUNDATION_LAUNCH_INPUTS_INVALID") from exc

    def recover_parent_chain(self, runtime: ProvisionedRuntime, materialization_receipt: dict[str, Any]) -> RecoveredParentChain:
        """Recover the accepted EAS-113 output with one fixed, fail-closed route.

        The only source location is the `work_dir` authenticated by the
        platform task record.  This deliberately rejects caller paths and does
        not mint, register, or otherwise substitute an unavailable 000 package.
        """
        if runtime.runtime_root.is_symlink() or not runtime.runtime_root.is_dir():
            _fail("FOUNDATION_PARENT_CHAIN_ROOT_INVALID")
        self._verify_materialization(runtime, materialization_receipt)
        source = self._platform_run_record()
        raw_work_dir = source.get("work_dir")
        if not isinstance(raw_work_dir, str) or not Path(raw_work_dir).is_absolute():
            _fail("FOUNDATION_PARENT_CHAIN_SOURCE_WORKDIR_INVALID")
        work_dir = Path(raw_work_dir).resolve(strict=False)
        if work_dir.is_symlink() or not work_dir.is_dir():
            _fail("FOUNDATION_PARENT_CHAIN_SOURCE_WORKDIR_INVALID")
        # This relative path is fixed by the delivered source task, not caller input.
        source_output = work_dir / "eas113_rerun_out"
        closure_source, evidence_source = source_output / "structure_closure.json", source_output / "evidence.json"
        recovery_dir = runtime.runtime_root / _RECOVERY_DIR
        self._private_directory(recovery_dir, "FOUNDATION_PARENT_CHAIN_RECOVERY_DIR_UNSAFE")
        closure_local, evidence_local = recovery_dir / "structure_closure.json", recovery_dir / "evidence.json"
        self._copy_verified(closure_source, closure_local, _RECOVERED_CLOSURE_BYTES)
        self._copy_verified(evidence_source, evidence_local, _RECOVERED_EVIDENCE_BYTES)
        try:
            closure = json.loads(closure_local.read_text(encoding="utf-8"))
            evidence = json.loads(evidence_local.read_text(encoding="utf-8"))
            validate_closure = importlib.import_module("east_v5.agents.220.closure").validate_structure_closure_package
            validate_closure(closure)
        except Exception as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_CLOSURE_INVALID") from exc
        envelope, payload = closure["envelope"], closure["payload"]
        if (envelope.get("mode"), envelope.get("producer_id"), envelope.get("content_hash"), envelope.get("storage_locator")) != ("foundation", "220", _RECOVERED_CLOSURE_HASH, None) or payload.get("foundation_task_ref", {}).get("content_hash") != _RECOVERED_TASK_HASH or (len(payload.get("tables", [])), len(payload.get("fields", [])), len(payload.get("references", []))) != (71, 837, 456):
            _fail("FOUNDATION_PARENT_CHAIN_CLOSURE_DRIFT")
        if not isinstance(evidence, dict) or evidence.get("asset_000", {}).get("ref", {}).get("content_hash") != _RECOVERED_000_ANCESTOR_HASH or evidence.get("closure_220", {}).get("ref", {}).get("content_hash") != _RECOVERED_CLOSURE_HASH or evidence.get("closure_self_validation", {}).get("closure_envelope_hash_identical") is not True or not isinstance(evidence.get("forbidden_module_calls"), dict) or any(value != 0 for value in evidence["forbidden_module_calls"].values()):
            _fail("FOUNDATION_PARENT_CHAIN_EVIDENCE_DRIFT")
        container = runtime.runtime_root / _CONTAINER_DIR
        required_container = set(_PARENT_CONTAINER_BYTES) | {_CONTAINER_MANIFEST, "approved-assets", "constraint-assets-runtime-manifest.json", "foundation-hierarchy-endpoint-mapping.json", "eas111-evidence.json"}
        try:
            if container.is_symlink() or not container.is_dir() or set(path.name for path in container.iterdir()) != required_container:
                _fail("FOUNDATION_PARENT_CHAIN_CONTAINER_INVALID")
            for name, expected in _PARENT_CONTAINER_BYTES.items():
                item = container / name
                if item.is_symlink() or not item.is_file() or _sha_file(item) != expected:
                    _fail("FOUNDATION_PARENT_CHAIN_CONTAINER_BYTE_DRIFT")
            intent = json.loads((container / "foundation_intent_package.json").read_text(encoding="utf-8"))
            projection = json.loads((container / "foundation_task_package_projection.json").read_text(encoding="utf-8"))
            manifest = json.loads((container / "manifest.json").read_text(encoding="utf-8"))
            if intent.get("content_sha256") != _INTENT_HASH or sha256(projection) != _PROJECTION_HASH or manifest.get("intent_content_sha256") != _INTENT_HASH or manifest.get("task_projection_content_sha256") != _PROJECTION_HASH:
                _fail("FOUNDATION_PARENT_CHAIN_CONTAINER_LINEAGE_DRIFT")
            rehydrated = self._rehydrate_task(projection, {key: envelope[key] for key in ("run_id", "trace_id", "created_at")})
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_CONTAINER_INVALID") from exc
        if rehydrated["envelope"]["content_hash"] != _RECOVERED_TASK_HASH or payload["foundation_task_ref"] != {key: rehydrated["envelope"][key] for key in ("artifact_id", "version", "content_hash")}:
            _fail("EAS114_210_REHYDRATION_DRIFT")
        try:
            ArtifactRegistry = importlib.import_module("east_v5.artifacts").ArtifactRegistry
            repo_root = Path(__file__).resolve().parents[3]
            registry = ArtifactRegistry(repo_root, {
                "repo_root": str(repo_root), "runtime_root": str(runtime.runtime_root),
                "reference_root": str(self.reference_root), "reference_read_only": True,
            }, "EAS-114", envelope["run_id"], envelope["attempt_no"])
            registered = registry.register(rehydrated["envelope"], rehydrated["payload"])
            task_ref = {key: registered[key] for key in ("artifact_id", "version", "content_hash")}
            if registry.resolve(task_ref) != rehydrated:
                _fail("FOUNDATION_PARENT_CHAIN_REGISTRY_READBACK_DRIFT")
        except FoundationDataPlaneError:
            raise
        except Exception as exc:
            raise FoundationDataPlaneError("FOUNDATION_PARENT_CHAIN_REGISTRY_UNAVAILABLE") from exc
        receipt = {
            "schema_version": "foundation_parent_chain_recovery_receipt/v1", "source_issue_id": _RECOVERY_SOURCE_ISSUE,
            "source_task_id": _RECOVERY_SOURCE_TASK, "source_runtime_id": _RECOVERY_SOURCE_RUNTIME,
            "source_comment_id": _RECOVERY_SOURCE_COMMENT, "closure_sha256": _RECOVERED_CLOSURE_BYTES,
            "evidence_sha256": _RECOVERED_EVIDENCE_BYTES, "foundation_task_hash": _RECOVERED_TASK_HASH,
            "closure_hash": envelope["content_hash"], "ancestor_000_hash": _RECOVERED_000_ANCESTOR_HASH,
            "root_binding_id": runtime.root_binding_id, "registry_task_ref": task_ref,
            "rehydrated_from_accepted_projection": True,
        }
        receipt["receipt_sha256"] = sha256(receipt)
        receipt_path = recovery_dir / "recovery-receipt.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            self._assert_private(receipt_path, 0o600, "FOUNDATION_PARENT_CHAIN_RECOVERY_DRIFT")
            existing_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if existing_receipt != receipt:
                _fail("FOUNDATION_PARENT_CHAIN_RECOVERY_DRIFT")
        else:
            self._atomic_json(receipt_path, receipt)
            self._assert_private(receipt_path, 0o600, "FOUNDATION_PARENT_CHAIN_RECOVERY_DRIFT")
        return RecoveredParentChain(rehydrated, closure, _RECOVERED_EVIDENCE_BYTES, receipt)
