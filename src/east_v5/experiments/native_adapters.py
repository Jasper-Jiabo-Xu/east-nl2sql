"""Fail-closed launch and normalization adapters for the six frozen baselines.

This module deliberately has one parser per upstream.  A shared ``sql`` field
or the former synthetic baseline executable is not accepted: treating six
different upstream protocols as one prompt wrapper would make a comparison
claim unverifiable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .s0_harness import HarnessError


ADAPTER_IDS = (
    "deepeye_sql_native_v1",
    "datagallery_text2sql_native_v1",
    "joydataagent_sql_native_v1",
    "databao_agent_native_v1",
    "reforce_native_v1",
    "autolink_native_v1",
)


def _text(value: Any, code: str = "INVALID_NATIVE_RESPONSE") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessError(code)
    return value


def _integer(value: Any, code: str = "INVALID_NATIVE_RESPONSE") -> int:
    if isinstance(value, bool):
        raise HarnessError(code)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HarnessError(code) from exc
    if result < 0:
        raise HarnessError(code)
    return result


def _deepeye(row: dict[str, Any]) -> tuple[str, int, int]:
    usage = row.get("usage")
    if not isinstance(usage, dict):
        raise HarnessError("INVALID_DEEPEYE_SQL_RESPONSE")
    return _text(row.get("final_sql"), "INVALID_DEEPEYE_SQL_RESPONSE"), _integer(usage.get("calls"), "INVALID_DEEPEYE_SQL_RESPONSE"), _integer(usage.get("total_tokens"), "INVALID_DEEPEYE_SQL_RESPONSE")


def _datagallery(row: dict[str, Any]) -> tuple[str, int, int]:
    result = row.get("dataagent_result")
    if not isinstance(result, dict):
        raise HarnessError("INVALID_DATAGALLERY_RESPONSE")
    stats = result.get("inference_stats")
    if not isinstance(stats, dict):
        raise HarnessError("INVALID_DATAGALLERY_RESPONSE")
    return _text(result.get("generated_sql"), "INVALID_DATAGALLERY_RESPONSE"), _integer(stats.get("llm_calls"), "INVALID_DATAGALLERY_RESPONSE"), _integer(stats.get("token_count"), "INVALID_DATAGALLERY_RESPONSE")


def _joydata(row: dict[str, Any]) -> tuple[str, int, int]:
    answer = row.get("joydata_answer")
    if not isinstance(answer, dict):
        raise HarnessError("INVALID_JOYDATAAGENT_RESPONSE")
    cost = answer.get("model_cost")
    if not isinstance(cost, dict):
        raise HarnessError("INVALID_JOYDATAAGENT_RESPONSE")
    return _text(answer.get("sql_text"), "INVALID_JOYDATAAGENT_RESPONSE"), _integer(cost.get("requests"), "INVALID_JOYDATAAGENT_RESPONSE"), _integer(cost.get("tokens"), "INVALID_JOYDATAAGENT_RESPONSE")


def _databao(row: dict[str, Any]) -> tuple[str, int, int]:
    thread = row.get("databao_thread")
    if not isinstance(thread, dict):
        raise HarnessError("INVALID_DATABAO_RESPONSE")
    telemetry = thread.get("telemetry")
    if not isinstance(telemetry, dict):
        raise HarnessError("INVALID_DATABAO_RESPONSE")
    return _text(thread.get("sql"), "INVALID_DATABAO_RESPONSE"), _integer(telemetry.get("llm_calls"), "INVALID_DATABAO_RESPONSE"), _integer(telemetry.get("tokens"), "INVALID_DATABAO_RESPONSE")


def _reforce(row: dict[str, Any]) -> tuple[str, int, int]:
    result = row.get("reforce_result")
    if not isinstance(result, dict):
        raise HarnessError("INVALID_REFORCE_RESPONSE")
    refinement = result.get("refinement")
    if not isinstance(refinement, dict):
        raise HarnessError("INVALID_REFORCE_RESPONSE")
    return _text(result.get("selected_sql"), "INVALID_REFORCE_RESPONSE"), _integer(refinement.get("generation_calls"), "INVALID_REFORCE_RESPONSE"), _integer(refinement.get("token_total"), "INVALID_REFORCE_RESPONSE")


def _autolink(row: dict[str, Any]) -> tuple[str, int, int]:
    result = row.get("autolink_result")
    if not isinstance(result, dict):
        raise HarnessError("INVALID_AUTOLINK_RESPONSE")
    exploration = result.get("exploration")
    if not isinstance(exploration, dict):
        raise HarnessError("INVALID_AUTOLINK_RESPONSE")
    return _text(result.get("final_query"), "INVALID_AUTOLINK_RESPONSE"), _integer(exploration.get("model_calls"), "INVALID_AUTOLINK_RESPONSE"), _integer(exploration.get("tokens"), "INVALID_AUTOLINK_RESPONSE")


PARSERS: dict[str, Callable[[dict[str, Any]], tuple[str, int, int]]] = {
    ADAPTER_IDS[0]: _deepeye,
    ADAPTER_IDS[1]: _datagallery,
    ADAPTER_IDS[2]: _joydata,
    ADAPTER_IDS[3]: _databao,
    ADAPTER_IDS[4]: _reforce,
    ADAPTER_IDS[5]: _autolink,
}


@dataclass(frozen=True)
class NativeAdapter:
    adapter_id: str
    command: list[str]
    transport_mode: str

    @classmethod
    def from_manifest(cls, baseline: dict[str, Any]) -> "NativeAdapter":
        adapter_id = baseline.get("native_adapter_id")
        if adapter_id not in PARSERS:
            raise HarnessError("UNKNOWN_NATIVE_ADAPTER")
        command = baseline.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise HarnessError("INVALID_BASELINE_COMMAND")
        mode = baseline.get("transport_mode")
        if mode not in {"native", "fake_transport"}:
            raise HarnessError("INVALID_TRANSPORT_MODE")
        rendered = " ".join(command)
        if "fixture_baseline" in rendered or (mode == "native" and ("fake_native_transport" in rendered or "{upstream_worktree}" not in rendered)):
            raise HarnessError("GENERIC_WRAPPER_PROHIBITED")
        return cls(adapter_id=adapter_id, command=command, transport_mode=mode)

    def require_credentials(self, model_contract: dict[str, Any]) -> None:
        if self.transport_mode == "fake_transport":
            return
        env_name = model_contract.get("api_key_env")
        if not isinstance(env_name, str) or env_name != "DEEPSEEK_API_KEY" or not os.environ.get(env_name):
            raise HarnessError("DEEPSEEK_AUTH_MISSING")

    def parse(self, row: dict[str, Any]) -> tuple[str, int, int]:
        return PARSERS[self.adapter_id](row)
