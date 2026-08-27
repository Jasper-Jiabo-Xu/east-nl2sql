from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASELINE_IDS = ("DeepEye-SQL", "DataGallery-Text2SQL", "JoyDataAgent-SQL", "Databao Agent", "ReFoRCE", "AutoLink")
HARD_RULES_HASH = "d00fa6028ff729f2776a7e779db2f530bcba8fba892765c8aa08c6e0aee6b463"
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
DEEPSEEK_CONTRACTS = {
    "deepseek-v4-flash": {"endpoint_kind": "openai_chat_completions", "reasoning_enabled": False, "base_url": "https://api.deepseek.com"},
}
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
ROUTE_MATRIX_PATH = ROOT / "contracts" / "experiments" / "route_compatibility_matrix.json"
ROUTE_MATRIX_KEYS = {"matrix_id", "s0_boundary", "routes"}
ROUTE_KEYS = {"baseline_id", "provider_compatibility", "s0_method_compatibility", "overall_runnable", "reason"}


class HarnessError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


# Imported after HarnessError: native adapters use the same stable failure type.
from east_v5.experiments.native_adapters import ADAPTER_IDS, NativeAdapter


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


def load_route_compatibility_matrix(path: Path = ROUTE_MATRIX_PATH) -> dict[str, Any]:
    matrix = load_json(path)
    if set(matrix) != ROUTE_MATRIX_KEYS or matrix.get("matrix_id") != "EAS-125-six-route-dual-axis-v1" or matrix.get("s0_boundary") != "question_public_schema_read_only_db":
        raise HarnessError("ROUTE_MATRIX_CONTRACT_DRIFT")
    routes = matrix.get("routes")
    if not isinstance(routes, list) or [route.get("baseline_id") for route in routes if isinstance(route, dict)] != list(BASELINE_IDS):
        raise HarnessError("ROUTE_MATRIX_ROUTE_SET_DRIFT")
    for route in routes:
        expected_keys = ROUTE_KEYS | ({"launcher_contract"} if route.get("baseline_id") == "Databao Agent" else set())
        if not isinstance(route, dict) or set(route) != expected_keys:
            raise HarnessError("ROUTE_MATRIX_CONTRACT_DRIFT")
        if route["provider_compatibility"] not in {"SUPPORTED_NATIVE_CONFIG", "SUPPORTED_VIA_HASHED_PROVIDER_OVERLAY"}:
            raise HarnessError("ROUTE_MATRIX_PROVIDER_DRIFT")
        if route["s0_method_compatibility"] not in {"SUPPORTED_NATIVE_METHOD", "S0_METHOD_INCOMPATIBLE"} or not isinstance(route["overall_runnable"], bool) or not isinstance(route["reason"], str) or not route["reason"]:
            raise HarnessError("ROUTE_MATRIX_CONTRACT_DRIFT")
        should_run = route["baseline_id"] == "Databao Agent"
        if route["overall_runnable"] != should_run or (should_run and route["s0_method_compatibility"] != "SUPPORTED_NATIVE_METHOD") or (not should_run and route["s0_method_compatibility"] != "S0_METHOD_INCOMPATIBLE"):
            raise HarnessError("ROUTE_MATRIX_RUNNABILITY_DRIFT")
    return matrix


