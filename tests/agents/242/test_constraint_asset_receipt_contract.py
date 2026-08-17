"""242/260 consumers may only accept a receipt issued by ConstraintAssetService."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.constraint_assets import consume_complete_table, validate_query_receipt_contract
from east_v5.governance import ContractError
from constraint_assets.test_service import ConstraintAssetServiceTests


class ConstraintAssetReceiptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receipt = json.loads((ROOT / "fixtures/constraint_assets/sanitized-query-receipt.json").read_text(encoding="utf-8"))

    def test_sanitized_fixture_is_schema_contract_only_not_source_proof(self) -> None:
        accepted = validate_query_receipt_contract(
            self.receipt,
            query_method="constraints_for_table",
            table_code="SANITIZED_TABLE",
        )
        self.assertTrue(accepted["complete"])

    def test_242_260_consumer_requires_real_service_and_closed_page_chain(self) -> None:
        harness = ConstraintAssetServiceTests(methodName="runTest")
        harness.setUp()
        try:
            service = harness.service()
            first = service.constraints_for_table("CHILD", limit=100)
            with self.assertRaisesRegex(ContractError, "ASSET_QUERY_RECEIPT_INVALID"):
                validate_query_receipt_contract({**first, "complete": True}, query_method="constraints_for_table", table_code="CHILD")
            completed = consume_complete_table(service, "constraints_for_table", "CHILD", page_size=100)
            self.assertEqual((completed["total"], completed["returned_count"]), (122, 122))
            with self.assertRaisesRegex(ContractError, "ASSET_QUERY_SERVICE_REQUIRED"):
                consume_complete_table(self.receipt, "constraints_for_table", "CHILD")
        finally:
            harness.tearDown()

    def test_fixture_contract_rejects_plain_drift_but_never_claims_origin(self) -> None:
        for key, value in (("total", 999), ("complete", False), ("artifact_id", "adapter-claimed-source"), ("records", [])):
            forged = copy.deepcopy(self.receipt)
            forged[key] = value
            with self.assertRaisesRegex(ContractError, "ASSET_QUERY_RECEIPT"):
                validate_query_receipt_contract(forged, query_method="constraints_for_table", table_code="SANITIZED_TABLE")


if __name__ == "__main__":
    unittest.main()
