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

from east_v5.constraint_assets import ConstraintAssetService, consume_complete_table, validate_query_receipt_contract, validate_reconciliation_manifest, validate_runtime_manifest
from east_v5.governance import ContractError
try:
    from constraint_assets.published_assets import PublishedTrgRuntime
except ModuleNotFoundError:  # direct module execution from the repository root
    from tests.constraint_assets.published_assets import PublishedTrgRuntime


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
        self.edges.write_text("\n".join(json.dumps({"source_table": f"PARENT_{number:03d}", "source_field": f"PARENT_{number:03d}.ID", "target_table": "CHILD", "target_field": "CHILD.ID", "edge_type": "REFERENCE", "metadata": {"ordinal": number}}) for number in range(122)) + "\n", encoding="utf-8")
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

    def test_published_trg_real_000_220_consumption_is_complete_and_deterministic(self) -> None:
        """Exercise the production service against every frozen, real TRG edge.

        The service is the fixed 000/220 query boundary.  This intentionally
        uses a temporary runtime copy rather than a Protocol stand-in or an
        in-repository runtime payload.
        """
        runtime = PublishedTrgRuntime()
        try:
            raw_edges = [json.loads(line) for line in runtime.paths["edges"].read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(raw_edges), 537)
            table_codes = sorted({edge["source_table"] for edge in raw_edges} | {edge["target_table"] for edge in raw_edges})
            self.assertGreaterEqual(len(table_codes), 29)

            def consume_all() -> dict[str, list[dict]]:
                service = runtime.service()
                return {
                    table: consume_complete_table(service, "graph_edges_for_table", table, page_size=100)["records"]
                    for table in table_codes
                }

            first, second = consume_all(), consume_all()
            self.assertEqual(first, second)
            returned = {edge["canonical_edge_hash"] for records in first.values() for edge in records}
            self.assertEqual(len(returned), 537)
            self.assertTrue(all("canonical_edge_hash" not in edge for edge in raw_edges))  # raw assets are not rewritten
            self.assertTrue(all({"source_table", "source_field", "target_table", "target_field", "edge_type", "canonical_edge_hash"} <= set(edge) for records in first.values() for edge in records))
        finally:
            runtime.close()

    def test_legacy_provider_consumer_graph_record_is_rejected(self) -> None:
        self.edges.write_text(json.dumps({"provider_table_code": "PARENT", "consumer_table_code": "CHILD", "edge_type": "REFERENCE"}) + "\n", encoding="utf-8")
        self._write_control_and_manifest()
        with self.assertRaisesRegex(ContractError, "ASSET_GRAPH_RECORD_INVALID"):
            self.service().graph_edges_for_table("CHILD")

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
        self.assertEqual(first["records"], second["records"])
        self.assertEqual((first["total"], first["records_hash"]), (second["total"], second["records_hash"]))
        self.assertNotEqual(first["next_cursor"], second["next_cursor"])
        self.assertEqual(service.constraints_for_table("CHILD", cursor=first["next_cursor"])["records"], service.constraints_for_table("CHILD", cursor=second["next_cursor"])["records"])
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
        self.assertEqual(edge_page["records"][0]["target_table"], "CHILD")

    def test_receipt_and_runtime_drift_fail_closed(self) -> None:
        service = self.service()
        result = service.constraints_for_table("CHILD", limit=100)
        validate_query_receipt_contract(result, query_method="constraints_for_table", table_code="CHILD")
        forged = dict(result)
        forged.update({"total": 100, "complete": True, "next_cursor": None})
        # This models an adapter that recomputes every *public* checksum.  The
        # structural helper accepts it by design; only the live service proof is
        # authoritative and must reject it.
        forged["receipt_hash"] = hashlib.sha256(json.dumps({name: item for name, item in forged.items() if name not in {"receipt_hash", "service_proof"}}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        validate_query_receipt_contract(forged, query_method="constraints_for_table", table_code="CHILD")
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_RECEIPT_ORIGIN_INVALID"):
            service._verify_service_receipt(forged, query_method="constraints_for_table", table_code="CHILD")
        completed = consume_complete_table(service, "constraints_for_table", "CHILD", page_size=100)
        self.assertEqual((completed["total"], completed["returned_count"], len(completed["records"])), (122, 122, 122))
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_SERVICE_REQUIRED"):
            consume_complete_table(object(), "constraints_for_table", "CHILD")
        with sqlite3.connect(self.sqlite) as con:
            con.execute("DELETE FROM multifield_constraint WHERE constraint_id = 'MFC-002'")
        with self.assertRaisesRegex(ContractError, "ASSET_PAYLOAD_HASH_DRIFT"):
            service.constraints_for_table("CHILD")

    def test_cursor_tamper_skip_replay_and_production_chain_replay_are_rejected(self) -> None:
        import base64

        service = self.service()
        first = service.constraints_for_table("CHILD", limit=100)
        padded = first["next_cursor"] + "=" * (-len(first["next_cursor"]) % 4)
        cursor = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        cursor["offset"] = 120
        unsigned = {key: value for key, value in cursor.items() if key != "signature"}
        # A caller can recreate the old public SHA-256 format, but it is no
        # longer a valid cursor capability.
        cursor["signature"] = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        tampered = base64.urlsafe_b64encode(json.dumps(cursor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_CURSOR_INVALID"):
            service.constraints_for_table("CHILD", limit=100, cursor=tampered)
        valid_cursor = first["next_cursor"]
        self.assertEqual(service.constraints_for_table("CHILD", limit=100, cursor=valid_cursor)["returned_count"], 22)
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_CURSOR_INVALID"):
            service.constraints_for_table("CHILD", limit=100, cursor=valid_cursor)
        self.assertEqual(consume_complete_table(service, "constraints_for_table", "CHILD", page_size=100)["returned_count"], 122)
        original_query = service.constraints_for_table

        def replay_page(table_code: str, *, limit: int = 100, cursor: str | None = None) -> dict[str, object]:
            return first if cursor is not None else original_query(table_code, limit=limit, cursor=cursor)

        service.constraints_for_table = replay_page  # type: ignore[method-assign]
        with self.assertRaisesRegex(ContractError, "ASSET_QUERY_CHAIN_GAP"):
            consume_complete_table(service, "constraints_for_table", "CHILD", page_size=100)


if __name__ == "__main__":
    unittest.main()
