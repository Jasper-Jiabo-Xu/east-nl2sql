"""242/260 consumers may only accept a receipt issued by ConstraintAssetService."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.constraint_assets import verify_query_receipt
from east_v5.governance import ContractError
from constraint_assets.test_service import ConstraintAssetServiceTests


class ConstraintAssetReceiptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receipt = json.loads((ROOT / "fixtures/constraint_assets/sanitized-query-receipt.json").read_text(encoding="utf-8"))

    def test_242_260_consumer_accepts_only_intact_service_receipt(self) -> None:
        accepted = verify_query_receipt(
            self.receipt,
            query_method="constraints_for_table",
            table_code="SANITIZED_TABLE",
            source_entry=self.receipt,
        )
        self.assertTrue(accepted["complete"])

    def test_242_260_consumer_accepts_real_service_pagination_only_after_closure(self) -> None:
        harness = ConstraintAssetServiceTests(methodName="runTest")
        harness.setUp()
        try:
            service = harness.service()
            first = service.constraints_for_table("CHILD", limit=100)
            with self.assertRaisesRegex(ContractError, "ASSET_QUERY_RECEIPT_INVALID"):
                verify_query_receipt({**first, "complete": True}, query_method="constraints_for_table", table_code="CHILD", source_entry=service.ca)
            final = service.constraints_for_table("CHILD", limit=100, cursor=first["next_cursor"])
            self.assertTrue(verify_query_receipt(final, query_method="constraints_for_table", table_code="CHILD", source_entry=service.ca)["complete"])
        finally:
            harness.tearDown()

    def test_242_260_consumer_rejects_adapter_claims_and_identity_drift(self) -> None:
        for key, value in (("total", 999), ("complete", False), ("artifact_id", "adapter-claimed-source"), ("records", [])):
            forged = copy.deepcopy(self.receipt)
            forged[key] = value
            with self.assertRaisesRegex(ContractError, "ASSET_QUERY_RECEIPT"):
                verify_query_receipt(forged, query_method="constraints_for_table", table_code="SANITIZED_TABLE", source_entry=self.receipt)


if __name__ == "__main__":
    unittest.main()
