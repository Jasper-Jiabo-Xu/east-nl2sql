from __future__ import annotations

import copy
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.architecture import scan_active_contracts, verify_architecture
from east_v5.foundation.compiler import compile_insert_batch
from east_v5.governance import ContractError, canonical_bytes, sha256


def package() -> dict[str, object]:
    return {
        "schema_version": "v5.foundation-verified-data/v1",
        "mode": "foundation",
        "base_database_version": "fixture-db-v1",
        "constraint_asset_version": "CA-V0.3.0",
        "graph_version": "TRG-V1.0.0",
        "records": [
            {"record_id": "child", "table": "CHILD_TABLE", "values": {"PARENT_ID": 1, "NAME": "x'); DROP TABLE T;--"}, "depends_on": ["parent"]},
            {"record_id": "parent", "table": "PARENT_TABLE", "values": {"ID": 1}, "depends_on": []},
        ],
    }


class ArchitectureTests(unittest.TestCase):
    def test_frozen_architecture_and_package_fanout(self) -> None:
        verified = verify_architecture(ROOT)
        self.assertEqual(verified["architecture"]["reference_versions"]["typed_reference_graph"], "TRG-V1.0.0")
        self.assertEqual(scan_active_contracts(ROOT), [])

    def test_compiler_is_deterministic_parameterized_and_topological(self) -> None:
        source = package()
        original = copy.deepcopy(source)
        first = compile_insert_batch(source, {"EVENT_TABLE"})
        second = compile_insert_batch(source, {"EVENT_TABLE"})
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(source, original)
        self.assertEqual([item["record_id"] for item in first["operations"]], ["parent", "child"])
        self.assertNotIn("DROP TABLE", first["operations"][1]["sql"])
        self.assertTrue(any(isinstance(value, str) and "DROP TABLE" in value for value in first["operations"][1]["parameters"]))
        supplied = first.pop("content_sha256")
        self.assertEqual(supplied, sha256(first))

    def test_downstream_sqlite_stub_consumes_batch_and_rolls_back_atomically(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE PARENT_TABLE (ID INTEGER PRIMARY KEY);"
            "CREATE TABLE CHILD_TABLE (PARENT_ID INTEGER, NAME TEXT NOT NULL, "
            "FOREIGN KEY(PARENT_ID) REFERENCES PARENT_TABLE(ID));"
        )
        connection.execute("PRAGMA foreign_keys=ON")
        batch = compile_insert_batch(package(), set())
        connection.execute("BEGIN IMMEDIATE")
        for operation in batch["operations"]:
            connection.execute(operation["sql"], operation["parameters"])
        connection.commit()
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM CHILD_TABLE").fetchone()[0], 1)

        failing = package()
        failing["records"].append({"record_id": "duplicate_parent", "table": "PARENT_TABLE", "values": {"ID": 1}, "depends_on": []})
        failed_batch = compile_insert_batch(failing, set())
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("BEGIN IMMEDIATE")
            try:
                for operation in failed_batch["operations"]:
                    connection.execute(operation["sql"], operation["parameters"])
            except sqlite3.IntegrityError:
                connection.rollback()
                raise
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM PARENT_TABLE").fetchone()[0], 1)

    def test_rejects_event_owned_missing_dependency_and_cycle(self) -> None:
        with self.assertRaisesRegex(ContractError, "FOUNDATION_EVENT_OWNED_REJECTED"):
            compile_insert_batch(package(), {"CHILD_TABLE"})
        missing = package()
        missing["records"][0]["depends_on"] = ["absent"]
        with self.assertRaisesRegex(ContractError, "FOUNDATION_DEPENDENCY_MISSING"):
            compile_insert_batch(missing, set())
        cycle = package()
        cycle["records"][1]["depends_on"] = ["child"]
        with self.assertRaisesRegex(ContractError, "FOUNDATION_DEPENDENCY_CYCLE"):
            compile_insert_batch(cycle, set())

    def test_rejects_unknown_field_invalid_identifier_and_non_scalar(self) -> None:
        unknown = package()
        unknown["unexpected"] = True
        with self.assertRaisesRegex(ContractError, "UNKNOWN_FIELD"):
            compile_insert_batch(unknown, set())
        identifier = package()
        identifier["records"][0]["table"] = "CHILD;DROP"
        with self.assertRaisesRegex(ContractError, "FOUNDATION_IDENTIFIER_INVALID"):
            compile_insert_batch(identifier, set())
        nonscalar = package()
        nonscalar["records"][0]["values"]["NAME"] = {"nested": "forbidden"}
        with self.assertRaisesRegex(ContractError, "FOUNDATION_VALUE_NOT_BINDABLE"):
            compile_insert_batch(nonscalar, set())


if __name__ == "__main__":
    unittest.main()
