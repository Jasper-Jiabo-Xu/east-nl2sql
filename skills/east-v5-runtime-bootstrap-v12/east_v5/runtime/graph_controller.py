"""Fail-closed, data-free v12 full runtime graph controller."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any


class GraphError(ValueError):
    pass


def _canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canon(value)).hexdigest()


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphError(code) from exc
    if not isinstance(value, dict):
        raise GraphError(code)
    return value


class GraphController:
    """The graph is the authority; callers cannot nominate successors or receipts."""

    def __init__(self, skill_root: Path, runtime_root: Path):
        self.skill_root = skill_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.graph = _load(self.skill_root / "config" / "full-runtime-graph.json", "RUNTIME_GRAPH_UNREADABLE")
        self.authority = _load(self.skill_root / "config" / "authority-matrix-v2.json", "RUNTIME_AUTHORITY_MATRIX_UNREADABLE")
        self.manifest = _load(self.skill_root / "manifest.json", "RUNTIME_SKILL_MANIFEST_UNREADABLE")
        self.manifest_hash = hashlib.sha256((self.skill_root / "manifest.json").read_bytes()).hexdigest()
        self.skill_identity_resolver = self._load_skill_identity_resolver()
        if self.graph.get("schema_version") != "east-v5-full-runtime-graph/v12" or self.manifest.get("skill_name") != "east-v5-runtime-bootstrap-v12":
            raise GraphError("RUNTIME_GRAPH_MANIFEST_DRIFT")
        self.agents = self.graph.get("real_agents")
        if not isinstance(self.agents, dict) or set(self.agents) != {"010", "110", "120", "130", "140", "150", "160", "170", "180", "210", "220", "230", "241", "242", "251", "252", "260"}:
            raise GraphError("RUNTIME_GRAPH_AGENT_SET_INVALID")
        self._validate_authority_matrix()

    def _load_skill_identity_resolver(self) -> dict[str, str]:
        relative = "config/skill-identity-resolver-v1.json"
        path = self.skill_root / relative
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise GraphError("RUNTIME_SKILL_IDENTITY_RESOLVER_DRIFT") from exc
        files = self.manifest.get("files")
        if not isinstance(files, dict) or files.get(relative) != hashlib.sha256(raw).hexdigest():
            raise GraphError("RUNTIME_SKILL_IDENTITY_RESOLVER_DRIFT")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GraphError("RUNTIME_SKILL_IDENTITY_RESOLVER_INVALID") from exc
        source = {
            "authority_matrix_version": "authority-matrix-v2",
            "authority_matrix_sha256": "503a5f376ce833902bdcd80428595181a49d966fd9783752ddd2b07601b21208",
            "decision": "EAS-101: Sol Git-only identity resolver contract",
        }
        if not isinstance(value, dict) or set(value) != {"schema_version", "source", "workspace_skill_ids"} or value.get("schema_version") != "east-v5-skill-identity-resolver/v1" or value.get("source") != source or not isinstance(value.get("workspace_skill_ids"), dict) or set(value["workspace_skill_ids"]) != {"east-v5-test-driven-development"}:
            raise GraphError("RUNTIME_SKILL_IDENTITY_RESOLVER_INVALID")
        mappings = value["workspace_skill_ids"]
        ids = list(mappings.values())
        if not all(_is_uuid(item) for item in ids) or len(ids) != len(set(ids)):
            raise GraphError("RUNTIME_SKILL_IDENTITY_RESOLVER_INVALID")
        return mappings

    def _validate_authority_matrix(self) -> None:
        """Bind every v12 runtime assertion to the frozen, approved matrix."""
        authority = self.authority
        rows = authority.get("rows")
        if authority.get("matrix_version") != "authority-matrix-v2" or authority.get("row_count") != 17 or authority.get("verdict_summary") != {"approved_exact": 17, "drift": 0, "unresolved": 0} or not isinstance(rows, list) or len(rows) != 17:
            raise GraphError("RUNTIME_AUTHORITY_MATRIX_INVALID")
        source = authority.get("authority_sources")
        correction = authority.get("matrix_correction")
        if not isinstance(source, dict) or source.get("v1_audit_artifact_sha256") != "3448bbd828d8bef764d1aa252645128edf3e5a8861d2ebd36fe7265842c982d5" or not isinstance(correction, dict) or correction.get("agent_id") != "140" or correction.get("superseded_v1_sha256") != "7314bbf8a60b99c75cf1c9151811a905c647e5da6367f39a08af29df73d53877" or correction.get("approved_instruction_sha256") != "1fddd3bcd5380b4b7779ae634a51bf7de99c9d50dd12d5581cbffcba720b8172":
            raise GraphError("RUNTIME_AUTHORITY_MATRIX_INVALID")
        by_agent = {row.get("agent_id"): row for row in rows if isinstance(row, dict)}
        approved_tdd = {"130", "140", "160", "252", "260"}
        hashes = self.manifest.get("instruction_hashes")
        if set(by_agent) != set(self.agents) or not isinstance(hashes, dict) or set(hashes) != set(self.agents):
            raise GraphError("RUNTIME_AUTHORITY_MATRIX_INVALID")
        self.approved_skill_bindings: dict[str, tuple[str, ...]] = {}
        for agent_id, configured in self.agents.items():
            row = by_agent[agent_id]
            expected_skills = ["east-v5-test-driven-development"] if agent_id in approved_tdd else []
            declared_skills = row.get("approved_skill_bindings")
            if not isinstance(declared_skills, list) or not all(isinstance(name, str) for name in declared_skills):
                raise GraphError("RUNTIME_AUTHORITY_MATRIX_INVALID")
            if any(name not in self.skill_identity_resolver for name in declared_skills):
                raise GraphError("RUNTIME_SKILL_IDENTITY_UNMAPPED")
            if row.get("uuid") != configured["uuid"] or row.get("approved_runtime_id") != configured["runtime_id"] or row.get("approved_instruction_sha256") != hashes[agent_id] or declared_skills != expected_skills or not isinstance(row.get("source_issue"), str) or not row["source_issue"] or not isinstance(row.get("sources"), list) or not {"Jiabo-A", "Sol-v12-A", "EAS-70"}.issubset(set(row["sources"])):
                raise GraphError("RUNTIME_AUTHORITY_MATRIX_INVALID")
            try:
                self.approved_skill_bindings[agent_id] = tuple(self.skill_identity_resolver[name] for name in declared_skills)
            except KeyError as exc:
                raise GraphError("RUNTIME_SKILL_IDENTITY_UNMAPPED") from exc

    def _normalized_skill_inventory(self, value: Any, code: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value) or len(value) != len(set(value)):
            raise GraphError(code)
        return tuple(sorted(value))

    def _expected_skill_inventory(self, agent_id: str, v12_skill_id: str) -> tuple[str, ...]:
        if not _is_uuid(v12_skill_id) or v12_skill_id in self.skill_identity_resolver.values():
            raise GraphError("RUNTIME_PREFLIGHT_CLAIMS_INVALID")
        return tuple(sorted((*self.approved_skill_bindings[agent_id], v12_skill_id)))

    @property
    def _state_path(self) -> Path:
        return self.runtime_root / "east-v5-full-runtime-v12-state.json"

    def _root(self, binding: str) -> None:
        try:
            mode = stat.S_IMODE(self.runtime_root.stat().st_mode)
        except OSError as exc:
            raise GraphError("RUNTIME_ROOT_UNAVAILABLE") from exc
        if not self.runtime_root.is_dir() or self.runtime_root.is_symlink() or mode != 0o700:
            raise GraphError("RUNTIME_ROOT_PERMISSIONS_INVALID")
        marker = self.runtime_root / "daemon-root-binding-v12.json"
        try:
            if marker.is_symlink() or stat.S_IMODE(marker.stat().st_mode) != 0o600:
                raise GraphError("RUNTIME_ROOT_BINDING_INVALID")
        except OSError as exc:
            raise GraphError("RUNTIME_ROOT_BINDING_INVALID") from exc
        if _load(marker, "RUNTIME_ROOT_BINDING_INVALID") != {"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": binding}:
            raise GraphError("RUNTIME_ROOT_BINDING_INVALID")

    def _state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"schema_version": "east-v5-full-runtime-state/v12", "preflights": {}, "runs": {}}
        value = _load(self._state_path, "RUNTIME_STATE_INVALID")
        if set(value) != {"schema_version", "preflights", "runs"} or value["schema_version"] != "east-v5-full-runtime-state/v12" or not isinstance(value["preflights"], dict) or not isinstance(value["runs"], dict):
            raise GraphError("RUNTIME_STATE_INVALID")
        return value

    def _save(self, state: dict[str, Any]) -> None:
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_bytes(_canon(state))
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._state_path)

    def _component(self, receipt: dict[str, Any]) -> str:
        bare = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if set(receipt) != {"schema_version", "component_id", "root_binding_id", "config_sha256", "receipt_sha256"} or bare.get("schema_version") != "east-v5-fixed-component-receipt/v1" or bare.get("component_id") != "000" or not all(isinstance(bare.get(k), str) and bare[k] for k in ("root_binding_id", "config_sha256")) or receipt.get("receipt_sha256") != _hash(bare):
            raise GraphError("RUNTIME_000_COMPONENT_RECEIPT_INVALID")
        return receipt["root_binding_id"]

    def full_preflight(self, claims: dict[str, Any], component_receipt: dict[str, Any]) -> dict[str, Any]:
        binding = self._component(component_receipt)
        self._root(binding)
        config_hash = hashlib.sha256((self.skill_root / "config" / "full-runtime-graph.json").read_bytes()).hexdigest()
        if component_receipt["config_sha256"] != config_hash:
            raise GraphError("RUNTIME_000_COMPONENT_CONFIG_DRIFT")
        required = {"schema_version", "skill_id", "skill_manifest_sha256", "config_sha256", "agents"}
        if set(claims) != required or claims.get("schema_version") != "east-v5-full-claims/v12" or claims.get("skill_manifest_sha256") != self.manifest_hash or claims.get("config_sha256") != config_hash or not isinstance(claims.get("skill_id"), str) or not claims["skill_id"] or not isinstance(claims.get("agents"), dict) or set(claims["agents"]) != set(self.agents):
            raise GraphError("RUNTIME_PREFLIGHT_CLAIMS_INVALID")
        hashes = self.manifest.get("instruction_hashes")
        if not isinstance(hashes, dict) or set(hashes) != set(self.agents):
            raise GraphError("RUNTIME_MANIFEST_INSTRUCTION_SET_INVALID")
        for agent_id, configured in self.agents.items():
            claim = claims["agents"][agent_id]
            if not isinstance(claim, dict) or set(claim) != {"agent_uuid", "runtime_id", "instructions_sha256", "enabled_skill_ids"} or claim.get("agent_uuid") != configured["uuid"] or claim.get("runtime_id") != configured["runtime_id"] or claim.get("instructions_sha256") != hashes[agent_id] or self._normalized_skill_inventory(claim.get("enabled_skill_ids"), "RUNTIME_PREFLIGHT_AGENT_DRIFT") != self._expected_skill_inventory(agent_id, claims["skill_id"]):
                raise GraphError("RUNTIME_PREFLIGHT_AGENT_DRIFT")
            claim["enabled_skill_ids"] = list(self._normalized_skill_inventory(claim["enabled_skill_ids"], "RUNTIME_PREFLIGHT_AGENT_DRIFT"))
        token = _hash({"claims": claims, "component_receipt": component_receipt, "manifest_sha256": self.manifest_hash})
        state = self._state()
        state["preflights"][token] = {"root_binding_id": binding, "claims_sha256": _hash(claims), "component_receipt_sha256": component_receipt["receipt_sha256"], "manifest_sha256": self.manifest_hash, "skill_id": claims["skill_id"]}
        self._save(state)
        return {"status": "accepted", "preflight_token": token, "real_agent_count": 17, "fixed_component_count": 1, "business_task_count": 0}

    def _validate_envelope(self, envelope: dict[str, Any], claim: dict[str, Any], state: dict[str, Any]) -> None:
        needed = {"schema_version", "run_id", "mode", "attempt", "target_agent_id", "target_agent_uuid", "root_binding_id", "preflight_token", "input_receipt_hashes", "outcome"}
        if set(envelope) != needed or envelope.get("schema_version") != "runtime_graph_envelope/v12" or envelope.get("mode") not in {"event", "foundation"} or not isinstance(envelope.get("run_id"), str) or not envelope["run_id"] or not isinstance(envelope.get("attempt"), int) or envelope["attempt"] not in {1, 2, 3} or envelope.get("outcome") not in {"success", "failure"} or not isinstance(envelope.get("input_receipt_hashes"), list) or len(envelope["input_receipt_hashes"]) != len(set(envelope["input_receipt_hashes"])):
            raise GraphError("RUNTIME_ENVELOPE_INVALID")
        target = envelope.get("target_agent_id")
        if target not in self.agents or envelope.get("target_agent_uuid") != self.agents[target]["uuid"]:
            raise GraphError("RUNTIME_TARGET_DRIFT")
        self._root(envelope["root_binding_id"])
        preflight = state["preflights"].get(envelope["preflight_token"])
        if not isinstance(preflight, dict) or preflight.get("root_binding_id") != envelope["root_binding_id"] or preflight.get("manifest_sha256") != self.manifest_hash:
            raise GraphError("RUNTIME_PREFLIGHT_REQUIRED")
        if set(claim) != {"agent_uuid", "runtime_id", "instructions_sha256", "enabled_skill_ids"} or claim.get("agent_uuid") != self.agents[target]["uuid"] or claim.get("runtime_id") != self.agents[target]["runtime_id"] or claim.get("instructions_sha256") != self.manifest["instruction_hashes"][target] or self._normalized_skill_inventory(claim.get("enabled_skill_ids"), "RUNTIME_TASK_CLAIM_DRIFT") != self._expected_skill_inventory(target, self._preflight_skill_id(envelope["preflight_token"], state)):
            raise GraphError("RUNTIME_TASK_CLAIM_DRIFT")
        if envelope["mode"] == "foundation" and target in {"230", "251", "252"}:
            raise GraphError("RUNTIME_FOUNDATION_FORBIDDEN_NODE")

    def _preflight_skill_id(self, token: str, state: dict[str, Any]) -> str:
        value = state["preflights"].get(token)
        if not isinstance(value, dict) or not isinstance(value.get("skill_id"), str):
            raise GraphError("RUNTIME_PREFLIGHT_REQUIRED")
        return value["skill_id"]

    def _new_envelope(self, prior: dict[str, Any], target: str, inputs: list[str], attempt: int | None = None) -> dict[str, Any]:
        return {"schema_version": "runtime_graph_envelope/v12", "run_id": prior["run_id"], "mode": prior["mode"], "attempt": prior["attempt"] if attempt is None else attempt, "target_agent_id": target, "target_agent_uuid": self.agents[target]["uuid"], "root_binding_id": prior["root_binding_id"], "preflight_token": prior["preflight_token"], "input_receipt_hashes": inputs, "outcome": "success"}

    def _successors(self, envelope: dict[str, Any], run: dict[str, Any], receipt_hash: str) -> list[dict[str, Any]]:
        target, mode = envelope["target_agent_id"], envelope["mode"]
        receipts = run["latest_receipts"]
        if target in {"170", "180"}:
            if not all(node in receipts for node in ("170", "180")):
                return []
            return [self._new_envelope(envelope, "110", [receipts["170"], receipts["180"]])]
        if target == "110":
            if set(envelope["input_receipt_hashes"]) == {receipts.get("170"), receipts.get("180")}:
                return [self._new_envelope(envelope, "210", [receipt_hash])]
            return [self._new_envelope(envelope, "120", [receipt_hash])]
        if target == "260":
            return [self._new_envelope(envelope, "210", [receipt_hash])]
        if target == "210" and any("260" in run["receipt_nodes"].get(value, []) for value in envelope["input_receipt_hashes"]):
            return [self._new_envelope(envelope, "010", [receipt_hash])]
        if target == "010" and envelope["input_receipt_hashes"]:
            return []
        if target in {"242", "252"} and mode == "event":
            if not all(node in receipts for node in ("242", "252")):
                return []
            return [self._new_envelope(envelope, "260", [receipts["242"], receipts["252"]])]
        edges = self.graph[f"{mode}_edges"].get(target, [])
        return [self._new_envelope(envelope, next_target, [receipt_hash]) for next_target in edges if next_target != "010" or target == "260"]

    def _validate_inputs(self, envelope: dict[str, Any], run: dict[str, Any]) -> None:
        nodes = {node for value in envelope["input_receipt_hashes"] for node in run["receipt_nodes"].get(value, [])}
        target, mode = envelope["target_agent_id"], envelope["mode"]
        valid = {
            "010": [set(), {"210"}], "110": [{"010"}, {"170", "180"}], "120": [{"110"}],
            "130": [{"120"}], "140": [{"130"}], "150": [{"140"}], "160": [{"150"}],
            "170": [{"160"}], "180": [{"160"}], "210": [{"110"}, {"260"}, {"010"}], "220": [{"210"}],
            "230": [{"220"}], "241": [{"230"}] if mode == "event" else [{"220"}], "242": [{"241"}],
            "251": [{"230"}], "252": [{"251"}], "260": [{"242", "252"}] if mode == "event" else [{"242"}],
        }
        if nodes not in valid[target]:
            raise GraphError("RUNTIME_EDGE_OR_BARRIER_INVALID")

    def run_task(self, envelope: dict[str, Any], claim: dict[str, Any], task_id: str) -> dict[str, Any]:
        if not isinstance(task_id, str) or not task_id:
            raise GraphError("RUNTIME_TASK_ID_INVALID")
        state = self._state()
        self._validate_envelope(envelope, claim, state)
        run = state["runs"].setdefault(envelope["run_id"], {"journal": {}, "latest_receipts": {}, "receipt_nodes": {}})
        digest = _hash(envelope)
        if task_id in run["journal"]:
            prior = run["journal"][task_id]
            if prior.get("envelope_sha256") != digest:
                raise GraphError("RUNTIME_DUPLICATE_TASK_DRIFT")
            return prior["result"]
        known = {item for item in run["latest_receipts"].values()}
        if any(value not in known for value in envelope["input_receipt_hashes"]):
            raise GraphError("RUNTIME_INPUT_RECEIPT_UNKNOWN")
        self._validate_inputs(envelope, run)
        target = envelope["target_agent_id"]
        if envelope["outcome"] == "failure":
            restart = self.graph["failure_restart"].get(target)
            if restart is None or envelope["attempt"] == 3:
                result = {"stage": "terminal_blocked", "next_tasks": [], "affected_restart": restart}
            else:
                result = {"stage": "committed", "next_tasks": [self._new_envelope(envelope, restart, envelope["input_receipt_hashes"], envelope["attempt"] + 1)], "affected_restart": restart}
        else:
            receipt = {"schema_version": "east-v5-runtime-receipt/v12", "run_id": envelope["run_id"], "agent_id": target, "attempt": envelope["attempt"], "input_receipt_hashes": envelope["input_receipt_hashes"]}
            receipt["content_hash"] = _hash(receipt)
            receipt_hash = receipt["content_hash"]
            run["latest_receipts"][target] = receipt_hash
            run["receipt_nodes"][receipt_hash] = [target]
            result = {"stage": "committed", "receipt": receipt, "next_tasks": self._successors(envelope, run, receipt_hash)}
        run["journal"][task_id] = {"envelope_sha256": digest, "result": result}
        self._save(state)
        return result

    def inspect(self, run_id: str) -> dict[str, Any]:
        run = self._state()["runs"].get(run_id)
        if not isinstance(run, dict):
            raise GraphError("RUNTIME_RUN_UNKNOWN")
        return {"run_id": run_id, "completed_agents": sorted(run["latest_receipts"]), "task_count": len(run["journal"])}
