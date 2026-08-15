from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver, ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from east_v5.artifacts import content_hash, validate_envelope
from east_v5.foundation.compiler import compile_insert_batch
from east_v5.governance import ContractError


PACKAGE_SCHEMA_PATH = ROOT / "contracts" / "packages" / "foundation-profile-package.schema.json"


def package_schema_validator() -> Draft202012Validator:
    schema = json.loads(PACKAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
    common = json.loads((ROOT / "contracts" / "common" / "common-envelope.schema.json").read_text(encoding="utf-8"))
    runtime = json.loads((ROOT / "contracts" / "v5-runtime-packages.schema.json").read_text(encoding="utf-8"))
    store = {schema["$id"]: schema, common["$id"]: common, runtime["$id"]: runtime}
    return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=store))


def foundation_profile_package() -> dict[str, object]:
    return json.loads((ROOT / "fixtures" / "artifacts" / "foundation-profile-valid.json").read_text(encoding="utf-8"))


def rehash(package: dict[str, object]) -> None:
    package["envelope"]["content_hash"] = content_hash(package["envelope"], package["payload"])


def validate_foundation_profile_package(package: dict[str, object]) -> None:
    # The registry supplies the stable, mutation-free envelope failures first.
    validate_envelope(ROOT, package["envelope"], package["payload"])
    try:
        package_schema_validator().validate(package)
    except ValidationError as exc:
        raise ContractError("FOUNDATION_PROFILE_SCHEMA_INVALID") from exc


def consume_220_stub(package: dict[str, object]) -> tuple[str, ...]:
    validate_foundation_profile_package(package)
    return tuple(package["payload"]["target_classes"])


def consume_241_stub(package: dict[str, object]) -> dict[str, int]:
    validate_foundation_profile_package(package)
    return dict(package["payload"]["target_counts"])


class FoundationProfilePackageTests(unittest.TestCase):
    def test_catalog_registers_exact_foundation_route(self) -> None:
        catalog = json.loads((ROOT / "config" / "v5-package-catalog.json").read_text(encoding="utf-8"))
        catalog_schema = json.loads((ROOT / "contracts" / "v5-package-catalog.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(catalog_schema).validate(catalog)
        entries = [item for item in catalog["packages"] if item["id"] == "foundation_profile"]
        self.assertEqual(entries, [{"id": "foundation_profile", "producer": "210", "consumers": ["220", "241"], "modes": ["foundation"], "payload_schema": "v5.foundation-profile/v1", "package_schema": "contracts/packages/foundation-profile-package.schema.json"}])

    def test_valid_fixture_passes_common_envelope_and_payload_schema(self) -> None:
        package = foundation_profile_package()
        validate_foundation_profile_package(package)
        self.assertEqual(package["envelope"]["content_hash"], content_hash(package["envelope"], package["payload"]))

    def test_220_and_241_downstream_stubs_consume_identical_package_without_mutation(self) -> None:
        package = foundation_profile_package()
        before = copy.deepcopy(package)
        self.assertEqual(consume_220_stub(package), ("FIXTURE_CUSTOMER",))
        self.assertEqual(consume_241_stub(package), {"FIXTURE_CUSTOMER": 2})
        self.assertEqual(package, before)

    def test_rejects_unregistered_legacy_type_before_write(self) -> None:
        package = foundation_profile_package()
        package["envelope"]["artifact_type"] = "foundation_task"
        rehash(package)
        with self.assertRaisesRegex(ContractError, "ARTIFACT_TYPE_INVALID"):
            validate_foundation_profile_package(package)

    def test_rejects_schema_and_route_drift(self) -> None:
        cases: list[tuple[str, callable, str]] = [
            ("missing_field", lambda p: p["payload"].pop("target_counts"), "FOUNDATION_PROFILE_SCHEMA_INVALID"),
            ("unknown_field", lambda p: p["payload"].update({"trigger": "legacy"}), "FOUNDATION_PROFILE_SCHEMA_INVALID"),
            ("producer", lambda p: p["envelope"].update({"producer_id": "010"}), "FOUNDATION_PROFILE_SCHEMA_INVALID"),
            ("mode", lambda p: p["envelope"].update({"mode": "event_data", "qa_id": "QA-fixture"}), "FOUNDATION_PROFILE_SCHEMA_INVALID"),
            ("ca_version", lambda p: p["payload"].update({"constraint_asset_version": "CA-V0.3.1"}), "FOUNDATION_PROFILE_SCHEMA_INVALID"),
            ("trg_version", lambda p: p["payload"].update({"graph_version": "TRG-V1.0.1"}), "FOUNDATION_PROFILE_SCHEMA_INVALID"),
        ]
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                package = foundation_profile_package()
                mutate(package)
                rehash(package)
                with self.assertRaisesRegex(ContractError, expected):
                    validate_foundation_profile_package(package)

    def test_rejects_hash_and_input_order_drift_before_downstream_consumption(self) -> None:
        hash_drift = foundation_profile_package()
        hash_drift["payload"]["target_counts"]["FIXTURE_CUSTOMER"] = 3
        with self.assertRaisesRegex(ContractError, "CONTENT_HASH_DRIFT"):
            consume_220_stub(hash_drift)

        input_order_drift = foundation_profile_package()
        input_order_drift["envelope"]["input_hashes"].reverse()
        rehash(input_order_drift)
        with self.assertRaisesRegex(ContractError, "INPUT_HASH_ORDER_INVALID"):
            consume_241_stub(input_order_drift)

    def test_event_owned_write_is_rejected(self) -> None:
        verified_data = {
            "schema_version": "v5.foundation-verified-data/v1",
            "mode": "foundation",
            "base_database_version": "fixture-db-v1",
            "constraint_asset_version": "CA-V0.3.0",
            "graph_version": "TRG-V1.0.0",
            "records": [{"record_id": "event_owned", "table": "EVENT_OWNED", "values": {"ID": 1}, "depends_on": []}],
        }
        with self.assertRaisesRegex(ContractError, "FOUNDATION_EVENT_OWNED_REJECTED"):
            compile_insert_batch(verified_data, {"EVENT_OWNED"})


if __name__ == "__main__":
    unittest.main()
