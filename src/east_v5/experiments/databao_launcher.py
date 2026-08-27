"""Closed source-attested launcher for the pinned Databao native API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "contracts" / "experiments" / "databao_launcher_contract.json"
REQUEST_KEYS = {"qa_id", "question", "public_schema", "read_only_db_path"}
OUTPUT_KEYS = {"qa_id", "sql", "native_method", "provider_endpoint", "model_calls", "model_tokens"}


class DatabaoLaunchError(ValueError):
    """Stable, secret-free failure from the native launch boundary."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatabaoLaunchError("DATABAO_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise DatabaoLaunchError("DATABAO_INVALID_JSON_OBJECT")
    return value


def _source_root() -> Path:
    raw = os.environ.get("EAST_DATABAO_UPSTREAM_PATH")
    if not raw:
        raise DatabaoLaunchError("DATABAO_UPSTREAM_PATH_MISSING")
    path = Path(raw).resolve()
    if not path.is_dir() or path.is_symlink():
        raise DatabaoLaunchError("DATABAO_UPSTREAM_PATH_INVALID")
    return path


def _verify_source(contract: dict[str, Any], source: Path) -> None:
    try:
        observed = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise DatabaoLaunchError("DATABAO_UPSTREAM_REVISION_UNAVAILABLE") from exc
    if observed != contract["commit"]:
        raise DatabaoLaunchError("DATABAO_UPSTREAM_COMMIT_DRIFT")
    entry = source / contract["entrypoint"]["path"]
    config = source / contract["provider"]["config_path"]
    if not entry.is_file() or _sha256(entry) != contract["entrypoint"]["sha256"]:
        raise DatabaoLaunchError("DATABAO_ENTRYPOINT_DRIFT")
    if not config.is_file() or _sha256(config) != contract["provider"]["config_sha256"]:
        raise DatabaoLaunchError("DATABAO_PROVIDER_CONFIG_DRIFT")
    for label in ("native_api", "native_output", "native_executor"):
        evidence = contract[label]
        path = source / evidence["path"]
        if not path.is_file() or _sha256(path) != evidence["sha256"]:
            raise DatabaoLaunchError(f"DATABAO_{label.upper()}_DRIFT")


def _validate_contract(contract: dict[str, Any]) -> None:
    expected = {"launcher_id", "baseline_id", "repo_url", "commit", "entrypoint", "native_api", "native_output", "native_executor", "provider", "input_mapping", "output_mapping", "prohibitions", "runtime"}
    if set(contract) != expected or contract.get("launcher_id") != "databao_native_launcher_v1" or contract.get("baseline_id") != "Databao Agent" or not isinstance(contract.get("commit"), str) or len(contract["commit"]) != 40:
        raise DatabaoLaunchError("DATABAO_CONTRACT_DRIFT")
    for label in ("entrypoint", "native_api", "native_output", "native_executor"):
        value = contract.get(label)
        if not isinstance(value, dict) or set(value) != {"path", "symbol", "sha256"} or not all(isinstance(value.get(key), str) and value[key] for key in ("path", "symbol", "sha256")) or len(value["sha256"]) != 64:
            raise DatabaoLaunchError("DATABAO_CONTRACT_DRIFT")
    provider = contract.get("provider")
    provider_keys = {"model", "base_url", "api_key_env", "endpoint", "temperature", "max_tokens", "timeout_seconds", "retry_count", "config_path", "config_sha256"}
    if not isinstance(provider, dict) or set(provider) != provider_keys or provider.get("model") != "deepseek-v4-flash" or provider.get("base_url") != "https://api.deepseek.com" or provider.get("api_key_env") != "DEEPSEEK_API_KEY" or provider.get("endpoint") != "openai_chat_completions" or provider.get("temperature") != 0 or provider.get("retry_count") != 3 or not all(isinstance(provider.get(key), int) and provider[key] > 0 for key in ("max_tokens", "timeout_seconds")) or not isinstance(provider.get("config_sha256"), str) or len(provider["config_sha256"]) != 64:
        raise DatabaoLaunchError("DATABAO_CONTRACT_DRIFT")
    if not isinstance(contract.get("prohibitions"), list) or set(contract["prohibitions"]) != {"gold_sql", "answer_contract", "business_events", "east_private_kb", "write_database"}:
        raise DatabaoLaunchError("DATABAO_CONTRACT_DRIFT")


