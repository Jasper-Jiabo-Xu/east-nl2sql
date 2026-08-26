"""Trusted v12 graph to Foundation v1 adapter bridge.

This module deliberately has no business-data construction API.  It binds the
already accepted v12 graph state, an actual EAS-15 registry artifact and its
``task_execution_receipt/v1`` before it derives the small v1 envelope accepted
by :class:`RuntimeAdapter`.  Grants are local, authenticated, and never leave
the daemon-owned runtime root.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any

from east_v5.artifacts import ArtifactRegistry, artifact_ref, validate_envelope
from east_v5.governance import ContractError, canonical_bytes, sha256
from east_v5.runtime.adapter import RuntimeAdapter
from east_v5.runtime.bootstrap import BootstrapEvidence, root_binding_id


_BRIDGE_DIR = "foundation-graph-adapter-bridge-v1"
_KEY = "bridge.key"
_GRANTS = "grants"
_HEX = set("0123456789abcdef")
_ROUTES = {"241": ("242", "bound_data"), "242": ("260", "verified_bound_data")}
_TARGETS = {
    "220": "94511939-5004-4f86-9f03-016d2484ff88",
    "241": "7df640f9-973f-4c46-8302-df1256f60146",
    "242": "4e801c18-7048-4227-a5c7-515f51a5e5ba",
}
_FOUNDATION_RUNTIME = "0e5e9dd9-5135-4937-bb03-92b77adb8395"


class FoundationGraphAdapterBridgeError(ContractError):
    pass


def _fail(code: str) -> None:
    raise FoundationGraphAdapterBridgeError(code)


def _canon(value: Any) -> bytes:
    return canonical_bytes(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canon(value)).hexdigest()


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FoundationGraphAdapterBridgeError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _ref(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"artifact_id", "version", "content_hash"}:
        _fail(code)
    if not isinstance(value["artifact_id"], str) or not isinstance(value["version"], int) or isinstance(value["version"], bool) or value["version"] < 1 or not isinstance(value["content_hash"], str) or len(value["content_hash"]) != 64 or set(value["content_hash"]) - _HEX:
        _fail(code)
    return dict(value)


class FoundationGraphAdapterBridge:
    """Derive only Foundation 241/242 v1 adapters from accepted v12 state."""

    def __init__(self, checkout: Path, roots: dict[str, Any], v12_skill_root: Path) -> None:
        self.checkout = Path(checkout).resolve()
        self.roots = dict(roots)
        self.runtime_root = Path(self.roots.get("runtime_root", "")).resolve()
        self.skill_root = Path(v12_skill_root).resolve()
        self.manifest = _load(self.skill_root / "manifest.json", "FOUNDATION_BRIDGE_V12_MANIFEST_UNREADABLE")
        self.manifest_hash = hashlib.sha256((self.skill_root / "manifest.json").read_bytes()).hexdigest()
        self.graph = _load(self.skill_root / "config" / "full-runtime-graph.json", "FOUNDATION_BRIDGE_V12_GRAPH_UNREADABLE")
        self._validate_static_inputs()
        self.ledger = self._prepare_ledger()

    def _validate_static_inputs(self) -> None:
        if not self.checkout.is_dir() or not self.runtime_root.is_dir() or not self.skill_root.is_dir():
            _fail("FOUNDATION_BRIDGE_ROOT_UNAVAILABLE")
        try:
            if self.runtime_root.is_symlink() or stat.S_IMODE(self.runtime_root.stat().st_mode) != 0o700:
                _fail("FOUNDATION_BRIDGE_RUNTIME_ROOT_UNSAFE")
        except OSError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_RUNTIME_ROOT_UNSAFE") from exc
        marker = _load(self.runtime_root / "daemon-root-binding-v12.json", "FOUNDATION_BRIDGE_ROOT_MARKER_INVALID")
        if set(marker) != {"schema_version", "root_binding_id"} or marker.get("schema_version") != "east-v5-daemon-root-binding/v12" or not isinstance(marker.get("root_binding_id"), str) or len(marker["root_binding_id"]) != 64 or set(marker["root_binding_id"]) - _HEX:
            _fail("FOUNDATION_BRIDGE_ROOT_MARKER_INVALID")
        self.root_marker = marker["root_binding_id"]
        if (self.manifest.get("schema_version"), self.manifest.get("skill_name"), self.manifest.get("skill_version")) != ("workspace_skill_bundle_manifest/v12", "east-v5-runtime-bootstrap-v12", "v12"):
            _fail("FOUNDATION_BRIDGE_V12_MANIFEST_INVALID")
        source_head = self.manifest.get("source_candidate_head")
        if not isinstance(source_head, str) or len(source_head) != 40 or set(source_head) - _HEX:
            _fail("FOUNDATION_BRIDGE_V12_MANIFEST_HEAD_INVALID")
        if self.graph.get("schema_version") != "east-v5-full-runtime-graph/v12" or not isinstance(self.graph.get("real_agents"), dict):
            _fail("FOUNDATION_BRIDGE_V12_GRAPH_INVALID")
        for target, uuid in _TARGETS.items():
            configured = self.graph["real_agents"].get(target)
            if not isinstance(configured, dict) or configured.get("uuid") != uuid:
                _fail("FOUNDATION_BRIDGE_TARGET_DRIFT")
        if any(self.graph["real_agents"][target].get("runtime_id") != _FOUNDATION_RUNTIME for target in ("241", "242")):
            _fail("FOUNDATION_BRIDGE_RUNTIME_DRIFT")

    def _prepare_ledger(self) -> Path:
        try:
            ledger = self.runtime_root / _BRIDGE_DIR
            ledger.mkdir(mode=0o700, exist_ok=True)
            grants = ledger / _GRANTS
            grants.mkdir(mode=0o700, exist_ok=True)
            if any(path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o700 for path in (ledger, grants)):
                _fail("FOUNDATION_BRIDGE_LEDGER_UNSAFE")
            key = ledger / _KEY
            if key.exists():
                if key.is_symlink() or stat.S_IMODE(key.stat().st_mode) != 0o600 or len(key.read_bytes()) != 32:
                    _fail("FOUNDATION_BRIDGE_KEY_INVALID")
            else:
                descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(os.urandom(32))
        except FileExistsError:
            return self._prepare_ledger()
        except OSError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_LEDGER_UNAVAILABLE") from exc
        return ledger

    def _key(self) -> bytes:
        self._assert_ledger_permissions()
        path = self.ledger / _KEY
        try:
            value = path.read_bytes()
            if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600 or len(value) != 32:
                _fail("FOUNDATION_BRIDGE_KEY_INVALID")
            return value
        except OSError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_KEY_UNAVAILABLE") from exc

    def _assert_ledger_permissions(self) -> None:
        """Recheck the mutable local trust boundary on every ledger access."""
        try:
            if any(path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o700 for path in (self.runtime_root, self.ledger, self.ledger / _GRANTS)):
                _fail("FOUNDATION_BRIDGE_LEDGER_UNSAFE")
        except OSError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_LEDGER_UNSAFE") from exc

    def _state(self) -> dict[str, Any]:
        state = _load(self.runtime_root / "east-v5-full-runtime-v12-state.json", "FOUNDATION_BRIDGE_V12_STATE_REQUIRED")
        if set(state) != {"schema_version", "preflights", "runs"} or state.get("schema_version") != "east-v5-full-runtime-state/v12" or not isinstance(state["preflights"], dict) or not isinstance(state["runs"], dict):
            _fail("FOUNDATION_BRIDGE_V12_STATE_INVALID")
        return state

    def _validate_v12(self, target: str, envelope: dict[str, Any], claims: dict[str, Any], receipt: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        state = self._state()
        required = {"schema_version", "run_id", "mode", "attempt", "target_agent_id", "target_agent_uuid", "root_binding_id", "preflight_token", "input_receipt_hashes", "outcome"}
        if set(envelope) != required or envelope.get("schema_version") != "runtime_graph_envelope/v12" or envelope.get("mode") != "foundation" or envelope.get("outcome") != "success" or envelope.get("target_agent_id") != target or envelope.get("target_agent_uuid") != _TARGETS[target] or envelope.get("root_binding_id") != self.root_marker or not isinstance(envelope.get("run_id"), str) or not envelope["run_id"] or envelope.get("attempt") not in {1, 2, 3} or not isinstance(envelope.get("preflight_token"), str):
            _fail("FOUNDATION_BRIDGE_V12_TASK_INVALID")
        preflight = state["preflights"].get(envelope["preflight_token"])
        if not isinstance(claims, dict) or claims.get("schema_version") != "east-v5-full-claims/v12" or not isinstance(claims.get("agents"), dict):
            _fail("FOUNDATION_BRIDGE_V12_CLAIM_DRIFT")
        claim = claims["agents"].get(target)
        if not isinstance(preflight, dict) or preflight.get("root_binding_id") != self.root_marker or preflight.get("manifest_sha256") != self.manifest_hash or preflight.get("claims_sha256") != _hash(claims):
            _fail("FOUNDATION_BRIDGE_PREFLIGHT_REQUIRED")
        if set(claim) != {"agent_uuid", "runtime_id", "instructions_sha256", "enabled_skill_ids"} or claim.get("agent_uuid") != _TARGETS[target] or claim.get("runtime_id") != _FOUNDATION_RUNTIME or claim.get("instructions_sha256") != self.manifest.get("instruction_hashes", {}).get(target) or not isinstance(claim.get("enabled_skill_ids"), list):
            _fail("FOUNDATION_BRIDGE_V12_CLAIM_DRIFT")
        bare = {key: value for key, value in receipt.items() if key != "content_hash"}
        if set(receipt) != {"schema_version", "run_id", "agent_id", "attempt", "input_receipt_hashes", "content_hash"} or receipt.get("schema_version") != "east-v5-runtime-receipt/v12" or receipt.get("agent_id") != "220" or receipt.get("run_id") != envelope["run_id"] or receipt.get("attempt") != envelope["attempt"] or receipt.get("content_hash") != _hash(bare):
            _fail("FOUNDATION_BRIDGE_V12_RECEIPT_INVALID")
        run = state["runs"].get(envelope["run_id"])
        if not isinstance(run, dict) or run.get("latest_receipts", {}).get("220") != receipt["content_hash"] or run.get("receipt_nodes", {}).get(receipt["content_hash"]) != ["220"]:
            _fail("FOUNDATION_BRIDGE_V12_LINEAGE_DRIFT")
        inputs = envelope.get("input_receipt_hashes")
        if not isinstance(inputs, list) or (target == "241" and inputs != [receipt["content_hash"]]):
            _fail("FOUNDATION_BRIDGE_V12_LINEAGE_DRIFT")
        if target == "242":
            prior = run.get("latest_receipts", {}).get("241")
            if not isinstance(prior, str) or inputs != [prior] or run.get("receipt_nodes", {}).get(prior) != ["241"]:
                _fail("FOUNDATION_BRIDGE_V12_LINEAGE_DRIFT")
        return state, run

    def _receipt_artifact(self, expected_agent: str, expected_route: str, receipt: dict[str, Any], *, run_id: str, attempt: int) -> tuple[dict[str, Any], dict[str, Any]]:
        bare = {key: value for key, value in receipt.items() if key != "content_hash"}
        if set(receipt) != {"schema_version", "task_id", "issue_id", "agent_id", "runtime_id", "input_ref", "output_ref", "run_id", "trace_id", "qa_id", "attempt", "route_target", "content_hash"} or receipt.get("schema_version") != "task_execution_receipt/v1" or receipt.get("content_hash") != sha256(bare) or receipt.get("agent_id") != _TARGETS[expected_agent] or receipt.get("runtime_id") != self.graph["real_agents"][expected_agent]["runtime_id"] or receipt.get("route_target") != expected_route or receipt.get("run_id") != run_id or receipt.get("attempt") != attempt:
            _fail("FOUNDATION_BRIDGE_TASK_RECEIPT_INVALID")
        input_ref = _ref(receipt.get("input_ref"), "FOUNDATION_BRIDGE_ARTIFACT_REF_INVALID")
        output_ref = _ref(receipt.get("output_ref"), "FOUNDATION_BRIDGE_ARTIFACT_REF_INVALID")
        registry = ArtifactRegistry(self.checkout, self.roots, str(receipt["issue_id"]), run_id, attempt)
        try:
            input_record = registry.resolve(input_ref)
            record = registry.resolve(output_ref)
            validate_envelope(self.checkout, input_record["envelope"], input_record["payload"])
            validate_envelope(self.checkout, record["envelope"], record["payload"])
        except ContractError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_ARTIFACT_UNREGISTERED") from exc
        output = record["envelope"]
        if artifact_ref(input_record["envelope"]) != input_ref or artifact_ref(output) != output_ref or output.get("mode") != "foundation" or input_record["envelope"].get("mode") != "foundation" or output.get("producer_id") != expected_agent:
            _fail("FOUNDATION_BRIDGE_ARTIFACT_LINEAGE_DRIFT")
        if output.get("parent_artifact_refs") != [input_ref] or output.get("input_hashes") != [input_ref["content_hash"]] or (output.get("run_id"), output.get("attempt_no"), output.get("trace_id"), output.get("qa_id")) != (receipt["run_id"], receipt["attempt"], receipt["trace_id"], receipt["qa_id"]):
            _fail("FOUNDATION_BRIDGE_ARTIFACT_LINEAGE_DRIFT")
        return output_ref, record

    @staticmethod
    def _grant_key(body: dict[str, Any]) -> str:
        """Stable idempotency identity; mutable evidence is checked as drift."""
        fields = ("schema_version", "issue_id", "run_id", "attempt", "v12_manifest_sha256", "v12_root_binding_id", "v12_preflight_token", "target_agent_id", "target_agent_uuid", "runtime_id", "route_target")
        return _hash({field: body.get(field) for field in fields})

    def _grant_path(self, grant_id: str) -> Path:
        self._assert_ledger_permissions()
        path = self.ledger / _GRANTS / f"{grant_id}.json"
        try:
            if path.exists() and (path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600):
                _fail("FOUNDATION_BRIDGE_GRANT_UNSAFE")
        except OSError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_GRANT_UNSAFE") from exc
        return path

    def _grant(self, body: dict[str, Any]) -> None:
        grant_id = self._grant_key(body)
        signed = {**body, "grant_id": grant_id, "signature": hmac.new(self._key(), _canon(body), hashlib.sha256).hexdigest()}
        path = self._grant_path(grant_id)
        try:
            if path.exists():
                if _load(path, "FOUNDATION_BRIDGE_GRANT_INVALID") != signed:
                    _fail("FOUNDATION_BRIDGE_GRANT_DRIFT")
            else:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(_canon(signed))
        except FileExistsError:
            self._grant(body)
        except OSError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_GRANT_UNAVAILABLE") from exc

    def runtime_input_gate(self, *, issue_id: str, run_id: str, attempt: int, v12_claims: dict[str, Any], component_receipt: dict[str, Any], snapshot_ref: dict[str, Any], generation_context_ref: dict[str, Any]) -> dict[str, Any]:
        """Materialize the sole machine-readable Foundation data-plane gate."""
        state = self._state()
        if not isinstance(issue_id, str) or not issue_id or not isinstance(run_id, str) or not run_id or attempt not in {1, 2, 3} or not isinstance(v12_claims, dict):
            _fail("FOUNDATION_BRIDGE_INPUT_GATE_INVALID")
        component = {key: value for key, value in component_receipt.items() if key != "receipt_sha256"} if isinstance(component_receipt, dict) else {}
        if set(component_receipt) != {"schema_version", "component_id", "root_binding_id", "config_sha256", "receipt_sha256"} or component_receipt.get("schema_version") != "east-v5-fixed-component-receipt/v1" or component_receipt.get("component_id") != "000" or component_receipt.get("root_binding_id") != self.root_marker or component_receipt.get("receipt_sha256") != _hash(component):
            _fail("FOUNDATION_BRIDGE_INPUT_GATE_000_INVALID")
        accepted = next((entry for entry in state["preflights"].values() if isinstance(entry, dict) and entry.get("root_binding_id") == self.root_marker and entry.get("manifest_sha256") == self.manifest_hash and entry.get("claims_sha256") == _hash(v12_claims) and entry.get("component_receipt_sha256") == component_receipt["receipt_sha256"]), None)
        if accepted is None:
            _fail("FOUNDATION_BRIDGE_INPUT_GATE_PREFLIGHT_REQUIRED")
        registry = ArtifactRegistry(self.checkout, self.roots, issue_id, run_id, attempt)
        refs = {"snapshot_ref": _ref(snapshot_ref, "FOUNDATION_BRIDGE_INPUT_GATE_REF_INVALID"), "generation_context_ref": _ref(generation_context_ref, "FOUNDATION_BRIDGE_INPUT_GATE_REF_INVALID")}
        try:
            records = {name: registry.resolve(reference) for name, reference in refs.items()}
            for record in records.values():
                validate_envelope(self.checkout, record["envelope"], record["payload"])
        except ContractError as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_INPUT_GATE_ARTIFACT_UNREGISTERED") from exc
        if (records["snapshot_ref"]["envelope"].get("artifact_type"), records["snapshot_ref"]["envelope"].get("producer_id"), records["snapshot_ref"]["envelope"].get("mode")) != ("database_read_snapshot", "EAS-19", "foundation") or (records["generation_context_ref"]["envelope"].get("artifact_type"), records["generation_context_ref"]["envelope"].get("producer_id"), records["generation_context_ref"]["envelope"].get("mode")) != ("foundation_generation_context", "EAS-19", "foundation"):
            _fail("FOUNDATION_BRIDGE_INPUT_GATE_ARTIFACT_LINEAGE_DRIFT")
        body = {"schema_version": "foundation_graph_adapter_input_gate/v1", "issue_id": issue_id, "run_id": run_id, "attempt": attempt, "v12_manifest_sha256": self.manifest_hash, "v12_root_binding_id": self.root_marker, "v12_preflight_token": next(token for token, entry in state["preflights"].items() if entry is accepted), "target_agent_id": "input-gate", "target_agent_uuid": "input-gate", "runtime_id": _FOUNDATION_RUNTIME, "route_target": "241", "claims_sha256": _hash(v12_claims), "component_receipt_sha256": component_receipt["receipt_sha256"], **refs}
        self._grant(body)
        return {**body, "gate_id": self._grant_key(body)}

    def _validate_gate(self, gate: dict[str, Any], *, issue_id: Any, run_id: str, attempt: int) -> str:
        if not isinstance(gate, dict):
            _fail("FOUNDATION_BRIDGE_INPUT_GATE_REQUIRED")
        gate_id = gate.get("gate_id")
        body = {key: value for key, value in gate.items() if key != "gate_id"}
        if not isinstance(gate_id, str) or gate_id != self._grant_key(body) or body.get("schema_version") != "foundation_graph_adapter_input_gate/v1" or body.get("issue_id") != issue_id or body.get("run_id") != run_id or body.get("attempt") != attempt:
            _fail("FOUNDATION_BRIDGE_INPUT_GATE_INVALID")
        saved = _load(self._grant_path(gate_id), "FOUNDATION_BRIDGE_INPUT_GATE_REQUIRED")
        signature = saved.get("signature")
        if saved.get("grant_id") != gate_id or {key: value for key, value in saved.items() if key not in {"grant_id", "signature"}} != body or not isinstance(signature, str) or not hmac.compare_digest(signature, hmac.new(self._key(), _canon(body), hashlib.sha256).hexdigest()):
            _fail("FOUNDATION_BRIDGE_INPUT_GATE_INVALID")
        return gate_id

    def _bootstrap(self, v12_binding: str) -> tuple[dict[str, Any], BootstrapEvidence]:
        # v1 has a separate root-binding algorithm; the v12 binding remains in
        # the authenticated bridge grant and cannot be substituted into v1.
        context = {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "foundation-graph-bridge", "project_id": "v1", "daemon_id": v12_binding[:16]}
        binding = root_binding_id(context)
        def digest(relative: str) -> str:
            return hashlib.sha256((self.checkout / relative).read_bytes()).hexdigest()
        try:
            import subprocess
            head = subprocess.check_output(["git", "-C", str(self.checkout), "rev-parse", "HEAD"], text=True).strip()
        except Exception as exc:
            raise FoundationGraphAdapterBridgeError("FOUNDATION_BRIDGE_CHECKOUT_UNAVAILABLE") from exc
        declaration = {"bootstrap_version": "east-v5-runtime-bootstrap/v1", "candidate_base_sha": head, "candidate_head_sha": head, "adapter_sha256": digest("src/east_v5/runtime/adapter.py"), "bootstrap_sha256": digest("src/east_v5/runtime/bootstrap.py"), "runner_sha256": digest("scripts/runtime_bootstrap.py"), "runtime_context": context, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v1", "skill_version": "v1", "skill_manifest_sha256": self.manifest_hash}}
        evidence = BootstrapEvidence(candidate_head_sha=head, adapter_sha256=declaration["adapter_sha256"], bootstrap_sha256=declaration["bootstrap_sha256"], runner_sha256=declaration["runner_sha256"], root_binding_id=binding, runner_entrypoint="scripts/runtime_bootstrap.py")
        return declaration, evidence

    def adapter_for(self, target: str, *, task_id: str, v12_envelope: dict[str, Any], v12_claims: dict[str, Any], v12_220_receipt: dict[str, Any], task_receipt: dict[str, Any], input_gate: dict[str, Any]) -> RuntimeAdapter:
        """Return a derived 241 or 242 adapter; callers supply no v1 routing data."""
        if target not in _ROUTES or not isinstance(task_id, str) or not task_id:
            _fail("FOUNDATION_BRIDGE_ARGUMENT_INVALID")
        self._validate_v12(target, v12_envelope, v12_claims, v12_220_receipt)
        gate_id = self._validate_gate(input_gate, issue_id=task_receipt.get("issue_id") if isinstance(task_receipt, dict) else None, run_id=v12_envelope["run_id"], attempt=v12_envelope["attempt"])
        parent_agent, parent_route = ("220", "241") if target == "241" else ("241", "242")
        input_ref, _record = self._receipt_artifact(parent_agent, parent_route, task_receipt, run_id=v12_envelope["run_id"], attempt=v12_envelope["attempt"])
        route, artifact_type = _ROUTES[target]
        declaration, evidence = self._bootstrap(v12_envelope["root_binding_id"])
        envelope = {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": task_receipt["issue_id"], "run_id": v12_envelope["run_id"], "trace_id": task_receipt["trace_id"], "qa_id": None, "attempt": v12_envelope["attempt"], "target_agent_id": target, "target_agent_uuid": _TARGETS[target], "root_binding_id": evidence.root_binding_id, "input_ref": input_ref, "expected_output": {"artifact_type": artifact_type, "producer_id": target, "route_target": route}, "execution_bootstrap": declaration}
        body = {"schema_version": "foundation_graph_adapter_bridge_grant/v1", "issue_id": task_receipt["issue_id"], "run_id": v12_envelope["run_id"], "attempt": v12_envelope["attempt"], "input_gate_id": gate_id, "v12_manifest_sha256": self.manifest_hash, "v12_root_binding_id": v12_envelope["root_binding_id"], "v12_preflight_token": v12_envelope["preflight_token"], "v12_envelope_sha256": _hash(v12_envelope), "v12_220_receipt_sha256": v12_220_receipt["content_hash"], "upstream_task_receipt_sha256": task_receipt["content_hash"], "upstream_artifact_ref": input_ref, "target_agent_id": target, "target_agent_uuid": _TARGETS[target], "runtime_id": _FOUNDATION_RUNTIME, "route_target": route, "v1_envelope_sha256": _hash(envelope)}
        self._grant(body)
        return RuntimeAdapter(self.checkout, self.roots, envelope, preflight=evidence)

    def consume_260_stub(self, receipt: dict[str, Any]) -> dict[str, Any]:
        """Minimal downstream proof: 260 receives only a verified 242 artifact."""
        output_ref, _record = self._receipt_artifact("242", "260", receipt, run_id=receipt.get("run_id", ""), attempt=receipt.get("attempt", 0))
        return {"schema_version": "foundation_graph_adapter_260_stub/v1", "target_agent_id": "260", "input_ref": output_ref, "upstream_receipt_hash": receipt["content_hash"]}
