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
SOURCE = ROOT / "skills/east-v5-runtime-bootstrap-v11"
PACKAGER = SOURCE / "scripts/pack_skill.py"
AGENTS = ("010", "110", "120", "130", "140", "150", "160", "170", "180", "210", "220", "230", "241", "242", "251", "252", "260")
SKILL_ID = "v11-test-skill"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


class FullRuntimeGraphV11Tests(unittest.TestCase):
    def archive(self, directory: Path) -> tuple[dict[str, object], Path]:
        repo = directory / "repo"
        skill = repo / "skills/east-v5-runtime-bootstrap-v11"
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
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=v11@example.invalid", "-c", "user.name=v11", "commit", "-qm", "v11"], check=True)
        head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        archive = directory / "v11.skill.zip"
        result = subprocess.run([sys.executable, str(PACKAGER), "--repo-root", str(repo), "--head", head, "--output", str(archive)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = directory / "installed"
        with zipfile.ZipFile(archive) as package:
            package.extractall(installed)
        skill_md = installed / "SKILL.md"
        skill_md.write_bytes(skill_md.read_bytes().replace(
            b"description: Execute the EAST V5 v11 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight.\n",
            b'description: "Execute the EAST V5 v11 full-graph controller for all 17 real Agents and the fixed 000 component, only after a complete fail-closed preflight."\n',
        ).replace(b"---\n\n# EAST V5 Runtime Bootstrap V11", b"---\n\n\n# EAST V5 Runtime Bootstrap V11"))
        return json.loads(result.stdout), installed

    def runtime(self, directory: Path, installed: Path) -> tuple[Path, str]:
        context = {"daemon": "local", "workspace": "test"}
        binding = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        root = directory / "daemon"
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        write(root / "daemon-root-binding-v11.json", {"schema_version": "east-v5-daemon-root-binding/v11", "root_binding_id": binding})
        os.chmod(root / "daemon-root-binding-v11.json", 0o600)
        return root, binding

    def claims(self, installed: Path, binding: str) -> tuple[dict[str, object], dict[str, object]]:
        manifest = json.loads((installed / "manifest.json").read_text())
        graph = json.loads((installed / "config/full-runtime-graph.json").read_text())
        claims = {"schema_version": "east-v5-full-claims/v11", "skill_id": SKILL_ID, "skill_manifest_sha256": sha(installed / "manifest.json"), "config_sha256": sha(installed / "config/full-runtime-graph.json"), "agents": {}}
        for agent in AGENTS:
            claims["agents"][agent] = {"agent_uuid": graph["real_agents"][agent]["uuid"], "runtime_id": graph["real_agents"][agent]["runtime_id"], "instructions_sha256": manifest["instruction_hashes"][agent], "enabled_skill_ids": [SKILL_ID]}
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
        return {"schema_version": "runtime_graph_envelope/v11", "run_id": run, "mode": mode, "attempt": attempt, "target_agent_id": target, "target_agent_uuid": graph["real_agents"][target]["uuid"], "root_binding_id": binding, "preflight_token": token, "input_receipt_hashes": inputs or [], "outcome": outcome}

    def claim(self, installed: Path, target: str) -> dict[str, object]:
        manifest = json.loads((installed / "manifest.json").read_text())
        graph = json.loads((installed / "config/full-runtime-graph.json").read_text())
        return {"agent_uuid": graph["real_agents"][target]["uuid"], "runtime_id": graph["real_agents"][target]["runtime_id"], "instructions_sha256": manifest["instruction_hashes"][target], "enabled_skill_ids": [SKILL_ID]}

    def run_task(self, installed: Path, directory: Path, root: Path, envelope: dict[str, object], task_id: str) -> dict[str, object]:
        envelope_file, claim_file = directory / f"{task_id}.envelope.json", directory / f"{task_id}.claim.json"
        write(envelope_file, envelope); write(claim_file, self.claim(installed, envelope["target_agent_id"]))
        result = self.call(installed, directory, root, "run-task", "--envelope-file", str(envelope_file), "--claim-file", str(claim_file), "--task-id", task_id)
        self.assertEqual(result.returncode, 0, f"{envelope['target_agent_id']}: {result.stderr}")
        return json.loads(result.stdout)

    def test_preflight_is_17_of_17_and_zero_task_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); _receipt, installed = self.archive(directory); root, binding = self.runtime(directory, installed); claims, component = self.claims(installed, binding)
            claims["agents"].pop("260")
            claims_file, component_file = directory / "bad.json", directory / "component.json"; write(claims_file, claims); write(component_file, component)
            bad = self.call(installed, directory, root, "full-preflight", "--claims-file", str(claims_file), "--component-receipt-file", str(component_file))
            self.assertEqual(bad.returncode, 2); self.assertIn("RUNTIME_PREFLIGHT_CLAIMS_INVALID", bad.stderr)
            self.assertFalse((root / "east-v5-full-runtime-v11-state.json").exists())

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
