#!/usr/bin/env python3
"""Append the approved chart-of-accounts foundation subasset to the V5 repo.

V0.1 is never rewritten.  This append-only packager creates V0.2 with explicit
payload hashes and only allow-listed source assets.
"""

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
V2 = PHASE / "extension_assets_v1/20260805_扩展数据元约束资产V2_机构部门与会计科目树绑定"
ACCOUNT_TREE = PHASE / "extension_assets_v1/20260805_会计科目树V1_人工审批后"
ORG_TREE = PHASE / "extension_assets_v1/20260805_机构部门统一树V1_人工审查"
CORE = PHASE / "core_east_asset_v1/20260805_人工审批后核心库"
REF_DB = PHASE / "code_value_libraries/20260805_一表一码表_层级补全_v4/code_reference_library_v3.sqlite"
VERSION = "CA-V0.2.0-foundation"
PACKAGE_ID = "CA-FOUNDATION-20260805-002"
BLOCKED_NAMES = ("corebank.db", "corebank_v4_final.db", ".env", "api-key", "deepseek-key", "glm-key")
PATTERNS = [re.compile(p) for p in (r"\bsk-[A-Za-z0-9_-]{12,}\b", r"\b(?:AKIA|ghp_|github_pat_|xoxb-|xoxp-|glpat-)[A-Za-z0-9_-]{8,}\b", r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----")]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def copy(source: Path, target: Path, repo: Path, kind: str) -> dict[str, object]:
    if any(token in source.name.lower() for token in BLOCKED_NAMES):
        raise RuntimeError(f"禁止发布：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": str(target.relative_to(repo)), "origin": str(source.relative_to(ROOT)), "sha256": sha(target), "size_bytes": target.stat().st_size, "classification": kind}


def scan(repo: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in repo.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in {".py", ".md", ".json", ".txt", ".yml", ".yaml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in PATTERNS:
            if pattern.search(text): findings.append({"path": str(path.relative_to(repo)), "pattern": pattern.pattern})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--publish-root", type=Path, required=True); args = parser.parse_args()
    repo = args.publish_root.resolve()
    delivery = repo / f"05_新版本交付层/约束资产/{VERSION}"
    if not (repo / ".git").exists() or not (repo / "05_新版本交付层/约束资产/CA-V0.1.0-foundation").exists():
        raise SystemExit("目标不是已初始化的 V5 发布仓库")
    if delivery.exists(): raise SystemExit(f"拒绝覆盖既有交付：{delivery}")
    required = [V2 / "constraint_asset_extension_v2.sqlite", V2 / "扩展数据元约束资产V2设计.md", V2 / "扩展资产V2构建审计.json", ACCOUNT_TREE / "synthetic_chart_of_accounts_tree_v1.sqlite", ACCOUNT_TREE / "会计科目树构建审计.json", ORG_TREE / "extension_org_department_tree_v1.sqlite", CORE / "local_code_value_library_core.sqlite", REF_DB]
    absent = [str(p) for p in required if not p.exists()]
    if absent: raise SystemExit("缺少发布源：" + "; ".join(absent))

    payload_sources = {
        V2 / "constraint_asset_extension_v2.sqlite": delivery / "constraint_assets.sqlite",
        CORE / "local_code_value_library_core.sqlite": delivery / "装配视图与码表引用域/本地码表.sqlite",
        REF_DB: delivery / "装配视图与码表引用域/国标与附件4码表.sqlite",
        ORG_TREE / "extension_org_department_tree_v1.sqlite": delivery / "装配视图与码表引用域/机构部门统一树.sqlite",
        ACCOUNT_TREE / "synthetic_chart_of_accounts_tree_v1.sqlite": delivery / "装配视图与码表引用域/会计科目树.sqlite",
        V2 / "扩展数据元约束资产V2设计.md": delivery / "000使用场景输入/数据元、机构部门树与会计科目树引用说明.md",
        V2 / "扩展资产V2构建审计.json": delivery / "覆盖与查询验收报告/扩展资产V2构建审计.json",
        ACCOUNT_TREE / "会计科目树构建审计.json": delivery / "覆盖与查询验收报告/会计科目树构建审计.json",
    }
    records = [copy(source, target, repo, "DELIVERY_PAYLOAD") for source, target in payload_sources.items()]
    snapshot = repo / "02_参考与旧版引入/按需引入台账/约束资产V2构建器来源快照"
    for name in ("build_synthetic_chart_of_accounts_tree.py", "approve_chart_of_accounts_tree.py", "build_extension_constraint_asset_v2_chart_of_accounts.py", "append_v5_foundation_v0_2_release.py"):
        records.append(copy(ROOT / "vnext/constraint_assets_v2" / name, snapshot / name, repo, "REFERENCE_CODE_SNAPSHOT"))
    content = hashlib.sha256()
    for item in sorted((x for x in records if x["classification"] == "DELIVERY_PAYLOAD"), key=lambda x: str(x["path"])):
        content.update(f"{item['path']}\0{item['sha256']}\n".encode("utf-8"))
    manifest = {
        "artifact_id": PACKAGE_ID, "asset_version": VERSION, "publication_status": "approved_partial_not_released", "created_at_utc": now(), "content_hash": content.hexdigest(),
        "inherits": {"artifact_id": "CA-FOUNDATION-20260805-001", "asset_version": "CA-V0.1.0-foundation", "mode": "append_only_new_delivery_directory"},
        "approval_refs": ["SRC_HUMAN_APPROVAL:20260805_机构部门统一树V1审查通过", "SRC_HUMAN_APPROVAL:20260805_扩展数据元机构部门树绑定_没什么问题", "SRC_HUMAN_APPROVAL:20260805_科目树四级扩展合同及发布批准"],
        "capabilities": ["DATA_ELEMENT_CONSTRAINTS", "LOCAL_AND_EXTERNAL_CODE_REFERENCES", "ORG_DEPARTMENT_TREE", "ORG_DEPARTMENT_TREE_BINDINGS", "CHART_OF_ACCOUNTS_TREE", "CHART_OF_ACCOUNTS_TREE_BINDINGS"],
        "not_in_scope": ["SINGLE_FIELD_CONSTRAINTS", "INTRA_TABLE_MULTI_FIELD_CONSTRAINTS", "CROSS_TABLE_MULTI_FIELD_CONSTRAINTS", "ODS_RELATIONS", "EMPLOYEE_TELLER_CUSTOMER_OBJECT_POOLS"],
        "consumer_guard": "仅可按 capabilities 使用，不能作为完整四类约束资产或000/数据生成流程的完整约束源。", "payload": [x for x in records if x["classification"] == "DELIVERY_PAYLOAD"],
    }
    dump(delivery / "manifest/asset_manifest.json", manifest)
    (delivery / "manifest/README.md").write_text("本目录为已批准的基础子资产 V0.2，包含机构部门树、会计科目树及其数据元绑定；仍非完整四类约束资产。运行期必须读取 asset_manifest.json 的 capabilities 与 consumer_guard。\n", encoding="utf-8")
    acceptance = repo / f"06_测试与验收/约束资产与000验收/{VERSION}_验收摘要.md"; acceptance.parent.mkdir(parents=True, exist_ok=True)
    acceptance.write_text("# CA-V0.2.0-foundation 验收摘要\n\n- 在 V0.1 基础上新增会计科目树：216 节点、208 父子关系、173 个可记账三级种子。\n- 支持 `XX/XXXX/XXXXXX/XXXXXXXX` 四层编码合同；当前不设虚拟四级节点。\n- 新增 `003002/003003/003004` 三条 READY 且 APPROVED 的树引用；全部树引用共 13 条。\n- 仍不含单字段、多字段与 ODS 约束资产。\n", encoding="utf-8")
    (repo / "README.md").write_text("# EAST NL2SQL V5\n\n私有的版本化项目仓库。禁止提交 API key、`.env`、模型原始响应或真实 CoreBank 数据库。\n\n已发布基础子资产：`CA-V0.1.0-foundation`（机构部门树）与 `CA-V0.2.0-foundation`（新增会计科目树）；两者均不是完整四类约束资产。\n", encoding="utf-8")
    findings = scan(repo)
    if findings: raise RuntimeError("安全扫描发现疑似密钥：" + json.dumps(findings, ensure_ascii=False))
    dump(repo / f"发布清单-{VERSION}.json", {"created_at_utc": now(), "package_id": PACKAGE_ID, "delivery_content_hash": manifest["content_hash"], "files": records, "security_findings": findings})
    print(json.dumps({"version": VERSION, "file_count": len(records), "content_hash": manifest["content_hash"], "security_findings": findings}, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
