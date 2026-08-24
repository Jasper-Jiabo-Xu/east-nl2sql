"""Read CA-V0.3.0 through its real schema, without changing frozen V5 000."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from east_v5.artifacts import content_hash, validate_envelope
from east_v5.governance import ContractError, load_json


def _fail(code: str) -> None:
    raise ContractError(code)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RealConstraintAssetAdapter:
    """Fail-closed V5.1 bridge from CA-V0.3.0 to canonical 000 payloads."""

    def __init__(self, repo_root: Path, runtime_root: Path):
        self.repo_root, self.runtime_root = repo_root.resolve(), runtime_root.resolve()

    def _lock(self, lock_path: Path) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(lock_path.read_text(encoding="utf-8"))
            Draft202012Validator(load_json(self.repo_root / "contracts/v5_1/runtime-input-lock.schema.json")).validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ContractError("V51_RUNTIME_LOCK_INVALID") from exc
        records = {item["role"]: item for item in raw["inputs"]}
        if set(records) != {"qa", "issue_registry", "ca_v030_sqlite", "code_table", "formal_db"}:
            _fail("V51_RUNTIME_LOCK_ROLE_INVALID")
        for record in records.values():
            path = Path(record["locator"])
            if not path.is_absolute() or self.runtime_root not in path.resolve(strict=False).parents or not path.is_file():
                _fail("V51_RUNTIME_LOCK_LOCATOR_INVALID")
            if _sha(path) != record["sha256"]:
                _fail("V51_RUNTIME_INPUT_DRIFT")
        return records

    def build_constraint_asset_package(self, *, lock_path: Path, table_code: str, field_refs: list[str], run_id: str, qa_id: str, trace_id: str, attempt_no: int = 1, created_at: str) -> dict[str, Any]:
        if not isinstance(table_code, str) or not table_code or not isinstance(field_refs, list) or not field_refs or len(set(field_refs)) != len(field_refs):
            _fail("V51_ASSET_REQUEST_INVALID")
        if attempt_no not in (1, 2, 3):
            _fail("ATTEMPT_OUT_OF_RANGE")
        lock = self._lock(lock_path)
        sqlite_path = Path(lock["ca_v030_sqlite"]["locator"])
        try:
            connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro&immutable=1", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(field_master)")}
            if not {"table_code", "endpoint"}.issubset(columns):
                _fail("V51_CA_SCHEMA_MISSING_COLUMN")
            rows = []
            for field_ref in field_refs:
                if not isinstance(field_ref, str) or field_ref.count(".") != 1:
                    _fail("V51_FIELD_REF_INVALID")
                prefix, field_id = field_ref.split(".", 1)
                if prefix != table_code or not field_id:
                    _fail("V51_FIELD_TABLE_MISMATCH")
                matched = connection.execute("SELECT endpoint FROM field_master WHERE table_code = ? AND endpoint = ?", (table_code, field_ref)).fetchall()
                if len(matched) != 1:
                    _fail("V51_FIELD_MAPPING_NOT_UNIQUE")
                rows.append({"record_type": "single_field", "data": {"table_id": table_code, "field_id": field_id, "field_ref": matched[0]["endpoint"]}, "source_refs": [{"source_type": "constraint_asset", "source_id": "CA-V0.3.0"}], "hierarchy_refs": []})
        except sqlite3.Error as exc:
            raise ContractError("V51_CA_QUERY_FAILED") from exc
        finally:
            if "connection" in locals(): connection.close()
        payload = {"request_id": f"v51-000:{run_id}:{qa_id}", "asset_version": "CA-V0.3.0", "executed_queries": [{"sql": "CA-V0.3.0 field_master exact endpoint lookup", "safety_check_result": "pass"}], "matched_records": rows, "constraint_summary": {"total_matched": len(rows), "asset_types_covered": ["single_field"]}, "unmatched_items": [], "query_trace": [{"round": 1, "sql": "CA-V0.3.0 field_master exact endpoint lookup", "elapsed_ms": 0, "row_count": len(rows), "exception": None}]}
        try:
            Draft202012Validator(load_json(self.repo_root / "contracts/packages/constraint-asset-package.schema.json")).validate(payload)
        except ValidationError as exc:
            raise ContractError("SCHEMA_VALIDATION_FAILED:CONSTRAINT_ASSET_PACKAGE") from exc
        envelope = {"artifact_id": f"v51-000-{run_id}", "artifact_type": "constraint_asset_package", "run_id": run_id, "qa_id": qa_id, "version": 1, "schema_version": "COMMON-ENVELOPE/v1", "content_hash": "0" * 64, "supersedes_ref": None, "attempt_no": attempt_no, "producer_id": "000", "parent_artifact_refs": [], "input_hashes": [], "status": "candidate", "mode": "question_sql", "created_at": created_at, "trace_id": trace_id, "storage_locator": None}
        envelope["content_hash"] = content_hash(envelope, payload)
        validate_envelope(self.repo_root, envelope, payload)
        return {"envelope": envelope, "payload": payload}
