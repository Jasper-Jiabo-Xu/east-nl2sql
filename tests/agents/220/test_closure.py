from __future__ import annotations
import importlib, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/"src"))
build_closure=importlib.import_module("east_v5.agents.220.closure").build_closure
from east_v5.governance import ContractError

PROFILE={"schema_version":"v5.foundation-profile/v1","base_database_version":"v1","target_classes":["CUSTOMER"],"target_counts":{},"constraint_asset_version":"CA-V0.3.0","graph_version":"TRG-V1.0.0"}
class ClosureTests(unittest.TestCase):
 def test_foundation_expands_readonly_assets(self):
  r=build_closure(PROFILE,[{"asset_version":"CA-V0.3.0","matched_records":[{"record_type":"single_field","data":{"table_id":"ACCOUNT","field_id":"ID"}}]}])
  self.assertEqual(r["tables"],["ACCOUNT","CUSTOMER"]); self.assertEqual(r["fields"],["ACCOUNT.ID"])
 def test_event_owned_rejected(self):
  with self.assertRaisesRegex(ContractError,"FOUNDATION_EVENT_OWNED_REJECTED"): build_closure({**PROFILE,"target_classes":["EVENT_OWNED:TX"]},[])
 def test_version_drift_rejected(self):
  with self.assertRaisesRegex(ContractError,"ASSET_VERSION_DRIFT"): build_closure(PROFILE,[{"asset_version":"x","matched_records":[]}])
