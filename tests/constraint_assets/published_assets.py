"""Ephemeral runtime binding for the frozen published constraint assets.

The helper copies approved, already versioned delivery assets into a temporary
runtime root.  It never changes the published payloads or uses a formal
database, allowing production consumers to be exercised against the genuine
TRG records without adding runtime data to Git.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from east_v5.constraint_assets import ConstraintAssetService

ROOT = Path(__file__).resolve().parents[2]
DELIVERY = ROOT / "05_新版本交付层"

ASSETS = {
    "single_field": DELIVERY / "约束资产" / "CA-V0.2.0-foundation" / "single_field_final.sqlite",
    "sqlite": DELIVERY / "约束资产" / "CA-V0.3.0-multifield" / "multifield_constraints.sqlite",
    "nodes": DELIVERY / "带类型引用图" / "TRG-V1.0.0" / "typed_reference_graph_nodes.jsonl",
    "edges": DELIVERY / "带类型引用图" / "TRG-V1.0.0" / "typed_reference_graph_edges.jsonl",
    "projections": DELIVERY / "带类型引用图" / "TRG-V1.0.0" / "typed_reference_graph_projections.jsonl",
    "closures": DELIVERY / "带类型引用图" / "TRG-V1.0.0" / "typed_reference_graph_closures.jsonl",
}
EXPECTED_PAYLOAD_HASHES = {
    "single_field": "f137be03a3814428b76507373d2032705dcacb1ff804adbddd96e397da5c1d6f",
    "sqlite": "5e2753235bf1e47c9d015a05d28c45a20d17a26789d1c88498f80b6ec43577bb",
    "nodes": "9d2247759f5071da6b8d22b57aa6b1515d17203d7ef02505895e202da84a6f4b",
    "edges": "51a4b2a64f95ece7e1239a06a10c6a1cd6d79e4baf7699107f042ff0e707a45c",
    "projections": "ddd202ba610d4e63600c2d242e8ac32bc5502ab7cbeb085183ab0951f65c5c30",
    "closures": "84b40c1055296dd5be27dbb2ef6c0b1f48aace4d505fc1783feca62e67f34c7a",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PublishedTrgRuntime:
    """Disposable runtime containing byte-identical copies of frozen assets."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        self.roots = {
            "repo_root": str(ROOT),
            "runtime_root": str(self.runtime),
            "reference_root": str(self.base / "reference"),
            "reference_read_only": True,
        }
        self.paths = {role: self.runtime / source.name for role, source in ASSETS.items()}
        for role, source in ASSETS.items():
            if not source.is_file():
                raise RuntimeError(f"missing frozen delivery asset: {role}")
            if digest(source) != EXPECTED_PAYLOAD_HASHES[role]:
                raise RuntimeError(f"frozen delivery asset hash drift: {role}")
            shutil.copy2(source, self.paths[role])
        self.control = self.base / "approved-assets.json"
        self.manifest = self.runtime / "asset-manifest.json"
        self._write_control_and_manifest()

    def close(self) -> None:
        self._tmp.cleanup()

    def service(self) -> ConstraintAssetService:
        return ConstraintAssetService(ROOT, self.roots, self.manifest, control_path=self.control)

    def _write_control_and_manifest(self) -> None:
        hashes = {role: digest(path) for role, path in self.paths.items()}
        if hashes != EXPECTED_PAYLOAD_HASHES:
            raise RuntimeError("temporary runtime copy hash drift")
        control = {
            "schema_version": "v5.constraint-assets-control/v1",
            "assets": [
                {
                    "artifact_type": "constraint_asset_ref", "artifact_id": "CA-FOUNDATION-20260805-002",
                    "asset_version": "CA-V0.2.0", "content_hash": "6cb0f385adae4e3e9e24558dc432132b43c437e8c2db5ce329c46bf68e8c4188",
                    "required_payload": {"single_field": hashes["single_field"]},
                    "required_sqlite_tables": ["field_master", "single_field_constraints"], "required_sqlite_views": [],
                },
                {
                    "artifact_type": "constraint_asset_ref", "artifact_id": "CA-MULTIFIELD-20260812-003",
                    "asset_version": "CA-V0.3.0", "content_hash": "cbcbd79e318f91522393403241b50919a656aa77d71c7f950f43725286b64d3d",
                    "required_payload": {"sqlite": hashes["sqlite"]},
                    "required_sqlite_tables": ["decision_audit", "evidence", "excluded_constraint_audit", "field_master", "multifield_constraint", "multifield_constraint_field", "release_meta", "source_manifest"],
                    "required_sqlite_views": ["approved_comparison_constraints", "approved_reference_constraints", "cross_table_constraints", "intra_table_constraints"],
                },
                {
                    "artifact_type": "typed_reference_graph_ref", "artifact_id": "EAS-TYPED-GRAPH-20260812-001",
                    "asset_version": "TRG-V1.0.0", "content_hash": "a3480f669bd9e97db78a8fec96fac7e317b43b9ed6f222d5c920bc227eaf3b6a",
                    "required_payload": {role: hashes[role] for role in ("nodes", "edges", "projections", "closures")},
                },
            ],
        }
        manifest = {
            "schema_version": "v5.constraint-assets-runtime-manifest/v1",
            "assets": [
                {"artifact_id": "CA-FOUNDATION-20260805-002", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.2.0", "content_hash": "6cb0f385adae4e3e9e24558dc432132b43c437e8c2db5ce329c46bf68e8c4188", "payload": {"single_field": {"locator": str(self.paths["single_field"]), "sha256": hashes["single_field"]}}},
                {"artifact_id": "CA-MULTIFIELD-20260812-003", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.3.0", "content_hash": "cbcbd79e318f91522393403241b50919a656aa77d71c7f950f43725286b64d3d", "payload": {"sqlite": {"locator": str(self.paths["sqlite"]), "sha256": hashes["sqlite"]}}},
                {"artifact_id": "EAS-TYPED-GRAPH-20260812-001", "artifact_type": "typed_reference_graph_ref", "asset_version": "TRG-V1.0.0", "content_hash": "a3480f669bd9e97db78a8fec96fac7e317b43b9ed6f222d5c920bc227eaf3b6a", "payload": {role: {"locator": str(self.paths[role]), "sha256": hashes[role]} for role in ("nodes", "edges", "projections", "closures")}},
            ],
        }
        self.control.write_text(json.dumps(control), encoding="utf-8")
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
