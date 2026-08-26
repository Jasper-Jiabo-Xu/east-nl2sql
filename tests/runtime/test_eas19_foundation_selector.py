"""Production-contract tests for EAS-19's Foundation parent selector."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from east_v5.agents.foundation_contract import validate_context
from east_v5.artifacts import content_hash
from east_v5.governance import sha256
from east_v5.runtime.eas19_foundation_selector import (
    Eas19FoundationParentSnapshotSelector,
    Eas19FoundationSelectorError,
)


ROOT = Path(__file__).resolve().parents[2]
build_closure = importlib.import_module("east_v5.agents.220.closure").build_closure


class Eas19FoundationParentSnapshotSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.database = self.base / "baseline.sqlite"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            'CREATE TABLE ORG_PARENT (ORG_ID TEXT PRIMARY KEY, ORG_NAME TEXT NOT NULL);'
            "INSERT INTO ORG_PARENT VALUES ('ORG-02', '乙机构');"
            "INSERT INTO ORG_PARENT VALUES ('ORG-01', '甲机构');"
        )
        connection.close()
        self.task = json.loads((ROOT / "fixtures" / "artifacts" / "foundation-task-package-valid.json").read_text(encoding="utf-8"))
        self.closure = build_closure(self.task, [])
        self._set_reference("ORG_PARENT.ORG_ID", "FIXTURE_CUSTOMER.PARENT_ID")
        self.mapping = {
            "schema_version": "foundation-hierarchy-endpoint-mapping/v1",
            "intent_content_hash": "a" * 64,
            "field_mapping": {"org_id": "ORG_PARENT.ORG_ID"},
        }
        self.mapping["content_hash"] = sha256(self.mapping)
        self.universe = {"schema_version": "v5.constraint-universe/v2", "content_sha256": "b" * 64}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _set_reference(self, source: str, target: str) -> None:
        self.closure["payload"]["references"] = [{"type": "cross_table", "data": {"from": source, "to": target}}]
        self.closure["envelope"]["content_hash"] = content_hash(self.closure["envelope"], self.closure["payload"])

    def _selector(self) -> Eas19FoundationParentSnapshotSelector:
        return Eas19FoundationParentSnapshotSelector(
            self.database, hashlib.sha256(self.database.read_bytes()).hexdigest(), self.database.stat().st_size,
        )

    def test_enumerates_every_distinct_parent_tuple_without_selecting_one(self) -> None:
        selection = self._selector().select(self.task, self.closure, hierarchy_mapping=self.mapping, resolver_universe=self.universe)
        snapshot = selection.database_snapshot
        records = snapshot["payload"]["object_state_records"]
        self.assertEqual([item["data"]["ORG_ID"] for item in records], ["ORG-01", "ORG-02"])
        self.assertEqual(len(records), 2)
        self.assertTrue(all(len(item["record_keys"]["primary_key"]) == 64 for item in records))
        self.assertEqual(snapshot["payload"]["executed_queries"], ['SELECT DISTINCT "ORG_ID" FROM "ORG_PARENT"'])
        self.assertEqual(selection.generation_context["payload"]["parent_record_refs"], [
            {"table_id": "ORG_PARENT", "record_key": item["record_keys"]["primary_key"]} for item in records
        ])
        validate_context(selection.generation_context, self.task, self.closure, snapshot)

    def test_rejects_reverse_reference_direction_instead_of_guessing_parent(self) -> None:
        self._set_reference("FIXTURE_CUSTOMER.PARENT_ID", "ORG_PARENT.ORG_ID")
        with self.assertRaisesRegex(Eas19FoundationSelectorError, "EAS19_FOUNDATION_SELECTOR_CLOSURE_DIRECTION_INVALID"):
            self._selector().select(self.task, self.closure, hierarchy_mapping=self.mapping, resolver_universe=self.universe)

    def test_rejects_empty_feasible_parent_set(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute("DELETE FROM ORG_PARENT")
        connection.commit(); connection.close()
        with self.assertRaisesRegex(Eas19FoundationSelectorError, "EAS19_FOUNDATION_SELECTOR_PARENT_SET_EMPTY"):
            self._selector().select(
                self.task, self.closure, hierarchy_mapping=self.mapping, resolver_universe=self.universe,
            )


if __name__ == "__main__":
    unittest.main()
