from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from east_v5.artifacts.registry import content_hash
from east_v5.governance import ContractError
from east_v5.validators.orm.validator import freeze_orm

ROOT = Path(__file__).resolve().parents[3]


def fixtures():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "valid-event-orm.json").read_text(encoding="utf-8"))
    return fixture["restricted_orm"], fixture["operation_closure"]


class FrozenOrmTests(unittest.TestCase):
    def test_deterministic_read_only_package_and_260_stub(self):
        orm, closure = fixtures(); before = copy.deepcopy((orm, closure))
        first, second = freeze_orm(ROOT, orm, closure), freeze_orm(ROOT, orm, closure)
        self.assertEqual(first, second); self.assertEqual(before, (orm, closure))
        self.assertEqual(first["envelope"]["artifact_type"], "frozen_orm")
        self.assertEqual(first["payload"]["verdict"], "PASS")
        self.assertEqual(first["payload"]["failures"], [])  # 260 consumer stub: only frozen, hashed PASS packages are acceptable.
        self.assertEqual(first["payload"]["code_sha256"], orm["payload"]["code_sha256"])

    def test_rejects_hash_drift_unknown_fields_foundation_and_order(self):
        cases = []
        orm, closure = fixtures(); orm["payload"]["code_sha256"] = "0" * 64; orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"]); cases.append((orm, closure, "CODE_HASH_DRIFT"))
        orm, closure = fixtures(); orm["payload"]["unknown"] = True; cases.append((orm, closure, "SCHEMA_VALIDATION_FAILED"))
        orm, closure = fixtures(); orm["envelope"]["mode"] = "foundation"; orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"]); cases.append((orm, closure, "PACKAGE_ASSOCIATION_INVALID"))
        orm, closure = fixtures(); orm["payload"]["operation_ids"] = ["wrong"]; orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"]); cases.append((orm, closure, "OPERATION_ORDER_MISMATCH"))
        for orm, closure, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ContractError, code): freeze_orm(ROOT, orm, closure)

    def test_rejects_dynamic_effects_nonrollback_and_lineage_drift(self):
        for bad_code, code in [("def apply(context, params):\n    import os\n", "TRANSACTION_REQUIRED"), ("def apply(context, params):\n    with context.transaction() as transaction:\n        transaction.insert(\"fixture_table\", {\"id\": \"real-value\"})\n        transaction.rollback()\n", "BUSINESS_DATA_OR_PLACEHOLDER_INVALID"), ("def apply(context, params):\n    with context.transaction() as transaction:\n        transaction.insert(\"fixture_table\", {\"id\": params[\"slot_id\"]})\n", "ROLLBACK_REQUIRED")]:
            orm, closure = fixtures(); orm["payload"]["code"] = bad_code; orm["payload"]["code_sha256"] = hashlib.sha256(bad_code.encode()).hexdigest(); orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"])
            with self.subTest(code=code), self.assertRaisesRegex(ContractError, code): freeze_orm(ROOT, orm, closure)
        orm, closure = fixtures(); closure["payload"]["governance_manifest_hash"] = "0" * 64; closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"]); orm["payload"]["operation_closure_ref"] = {key: closure["envelope"][key] for key in ("artifact_id", "version", "content_hash")}; orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"])
        with self.assertRaisesRegex(ContractError, "MANIFEST_LINEAGE_DRIFT"): freeze_orm(ROOT, orm, closure)

    def test_rejects_blocked_manual_and_cross_attempt_inputs(self):
        orm, closure = fixtures(); orm["envelope"]["status"] = "blocked_manual"; orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"])
        with self.assertRaisesRegex(ContractError, "UPSTREAM_BLOCKED_MANUAL"): freeze_orm(ROOT, orm, closure)
        orm, closure = fixtures(); closure["envelope"]["attempt_no"] = 2; closure["envelope"]["content_hash"] = content_hash(closure["envelope"], closure["payload"]); orm["payload"]["operation_closure_ref"] = {key: closure["envelope"][key] for key in ("artifact_id", "version", "content_hash")}; orm["envelope"]["content_hash"] = content_hash(orm["envelope"], orm["payload"])
        with self.assertRaisesRegex(ContractError, "ATTEMPT_ISOLATION_VIOLATION"): freeze_orm(ROOT, orm, closure)


if __name__ == "__main__":
    unittest.main()
