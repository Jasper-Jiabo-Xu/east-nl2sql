from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "experiments"


class ExperimentContractSchemaTests(unittest.TestCase):
    def _load(self, name: str) -> dict[str, object]:
        return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))

    def test_route_matrix_and_databao_contract_are_closed(self) -> None:
        matrix_schema = self._load("route_compatibility_matrix.schema.json")
        launcher_schema = self._load("databao_launcher_contract.schema.json")
        matrix = self._load("route_compatibility_matrix.json")
        launcher = self._load("databao_launcher_contract.json")
        jsonschema.Draft202012Validator(matrix_schema).validate(matrix)
        jsonschema.Draft202012Validator(launcher_schema).validate(launcher)
        invalid_matrix = copy.deepcopy(matrix)
        invalid_matrix["routes"][0]["unexpected"] = True
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(matrix_schema).validate(invalid_matrix)
        invalid_launcher = copy.deepcopy(launcher)
        invalid_launcher["provider"]["key_value"] = "not-allowed"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(launcher_schema).validate(invalid_launcher)

    def test_baseline_schema_enumerates_only_the_six_adapter_ids(self) -> None:
        schema = self._load("baseline_run_manifest.schema.json")
        adapter = schema["properties"]["baselines"]["items"]["properties"]["native_adapter_id"]
        self.assertEqual(adapter["enum"], ["deepeye_sql_native_v1", "datagallery_text2sql_native_v1", "joydataagent_sql_native_v1", "databao_agent_native_v1", "reforce_native_v1", "autolink_native_v1"])


if __name__ == "__main__":
    unittest.main()
