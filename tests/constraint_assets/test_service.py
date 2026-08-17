from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.constraint_assets import ConstraintAssetService, validate_reconciliation_manifest, validate_runtime_manifest, verify_query_receipt
from east_v5.governance import ContractError


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConstraintAssetServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        self.roots = {"repo_root": str(ROOT), "runtime_root": str(self.runtime), "reference_root": str(self.base / "reference"), "reference_read_only": True}
        self.sqlite = self.runtime / "ca.sqlite"
        self.single = self.runtime / "single.sqlite"
        self.edges = self.runtime / "edges.jsonl"
        self.nodes = self.runtime / "nodes.jsonl"
        self.projections = self.runtime / "projections.jsonl"
        self.closures = self.runtime / "closures.jsonl"
        self._create_sqlite()
        self._create_single_field_sqlite()
        self.edges.write_text("\n".join(json.dumps({"provider_table_code": f"PARENT_{number:03d}", "consumer_table_code": "CHILD", "edge_type": "REFERENCE", "metadata": {"ordinal": number}}) for number in range(122)) + "\n", encoding="utf-8")
        for path in (self.nodes, self.projections, self.closures):
            path.write_text("{}\n", encoding="utf-8")
        self.control = self.base / "approved-assets.json"
        self.manifest = self.runtime / "asset-manifest.json"
        self._write_control_and_manifest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_sqlite(self) -> None:
        con = sqlite3.connect(self.sqlite)
        con.executescript("""
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
        INSERT INTO field_master VALUES ('child-id', 'CHILD.ID', 'CHILD');
        INSERT INTO multifield_constraint VALUES ('MFC-1', 'REFERENCE_EXISTENCE', 'CROSS_TABLE', '{ "kind" : "ref" }', '[ "E-1" ]', 'a', 'APPROVED');
        INSERT INTO multifield_constraint_field VALUES ('MFC-1', 1, 'CHILD.ID');
        INSERT INTO multifield_constraint_field VALUES ('MFC-1', 2, 'CHILD.ID');
        """)
        for number in range(2, 123):
            con.execute("INSERT INTO multifield_constraint VALUES (?, 'COMPARISON', 'INTRA_TABLE', '{\"kind\":\"cmp\"}', '[\"E-2\"]', ?, 'APPROVED')", (f"MFC-{number:03d}", f"hash-{number}"))
            con.execute("INSERT INTO multifield_constraint_field VALUES (?, 1, 'CHILD.ID')", (f"MFC-{number:03d}",))
        con.commit(); con.close()

    def _create_single_field_sqlite(self) -> None:
        con = sqlite3.connect(self.single)
        con.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE field_master (field_id TEXT PRIMARY KEY, table_code TEXT NOT NULL, field_code TEXT NOT NULL);
        CREATE TABLE single_field_constraints (id INTEGER PRIMARY KEY, field_id TEXT NOT NULL REFERENCES field_master(field_id), constraint_item_type TEXT NOT NULL, value_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, review_status TEXT NOT NULL);
        INSERT INTO field_master VALUES ('sf-child', 'CHILD', 'STATUS');
        INSERT INTO single_field_constraints VALUES (1, 'sf-child', 'CODE_DOMAIN', '{ "codes" : ["A", "B"] }', '[ "SF-E-1" ]', 'APPROVED');
        INSERT INTO single_field_constraints VALUES (2, 'sf-child', 'NULLABLE', '{ "nullable" : "NO" }', '[ "SF-E-2" ]', 'APPROVED');
        """)
        con.commit(); con.close()

    def _write_control_and_manifest(self) -> None:
        ca_hash, single_hash, edge_hash = digest(self.sqlite), digest(self.single), digest(self.edges)
        payloads = {"nodes": digest(self.nodes), "edges": edge_hash, "projections": digest(self.projections), "closures": digest(self.closures)}
        control = {"schema_version": "v5.constraint-assets-control/v1", "assets": [
            {"artifact_type": "constraint_asset_ref", "artifact_id": "single-id", "asset_version": "CA-V0.2.0", "content_hash": "c" * 64, "required_payload": {"single_field": single_hash}, "required_sqlite_tables": ["field_master", "single_field_constraints"], "required_sqlite_views": []},
            {"artifact_type": "constraint_asset_ref", "artifact_id": "ca-id", "asset_version": "CA-V0.3.0", "content_hash": "a" * 64, "required_payload": {"sqlite": ca_hash}, "required_sqlite_tables": ["decision_audit", "evidence", "excluded_constraint_audit", "field_master", "multifield_constraint", "multifield_constraint_field", "release_meta", "source_manifest"], "required_sqlite_views": ["approved_comparison_constraints", "approved_reference_constraints", "cross_table_constraints", "intra_table_constraints"]},
            {"artifact_type": "typed_reference_graph_ref", "artifact_id": "graph-id", "asset_version": "TRG-V1.0.0", "content_hash": "b" * 64, "required_payload": payloads}
        ]}
        self.control.write_text(json.dumps(control), encoding="utf-8")
        manifest = {"schema_version": "v5.constraint-assets-runtime-manifest/v1", "assets": [
            {"artifact_id": "single-id", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.2.0", "content_hash": "c" * 64, "payload": {"single_field": {"locator": str(self.single), "sha256": single_hash}}},
            {"artifact_id": "ca-id", "artifact_type": "constraint_asset_ref", "asset_version": "CA-V0.3.0", "content_hash": "a" * 64, "payload": {"sqlite": {"locator": str(self.sqlite), "sha256": ca_hash}}},
            {"artifact_id": "graph-id", "artifact_type": "typed_reference_graph_ref", "asset_version": "TRG-V1.0.0", "content_hash": "b" * 64, "payload": {role: {"locator": str(getattr(self, role)), "sha256": value} for role, value in payloads.items()}}
        ]}
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    def service(self) -> ConstraintAssetService:
        return ConstraintAssetService(ROOT, self.roots, self.manifest, control_path=self.control)

    def test_000_and_220_stubs_consume_same_versioned_readonly_assets(self) -> None:
        service = self.service()
        zero_stub = service.constraints_for_table("CHILD")
        structure_stub = service.graph_edges_for_table("CHILD")
        self.assertEqual(zero_stub["asset_version"], "CA-V0.3.0")
        self.assertEqual(zero_stub["content_hash"], "a" * 64)
        self.assertEqual(zero_stub["total"], 122)
        self.assertEqual(zero_stub["returned_count"], 100)
        self.assertEqual(len({row["constraint_id"] for row in zero_stub["records"]}), 100)
        self.assertIn("MFC-1", {row["constraint_id"] for row in service.constraints_for_table("CHILD", limit=500)["records"]})
        self.assertEqual(structure_stub["asset_version"], "TRG-V1.0.0")
        self.assertEqual(structure_stub["total"], 122)
        with self.assertRaises(sqlite3.OperationalError):
            con = sqlite3.connect(f"file:{self.sqlite}?mode=ro", uri=True)
            con.execute("DELETE FROM field_master")

    def test_hash_drift_unknown_version_and_outside_runtime_are_rejected_before_query(self) -> None:
        data = json.loads(self.manifest.read_text())
        data["assets"][1]["payload"]["sqlite"]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "ASSET_PAYLOAD_HASH_DRIFT"):
            self.service()
        self._write_control_and_manifest()
        data = json.loads(self.manifest.read_text())
        data["assets"][1]["asset_version"] = "CA-V9.9.9"
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "ASSET_VERSION_UNSUPPORTED"):
            validate_runtime_manifest(ROOT, self.roots, self.manifest, control_path=self.control)
        self._write_control_and_manifest()
        data = json.loads(self.manifest.read_text())
        data["assets"][1]["payload"]["sqlite"]["locator"] = str(self.base / "outside.sqlite")
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "ASSET_LOCATOR_OUT_OF_RUNTIME"):
            self.service()

    def test_schema_and_query_rejections_are_stable_and_idempotent(self) -> None:
        service = self.service()
        first, second = service.constraints_for_table("CHILD"), service.constraints_for_table("CHILD")
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_INVALID"):
            service.constraints_for_table("")
        for value in (0, 501):
            with self.assertRaisesRegex(ContractError, "ASSET_QUERY_INVALID"):
                service.constraints_for_table("CHILD", limit=value)
        data = json.loads(self.manifest.read_text())
        data["assets"][0]["unexpected"] = True
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "ASSET_RUNTIME_MANIFEST_INVALID"):
            self.service()

    def test_reconciliation_manifest_freezes_ca_v020_roles_and_hashes(self) -> None:
        service = self.service()
        manifest = service.reconciliation_manifest()
        self.assertEqual(manifest["schema_version"], "v5.constraint-assets-reconciliation/v1")
        self.assertEqual(manifest["asset"]["asset_version"], "CA-V0.2.0-foundation")
        self.assertEqual(manifest["human_override"]["effective_role"], "single_field_and_code_tables")
        roles = {record["role"]: record for record in manifest["registered_files"]}
        self.assertEqual(set(roles), {"single_field", "local_codes", "national_codes"})
        self.assertEqual(roles["single_field"]["sha256"], "f137be03a3814428b76507373d2032705dcacb1ff804adbddd96e397da5c1d6f")
        self.assertFalse(roles["single_field"]["registered_in_legacy_manifest"])
        self.assertIn("SINGLE_FIELD_CONSTRAINTS", manifest["legacy_source_facts"]["legacy_not_in_scope"])
        # A valid reconciliation manifest must round-trip through the schema unchanged.
        self.assertEqual(validate_reconciliation_manifest(ROOT), manifest)
        # Drifted hash and unknown field are rejected before any consumer can use it.
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            contracts = temp_root / "contracts" / "constraint_assets"
            contracts.mkdir(parents=True)
            src = ROOT / "contracts" / "constraint_assets"
            for name in ("reconciliation-manifest.json", "reconciliation-manifest.schema.json"):
                (contracts / name).write_bytes((src / name).read_bytes())
            bad_hash = json.loads((contracts / "reconciliation-manifest.json").read_text())
            bad_hash["registered_files"][0]["sha256"] = "0" * 64
            (contracts / "reconciliation-manifest.json").write_text(json.dumps(bad_hash), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "ASSET_RECONCILIATION_HASH_DRIFT"):
                validate_reconciliation_manifest(temp_root)
            unknown = json.loads((contracts / "reconciliation-manifest.json").read_text())
            unknown["unexpected"] = True
            (contracts / "reconciliation-manifest.json").write_text(json.dumps(unknown), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "ASSET_RECONCILIATION_MANIFEST_INVALID"):
                validate_reconciliation_manifest(temp_root)

    def test_242_and_252_stubs_consume_versioned_readonly_assets(self) -> None:
        service = self.service()
        # 242 (data validator) consumes the CA-V0.3.0 constraints to validate a bound record.
        constraints = service.constraints_for_table("CHILD")
        self.assertEqual(constraints["asset_version"], "CA-V0.3.0")
        record = {"table_code": "CHILD", "constraint_id": "MFC-1"}
        validator_verdict = {"consumer": "242", "constraint_ref": constraints["content_hash"], "rows": constraints["records"], "record": record}
        self.assertEqual(validator_verdict["rows"][0]["constraint_id"], "MFC-002")
        # 252 (ORM hash freezer) consumes the TRG-V1.0.0 edges and freezes a deterministic hash.
        edges = service.graph_edges_for_table("CHILD")
        self.assertEqual(edges["asset_version"], "TRG-V1.0.0")
        freezer_hash = hashlib.sha256(json.dumps(edges["records"], sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(len(freezer_hash), 64)
        # Both downstream stubs observe the same immutable content identity.
        self.assertEqual(constraints["content_hash"], "a" * 64)
        self.assertEqual(edges["content_hash"], "b" * 64)

    def test_real_service_paginates_all_three_sources_and_rejects_cursor_confusion(self) -> None:
        service = self.service()
        first = service.constraints_for_table("CHILD", limit=100)
        self.assertEqual((first["total"], first["returned_count"], first["complete"]), (122, 100, False))
        second = service.constraints_for_table("CHILD", limit=100, cursor=first["next_cursor"])
        self.assertEqual((second["returned_count"], second["complete"]), (22, True))
        self.assertEqual(len({row["constraint_id"] for row in first["records"] + second["records"]}), 122)
        self.assertEqual(first["records"][0]["canonical_rule_hash"], hashlib.sha256(json.dumps({key: value for key, value in first["records"][0].items() if key not in {"content_sha256", "canonical_rule_hash"}}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest())
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_CURSOR_INVALID"):
            service.graph_edges_for_table("CHILD", limit=100, cursor=first["next_cursor"])
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_CURSOR_INVALID"):
            service.constraints_for_table("OTHER", limit=100, cursor=first["next_cursor"])
        field_rules = service.field_rules_for_table("CHILD", limit=1)
        self.assertEqual((field_rules["asset_version"], field_rules["total"], field_rules["returned_count"]), ("CA-V0.2.0", 2, 1))
        edge_page = service.graph_edges_for_table("CHILD", limit=100)
        self.assertEqual((edge_page["total"], edge_page["returned_count"], edge_page["complete"]), (122, 100, False))
        self.assertEqual(edge_page["records"][0]["consumer_table_code"], "CHILD")

    def test_receipt_and_runtime_drift_fail_closed(self) -> None:
        service = self.service()
        result = service.constraints_for_table("CHILD", limit=2)
        verify_query_receipt(result, query_method="constraints_for_table", table_code="CHILD", source_entry=service.ca)
        for key, value, code in (("total", 99, "ASSET_QUERY_RECEIPT_INVALID"), ("table_code", "OTHER", "ASSET_QUERY_RECEIPT_TABLE_MISMATCH"), ("query_method", "graph_edges_for_table", "ASSET_QUERY_RECEIPT_METHOD_MISMATCH")):
            forged = dict(result)
            forged[key] = value
            if key in {"table_code", "query_method"}:
                forged["receipt_hash"] = hashlib.sha256(json.dumps({name: item for name, item in forged.items() if name != "receipt_hash"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            with self.assertRaisesRegex(ContractError, code):
                verify_query_receipt(forged, query_method="constraints_for_table", table_code="CHILD", source_entry=service.ca)
        with sqlite3.connect(self.sqlite) as con:
            con.execute("DELETE FROM multifield_constraint WHERE constraint_id = 'MFC-002'")
        with self.assertRaisesRegex(ContractError, "ASSET_PAYLOAD_HASH_DRIFT"):
            service.constraints_for_table("CHILD")


if __name__ == "__main__":
    unittest.main()
