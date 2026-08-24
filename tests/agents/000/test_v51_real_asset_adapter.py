from __future__ import annotations

import hashlib
from importlib import import_module
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from east_v5.governance import ContractError

ROOT = Path(__file__).resolve().parents[3]
Adapter = import_module("east_v5_1.agents.000").RealConstraintAssetAdapter
TIME = "2026-08-24T00:00:00+00:00"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V51RealAssetAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / "runtime"; self.runtime.mkdir()
        self.ca, self.qa, self.registry, self.code, self.db = (self.runtime / name for name in ("ca.sqlite", "qa.jsonl", "registry.jsonl", "codes.json", "formal.db"))
        connection = sqlite3.connect(self.ca); connection.execute("CREATE TABLE field_master (endpoint TEXT, table_code TEXT)"); connection.execute("INSERT INTO field_master VALUES ('T1.F1', 'T1')"); connection.commit(); connection.close()
        for path in (self.qa, self.registry, self.code, self.db): path.write_text("{}\n", encoding="utf-8")
        items = [("qa", self.qa), ("issue_registry", self.registry), ("ca_v030_sqlite", self.ca), ("code_table", self.code), ("formal_db", self.db)]
        self.lock = self.runtime / "lock.json"; self.lock.write_text(json.dumps({"schema_version": "v5_1.runtime-input-lock/v1", "drift_error": "V51_RUNTIME_INPUT_DRIFT", "inputs": [{"id": role, "role": role, "locator": str(path), "sha256": digest(path)} for role, path in items]}), encoding="utf-8")
        self.adapter = Adapter(ROOT, self.runtime)

    def build(self, **kwargs):
        return self.adapter.build_constraint_asset_package(lock_path=self.lock, table_code="T1", field_refs=["T1.F1"], run_id="run-v51", qa_id="opaque-1", trace_id="trace-v51", created_at=TIME, **kwargs)

    def test_real_schema_is_normalized_stably(self) -> None:
        first, second = self.build(), self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["payload"]["matched_records"][0]["data"], {"table_id": "T1", "field_id": "F1", "field_ref": "T1.F1"})

    def test_drift_missing_column_and_nonunique_mapping_fail_closed(self) -> None:
        data = json.loads(self.lock.read_text()); data["inputs"][0]["sha256"] = "0" * 64; self.lock.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "V51_RUNTIME_INPUT_DRIFT"):
            self.build()
        # Restore lock, then demonstrate an unmapped endpoint is rejected rather than guessed.
        self.setUp()
        with self.assertRaisesRegex(ContractError, "V51_FIELD_MAPPING_NOT_UNIQUE"):
            self.adapter.build_constraint_asset_package(lock_path=self.lock, table_code="T1", field_refs=["T1.F9"], run_id="run-v51", qa_id="opaque-1", trace_id="trace-v51", created_at=TIME)

