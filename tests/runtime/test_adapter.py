from __future__ import annotations

import hashlib
import json
import importlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import artifact_ref
from east_v5.runtime import RuntimeAdapter, RuntimeAdapterError, RuntimeBootstrap, RuntimeBootstrapError, root_binding_id
from east_v5.runtime.bootstrap import BootstrapEvidence

bootstrap_mod = importlib.import_module("east_v5.runtime.bootstrap")

FormalReleaseCommitter = importlib.import_module("east_v5.agents.010.committer").FormalReleaseCommitter


def _bootstrap() -> dict[str, object]:
    context = {"resolver_version": "daemon_local_platform_data_resolver_v1", "workspace_id": "workspace", "project_id": "project", "daemon_id": "daemon"}
    return {"bootstrap_version": "east-v5-runtime-bootstrap/v1", "candidate_base_sha": "a" * 40, "candidate_head_sha": "b" * 40, "adapter_sha256": "c" * 64, "bootstrap_sha256": "d" * 64, "runner_sha256": "e" * 64, "runtime_context": context, "skill_bundle": {"skill_name": "east-v5-runtime-bootstrap-v1", "skill_version": "v1", "skill_manifest_sha256": "f" * 64}}


def _envelope(package: dict[str, object] | None = None) -> dict[str, object]:
    bootstrap = _bootstrap()
    return {"schema_version": "task_input_envelope/v1", "adapter_version": "east-v5-runtime-adapter/v1", "issue_id": "EAS-70", "run_id": "probe-run", "trace_id": "probe-trace", "qa_id": package["envelope"]["qa_id"] if package else "QA", "attempt": 2, "target_agent_id": "010", "target_agent_uuid": "010-uuid", "root_binding_id": root_binding_id(bootstrap["runtime_context"]), "input_ref": None, "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "110"}, "execution_bootstrap": bootstrap}


def _evidence(envelope: dict[str, object]) -> BootstrapEvidence:
    declaration = envelope["execution_bootstrap"]
    return BootstrapEvidence(candidate_head_sha=declaration["candidate_head_sha"], adapter_sha256=declaration["adapter_sha256"], bootstrap_sha256=declaration["bootstrap_sha256"], runner_sha256=declaration["runner_sha256"], root_binding_id=envelope["root_binding_id"], runner_entrypoint="scripts/runtime_bootstrap.py")


class RuntimeAdapterTests(unittest.TestCase):
    def test_bootstrap_is_the_only_fixed_parent_container_materializer(self) -> None:
        """The approved public seam has no caller attachment or path arguments."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime"; root.mkdir(parents=True); root.chmod(0o700)
            field_mapping = {"CHILD_ORG_ID": "PARENT_ORG_ID"}
            values = {
                "foundation_intent_package.json": {
                    "content_sha256": "a" * 64,
                    "hierarchy_asset_refs": {
                        "org_tree": {
                            "field_mapping": field_mapping,
                            "asset": "ignored", "locator_key": "ignored", "note": "ignored",
                        },
                        "other_hierarchy_sibling": "ignored",
                    },
                },
                "foundation_task_package_projection.json": {"projection": "sanitized"},
                "manifest.json": {"manifest": "sanitized"},
            }
            attachments = []
            for index, (filename, value) in enumerate(values.items(), start=1):
                raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                semantic = value["content_sha256"] if filename == "foundation_intent_package.json" else bootstrap_mod.sha256(value)
                attachments.append({"id": f"attachment-{index}", "filename": filename, "byte_sha256": hashlib.sha256(raw).hexdigest(), "semantic_sha256": semantic})
            evidence_raw = b"sanitized-eas111-evidence"
            checkout = Path(tmp) / "checkout"; checkout.mkdir()
            source = checkout / "asset.bin"; source.write_bytes(b"sanitized-asset")
            config = {"schema_version": "foundation-parent-chain-materializer-config/v2", "container_schema_version": "foundation-parent-chain-container-manifest/v2", "attachments": attachments, "eas111_evidence": {"id": "evidence", "download_filename": "evidence.json", "filename": "eas111-evidence.json", "byte_sha256": hashlib.sha256(evidence_raw).hexdigest()}, "assets": [{"asset_version": "CA-V0.2.0", "artifact_id": "CA", "artifact_type": "constraint_asset_ref", "content_hash": "a" * 64, "role": "single_field", "logical_path": "asset.bin", "filename": "asset.bin", "byte_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}], "runtime_manifest_without_locators_sha256": "c" * 64, "hierarchy_mapping_sha256": "d" * 64}
            calls = []
            def runner(command, **_kwargs):
                if command[:3] != ["multica", "attachment", "download"]:
                    raise AssertionError("materializer may only use the fixed Multica attachment command")
                calls.append(command)
                if command[3] == "evidence":
                    (Path(command[-1]) / "evidence.json").write_bytes(evidence_raw)
                else:
                    item = next(item for item in attachments if item["id"] == command[3])
                    (Path(command[-1]) / item["filename"]).write_text(json.dumps(values[item["filename"]], sort_keys=True, separators=(",", ":")))
                return SimpleNamespace(stdout="")
            bootstrap = RuntimeBootstrap.__new__(RuntimeBootstrap)
            bootstrap.runner = runner; bootstrap.declaration = {"skill_bundle": {"skill_manifest_sha256": "b" * 64}}
            bootstrap.checkout = checkout
            bootstrap.preflight = lambda: BootstrapEvidence("c" * 40, "d" * 64, "e" * 64, "f" * 64, "g" * 64, "scripts/runtime_bootstrap.py")
            bootstrap.resolve_runtime_root = lambda: root
            runtime_manifest = {"schema_version": "v5.constraint-assets-runtime-manifest/v1", "assets": [{"artifact_id": "CA", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.2.0", "content_hash": "a" * 64, "payload": {"single_field": {"locator": str(root / "asset.bin"), "sha256": config["assets"][0]["byte_sha256"]}}}]}
            bootstrap._runtime_manifest = lambda *_args: runtime_manifest
            bootstrap._without_locators = lambda _manifest: {}
            config["runtime_manifest_without_locators_sha256"] = bootstrap_mod.sha256({})
            config["hierarchy_mapping_sha256"] = bootstrap_mod.sha256({
                "schema_version": "foundation-hierarchy-endpoint-mapping/v1",
                "intent_content_hash": "a" * 64,
                "field_mapping": field_mapping,
            })
            bootstrap._materializer_config = lambda: (config, bootstrap_mod.sha256(config))
            bootstrap._verify_runtime_manifest = lambda *_args: None
            first = bootstrap.materialize_foundation_parent_chain()
            second = bootstrap.materialize_foundation_parent_chain()
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 4)
            self.assertEqual(first["root_binding_id"], "g" * 64)
            container = root / "foundation-parent-chain-container-v1"
            self.assertTrue((container / "eas111-evidence.json").is_file())
            self.assertFalse((container / "evidence.json").exists())
            mapping = json.loads((container / "foundation-hierarchy-endpoint-mapping.json").read_text())
            self.assertEqual(mapping, {
                "schema_version": "foundation-hierarchy-endpoint-mapping/v1",
                "intent_content_hash": "a" * 64,
                "field_mapping": field_mapping,
                "content_hash": config["hierarchy_mapping_sha256"],
            })

    def test_eas111_filename_mapping_failures_leave_no_partial_container(self) -> None:
        """A fixed platform filename may only become the canonical name atomically."""
        for failure in ("missing", "symlink", "hash", "canonical_conflict"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "runtime"; root.mkdir(parents=True); root.chmod(0o700)
                checkout = Path(tmp) / "checkout"; checkout.mkdir()
                source = checkout / "asset.bin"; source.write_bytes(b"sanitized-asset")
                values = {
                    "foundation_intent_package.json": {"content_sha256": "a" * 64},
                    "foundation_task_package_projection.json": {"projection": "sanitized"},
                    "manifest.json": {"manifest": "sanitized"},
                }
                attachments = []
                for index, (filename, value) in enumerate(values.items(), start=1):
                    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                    semantic = value["content_sha256"] if filename == "foundation_intent_package.json" else bootstrap_mod.sha256(value)
                    attachments.append({"id": f"attachment-{index}", "filename": filename, "byte_sha256": hashlib.sha256(raw).hexdigest(), "semantic_sha256": semantic})
                evidence_raw = b"sanitized-eas111-evidence"
                evidence = {"id": "evidence", "download_filename": "evidence.json", "filename": "eas111-evidence.json", "byte_sha256": hashlib.sha256(evidence_raw).hexdigest()}
                config = {"schema_version": "foundation-parent-chain-materializer-config/v2", "container_schema_version": "foundation-parent-chain-container-manifest/v2", "attachments": attachments, "eas111_evidence": evidence, "assets": [{"asset_version": "CA-V0.2.0", "artifact_id": "CA", "artifact_type": "constraint_asset_ref", "content_hash": "a" * 64, "role": "single_field", "logical_path": "asset.bin", "filename": "asset.bin", "byte_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}], "runtime_manifest_without_locators_sha256": bootstrap_mod.sha256({}), "hierarchy_mapping_sha256": "d" * 64}

                def runner(command, **_kwargs):
                    directory = Path(command[-1])
                    if command[3] != "evidence":
                        item = next(item for item in attachments if item["id"] == command[3])
                        (directory / item["filename"]).write_text(json.dumps(values[item["filename"]], sort_keys=True, separators=(",", ":")))
                    elif failure == "symlink":
                        (directory / evidence["download_filename"]).symlink_to("outside")
                    elif failure == "hash":
                        (directory / evidence["download_filename"]).write_bytes(b"drift")
                    elif failure == "canonical_conflict":
                        (directory / evidence["download_filename"]).write_bytes(evidence_raw)
                        (directory / evidence["filename"]).write_bytes(evidence_raw)
                    elif failure != "missing":
                        raise AssertionError(failure)
                    return SimpleNamespace(stdout="")

                bootstrap = RuntimeBootstrap.__new__(RuntimeBootstrap)
                bootstrap.runner = runner; bootstrap.declaration = {"skill_bundle": {"skill_manifest_sha256": "b" * 64}}
                bootstrap.checkout = checkout
                bootstrap.preflight = lambda: BootstrapEvidence("c" * 40, "d" * 64, "e" * 64, "f" * 64, "g" * 64, "scripts/runtime_bootstrap.py")
                bootstrap.resolve_runtime_root = lambda: root
                bootstrap._runtime_manifest = lambda *_args: {"schema_version": "v5.constraint-assets-runtime-manifest/v1", "assets": []}
                bootstrap._without_locators = lambda _manifest: {}
                bootstrap._materializer_config = lambda: (config, bootstrap_mod.sha256(config))
                bootstrap._verify_runtime_manifest = lambda *_args: None
                bootstrap._hierarchy_mapping = lambda *_args: {"content_hash": "d" * 64}
                with self.assertRaisesRegex(RuntimeBootstrapError, "FOUNDATION_PARENT_MATERIALIZER_EAS111_EVIDENCE_DRIFT"):
                    bootstrap.materialize_foundation_parent_chain()
                self.assertFalse((root / "foundation-parent-chain-container-v1").exists())
                self.assertFalse(any(path.name.startswith(".foundation-parent-chain-staging-") for path in root.iterdir()))
                self.assertFalse((root / ".foundation-parent-chain-materializer-v1.key").exists())

    def test_materializer_config_rejects_unsafe_or_ambiguous_eas111_names(self) -> None:
        original = json.loads((ROOT / "config" / "foundation-parent-chain-materializer-v1.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            bootstrap = RuntimeBootstrap.__new__(RuntimeBootstrap); bootstrap.checkout = checkout
            for download_name in ("../evidence.json", "eas111-evidence.json", "approved-assets"):
                with self.subTest(download_name=download_name):
                    value = json.loads(json.dumps(original))
                    value["eas111_evidence"]["download_filename"] = download_name
                    path = checkout / "config.json"; path.write_text(json.dumps(value))
                    with patch.object(bootstrap_mod, "_MATERIALIZER_CONFIG", "config.json"), patch.object(bootstrap_mod, "_MATERIALIZER_CONFIG_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()):
                        with self.assertRaisesRegex(RuntimeBootstrapError, "FOUNDATION_PARENT_MATERIALIZER_CONFIG_INVALID"):
                            bootstrap._materializer_config()

    def test_hierarchy_mapping_accepts_only_the_frozen_object_projection(self) -> None:
        """No list/locator/sibling compatibility path may enter the mapping."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foundation_intent_package.json"
            bootstrap = RuntimeBootstrap.__new__(RuntimeBootstrap)
            field_mapping = {"CHILD_ORG_ID": "PARENT_ORG_ID"}
            bare = {
                "schema_version": "foundation-hierarchy-endpoint-mapping/v1",
                "intent_content_hash": "a" * 64,
                "field_mapping": field_mapping,
            }
            config = {"attachments": [{"semantic_sha256": "a" * 64}], "hierarchy_mapping_sha256": bootstrap_mod.sha256(bare)}
            source = {
                "content_sha256": "a" * 64,
                "hierarchy_asset_refs": {
                    "org_tree": {
                        "field_mapping": field_mapping,
                        "asset": "ignored", "locator_key": "ignored", "note": "ignored",
                    },
                    "other_hierarchy_sibling": "ignored",
                },
            }
            path.write_text(json.dumps(source))
            self.assertEqual(bootstrap._hierarchy_mapping(path, config), {**bare, "content_hash": bootstrap_mod.sha256(bare)})

            bad_sources = {
                "old_list": {**source, "hierarchy_asset_refs": [{"org_tree": {"field_mapping": field_mapping}}]},
                "missing_org_tree": {**source, "hierarchy_asset_refs": {}},
                "empty_mapping": {**source, "hierarchy_asset_refs": {"org_tree": {"field_mapping": {}}}},
                "wrong_intent_hash": {**source, "content_sha256": "b" * 64},
            }
            for name, bad in bad_sources.items():
                with self.subTest(name=name):
                    path.write_text(json.dumps(bad))
                    with self.assertRaisesRegex(RuntimeBootstrapError, "FOUNDATION_PARENT_MATERIALIZER_HIERARCHY_MAPPING_INVALID"):
                        bootstrap._hierarchy_mapping(path, config)
            for name, bad_config in {
                "mapping_drift": {**config, "hierarchy_mapping_sha256": "0" * 64},
                "old_frozen_hash": {**config, "hierarchy_mapping_sha256": "72677a770efa584297e2c49b51225576eb61a4bd934e235779e3dc85528ebe84"},
            }.items():
                with self.subTest(name=name):
                    path.write_text(json.dumps(source))
                    with self.assertRaisesRegex(RuntimeBootstrapError, "FOUNDATION_PARENT_MATERIALIZER_HIERARCHY_MAPPING_DRIFT"):
                        bootstrap._hierarchy_mapping(path, bad_config)
            tampered = {**source, "hierarchy_asset_refs": {"org_tree": {"field_mapping": {"CHILD_ORG_ID": "ATTACKER_PARENT"}}}}
            path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(RuntimeBootstrapError, "FOUNDATION_PARENT_MATERIALIZER_HIERARCHY_MAPPING_DRIFT"):
                bootstrap._hierarchy_mapping(path, config)
            self.assertEqual(set(item.name for item in Path(tmp).iterdir()), {path.name})

    def test_registers_then_reads_back_before_dispatch(self) -> None:
        payload = json.loads((ROOT / "fixtures" / "penalty" / "matched.json").read_text(encoding="utf-8"))
        package = FormalReleaseCommitter(ROOT).build_penalty_source_package(payload, run_id="probe-run", trace_id="probe-trace", created_at="2026-08-18T00:00:00+00:00", attempt_no=2)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            roots = {"repo_root": str(base / "repo"), "runtime_root": str(base / "runtime"), "reference_root": str(base / "reference"), "reference_read_only": True}
            envelope = _envelope(package)
            result = RuntimeAdapter(ROOT, roots, envelope, preflight=_evidence(envelope)).register_output(package, task_id="task-010", runtime_id="runtime-010")
            self.assertEqual(result["next_dispatch"]["target"], "110")
            self.assertEqual(result["receipt"]["output_ref"], artifact_ref(package["envelope"]))
            self.assertIsNone(result["receipt"]["input_ref"])
            consumer_envelope = {**envelope, "target_agent_id": "110", "target_agent_uuid": "110-uuid", "input_ref": artifact_ref(package["envelope"]), "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "120"}}
            consumed = RuntimeAdapter(ROOT, roots, consumer_envelope, preflight=_evidence(consumer_envelope)).consume_input(task_id="task-110", runtime_id="runtime-110")
            self.assertEqual(consumed["next_dispatch"]["target"], "120")
            self.assertEqual(consumed["receipt"]["output_ref"], artifact_ref(package["envelope"]))

            calls = []
            def runner(command, **_kwargs):
                calls.append(command)
                if command[2] == "create": return SimpleNamespace(stdout='{"id":"next-issue"}')
                return SimpleNamespace(stdout='[{"id":"next-task","issue_id":"next-issue","agent_id":"120-uuid"}]')
            launched = RuntimeAdapter(ROOT, roots, consumer_envelope, preflight=_evidence(consumer_envelope)).launch_next_task(receipt=consumed["receipt"], platform_parent_issue_id="parent-issue", project_id="project", target_agent_id="120", target_agent_uuid="120-uuid", expected_output={"artifact_type": "penalty_fact_package", "producer_id": "120", "route_target": "130"}, runner=runner)
            self.assertEqual((launched["issue_id"], launched["task_id"]), ("next-issue", "next-task"))
            self.assertEqual([call[2] for call in calls], ["create", "runs"])

    def test_rejects_route_before_registry_write(self) -> None:
        envelope = _envelope()
        envelope["input_ref"] = {"artifact_id": "source", "version": 1, "content_hash": "a" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            adapter = RuntimeAdapter(ROOT, roots, envelope, preflight=_evidence(envelope))
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_OUTPUT_TRANSPORT_INVALID"):
                adapter.register_output({}, task_id="task", runtime_id="runtime")

    def test_missing_bootstrap_rejects_before_registry_creation(self) -> None:
        envelope = _envelope()
        del envelope["execution_bootstrap"]
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_BOOTSTRAP_MISSING"):
                RuntimeAdapter(ROOT, roots, envelope)
            self.assertFalse((Path(tmp) / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-70" / "probe-run" / "2" / "artifact-registry.json").exists())

    def test_missing_skill_bundle_rejects_before_registry_creation(self) -> None:
        envelope = _envelope()
        del envelope["execution_bootstrap"]["skill_bundle"]
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_SKILL_BUNDLE_MISSING"):
                RuntimeAdapter(ROOT, roots, envelope)
            self.assertFalse((Path(tmp) / "runtime").exists())

    def test_bootstrap_verifies_checkout_hashes_and_root_binding_before_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "candidate"
            adapter_path = checkout / "src" / "east_v5" / "runtime" / "adapter.py"
            bootstrap_path = checkout / "src" / "east_v5" / "runtime" / "bootstrap.py"
            runner_path = checkout / "scripts" / "runtime_bootstrap.py"
            adapter_path.parent.mkdir(parents=True)
            adapter_path.write_text("adapter-v1", encoding="utf-8")
            bootstrap_path.write_text("bootstrap-v1", encoding="utf-8")
            runner_path.parent.mkdir(parents=True)
            runner_path.write_text("print('runner-v1')", encoding="utf-8")
            import subprocess
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "candidate"], check=True)
            head = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            bootstrap = _bootstrap()
            bootstrap["candidate_head_sha"] = head
            bootstrap["adapter_sha256"] = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
            bootstrap["bootstrap_sha256"] = hashlib.sha256(bootstrap_path.read_bytes()).hexdigest()
            bootstrap["runner_sha256"] = hashlib.sha256(runner_path.read_bytes()).hexdigest()
            envelope = _envelope()
            envelope["execution_bootstrap"] = bootstrap
            envelope["root_binding_id"] = root_binding_id(bootstrap["runtime_context"])
            with patch.object(bootstrap_mod.tempfile, "gettempdir", return_value="/not-the-test-directory"), patch.object(bootstrap_mod, "_contains_symlink", return_value=False):
                evidence = RuntimeBootstrap(checkout, envelope, environ={"V5_RUNTIME_ROOT": str(Path(tmp) / "runtime")}).preflight()
            self.assertEqual(evidence.candidate_head_sha, head)
            self.assertEqual(evidence.root_binding_id, envelope["root_binding_id"])

    def test_bootstrap_code_hash_drift_has_zero_root_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "candidate"
            adapter_path = checkout / "src" / "east_v5" / "runtime" / "adapter.py"
            bootstrap_path = checkout / "src" / "east_v5" / "runtime" / "bootstrap.py"
            runner_path = checkout / "scripts" / "runtime_bootstrap.py"
            adapter_path.parent.mkdir(parents=True); runner_path.parent.mkdir(parents=True)
            adapter_path.write_text("adapter-v1", encoding="utf-8"); bootstrap_path.write_text("bootstrap-v1", encoding="utf-8"); runner_path.write_text("pass", encoding="utf-8")
            import subprocess
            subprocess.run(["git", "init", "-q", str(checkout)], check=True); subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "candidate"], check=True)
            bootstrap = _bootstrap()
            bootstrap["candidate_head_sha"] = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            bootstrap["bootstrap_sha256"] = hashlib.sha256(bootstrap_path.read_bytes()).hexdigest()
            bootstrap["runner_sha256"] = hashlib.sha256(runner_path.read_bytes()).hexdigest()
            envelope = _envelope(); envelope["execution_bootstrap"] = bootstrap; envelope["root_binding_id"] = root_binding_id(bootstrap["runtime_context"])
            root = Path(tmp) / "runtime"
            with patch.object(bootstrap_mod.tempfile, "gettempdir", return_value="/not-the-test-directory"), patch.object(bootstrap_mod, "_contains_symlink", return_value=False):
                with self.assertRaisesRegex(RuntimeBootstrapError, "RUNTIME_BOOTSTRAP_CODE_HASH_DRIFT"):
                    RuntimeBootstrap(checkout, envelope, environ={"V5_RUNTIME_ROOT": str(root)}).preflight()
            self.assertFalse(root.exists())

    def test_registry_unresolvable_has_no_next_dispatch(self) -> None:
        envelope = _envelope()
        envelope.update({"target_agent_id": "110", "target_agent_uuid": "110-uuid", "input_ref": {"artifact_id": "source", "version": 1, "content_hash": "a" * 64}, "expected_output": {"artifact_type": "penalty_source_package", "producer_id": "010", "route_target": "120"}})
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_INPUT_RESOLUTION_REJECTED"):
                RuntimeAdapter(ROOT, roots, envelope, preflight=_evidence(envelope)).consume_input(task_id="task-110", runtime_id="runtime-110")
            self.assertFalse((Path(tmp) / "runtime" / "vnext" / "03_构建过程层" / "issues" / "EAS-70" / "probe-run" / "2" / "artifact-registry.json").exists())

    def test_unverified_bootstrap_has_zero_registry_write(self) -> None:
        envelope = _envelope()
        with tempfile.TemporaryDirectory() as tmp:
            roots = {"repo_root": str(Path(tmp) / "repo"), "runtime_root": str(Path(tmp) / "runtime"), "reference_root": str(Path(tmp) / "reference"), "reference_read_only": True}
            with self.assertRaisesRegex(RuntimeAdapterError, "RUNTIME_BOOTSTRAP_UNVERIFIED"):
                RuntimeAdapter(ROOT, roots, envelope)
            self.assertFalse((Path(tmp) / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
