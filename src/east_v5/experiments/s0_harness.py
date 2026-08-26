from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASELINE_IDS = ("DeepEye-SQL", "DataGallery-Text2SQL", "JoyDataAgent-SQL", "Databao Agent", "ReFoRCE", "AutoLink")
HARD_RULES_HASH = "d00fa6028ff729f2776a7e779db2f530bcba8fba892765c8aa08c6e0aee6b463"
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
ALLOWED_MODEL_IDS = {"synthetic-local-v1"}
FORBIDDEN_KEYS = {
    "gold_sql",
    "answer_contract",
    "sql_explanation",
    "skeleton",
    "business_events",
    "east_private_kb",
    "oracle_table_hint",
    "oracle_field_hint",
}
FORBIDDEN_VALUE_RE = re.compile(r"\b(gold\s+sql|answer\s+contract|sql\s+explanation|business\s+events|east\s+private\s+kb)\b", re.I)
READ_ONLY_SQL_RE = re.compile(r"^\s*(select|with)\b", re.I)


class HarnessError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError("INVALID_JSON", str(exc)) from exc
    if not isinstance(value, dict):
        raise HarnessError("INVALID_JSON_OBJECT")
    return value


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def _scan_for_leakage(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS:
                raise HarnessError("S0_LEAKAGE_FIELD", f"{path}.{key}")
            _scan_for_leakage(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_leakage(child, f"{path}[{index}]")
    elif isinstance(value, str) and FORBIDDEN_VALUE_RE.search(value):
        raise HarnessError("S0_LEAKAGE_VALUE", path)


def validate_dataset_manifest(manifest: dict[str, Any]) -> None:
    _scan_for_leakage(manifest)
    if manifest.get("s0_boundary") != "qa_id_clear_question_public_schema":
        raise HarnessError("INVALID_S0_BOUNDARY")
    questions = manifest.get("questions")
    if not isinstance(questions, list) or not questions:
        raise HarnessError("EMPTY_DATASET")
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            raise HarnessError("INVALID_QUESTION")
        if set(question) != {"qa_id", "clear_question", "public_semantic_schema"}:
            raise HarnessError("INVALID_S0_QUESTION_FIELDS")
        qa_id = question["qa_id"]
        if not isinstance(qa_id, str) or not qa_id or qa_id in seen:
            raise HarnessError("INVALID_QA_ID")
        seen.add(qa_id)
        if not isinstance(question["clear_question"], str) or not question["clear_question"]:
            raise HarnessError("INVALID_CLEAR_QUESTION")
        if not isinstance(question["public_semantic_schema"], dict):
            raise HarnessError("INVALID_PUBLIC_SCHEMA")


def validate_experiment_contract(contract: dict[str, Any]) -> None:
    if contract.get("stage") != "stage1b_s0_synthetic_smoke":
        raise HarnessError("INVALID_EXPERIMENT_STAGE")
    if contract.get("hard_rules_hash") != HARD_RULES_HASH:
        raise HarnessError("HARD_RULES_HASH_MISMATCH")
    if contract.get("concurrency") != 6:
        raise HarnessError("INVALID_CONCURRENCY")
    if contract.get("retry_policy") != {"retryable_failure_codes": ["TRANSPORT_ERROR"], "max_attempts": 3}:
        raise HarnessError("INVALID_RETRY_POLICY")
    _scan_for_leakage(contract)


def validate_run_manifest(manifest: dict[str, Any]) -> None:
    baselines = manifest.get("baselines")
    if not isinstance(baselines, list) or [item.get("baseline_id") for item in baselines if isinstance(item, dict)] != list(BASELINE_IDS):
        raise HarnessError("INVALID_BASELINE_SET")
    cache_namespaces: set[str] = set()
    for baseline in baselines:
        if not isinstance(baseline, dict):
            raise HarnessError("INVALID_BASELINE")
        model = baseline.get("model_contract")
        if not isinstance(model, dict):
            raise HarnessError("MISSING_MODEL_CONTRACT")
        if model.get("model_id") not in ALLOWED_MODEL_IDS:
            raise HarnessError("UNKNOWN_MODEL_ID")
        if not model.get("base_url"):
            raise HarnessError("MISSING_ENDPOINT")
        if baseline.get("commit") in ("", None, "unknown"):
            raise HarnessError("MISSING_BASELINE_COMMIT")
        command = baseline.get("command")
        if not isinstance(command, list) or not command:
            raise HarnessError("INVALID_BASELINE_COMMAND")
        namespace = baseline.get("cache_namespace")
        if not isinstance(namespace, str) or not namespace:
            raise HarnessError("MISSING_CACHE_NAMESPACE")
        if namespace in cache_namespaces:
            raise HarnessError("CACHE_NAMESPACE_COLLISION")
        cache_namespaces.add(namespace)
    _scan_for_leakage(manifest)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def consume_s0_manifest(dataset_manifest_path: Path, evidence_receipt_path: Path | None = None) -> dict[str, Any]:
    payload_bytes = dataset_manifest_path.read_bytes()
    manifest = json.loads(payload_bytes)
    if not isinstance(manifest, dict):
        raise HarnessError("INVALID_JSON_OBJECT")
    validate_dataset_manifest(manifest)
    receipt: dict[str, Any] = {}
    if evidence_receipt_path is not None:
        receipt = load_json(evidence_receipt_path)
        expected_hash = receipt.get("s0_dataset_manifest_sha256")
        expected_bytes = receipt.get("s0_bytes")
        expected_ids = [anchor.get("qa_id") for anchor in receipt.get("anchors", []) if isinstance(anchor, dict)]
        if expected_hash != hashlib.sha256(payload_bytes).hexdigest():
            raise HarnessError("S0_HASH_MISMATCH")
        if expected_bytes != len(payload_bytes):
            raise HarnessError("S0_BYTES_MISMATCH")
        observed_ids = [question["qa_id"] for question in manifest.get("questions", [])]
        if expected_ids and observed_ids != expected_ids:
            raise HarnessError("S0_QA_ORDER_MISMATCH")
    return {
        "status": "ok",
        "mode": "contract_consumption_only",
        "question_count": len(manifest["questions"]),
        "qa_ids": [question["qa_id"] for question in manifest["questions"]],
        "s0_dataset_manifest_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "s0_bytes": len(payload_bytes),
        "selection_manifest_sha256": manifest.get("selection_manifest_sha256"),
        "receipt_checked": bool(receipt),
    }


@dataclass(frozen=True)
class RunPaths:
    root: Path
    baseline_dir: Path
    raw_jsonl: Path
    predictions_jsonl: Path
    trace_json: Path


def _paths(output_dir: Path, baseline_id: str, attempt: int) -> RunPaths:
    safe_id = baseline_id.lower().replace(" ", "_").replace("-", "_")
    baseline_dir = output_dir / "runs" / safe_id / f"attempt-{attempt:02d}"
    return RunPaths(output_dir, baseline_dir, baseline_dir / "raw_predictions.jsonl", baseline_dir / "predictions.jsonl", baseline_dir / "trace.json")


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise HarnessError("INVALID_BASELINE_OUTPUT")
        rows.append(value)
    return rows


def _prediction_failure(baseline: dict[str, Any], attempt: int, code: str, trace_ref: str) -> dict[str, Any]:
    return {
        "qa_id": "__run__",
        "baseline_id": baseline["baseline_id"],
        "baseline_commit": baseline["commit"],
        "backbone_model_id": baseline["model_contract"]["model_id"],
        "attempt": attempt,
        "prediction_sql_raw": "",
        "prediction_sql_normalized": "",
        "raw_sql_hash": "",
        "normalized_sql_hash": "",
        "execution_status": "failed",
        "failure_code": code,
        "trace_ref": trace_ref,
        "token_calls": 0,
        "token_total": 0,
    }


def _run_one(baseline: dict[str, Any], dataset_path: Path, dataset: dict[str, Any], output_dir: Path, max_attempts: int) -> list[dict[str, Any]]:
    baseline_id = baseline["baseline_id"]
    last_failure = "TRANSPORT_ERROR"
    for attempt in range(1, max_attempts + 1):
        paths = _paths(output_dir, baseline_id, attempt)
        if paths.predictions_jsonl.exists() or paths.raw_jsonl.exists():
            raise HarnessError("DUPLICATE_ATTEMPT", str(paths.baseline_dir))
        paths.baseline_dir.mkdir(parents=True, exist_ok=False)
        command = [
            token.format(
                python=sys.executable,
                dataset=str(dataset_path),
                output_jsonl=str(paths.raw_jsonl),
                baseline_id=baseline_id,
            )
            for token in baseline["command"]
        ]
        started = time.time()
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(SRC) if not env.get("PYTHONPATH") else str(SRC) + os.pathsep + env["PYTHONPATH"]
            completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=int(baseline.get("timeout_seconds", 20)), check=False)
        except subprocess.TimeoutExpired as exc:
            _atomic_write_json(paths.trace_json, {"failure_code": "TIMEOUT", "cache_namespace": baseline.get("cache_namespace"), "stdout": exc.stdout or "", "stderr": exc.stderr or ""})
            return [_prediction_failure(baseline, attempt, "TIMEOUT", str(paths.trace_json.relative_to(output_dir)))]
        elapsed_ms = int((time.time() - started) * 1000)
        _atomic_write_json(paths.trace_json, {"command": command, "returncode": completed.returncode, "elapsed_ms": elapsed_ms, "cache_namespace": baseline.get("cache_namespace"), "stdout": completed.stdout, "stderr": completed.stderr})
        if completed.returncode != 0:
            last_failure = "TRANSPORT_ERROR"
            if attempt < max_attempts:
                continue
            return [_prediction_failure(baseline, attempt, last_failure, str(paths.trace_json.relative_to(output_dir)))]

        raw_rows = _read_jsonl(paths.raw_jsonl)
        expected_ids = [question["qa_id"] for question in dataset["questions"]]
        if [row.get("qa_id") for row in raw_rows] != expected_ids:
            return [_prediction_failure(baseline, attempt, "INVALID_BASELINE_OUTPUT", str(paths.trace_json.relative_to(output_dir)))]
        predictions: list[dict[str, Any]] = []
        for row in raw_rows:
            sql = row.get("sql")
            if not isinstance(sql, str) or not READ_ONLY_SQL_RE.match(sql):
                return [_prediction_failure(baseline, attempt, "ILLEGAL_SQL_OUTPUT", str(paths.trace_json.relative_to(output_dir)))]
            token_total = int(row.get("token_total", 0))
            if token_total > int(baseline["model_contract"].get("max_tokens", 0)):
                return [_prediction_failure(baseline, attempt, "BUDGET_EXCEEDED", str(paths.trace_json.relative_to(output_dir)))]
            normalized = normalize_sql(sql)
            predictions.append(
                {
                    "qa_id": row["qa_id"],
                    "baseline_id": baseline_id,
                    "baseline_commit": baseline["commit"],
                    "backbone_model_id": baseline["model_contract"]["model_id"],
                    "attempt": attempt,
                    "prediction_sql_raw": sql,
                    "prediction_sql_normalized": normalized,
                    "raw_sql_hash": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                    "normalized_sql_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    "execution_status": "ok",
                    "failure_code": "",
                    "trace_ref": str(paths.trace_json.relative_to(output_dir)),
                    "token_calls": int(row.get("token_calls", 0)),
                    "token_total": token_total,
                }
            )
        _atomic_write_jsonl(paths.predictions_jsonl, predictions)
        return predictions
    return [_prediction_failure(baseline, max_attempts, last_failure, "")]


def run_harness(experiment_contract_path: Path, dataset_manifest_path: Path, baseline_run_manifest_path: Path, output_dir: Path, mode: str = "concurrent") -> dict[str, Any]:
    contract = load_json(experiment_contract_path)
    dataset = load_json(dataset_manifest_path)
    manifest = load_json(baseline_run_manifest_path)
    validate_experiment_contract(contract)
    validate_dataset_manifest(dataset)
    validate_run_manifest(manifest)

    output_dir.mkdir(parents=True, exist_ok=True)
    baselines = manifest["baselines"]
    max_attempts = contract["retry_policy"]["max_attempts"]
    if mode == "serial":
        results = [_run_one(baseline, dataset_manifest_path, dataset, output_dir, max_attempts) for baseline in baselines]
    elif mode == "concurrent":
        with concurrent.futures.ThreadPoolExecutor(max_workers=contract["concurrency"]) as executor:
            futures = [executor.submit(_run_one, baseline, dataset_manifest_path, dataset, output_dir, max_attempts) for baseline in baselines]
            results = [future.result() for future in futures]
    else:
        raise HarnessError("INVALID_RUN_MODE")

    predictions = [row for result in results for row in result]
    predictions.sort(key=lambda row: (row["baseline_id"], row["qa_id"], row["attempt"]))
    predictions_path = output_dir / "predictions.jsonl"
    _atomic_write_jsonl(predictions_path, predictions)
    trace_refs_by_baseline: dict[str, set[str]] = {}
    for row in predictions:
        trace_refs_by_baseline.setdefault(row["baseline_id"], set()).add(row["trace_ref"])
    summary = {
        "callback_condition": "SIX_BASELINE_COMMON_HARNESS_CONCURRENCY6_STATIC_SMOKE_PASS",
        "mode": mode,
        "baseline_count": len(baselines),
        "question_count": len(dataset["questions"]),
        "candidate_count": len([row for row in predictions if row["execution_status"] == "ok"]),
        "failure_count": len([row for row in predictions if row["execution_status"] != "ok"]),
        "collection_hash": stable_hash(predictions),
        "predictions_jsonl": str(predictions_path),
        "trace_refs_by_baseline_isolated": all(len(refs) >= 1 for refs in trace_refs_by_baseline.values()) and len(trace_refs_by_baseline) == len(baselines),
        "cache_namespaces_unique": len({baseline["cache_namespace"] for baseline in baselines}) == len(baselines),
    }
    _atomic_write_json(output_dir / "eval_summary.json", summary)
    return summary
