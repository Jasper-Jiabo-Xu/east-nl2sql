#!/usr/bin/env python3
"""Create a clean, allow-listed V5 foundation release tree in another Git repo."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PHASE = ROOT / "kb/working/20260727_000_goal_mode_v1/构建过程层/04_字段多字段与对象明细状态提取-V2"
CORE = PHASE / "core_east_asset_v1/20260805_人工审批后核心库"
TREE = PHASE / "extension_assets_v1/20260805_机构部门统一树V1_人工审查"
EXT = PHASE / "extension_assets_v1/20260805_扩展数据元约束资产V1_机构部门树绑定"
REF_DB = PHASE / "code_value_libraries/20260805_一表一码表_层级补全_v4/code_reference_library_v3.sqlite"
MATERIALS = ROOT / "data/raw/20260626_east_materials/east材料"
MODEL = ROOT / "data/raw/20260626_east_model/east模型"
COREBANK_INFO = ROOT / "data/raw/20260626_corebank_v4/corebank材料/info_analyzed_non_empty_datetime_adj.xlsx"
PACKAGE_ID = "CA-FOUNDATION-20260805-001"
PACKAGE_VERSION = "CA-V0.1.0-foundation"
BLOCKED = ("corebank.db", "corebank_v4_final.db", ".env", "api-key", "deepseek-key", "glm-key")
PATTERNS = [re.compile(item) for item in (r"\bsk-[A-Za-z0-9_-]{12,}\b", r"\b(?:AKIA|ghp_|github_pat_|xoxb-|xoxp-|glpat-)[A-Za-z0-9_-]{8,}\b", r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----")]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy(source: Path, target: Path) -> dict[str, object]:
    if any(token in source.name.lower() for token in BLOCKED):
        raise RuntimeError(f"禁止复制：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": str(target), "origin": str(source.relative_to(ROOT)), "sha256": sha(target), "size_bytes": target.stat().st_size}


def source_files(folder: Path):
    return [item for item in sorted(folder.rglob("*")) if item.is_file() and item.name != ".DS_Store" and not item.name.startswith(".~")]


def rel(repo: Path, record: dict[str, object]) -> dict[str, object]:
    return {**record, "path": str(Path(str(record["path"])).relative_to(repo))}


def scan(repo: Path) -> list[dict[str, str]]:
    findings = []
    text_suffixes = {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".txt"}
    for path in repo.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in text_suffixes:
            continue
        try:
            data = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in PATTERNS:
            if pattern.search(data):
                findings.append({"path": str(path.relative_to(repo)), "pattern": pattern.pattern})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish-root", type=Path, required=True)
    args = parser.parse_args()
    repo = args.publish_root.resolve()
    if not (repo / ".git").exists():
        raise SystemExit("目标必须是独立 Git 仓库")
    if any((repo / item).exists() for item in ("00_治理与合同", "01_来源冻结层", "05_新版本交付层")):
        raise SystemExit("拒绝覆盖已有 V5 发布树")
    must_exist = [CORE / "constraint_asset_core.sqlite", CORE / "local_code_value_library_core.sqlite", TREE / "extension_org_department_tree_v1.sqlite", EXT / "constraint_asset_extension_v1.sqlite", REF_DB, MATERIALS, MODEL, COREBANK_INFO]
    missing = [str(item) for item in must_exist if not item.exists()]
    if missing:
        raise SystemExit("缺少允许来源：" + "; ".join(missing))

    (repo / ".gitignore").write_text(".DS_Store\n__pycache__/\n*.py[cod]\n.env\n.env.*\ndeepseek-key/\nGLM-key/\n**/api-key.txt\n**/*key*.txt\n**/deepseek_raw/\n**/glm_raw/\n**/model_cache/\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("*.sqlite -diff\n*.xlsx -diff\n*.xls -diff\n*.doc -diff\n*.pdf -diff\n", encoding="utf-8")
    (repo / "README.md").write_text("# EAST NL2SQL V5\n\n私有的版本化项目仓库。禁止提交 API key、`.env`、模型原始响应或真实 CoreBank 数据库。当前 `CA-V0.1.0-foundation` 是已批准的基础子资产，不是完整四类约束资产。\n", encoding="utf-8")

    records: list[dict[str, object]] = []
    governance = {
        "结构图/EAST数据集大图-V5-人工.drawio": "00_治理与合同/架构快照/EAST数据集大图-V5-人工.drawio",
        "结构图/V5项目文件树与归档规范.md": "00_治理与合同/架构快照/V5项目文件树与归档规范.md",
        "结构图/V5项目上下文总览.md": "00_治理与合同/架构快照/V5项目上下文总览.md",
        "结构图/agent划分与任务输入输出清单-V2.xlsx": "00_治理与合同/架构快照/agent划分与任务输入输出清单-V2.xlsx",
        "结构图/约束资产V1-字段多字段与对象明细状态通路交接.md": "00_治理与合同/包协议/约束资产/约束资产V1-字段多字段与对象明细状态通路交接.md",
        "vnext/constraint_assets_v2/Agent1_V3合同与Prompt.md": "00_治理与合同/提示词与固定码值/约束资产/Agent1_V3合同与Prompt.md",
        "vnext/constraint_assets_v2/V2任务合同与硬代码设计.md": "00_治理与合同/包协议/约束资产/V2任务合同与硬代码设计.md",
    }
    for source_name, target_name in governance.items():
        item = rel(repo, copy(ROOT / source_name, repo / target_name)); item["classification"] = "GOVERNANCE"; records.append(item)

    frozen: list[dict[str, object]] = []
    for source_root, target_root, kind in [(MATERIALS, repo / "01_来源冻结层/EAST原始材料快照/east材料", "EAST_RAW"), (MODEL, repo / "01_来源冻结层/EAST模型快照/east模型", "EAST_MODEL")]:
        for source in source_files(source_root):
            item = rel(repo, copy(source, target_root / source.relative_to(source_root))); item["classification"] = kind; frozen.append(item); records.append(item)
    item = rel(repo, copy(COREBANK_INFO, repo / "01_来源冻结层/CoreBank分布参考/info_analyzed_non_empty_datetime_adj.xlsx")); item["classification"] = "RESTRICTED_COREBANK_SCHEMA_REFERENCE"; frozen.append(item); records.append(item)
    dump(repo / "01_来源冻结层/文件哈希与版本清单/来源冻结manifest.json", {"frozen_at_utc": now(), "policy": "Only approved EAST sources/models plus CoreBank info workbook; no CoreBank database.", "files": frozen})
    (repo / "01_来源冻结层/CoreBank分布参考/README.md").write_text("仅可保存获批的字段元数据工作簿；严禁添加 CoreBank SQLite、WAL、SHM、真实记录或导出明细。\n", encoding="utf-8")

    snapshot = repo / "02_参考与旧版引入/按需引入台账/约束资产V2构建器来源快照"
    for source in sorted((ROOT / "vnext/constraint_assets_v2").glob("*")):
        if source.is_file() and source.suffix in {".py", ".md"}:
            item = rel(repo, copy(source, snapshot / source.name)); item["classification"] = "REFERENCE_CODE_SNAPSHOT"; records.append(item)
    (snapshot / "引入台账.md").write_text("来源为本地 V2 构建器。本快照仅用于复现与改造；含历史工作区路径，禁止作为 V5 运行期入口。\n", encoding="utf-8")

    delivery = repo / f"05_新版本交付层/约束资产/{PACKAGE_VERSION}"
    payload = {
        EXT / "constraint_asset_extension_v1.sqlite": delivery / "constraint_assets.sqlite",
        CORE / "local_code_value_library_core.sqlite": delivery / "装配视图与码表引用域/本地码表.sqlite",
        REF_DB: delivery / "装配视图与码表引用域/国标与附件4码表.sqlite",
        TREE / "extension_org_department_tree_v1.sqlite": delivery / "装配视图与码表引用域/机构部门统一树.sqlite",
        EXT / "扩展数据元约束资产V1设计.md": delivery / "000使用场景输入/数据元与机构部门树引用说明.md",
        EXT / "扩展资产构建审计.json": delivery / "覆盖与查询验收报告/扩展资产构建审计.json",
        TREE / "构建审计.json": delivery / "覆盖与查询验收报告/机构部门树构建审计.json",
        CORE / "核心库构建审计.json": delivery / "覆盖与查询验收报告/核心数据元构建审计.json",
    }
    payload_records = []
    for source, target in payload.items():
        item = rel(repo, copy(source, target)); item["classification"] = "DELIVERY_PAYLOAD"; records.append(item); payload_records.append(item)
    h = hashlib.sha256()
    for item in sorted(payload_records, key=lambda row: str(row["path"])):
        h.update(f"{item['path']}\0{item['sha256']}\n".encode("utf-8"))
    content_hash = h.hexdigest()
    manifest = {"artifact_id": PACKAGE_ID, "asset_version": PACKAGE_VERSION, "publication_status": "approved_partial_not_released", "created_at_utc": now(), "content_hash": content_hash, "approval_refs": ["SRC_HUMAN_APPROVAL:20260805_机构部门统一树V1审查通过", "SRC_HUMAN_APPROVAL:20260805_扩展数据元机构部门树绑定_没什么问题"], "capabilities": ["DATA_ELEMENT_CONSTRAINTS", "LOCAL_AND_EXTERNAL_CODE_REFERENCES", "ORG_DEPARTMENT_TREE", "ORG_DEPARTMENT_TREE_BINDINGS"], "not_in_scope": ["SINGLE_FIELD_CONSTRAINTS", "INTRA_TABLE_MULTI_FIELD_CONSTRAINTS", "CROSS_TABLE_MULTI_FIELD_CONSTRAINTS", "ODS_RELATIONS", "ACCOUNTING_SUBJECT_TREE", "EMPLOYEE_TELLER_CUSTOMER_OBJECT_POOLS"], "consumer_guard": "不得作为完整约束资产或供000/数据生成流程的完整约束源；仅可按 capabilities 读取。", "payload": payload_records}
    dump(delivery / "manifest/asset_manifest.json", manifest)
    (delivery / "manifest/README.md").write_text("本目录是已批准基础子资产，而非完整四层约束资产。运行期必须读取 manifest 的 capabilities 与 consumer_guard。\n", encoding="utf-8")
    (repo / "06_测试与验收/约束资产与000验收/CA-V0.1.0-foundation_验收摘要.md").parent.mkdir(parents=True, exist_ok=True)
    (repo / "06_测试与验收/约束资产与000验收/CA-V0.1.0-foundation_验收摘要.md").write_text("# CA-V0.1.0-foundation 验收摘要\n\n- 核心数据元 301 条，继承表行数一致。\n- 机构部门树 910 节点、909 父子关系、13 个部门类型。\n- 树引用 10 条：5 条 READY，5 条 BLOCKED_EXTERNAL_MAPPING。\n- 本版本不含单字段、多字段、ODS 与科目树。\n", encoding="utf-8")
    findings = scan(repo)
    dump(repo / "00_治理与合同/发布规范/安全扫描报告.json", {"scanned_at_utc": now(), "findings": findings, "blocked_categories": ["API keys", "dotenv", "CoreBank databases", "LLM raw responses", "working/candidate assets"]})
    if findings:
        raise RuntimeError("安全扫描发现疑似密钥：" + json.dumps(findings, ensure_ascii=False))
    dump(repo / "发布清单.json", {"created_at_utc": now(), "package_id": PACKAGE_ID, "delivery_content_hash": content_hash, "tracked_file_count": len(records), "tracked_files": records})
    print(json.dumps({"publish_root": str(repo), "files": len(records), "content_hash": content_hash, "security_findings": findings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