def _validate_request(request: dict[str, Any]) -> None:
    if set(request) != REQUEST_KEYS:
        raise DatabaoLaunchError("DATABAO_INPUT_CONTRACT_DRIFT")
    if not isinstance(request["qa_id"], str) or not request["qa_id"]:
        raise DatabaoLaunchError("DATABAO_INVALID_QA_ID")
    if not isinstance(request["question"], str) or not request["question"]:
        raise DatabaoLaunchError("DATABAO_INVALID_QUESTION")
    if not isinstance(request["public_schema"], dict):
        raise DatabaoLaunchError("DATABAO_INVALID_PUBLIC_SCHEMA")
    db_value = request["read_only_db_path"]
    if not isinstance(db_value, str):
        raise DatabaoLaunchError("DATABAO_READ_ONLY_DB_REQUIRED")
    db_path = Path(db_value)
    if not db_path.is_file() or db_path.is_symlink():
        raise DatabaoLaunchError("DATABAO_READ_ONLY_DB_REQUIRED")


def _provider_base_url(contract: dict[str, Any]) -> str:
    """Permit loopback only for the hermetic fake-endpoint integration test."""
    endpoint = os.environ.get("EAST_DATABAO_FAKE_ENDPOINT")
    if endpoint:
        if not endpoint.startswith("http://127.0.0.1:"):
            raise DatabaoLaunchError("DATABAO_FAKE_ENDPOINT_INVALID")
        return endpoint
    return contract["provider"]["base_url"]


def launch(request: dict[str, Any]) -> dict[str, Any]:
    """Run upstream ``thread.ask`` and return the native ``thread.code`` SQL."""
    contract = _load_object(CONTRACT_PATH)
    _validate_contract(contract)
    _validate_request(request)
    _verify_source(contract, _source_root())
    if not os.environ.get(contract["provider"]["api_key_env"]):
        raise DatabaoLaunchError("DATABAO_AUTH_MISSING")

    # Defer imports so static repository tests do not need the isolated runtime.
    import duckdb
    import databao.agent as bao
    from databao.agent.configs.llm import LLMConfig
    from databao.agent.executors import llm as databao_llm

    connection = duckdb.connect(str(Path(request["read_only_db_path"]).resolve()), read_only=True)
    domain = bao.domain()
    domain.add_db(connection, name="db1")
    api_key = os.environ[contract["provider"]["api_key_env"]]
    old_openai_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = api_key
    try:
        llm_config = LLMConfig(
            name=contract["provider"]["model"],
            temperature=contract["provider"]["temperature"],
            max_tokens=contract["provider"]["max_tokens"],
            timeout=contract["provider"]["timeout_seconds"],
            api_base_url=_provider_base_url(contract),
            use_responses_api=False,
            model_kwargs={"max_retries": 0},
        )
        # The benchmark's direct Agent/Thread API; EAST supplies no prompt,
        # retrieval, SQL, or execution semantics.
        thread = bao.agent(domain=domain, llm_config=llm_config, stream_ask=False).thread()
        # The shared harness owns the only retry loop.  Disable Databao's
        # broad internal retry wrapper so model/invalid failures do not become
        # hidden extra semantic calls; only transport errors are retried there.
        original_call = databao_llm.call_model_with_retry
        databao_llm.call_model_with_retry = lambda model, messages: model.invoke(messages)
        try:
            thread.ask(request["question"])
        finally:
            databao_llm.call_model_with_retry = original_call
        sql = thread.code()
        # Databao's execution result retains the source-generated message log.
        # Count it rather than inventing provider telemetry in the adapter.
        result = getattr(thread, "_data_result", None)
        messages = getattr(result, "meta", {}).get("messages", []) if result is not None else []
        model_calls = sum(getattr(message, "type", "") == "ai" for message in messages)
        model_tokens = sum(
            int((getattr(message, "usage_metadata", None) or {}).get("total_tokens", 0))
            for message in messages
        )
    finally:
        if old_openai_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_openai_key
    if not isinstance(sql, str) or not sql.strip():
        raise DatabaoLaunchError("DATABAO_NATIVE_OUTPUT_INVALID")
    return {
        "qa_id": request["qa_id"],
        "sql": sql,
        "native_method": "bao.agent.thread.ask.thread.code",
        "provider_endpoint": "fixture" if os.environ.get("EAST_DATABAO_FAKE_ENDPOINT") else "deepseek",
        "model_calls": model_calls,
        "model_tokens": model_tokens,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = launch(_load_object(args.request))
    except DatabaoLaunchError as exc:
        print(exc.args[0], flush=True)
        return 2
    except Exception as exc:
        # The adapter boundary must never persist SDK exceptions (which can
        # contain request context).  A missing model is non-retryable; all
        # other provider-side failures are a bounded transport retry class.
        text = str(exc).lower()
        print("DATABAO_MODEL_UNAVAILABLE" if "model" in text and ("not found" in text or "unavailable" in text) else "DATABAO_TRANSPORT_ERROR", flush=True)
        return 2
    if set(result) != OUTPUT_KEYS:
        print("DATABAO_OUTPUT_CONTRACT_DRIFT", flush=True)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
