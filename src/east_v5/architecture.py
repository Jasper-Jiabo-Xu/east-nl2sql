from __future__ import annotations

from pathlib import Path
from typing import Any

from east_v5.governance import ContractError, load_json


EVENT_DATA = ["210", "220", "230", "241", "242", "260", "210", "010"]
EVENT_ORM = ["210", "220", "230", "251", "252", "260", "210", "010"]
FOUNDATION = ["210", "220", "241", "242", "260", "210", "010"]
_ACTIVE_TEXT_ROOTS = ("agents/contracts", "prompts", "skills/east-v5-runtime", "docs/architecture")
_STALE_AFFIRMATIONS = (
    "230 仅供 251",
    "241 只读取结构闭包",
    "241 不读操作闭包",
    "242 生成 INSERT",
    "独立 ODS 资产为运行期事实源",
    "Foundation 使用 ORM",
)


def verify_architecture(repo_root: Path) -> dict[str, Any]:
    architecture = load_json(repo_root / "config" / "v5-architecture.json")
    packages = load_json(repo_root / "config" / "v5-package-catalog.json")
    pipelines = architecture["pipelines"]
    if pipelines != {"event_data": EVENT_DATA, "event_orm": EVENT_ORM, "foundation": FOUNDATION}:
        raise ContractError("ARCHITECTURE_PIPELINE_DRIFT")
    foundation = architecture["foundation"]
    if foundation["forbidden_agents"] != ["230", "251", "252"]:
        raise ContractError("FOUNDATION_AGENT_BOUNDARY_DRIFT")
    if "operation_closure" not in foundation["forbidden_artifacts"]:
        raise ContractError("FOUNDATION_OPERATION_CLOSURE_FORBIDDEN")
    fan_out = architecture["fan_out"]
    if fan_out != {"producer": "230", "artifact": "operation_closure", "consumers": ["241", "251"], "mode": "event"}:
        raise ContractError("OPERATION_CLOSURE_FANOUT_DRIFT")
    package_index = {item["id"]: item for item in packages["packages"]}
    if package_index["operation_closure"]["consumers"] != ["241", "251"]:
        raise ContractError("PACKAGE_CONSUMER_DRIFT")
    if package_index["verified_bound_data"]["consumers"] != ["260"]:
        raise ContractError("VERIFIED_DATA_CONSUMER_DRIFT")
    if package_index["frozen_orm"]["consumers"] != ["260"]:
        raise ContractError("FROZEN_ORM_CONSUMER_DRIFT")
    return {"architecture": architecture, "packages": packages}


def scan_active_contracts(repo_root: Path) -> list[str]:
    findings: list[str] = []
    for relative_root in _ACTIVE_TEXT_ROOTS:
        root = repo_root / relative_root
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".md", ".json", ".py"}:
                continue
            content = path.read_text(encoding="utf-8")
            for phrase in _STALE_AFFIRMATIONS:
                if phrase in content:
                    findings.append(f"{path.relative_to(repo_root)}:{phrase}")
    if findings:
        raise ContractError("STALE_ARCHITECTURE_CLAIM:" + ",".join(findings))
    return findings
