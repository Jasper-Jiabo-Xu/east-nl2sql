"""Deterministic sanitized runtime fixture for the real ``ConstraintAssetService``.

Builds a minimal, desensitized runtime data plane — CA-V0.2.0 single-field
SQLite, CA-V0.3.0 multi-field SQLite (with a >LIMIT table for pagination
closure) and TRG-V1.0.0 edges JSONL — plus a matching sanitized control file and
runtime manifest, all keyed to hashes computed at build time.  This is how the
real ``build_constraint_asset_resolver`` → 242 → 260 path is exercised without
any ``FixtureQueryService`` or other Protocol stand-in, and without committing
real SQLite, real records or logs.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from east_v5.constraint_assets.service import ConstraintAssetService

from .resolver import build_constraint_asset_resolver

ROOT = Path(__file__).resolve().parents[4]

# --- sanitized expression bodies (desensitized, no business data) ---

COMPARISON_EXPRESSION = {
    "assertion": {"left": "FIXTURE_T001.F001", "operator": "<=", "right": "FIXTURE_T001.F002"},
    "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0",
}
REFERENCE_EXPRESSION = {
    "kind": "REFERENCE_EXISTENCE", "schema_version": "EAS-MFC-1.0",
    "condition_text": "FIXTURE_T001.F001 必须存在于 FIXTURE_T002.PK001",
    "consumer_field": "FIXTURE_T001.F001", "direction": "PROVIDER_TO_CONSUMER",
    "provider_fields": ["FIXTURE_T002.PK001"], "provider_match": "ONE",
}
BIG_COMPARISON_EXPRESSION = {
    "assertion": {"left": "BIG.F1", "operator": "<=", "right": "BIG.F2"},
    "kind": "COMPARISON", "schema_version": "EAS-MFC-1.0",
}

PAGINATION_TOTAL = 122


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SanitizedRuntime:
    """A disposable, deterministic sanitized constraint-asset data plane."""

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
        self.sqlite = self.runtime / "ca.sqlite"
        self.single = self.runtime / "single.sqlite"
        self.edges = self.runtime / "edges.jsonl"
        self.nodes = self.runtime / "nodes.jsonl"
        self.projections = self.runtime / "projections.jsonl"
        self.closures = self.runtime / "closures.jsonl"
        self._create_sqlite()
        self._create_single_field_sqlite()
        self.edges.write_text(
            json.dumps({
                "source_table": "FIXTURE_T002", "source_field": "FIXTURE_T002.PK001",
                "target_table": "FIXTURE_T001", "target_field": "FIXTURE_T001.F001",
                "edge_type": "REFERENCE",
                "expression": {
                    "direction": "PROVIDER_TO_CONSUMER",
                    "provider_fields": ["FIXTURE_T002.PK001"],
                    "consumer_field": "FIXTURE_T001.F001",
                },
            }) + "\n",
            encoding="utf-8",
        )
        for path in (self.nodes, self.projections, self.closures):
            path.write_text("{}\n", encoding="utf-8")
        self.control = self.base / "approved-assets.json"
        self.manifest = self.runtime / "asset-manifest.json"
        self._write_control_and_manifest()

    def close(self) -> None:
        self._tmp.cleanup()

    def _create_sqlite(self) -> None:
        con = sqlite3.connect(self.sqlite)
        con.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE decision_audit (id TEXT PRIMARY KEY);
            CREATE TABLE evidence (id TEXT PRIMARY KEY);
            CREATE TABLE excluded_constraint_audit (id TEXT PRIMARY KEY);
            CREATE TABLE field_master (field_id TEXT PRIMARY KEY, endpoint TEXT UNIQUE, table_code TEXT NOT NULL);
            CREATE TABLE multifield_constraint (constraint_id TEXT PRIMARY KEY, constraint_item_type TEXT NOT NULL, scope TEXT NOT NULL, structured_expression_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, content_sha256 TEXT NOT NULL, approval_status TEXT NOT NULL);
            CREATE TABLE multifield_constraint_field (constraint_id TEXT NOT NULL REFERENCES multifield_constraint(constraint_id), field_ordinal INTEGER NOT NULL, field_ref TEXT NOT NULL REFERENCES field_master(endpoint), PRIMARY KEY(constraint_id, field_ordinal));
            CREATE TABLE release_meta (id TEXT PRIMARY KEY);
            CREATE TABLE source_manifest (id TEXT PRIMARY KEY);
            CREATE VIEW approved_comparison_constraints AS SELECT * FROM multifield_constraint WHERE constraint_item_type = 'COMPARISON';
            CREATE VIEW approved_reference_constraints AS SELECT * FROM multifield_constraint WHERE constraint_item_type = 'REFERENCE_EXISTENCE';
            CREATE VIEW cross_table_constraints AS SELECT * FROM multifield_constraint WHERE scope = 'CROSS_TABLE';
            CREATE VIEW intra_table_constraints AS SELECT * FROM multifield_constraint WHERE scope = 'INTRA_TABLE';
            """
        )
        con.execute("INSERT INTO field_master VALUES ('f-t001-f001', 'FIXTURE_T001.F001', 'FIXTURE_T001')")
        con.execute("INSERT INTO field_master VALUES ('f-t001-f002', 'FIXTURE_T001.F002', 'FIXTURE_T001')")
        con.execute("INSERT INTO field_master VALUES ('f-t002-pk001', 'FIXTURE_T002.PK001', 'FIXTURE_T002')")
        con.execute("INSERT INTO field_master VALUES ('f-big-f1', 'BIG.F1', 'BIG')")
        con.execute("INSERT INTO field_master VALUES ('f-big-f2', 'BIG.F2', 'BIG')")

        con.execute(
            "INSERT INTO multifield_constraint VALUES ('C-CMP-T001', 'COMPARISON', 'INTRA_TABLE', ?, ?, 'h-cmp', 'APPROVED')",
            (json.dumps(COMPARISON_EXPRESSION), json.dumps(["E-1"])),
        )
        con.execute(
            "INSERT INTO multifield_constraint VALUES ('C-REF-T001-F001', 'REFERENCE_EXISTENCE', 'CROSS_TABLE', ?, ?, 'h-ref', 'APPROVED')",
            (json.dumps(REFERENCE_EXPRESSION), json.dumps(["E-2"])),
        )
        con.execute("INSERT INTO multifield_constraint_field VALUES ('C-CMP-T001', 1, 'FIXTURE_T001.F001')")
        con.execute("INSERT INTO multifield_constraint_field VALUES ('C-CMP-T001', 2, 'FIXTURE_T001.F002')")
        con.execute("INSERT INTO multifield_constraint_field VALUES ('C-REF-T001-F001', 1, 'FIXTURE_T001.F001')")
        con.execute("INSERT INTO multifield_constraint_field VALUES ('C-REF-T001-F001', 2, 'FIXTURE_T002.PK001')")

        for number in range(1, PAGINATION_TOTAL + 1):
            cid = f"BIG-{number:03d}"
            con.execute(
                "INSERT INTO multifield_constraint VALUES (?, 'COMPARISON', 'INTRA_TABLE', ?, ?, ?, 'APPROVED')",
                (cid, json.dumps(BIG_COMPARISON_EXPRESSION), json.dumps([]), f"h-big-{number}"),
            )
            con.execute("INSERT INTO multifield_constraint_field VALUES (?, 1, 'BIG.F1')", (cid,))
            con.execute("INSERT INTO multifield_constraint_field VALUES (?, 2, 'BIG.F2')", (cid,))
        con.commit()
        con.close()

    def _create_single_field_sqlite(self) -> None:
        con = sqlite3.connect(self.single)
        con.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE field_master (field_id TEXT PRIMARY KEY, table_code TEXT NOT NULL, field_code TEXT NOT NULL);
            CREATE TABLE single_field_constraints (id INTEGER PRIMARY KEY, field_id TEXT NOT NULL REFERENCES field_master(field_id), constraint_item_type TEXT NOT NULL, value_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, review_status TEXT NOT NULL);
            """
        )
        con.execute("INSERT INTO field_master VALUES ('sf-f002', 'FIXTURE_T001', 'F002')")
        con.execute("INSERT INTO field_master VALUES ('sf-c001', 'FIXTURE_CUSTOMER', 'C001')")
        con.execute("INSERT INTO single_field_constraints VALUES (1, 'sf-f002', 'NULLABLE', ?, ?, 'APPROVED')", (json.dumps({"nullable": "NO"}), json.dumps(["SF-E-1"])))
        con.execute("INSERT INTO single_field_constraints VALUES (2, 'sf-c001', 'NULLABLE', ?, ?, 'APPROVED')", (json.dumps({"nullable": "NO"}), json.dumps(["SF-E-2"])))
        con.commit()
        con.close()

    def _write_control_and_manifest(self) -> None:
        ca_hash, single_hash = _digest(self.sqlite), _digest(self.single)
        payloads = {"nodes": _digest(self.nodes), "edges": _digest(self.edges), "projections": _digest(self.projections), "closures": _digest(self.closures)}
        control = {
            "schema_version": "v5.constraint-assets-control/v1",
            "assets": [
                {"artifact_type": "constraint_asset_ref", "artifact_id": "single-id", "asset_version": "CA-V0.2.0", "content_hash": "c" * 64, "required_payload": {"single_field": single_hash}, "required_sqlite_tables": ["field_master", "single_field_constraints"], "required_sqlite_views": []},
                {"artifact_type": "constraint_asset_ref", "artifact_id": "ca-id", "asset_version": "CA-V0.3.0", "content_hash": "a" * 64, "required_payload": {"sqlite": ca_hash}, "required_sqlite_tables": ["decision_audit", "evidence", "excluded_constraint_audit", "field_master", "multifield_constraint", "multifield_constraint_field", "release_meta", "source_manifest"], "required_sqlite_views": ["approved_comparison_constraints", "approved_reference_constraints", "cross_table_constraints", "intra_table_constraints"]},
                {"artifact_type": "typed_reference_graph_ref", "artifact_id": "graph-id", "asset_version": "TRG-V1.0.0", "content_hash": "b" * 64, "required_payload": payloads},
            ],
        }
        self.control.write_text(json.dumps(control), encoding="utf-8")
        manifest = {
            "schema_version": "v5.constraint-assets-runtime-manifest/v1",
            "assets": [
                {"artifact_id": "single-id", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.2.0", "content_hash": "c" * 64, "payload": {"single_field": {"locator": str(self.single), "sha256": single_hash}}},
                {"artifact_id": "ca-id", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.3.0", "content_hash": "a" * 64, "payload": {"sqlite": {"locator": str(self.sqlite), "sha256": ca_hash}}},
                {"artifact_id": "graph-id", "artifact_type": "typed_reference_graph_ref", "asset_version": "TRG-V1.0.0", "content_hash": "b" * 64, "payload": {role: {"locator": str(getattr(self, role)), "sha256": value} for role, value in payloads.items()}},
            ],
        }
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    def service(self) -> ConstraintAssetService:
        return ConstraintAssetService(ROOT, self.roots, self.manifest, control_path=self.control)

    def resolver(self) -> Any:
        return build_constraint_asset_resolver(ROOT, self.roots, self.manifest, control_path=self.control)