def validate_run_manifest(manifest: dict[str, Any]) -> None:
    baselines = manifest.get("baselines")
    if not isinstance(baselines, list) or [item.get("baseline_id") for item in baselines if isinstance(item, dict)] != list(BASELINE_IDS):
        raise HarnessError("INVALID_BASELINE_SET")
    cache_namespaces: set[str] = set()
    adapter_ids: set[str] = set()
    for index, baseline in enumerate(baselines):
        if not isinstance(baseline, dict):
            raise HarnessError("INVALID_BASELINE")
        model = baseline.get("model_contract")
        if not isinstance(model, dict):
            raise HarnessError("MISSING_MODEL_CONTRACT")
        required_model = {"provider", "model_id", "base_url", "endpoint_kind", "api_key_env", "reasoning_enabled", "temperature", "max_tokens", "timeout_seconds", "retry_count"}
        if set(model) != required_model or model.get("provider") != "deepseek_openai_compatible": raise HarnessError("INVALID_DEEPSEEK_PROVIDER")
        expected = DEEPSEEK_CONTRACTS.get(model.get("model_id"))
        if expected is None: raise HarnessError("UNKNOWN_MODEL_ID")
        if not isinstance(model.get("base_url"), str) or not model["base_url"]:
            raise HarnessError("MISSING_ENDPOINT")
        if any(model.get(key) != value for key, value in expected.items()): raise HarnessError("DEEPSEEK_CONTRACT_DRIFT")
        if model.get("api_key_env") != "DEEPSEEK_API_KEY": raise HarnessError("INVALID_DEEPSEEK_KEY_SOURCE")
        if isinstance(model.get("temperature"), bool) or not isinstance(model.get("temperature"), (int, float)) or isinstance(model.get("max_tokens"), bool) or not isinstance(model.get("max_tokens"), int) or model["max_tokens"] < 1:
            raise HarnessError("INVALID_MODEL_BUDGET")
        if not isinstance(model.get("timeout_seconds"), int) or model["timeout_seconds"] < 1 or model.get("retry_count") != 3:
            raise HarnessError("INVALID_MODEL_RETRY_POLICY")
        routes = baseline.get("model_routes")
        route_keys = {"stage", "model", "thinking", "reasoning_effort", "temperature", "max_tokens", "timeout_seconds", "retry_count", "call_budget", "token_budget"}
        if not isinstance(routes, list) or not routes or len({route.get("stage") for route in routes if isinstance(route, dict)}) != len(routes):
            raise HarnessError("INVALID_MODEL_ROUTES")
        if baseline["baseline_id"] == "AutoLink" and [route.get("stage") for route in routes if isinstance(route, dict)] != ["complete_schema", "sql_generation", "sql_revise", "sql_selection"]:
            raise HarnessError("AUTOLINK_MODEL_ROUTE_DRIFT")
        for route in routes:
            if not isinstance(route, dict) or set(route) != route_keys or route.get("model") != "deepseek-v4-flash" or not isinstance(route.get("thinking"), bool) or route.get("retry_count") != 3 or not all(isinstance(route.get(key), int) and route[key] > 0 for key in ("max_tokens", "timeout_seconds", "call_budget", "token_budget")) or route["token_budget"] < route["max_tokens"]:
                raise HarnessError("INVALID_MODEL_ROUTES")
            if route["thinking"]:
                if route.get("reasoning_effort") != "high" or route.get("temperature") is not None:
                    raise HarnessError("INVALID_MODEL_ROUTES")
            elif route.get("reasoning_effort") is not None or route.get("temperature") != 0:
                raise HarnessError("INVALID_MODEL_ROUTES")
        if baseline["baseline_id"] == "AutoLink":
            overlay = baseline.get("provider_overlay")
            expected_overlay = {"upstream_commit", "original_file_sha256", "patch_sha256", "patched_tree_sha256"}
            if not isinstance(overlay, dict) or set(overlay) != expected_overlay or overlay.get("upstream_commit") != baseline["commit"] or not isinstance(overlay.get("original_file_sha256"), dict) or set(overlay["original_file_sha256"]) != {"complete_schema", "sql_generation", "sql_revise", "sql_selection"} or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in [overlay["patch_sha256"], overlay["patched_tree_sha256"], *overlay["original_file_sha256"].values()]):
                raise HarnessError("AUTOLINK_OVERLAY_EVIDENCE_DRIFT")
        if not isinstance(baseline.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", baseline["commit"]): raise HarnessError("MISSING_BASELINE_COMMIT")
        if not isinstance(baseline.get("repo_url"), str) or not baseline["repo_url"].startswith("https://") or baseline.get("license") in {"", None, "upstream-license-review-required"} or not isinstance(baseline.get("native_entrypoint"), dict) or set(baseline["native_entrypoint"]) != {"path", "symbol", "arguments"} or not all(isinstance(baseline["native_entrypoint"].get(key), str) and baseline["native_entrypoint"][key] for key in ("path", "symbol")) or not isinstance(baseline["native_entrypoint"].get("arguments"), list):
            raise HarnessError("INVALID_UPSTREAM_EVIDENCE")
        adapter = NativeAdapter.from_manifest(baseline)
        evidence = baseline["upstream_evidence"]
        if evidence["entrypoint_path"] != baseline["native_entrypoint"]["path"] or not any(evidence["entrypoint_path"] in token for token in baseline["command"]):
            raise HarnessError("NATIVE_ENTRYPOINT_EVIDENCE_DRIFT")
        if adapter.adapter_id != ADAPTER_IDS[index] or adapter.adapter_id in adapter_ids:
            raise HarnessError("NATIVE_ADAPTER_MAPPING_DRIFT")
        adapter_ids.add(adapter.adapter_id)
        namespace = baseline.get("cache_namespace")
        if not isinstance(namespace, str) or not namespace:
            raise HarnessError("MISSING_CACHE_NAMESPACE")
        if namespace in cache_namespaces:
            raise HarnessError("CACHE_NAMESPACE_COLLISION")
        cache_namespaces.add(namespace)
    _scan_for_leakage(manifest)


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


def _prediction_failure(baseline: dict[str, Any], attempt: int, code: str, trace_ref: str, qa_id: str = "__run__") -> dict[str, Any]:
    return {
        "qa_id": qa_id,
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
        "latency_ms": 0,
    }


def _redacted_digest(value: str | bytes | None) -> dict[str, Any]:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = value or ""
    secret = os.environ.get("DEEPSEEK_API_KEY")
    if secret:
        value = value.replace(secret, "[REDACTED]")
    return {"sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(), "bytes": len(value.encode("utf-8")), "redacted": True}


def _stable_incompatible_rows(baseline: dict[str, Any], dataset: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    """Fail before credentials, worktree, database, or provider access."""
    paths = _paths(output_dir, baseline["baseline_id"], 1)
    if paths.baseline_dir.exists():
        raise HarnessError("DUPLICATE_ATTEMPT", str(paths.baseline_dir))
    paths.baseline_dir.mkdir(parents=True, exist_ok=False)
    _atomic_write_json(paths.trace_json, {
        "failure_code": "S0_METHOD_INCOMPATIBLE",
        "adapter_id": baseline["native_adapter_id"],
        "cache_namespace": baseline["cache_namespace"],
        "preflight_only": True,
    })
    ref = str(paths.trace_json.relative_to(output_dir))
    return [_prediction_failure(baseline, 1, "S0_METHOD_INCOMPATIBLE", ref, question["qa_id"]) for question in dataset["questions"]]


def _databao_failure_code(stdout: str) -> str:
    if "DATABAO_MODEL_UNAVAILABLE" in stdout:
        return "MODEL_UNAVAILABLE"
    if "DATABAO_NATIVE_OUTPUT_INVALID" in stdout:
        return "INVALID_BASELINE_OUTPUT"
    if "DATABAO_AUTH_MISSING" in stdout:
        return "DEEPSEEK_AUTH_MISSING"
    if "DATABAO_TRANSPORT_ERROR" in stdout:
        return "TRANSPORT_ERROR"
    return "INVALID_BASELINE_OUTPUT"


def _run_databao_native(baseline: dict[str, Any], dataset: dict[str, Any], output_dir: Path, max_attempts: int) -> list[dict[str, Any]]:
    runtime = os.environ.get("EAST_DATABAO_RUNTIME_PYTHON")
    db_path = os.environ.get("EAST_DATABAO_READ_ONLY_DB_PATH")
    if not runtime or not db_path:
        paths = _paths(output_dir, baseline["baseline_id"], 1)
        if paths.baseline_dir.exists():
            raise HarnessError("DUPLICATE_ATTEMPT", str(paths.baseline_dir))
        paths.baseline_dir.mkdir(parents=True, exist_ok=False)
        _atomic_write_json(paths.trace_json, {"failure_code": "DATABAO_RUNTIME_OR_DB_UNAVAILABLE", "adapter_id": baseline["native_adapter_id"], "cache_namespace": baseline["cache_namespace"], "preflight_only": True})
        ref = str(paths.trace_json.relative_to(output_dir))
        return [_prediction_failure(baseline, 1, "DATABAO_RUNTIME_OR_DB_UNAVAILABLE", ref, question["qa_id"]) for question in dataset["questions"]]
    outputs: list[dict[str, Any]] = []
    for question in dataset["questions"]:
        last_code = "TRANSPORT_ERROR"
        for attempt in range(1, max_attempts + 1):
            paths = _paths(output_dir, baseline["baseline_id"], attempt)
            if paths.baseline_dir.exists():
                raise HarnessError("DUPLICATE_ATTEMPT", str(paths.baseline_dir))
            paths.baseline_dir.mkdir(parents=True, exist_ok=False)
            request_path = paths.baseline_dir / "databao-request.json"
            result_path = paths.baseline_dir / "databao-result.json"
            _atomic_write_json(request_path, {"qa_id": question["qa_id"], "question": question["clear_question"], "public_schema": question["public_semantic_schema"], "read_only_db_path": db_path})
            env = os.environ.copy()
            source = env.get("EAST_DATABAO_UPSTREAM_PATH", "")
            env["PYTHONPATH"] = str(SRC) + (os.pathsep + source if source else "")
            started = time.time()
            try:
                completed = subprocess.run([runtime, "-m", "east_v5.experiments.databao_launcher", "--request", str(request_path), "--output", str(result_path)], cwd=ROOT, env=env, text=True, capture_output=True, timeout=int(baseline["timeout_seconds"]), check=False)
            except subprocess.TimeoutExpired as exc:
                _atomic_write_json(paths.trace_json, {"failure_code": "TIMEOUT", "adapter_id": baseline["native_adapter_id"], "cache_namespace": baseline["cache_namespace"], "stdout": _redacted_digest(exc.stdout), "stderr": _redacted_digest(exc.stderr)})
                outputs.append(_prediction_failure(baseline, attempt, "TIMEOUT", str(paths.trace_json.relative_to(output_dir)), question["qa_id"]))
                break
            elapsed_ms = int((time.time() - started) * 1000)
            _atomic_write_json(paths.trace_json, {"returncode": completed.returncode, "elapsed_ms": elapsed_ms, "adapter_id": baseline["native_adapter_id"], "cache_namespace": baseline["cache_namespace"], "stdout": _redacted_digest(completed.stdout), "stderr": _redacted_digest(completed.stderr)})
            if completed.returncode:
                last_code = _databao_failure_code(completed.stdout)
                if last_code == "TRANSPORT_ERROR" and attempt < max_attempts:
                    continue
                outputs.append(_prediction_failure(baseline, attempt, last_code, str(paths.trace_json.relative_to(output_dir)), question["qa_id"]))
                break
            try:
                result = load_json(result_path)
                sql = result["sql"]
                calls, tokens = result["model_calls"], result["model_tokens"]
                if not isinstance(sql, str) or not READ_ONLY_SQL_RE.match(sql) or isinstance(calls, bool) or not isinstance(calls, int) or isinstance(tokens, bool) or not isinstance(tokens, int) or calls < 1 or tokens < 0:
                    raise ValueError
            except (KeyError, ValueError, TypeError, HarnessError):
                outputs.append(_prediction_failure(baseline, attempt, "INVALID_BASELINE_OUTPUT", str(paths.trace_json.relative_to(output_dir)), question["qa_id"]))
                break
            normalized = normalize_sql(sql)
            outputs.append({"qa_id": question["qa_id"], "baseline_id": baseline["baseline_id"], "baseline_commit": baseline["commit"], "backbone_model_id": baseline["model_contract"]["model_id"], "attempt": attempt, "prediction_sql_raw": "", "prediction_sql_normalized": "", "raw_sql_hash": hashlib.sha256(sql.encode("utf-8")).hexdigest(), "normalized_sql_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(), "execution_status": "ok", "failure_code": "", "trace_ref": str(paths.trace_json.relative_to(output_dir)), "token_calls": calls, "token_total": tokens, "latency_ms": elapsed_ms})
            break
    return outputs


def _run_one(baseline: dict[str, Any], dataset_path: Path, dataset: dict[str, Any], output_dir: Path, max_attempts: int, native_worktree_root: Path | None, route: dict[str, Any]) -> list[dict[str, Any]]:
    # The matrix is the authoritative first gate.  Incompatible routes must
    # never fall through to the legacy fixture command surface.
    if route["s0_method_compatibility"] == "S0_METHOD_INCOMPATIBLE":
        return _stable_incompatible_rows(baseline, dataset, output_dir)
    if baseline["baseline_id"] == "Databao Agent" and route["overall_runnable"]:
        return _run_databao_native(baseline, dataset, output_dir, max_attempts)
    raise HarnessError("ROUTE_LAUNCHER_CONTRACT_DRIFT")


def run_harness(experiment_contract_path: Path, dataset_manifest_path: Path, baseline_run_manifest_path: Path, output_dir: Path, mode: str = "concurrent", native_worktree_root: Path | None = None, route_matrix_path: Path = ROUTE_MATRIX_PATH) -> dict[str, Any]:
    contract = load_json(experiment_contract_path)
    dataset = load_json(dataset_manifest_path)
    manifest = load_json(baseline_run_manifest_path)
    validate_experiment_contract(contract)
    validate_dataset_manifest(dataset)
    validate_run_manifest(manifest)
    matrix = load_route_compatibility_matrix(route_matrix_path)
    routes = {route["baseline_id"]: route for route in matrix["routes"]}

    output_dir.mkdir(parents=True, exist_ok=True)
    baselines = manifest["baselines"]
    max_attempts = contract["retry_policy"]["max_attempts"]
    if mode == "serial":
        results = [_run_one(baseline, dataset_manifest_path, dataset, output_dir, max_attempts, native_worktree_root, routes[baseline["baseline_id"]]) for baseline in baselines]
    elif mode == "concurrent":
        with concurrent.futures.ThreadPoolExecutor(max_workers=contract["concurrency"]) as executor:
            futures = [executor.submit(_run_one, baseline, dataset_manifest_path, dataset, output_dir, max_attempts, native_worktree_root, routes[baseline["baseline_id"]]) for baseline in baselines]
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
        "collection_hash": stable_hash([{key: value for key, value in row.items() if key not in {"latency_ms", "trace_ref"}} for row in predictions]),
        "predictions_jsonl": str(predictions_path),
        "trace_refs_by_baseline_isolated": all(len(refs) >= 1 for refs in trace_refs_by_baseline.values()) and len(trace_refs_by_baseline) == len(baselines),
        "cache_namespaces_unique": len({baseline["cache_namespace"] for baseline in baselines}) == len(baselines),
    }
    _atomic_write_json(output_dir / "eval_summary.json", summary)
    return summary
