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
OUTPUT_KEYS = {"qa_id", "sql", "native_method", "provider_endpoint"}


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
    _validate_request(request)
    _verify_source(contract, _source_root())
    if not os.environ.get(contract["provider"]["api_key_env"]):
        raise DatabaoLaunchError("DATABAO_AUTH_MISSING")

    # Defer imports so static repository tests do not need the isolated runtime.
    import duckdb
    import databao.agent as bao
    from databao.agent.configs.llm import LLMConfig

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
        )
        # The benchmark's direct Agent/Thread API; EAST supplies no prompt,
        # retrieval, SQL, or execution semantics.
        thread = bao.agent(domain=domain, llm_config=llm_config, stream_ask=False).thread()
        thread.ask(request["question"])
        sql = thread.code()
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
    if set(result) != OUTPUT_KEYS:
        print("DATABAO_OUTPUT_CONTRACT_DRIFT", flush=True)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
