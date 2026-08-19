from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "skills/east-v5-runtime-bootstrap-v12"
PACKAGER = SOURCE / "scripts/pack_skill.py"
AUTHORITY = ROOT / "config/authority-matrix-v2.json"
AGENTS = ("010", "110", "120", "130", "140", "150", "160", "170", "180", "210", "220", "230", "241", "242", "251", "252", "260")
SKILL_ID = "f42ba062-5a2d-430f-812e-c147322cc79e"
TDD_ID = "e4b03e06-c351-449c-9211-e48dae737874"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


class FullRuntimeGraphV12Tests(unittest.TestCase):
    def archive(self, directory: Path) -> tuple[dict[str, object], Path]:
        repo = directory / "repo"
        skill = repo / "skills/east-v5-runtime-bootstrap-v12"
        skill.parent.mkdir(parents=True)
        shutil.copytree(SOURCE, skill)
        # Exercise the project ignore policy: the bundled runtime source must be tracked.
        shutil.copyfile(ROOT / ".gitignore", repo / ".gitignore")
        for agent in AGENTS:
            prompt = repo / "agents" / agent / "prompt.md"
            prompt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "agents" / agent / "prompt.md", prompt)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=v12@example.invalid", "-c", "user.name=v12", "commit", "-qm", "v12"], check=True)
        head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        archive = directory / "v12.skill.zip"
        result = subprocess.run([sys.executable, str(PACKAGER), "--repo-root", str(repo), "--head", head, "--output", str(archive)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = directory / "installed"
        with zipfile.ZipFile(archive) as package:
            package.extractall(installed)
        skill_md = installed / "SKILL.md"
        skill_md.write_bytes(skill_md.read_bytes().replace(
            b"description: Execute the EAST V5 v12 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight.\n",
            b'description: "Execute the EAST V5 v12 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight."\n',
        ).replace(b"---\n\n# EAST V5 Runtime Bootstrap V12", b"---\n\n\n# EAST V5 Runtime Bootstrap V12"))
        return json.loads(result.stdout), installed

    def runtime(self, directory: Path, installed: Path) -> tuple[Path, str]:
        context = {"daemon": "local", "workspace": "test"}
        binding = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        root = directory / "daemon"
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        write(root / "daemon-root-binding-v12.json", {"schema_version": "east-v5-daemon-root-binding/v12", "root_binding_id": binding})
        os.chmod(root / "daemon-root-binding-v12.json", 0o600)
        return root, binding

    def claims(self, installed: Path, binding: str) -> tuple[dict[str, object], dict[str, object]]:
        manifest = json.loads((installed / "manifest.json").read_text())
        graph = json.loads((installed / "config/full-runtime-graph.json").read_text())
        authority = json.loads((installed / "config/authority-matrix-v2.json").read_text())
        resolver = json.loads((installed / "config/skill-identity-resolver-v1.json").read_text())
        workspace_ids = resolver["workspace_skill_ids"]
        approved_skills = {row["agent_id"]: [workspace_ids[name] for name in row["approved_skill_bindings"]] for row in authority["rows"]}
        claims = {"schema_version": "east-v5-full-claims/v12", "skill_id": SKILL_ID, "skill_manifest_sha256": sha(installed / "manifest.json"), "config_sha256": sha(installed / "config/full-runtime-graph.json"), "agents": {}}
        for agent in AGENTS:
            claims["agents"][agent] = {"agent_uuid": graph["real_agents"][agent]["uuid"], "runtime_id": graph["real_agents"][agent]["runtime_id"], "instructions_sha256": manifest["instruction_hashes"][agent], "enabled_skill_ids": sorted([*approved_skills[agent], SKILL_ID])}
        component = {"schema_version": "east-v5-fixed-component-receipt/v1", "component_id": "000", "root_binding_id": binding, "config_sha256": claims["config_sha256"]}
        component["receipt_sha256"] = hashlib.sha256(json.dumps(component, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return claims, component

    def call(self, installed: Path, directory: Path, root: Path, command: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-I", str(installed / "scripts/controller.py"), command, "--runtime-root", str(root), *arguments], capture_output=True, text=True, cwd=directory)

    def preflight(self, installed: Path, directory: Path, root: Path, claims: dict[str, object], component: dict[str, object]) -> str:
        claims_file, component_file = directory / "claims.json", directory / "component.json"
        write(claims_file, claims); write(component_file, component)
        result = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["preflight_token"]

    def envelope(self, installed: Path, binding: str, token: str, run: str, target: str, inputs: list[str] | None = None, attempt: int = 1, outcome: str = "success", mode: str = "event") -> dict[str, object]:
        graph = json.loads((installed / "config/full-runtime-graph.json").read_text())
        return {"schema_version": "runtime_graph_envelope/v12", "run_id": run, "mode": mode, "attempt": attempt, "target_agent_id": target, "target_agent_uuid": graph["real_agents"][target]["uuid"], "root_binding_id": binding, "preflight_token": token, "input_receipt_hashes": inputs or [], "outcome": outcome}

    def claim(self, installed: Path, target: str) -> dict[str, object]:
        manifest = json.loads((installed / "manifest.json").read_text())
        graph = json.loads((installed / "config/full-runtime-graph.json").read_text())
        authority = json.loads((installed / "config/authority-matrix-v2.json").read_text())
        resolver = json.loads((installed / "config/skill-identity-resolver-v1.json").read_text())
        approved_names = next(row["approved_skill_bindings"] for row in authority["rows"] if row["agent_id"] == target)
        approved = [resolver["workspace_skill_ids"][name] for name in approved_names]
        return {"agent_uuid": graph["real_agents"][target]["uuid"], "runtime_id": graph["real_agents"][target]["runtime_id"], "instructions_sha256": manifest["instruction_hashes"][target], "enabled_skill_ids": sorted([*approved, SKILL_ID])}

    def run_task(self, installed: Path, directory: Path, root: Path, envelope: dict[str, object], task_id: str) -> dict[str, object]:
        result = self.run_raw(installed, directory, root, envelope, task_id)
        self.assertEqual(result.returncode, 0, f"{envelope['target_agent_id']}: {result.stderr}")
        return json.loads(result.stdout)

    def run_raw(self, installed: Path, directory: Path, root: Path, envelope: dict[str, object], task_id: str, claim: dict[str, object] | None = None) -> subprocess.CompletedProcess[str]:
        envelope_file, claim_file = directory / f"{task_id}.envelope.json", directory / f"{task_id}.claim.json"
        write(envelope_file, envelope)
        write(claim_file, self.claim(installed, envelope["target_agent_id"]) if claim is None else claim)
        return self.call(installed, directory, root, "run-task", "--envelope-file", str(envelope_file), "--claim-file", str(claim_file), "--task-id", task_id)

    def test_preflight_is_17_of_17_and_zero_task_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
            claims["agents"].pop("260")
            claims_file, component_file = directory / "bad.json", directory / "component.json"; write(claims_file, claims); write(component_file, component)
            bad = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
            self.assertEqual(bad.returncode, 2); self.assertIn("RUNTIME_PREFLIGHT_CLAIMS_INVALID", bad.stderr)
            self.assertFalse((root / "east-v5-full-runtime-v12-state.json").exists())

    def test_authority_matrix_is_17_of_17_and_matrix_tampering_fails_closed(self) -> None:
        matrix = json.loads(AUTHORITY.read_text())
        self.assertEqual(matrix["matrix_version"], "authority-matrix-v2")
        self.assertEqual(matrix["row_count"], 17)
        self.assertEqual(matrix["verdict_summary"], {"approved_exact": 17, "drift": 0, "unresolved": 0})
        self.assertEqual(matrix["matrix_correction"]["agent_id"], "140")
        self.assertEqual(matrix["matrix_correction"]["approved_instruction_sha256"], "1fddd3bcd5380b4b7779ae634a51bf7de99c9d50dd12d5581cbffcba720b8172")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory)
            self.assertEqual((installed / "config/authority-matrix-v2.json").read_bytes(), AUTHORITY.read_bytes())
            root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
            embedded = json.loads((installed / "config/authority-matrix-v2.json").read_text())
            embedded["rows"][0]["approved_skill_bindings"] = ["east-v5-test-driven-development"]
            write(installed / "config/authority-matrix-v2.json", embedded)
            claims_file, component_file = directory / "claims.json", directory / "component.json"; write(claims_file, claims); write(component_file, component)
            rejected = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
            self.assertEqual(rejected.returncode, 2); self.assertIn("RUNTIME_AUTHORITY_MATRIX_INVALID", rejected.stderr)
            self.assertFalse((root / "east-v5-full-runtime-v12-state.json").exists())

    def test_full_skill_inventory_accepts_approved_tdd_plus_v12_at_preflight_and_task_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
            self.assertEqual(claims["agents"]["010"]["enabled_skill_ids"], [SKILL_ID])
            self.assertEqual(claims["agents"]["130"]["enabled_skill_ids"], [TDD_ID, SKILL_ID])
            # Order is normalized by the controller, not imposed on platform inventory producers.
            claims["agents"]["130"]["enabled_skill_ids"].reverse()
            token = self.preflight(installed, directory, root, claims, component)
            r010 = self.run_task(installed, directory, root, self.envelope(installed, binding, token, "approved-tdd", "010"), "approved-010")
            r110 = self.run_task(installed, directory, root, r010["next_tasks"][0], "approved-110")
            r120 = self.run_task(installed, directory, root, r110["next_tasks"][0], "approved-120")
            r130 = self.run_task(installed, directory, root, r120["next_tasks"][0], "approved-130")
            self.assertEqual(r130["receipt"]["agent_id"], "130")

    def test_preflight_rejects_missing_unapproved_and_legacy_skill_inventory_without_state(self) -> None:
        cases = (
            ("130", [SKILL_ID], "missing-approved-tdd"),
            ("010", [TDD_ID, SKILL_ID], "unexpected-tdd"),
            ("010", ["east-v5-test-driven-development", SKILL_ID], "display-name-is-not-platform-id"),
            ("010", ["east-v5-runtime-bootstrap-v11", SKILL_ID], "legacy-v11"),
            ("010", ["other-skill", SKILL_ID], "other-extra"),
            ("010", [SKILL_ID, SKILL_ID], "duplicate-id"),
        )
        for agent, inventory, label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
                claims["agents"][agent]["enabled_skill_ids"] = inventory
                claims_file, component_file = directory / "claims.json", directory / "component.json"; write(claims_file, claims); write(component_file, component)
                rejected = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
                self.assertEqual(rejected.returncode, 2); self.assertIn("RUNTIME_PREFLIGHT_AGENT_DRIFT", rejected.stderr)
                self.assertFalse((root / "east-v5-full-runtime-v12-state.json").exists())

    def test_task_claim_repeats_complete_skill_inventory_validation_without_run_persistence(self) -> None:
        cases = (
            ("130", [SKILL_ID], "missing-approved-tdd"),
            ("010", [TDD_ID, SKILL_ID], "unexpected-tdd"),
            ("130", ["east-v5-test-driven-development", SKILL_ID], "display-name-is-not-platform-id"),
            ("010", ["east-v5-runtime-bootstrap-v11", SKILL_ID], "legacy-v11"),
            ("010", ["other-skill", SKILL_ID], "other-extra"),
            ("010", [SKILL_ID, SKILL_ID], "duplicate-id"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding); token = self.preflight(installed, directory, root, claims, component)
            for target, inventory, label in cases:
                with self.subTest(label=label):
                    claim = self.claim(installed, target); claim["enabled_skill_ids"] = inventory
                    rejected = self.run_raw(installed, directory, root, self.envelope(installed, binding, token, f"task-{label}", target), f"task-{label}", claim)
                    self.assertEqual(rejected.returncode, 2); self.assertIn("RUNTIME_TASK_CLAIM_DRIFT", rejected.stderr)
                    inspected = self.call(installed, directory, root, "inspect-run", "--run-id", f"task-{label}")
                    self.assertEqual(inspected.returncode, 2); self.assertIn("RUNTIME_RUN_UNKNOWN", inspected.stderr)

    def refresh_manifest_file_hash(self, installed: Path, relative: str) -> None:
        manifest_path = installed / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][relative] = sha(installed / relative)
        write(manifest_path, manifest)

    def test_resolver_and_claim_identity_fail_closed_before_state(self) -> None:
        resolver_path = "config/skill-identity-resolver-v1.json"
        cases = (
            ("resolver-missing", "RUNTIME_SKILL_IDENTITY_RESOLVER_DRIFT", lambda installed: (installed / resolver_path).unlink()),
            ("resolver-hash-drift", "RUNTIME_SKILL_IDENTITY_RESOLVER_DRIFT", lambda installed: write(installed / resolver_path, {"schema_version": "tampered"})),
            ("resolver-non-uuid", "RUNTIME_SKILL_IDENTITY_RESOLVER_INVALID", lambda installed: self._tamper_resolver(installed, "uuid")),
            ("resolver-name", "RUNTIME_SKILL_IDENTITY_RESOLVER_INVALID", lambda installed: self._tamper_resolver(installed, "name")),
        )
        for label, code, mutate in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
                mutate(installed)
                claims_file, component_file = directory / "claims.json", directory / "component.json"; write(claims_file, claims); write(component_file, component)
                rejected = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
                self.assertEqual(rejected.returncode, 2); self.assertIn(code, rejected.stderr)
                self.assertFalse((root / "east-v5-full-runtime-v12-state.json").exists())
        for invalid_skill_id in ("east-v5-runtime-bootstrap-v12", TDD_ID):
            with self.subTest(invalid_skill_id=invalid_skill_id), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
                claims["skill_id"] = invalid_skill_id
                claims_file, component_file = directory / "claims.json", directory / "component.json"; write(claims_file, claims); write(component_file, component)
                rejected = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
                self.assertEqual(rejected.returncode, 2); self.assertIn("RUNTIME_PREFLIGHT_CLAIMS_INVALID", rejected.stderr)
                self.assertFalse((root / "east-v5-full-runtime-v12-state.json").exists())

    def _tamper_resolver(self, installed: Path, kind: str) -> None:
        relative = "config/skill-identity-resolver-v1.json"
        resolver = json.loads((installed / relative).read_text())
        if kind == "uuid":
            resolver["workspace_skill_ids"]["east-v5-test-driven-development"] = "not-a-uuid"
        elif kind == "name":
            resolver["workspace_skill_ids"] = {"unexpected-logical-name": TDD_ID}
        else:
            raise AssertionError(kind)
        write(installed / relative, resolver)
        self.refresh_manifest_file_hash(installed, relative)

    def test_unmapped_authority_name_is_rejected_before_preflight_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
            authority_path = installed / "config/authority-matrix-v2.json"
            authority = json.loads(authority_path.read_text())
            next(row for row in authority["rows"] if row["agent_id"] == "130")["approved_skill_bindings"] = ["unmapped-logical-skill"]
            write(authority_path, authority)
            claims_file, component_file = directory / "claims.json", directory / "component.json"; write(claims_file, claims); write(component_file, component)
            rejected = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
            self.assertEqual(rejected.returncode, 2); self.assertIn("RUNTIME_SKILL_IDENTITY_UNMAPPED", rejected.stderr)
            self.assertFalse((root / "east-v5-full-runtime-v12-state.json").exists())

    def test_real_full_event_graph_barriers_replay_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding); token = self.preflight(installed, directory, root, claims, component)
            run_id = "full-event"
            current = self.envelope(installed, binding, token, run_id, "010")
            result = self.run_task(installed, directory, root, current, "task-010")
            replay = self.run_task(installed, directory, root, current, "task-010")
            self.assertEqual(result, replay)
            for target in ("110", "120", "130", "140", "150"):
                current = result["next_tasks"][0]; self.assertEqual(current["target_agent_id"], target); result = self.run_task(installed, directory, root, current, f"task-{target}")
            current = result["next_tasks"][0]; self.assertEqual(current["target_agent_id"], "160"); result = self.run_task(installed, directory, root, current, "task-160")
            self.assertEqual({item["target_agent_id"] for item in result["next_tasks"]}, {"170", "180"})
            r170 = self.run_task(installed, directory, root, next(item for item in result["next_tasks"] if item["target_agent_id"] == "170"), "task-170")
            self.assertEqual(r170["next_tasks"], [])
            r180 = self.run_task(installed, directory, root, next(item for item in result["next_tasks"] if item["target_agent_id"] == "180"), "task-180")
            current = r180["next_tasks"][0]; self.assertEqual(current["target_agent_id"], "110")
            for target in ("110", "210", "220", "230"):
                self.assertEqual(current["target_agent_id"], target); result = self.run_task(installed, directory, root, current, f"data-{target}"); current = result["next_tasks"][0]
            self.assertEqual(current["target_agent_id"], "241")
            r241 = self.run_task(installed, directory, root, current, "data-241")
            r251 = self.run_task(installed, directory, root, result["next_tasks"][1], "data-251")
            r242 = self.run_task(installed, directory, root, r241["next_tasks"][0], "data-242")
            self.assertEqual(r242["next_tasks"], [])
            r252 = self.run_task(installed, directory, root, r251["next_tasks"][0], "data-252")
            current = r252["next_tasks"][0]
            for target in ("260", "210", "010"):
                self.assertEqual(current["target_agent_id"], target); result = self.run_task(installed, directory, root, current, f"final-{target}"); current = result["next_tasks"][0] if result["next_tasks"] else None
            self.assertIsNone(current)
            inspected = self.call(installed, directory, root, "inspect-run", "--run-id", run_id)
            self.assertEqual(json.loads(inspected.stdout)["completed_agents"], sorted(AGENTS))

    def test_failure_restarts_only_affected_chain_and_unknown_input_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding); token = self.preflight(installed, directory, root, claims, component)
            bad = self.envelope(installed, binding, token, "reject", "120", ["unknown"])
            bad_file, claim_file = directory / "bad-envelope.json", directory / "bad-claim.json"; write(bad_file, bad); write(claim_file, self.claim(installed, "120"))
            rejected = self.call(installed, directory, root, "run-task", "--envelope-file", str(bad_file), "--claim-file", str(claim_file), "--task-id", "bad")
            self.assertEqual(rejected.returncode, 2); self.assertIn("RUNTIME_INPUT_RECEIPT_UNKNOWN", rejected.stderr)
            initial = self.envelope(installed, binding, token, "failure", "010")
            r010 = self.run_task(installed, directory, root, initial, "f010")
            r110 = self.run_task(installed, directory, root, r010["next_tasks"][0], "f110")
            r120 = self.run_task(installed, directory, root, r110["next_tasks"][0], "f120")
            failure = self.envelope(installed, binding, token, "failure", "120", [r110["receipt"]["content_hash"]], outcome="failure")
            recovered = self.run_task(installed, directory, root, failure, "f120-failure")
            self.assertEqual(recovered["affected_restart"], "120")
            self.assertEqual(recovered["next_tasks"][0]["target_agent_id"], "120")
            self.assertEqual(recovered["next_tasks"][0]["attempt"], 2)

    def test_duplicate_input_receipt_is_rejected_without_business_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding); token = self.preflight(installed, directory, root, claims, component)
            r010 = self.run_task(installed, directory, root, self.envelope(installed, binding, token, "duplicate-input", "010"), "duplicate-010")
            source = r010["receipt"]["content_hash"]
            duplicate = self.envelope(installed, binding, token, "duplicate-input", "110", [source, source])
            rejected = self.run_raw(installed, directory, root, duplicate, "duplicate-110")
            self.assertEqual(rejected.returncode, 2); self.assertIn("RUNTIME_ENVELOPE_INVALID", rejected.stderr)
            inspected = json.loads(self.call(installed, directory, root, "inspect-run", "--run-id", "duplicate-input").stdout)
            self.assertEqual(inspected, {"run_id": "duplicate-input", "completed_agents": ["010"], "task_count": 1})

    def test_task_and_claim_or_preflight_drift_are_rejected_without_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding); token = self.preflight(installed, directory, root, claims, component)
            initial = self.envelope(installed, binding, token, "drift", "010")
            committed = self.run_task(installed, directory, root, initial, "drift-010")
            before = json.loads(self.call(installed, directory, root, "inspect-run", "--run-id", "drift").stdout)
            envelope_drift = dict(initial); envelope_drift["outcome"] = "failure"
            duplicate = self.run_raw(installed, directory, root, envelope_drift, "drift-010")
            self.assertEqual(duplicate.returncode, 2); self.assertIn("RUNTIME_DUPLICATE_TASK_DRIFT", duplicate.stderr)
            successor = committed["next_tasks"][0]
            instruction_drift = self.claim(installed, "110"); instruction_drift["instructions_sha256"] = "0" * 64
            bad_claim = self.run_raw(installed, directory, root, successor, "drift-110-claim", instruction_drift)
            self.assertEqual(bad_claim.returncode, 2); self.assertIn("RUNTIME_TASK_CLAIM_DRIFT", bad_claim.stderr)
            preflight_drift = dict(successor); preflight_drift["preflight_token"] = "0" * 64
            bad_token = self.run_raw(installed, directory, root, preflight_drift, "drift-110-token")
            self.assertEqual(bad_token.returncode, 2); self.assertIn("RUNTIME_PREFLIGHT_REQUIRED", bad_token.stderr)
            after = json.loads(self.call(installed, directory, root, "inspect-run", "--run-id", "drift").stdout)
            self.assertEqual(after, before)

    def test_third_failure_is_terminal_and_emits_no_successor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding); token = self.preflight(installed, directory, root, claims, component)
            r010 = self.run_task(installed, directory, root, self.envelope(installed, binding, token, "third-failure", "010"), "third-010")
            r110 = self.run_task(installed, directory, root, r010["next_tasks"][0], "third-110")
            terminal = self.envelope(installed, binding, token, "third-failure", "120", [r110["receipt"]["content_hash"]], attempt=3, outcome="failure")
            result = self.run_task(installed, directory, root, terminal, "third-120")
            self.assertEqual(result, {"stage": "terminal_blocked", "next_tasks": [], "affected_restart": "120"})
            inspected = json.loads(self.call(installed, directory, root, "inspect-run", "--run-id", "third-failure").stdout)
            self.assertEqual(inspected, {"run_id": "third-failure", "completed_agents": ["010", "110"], "task_count": 3})

    def test_foundation_is_an_independent_chain_and_forbids_orm_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding); token = self.preflight(installed, directory, root, claims, component)
            initial = self.envelope(installed, binding, token, "foundation", "010", mode="foundation")
            result = self.run_task(installed, directory, root, initial, "foundation-010")
            for index, target in enumerate(("210", "220", "241", "242", "260", "210", "010"), 1):
                current = result["next_tasks"][0]
                self.assertEqual(current["target_agent_id"], target)
                self.assertEqual(current["mode"], "foundation")
                result = self.run_task(installed, directory, root, current, f"foundation-{index}")
            self.assertEqual(result["next_tasks"], [])
            forbidden = self.envelope(installed, binding, token, "bad-foundation", "230", mode="foundation")
            envelope_file, claim_file = directory / "forbidden.json", directory / "forbidden-claim.json"; write(envelope_file, forbidden); write(claim_file, self.claim(installed, "230"))
            blocked = self.call(installed, directory, root, "run-task", "--envelope-file", str(envelope_file), "--claim-file", str(claim_file), "--task-id", "forbidden")
            self.assertEqual(blocked.returncode, 2); self.assertIn("RUNTIME_FOUNDATION_FORBIDDEN_NODE", blocked.stderr)
