#!/usr/bin/env python3
"""Idempotently append EAS-44's approved V5 override to affected Multica issues."""
from __future__ import annotations

import argparse
import json
import subprocess

MARKER = "## EAS-44 现行 V5 合同覆盖（2026-08-13）"
ISSUES = {
    "EAS-13": ("b1ad33c5-fd17-44a1-913c-14ba26b6e43a", "历史/运行期废止。对象—明细—状态语义仅迁移为跨表结构化约束与 TRG-V1.0.0 引用/顺序边；不得恢复独立 ODS 资产、生成器或验证器。"),
    "EAS-16": ("5a7907db-82b4-4513-98a0-6850f4697ebc", "查询接口必须同时版本化提供 CA-V0.3.0 与 TRG-V1.0.0，并以 content hash 校验；不得把 EAS-13 或旧 registry 当运行期事实源。000/220 下游 Stub 必须消费成功。"),
    "EAS-18": ("78a0e19a-37b7-4283-84f8-8de9b6357c53", "252 只验证 251 ORM 的 AST、白名单 API、事务/回滚、dry-run、操作顺序并冻结代码哈希；移除独立 ODS 生成/验证职责，Foundation 禁止调用。"),
    "EAS-20": ("0495a7dc-8987-45a4-b39c-5bb9f01212c7", "000 只检索版本化约束/引用资产与来源证据；必须支持 CA-V0.3.0、TRG-V1.0.0，禁止检索旧生成器、旧 registry 或独立 ODS 为事实源。"),
    "EAS-29": ("14102143-7636-4737-a6ef-9a779d4589d5", "220 输出唯一结构闭包，消费者为 230/241/251/252/260；Foundation 对 EVENT_OWNED 写入请求写前拒绝。"),
    "EAS-30": ("9e092145-c3e2-4fe2-867f-47fe4d9b00c3", "230 仅服务事件链，输出唯一操作闭包并同时供 241/251 消费；不得进入 Foundation 或生成数据/ORM。"),
    "EAS-31": ("6778b838-8650-4c1e-b4cb-c24e073f0b98", "241 在事件链同时消费 220 结构闭包、230 操作闭包和只读快照；Foundation 消费 profile、220、CA-V0.3.0、TRG-V1.0.0 与快照且不读操作闭包。只生成/修改绑定数据。"),
    "EAS-32": ("98727c6b-41d2-4229-b57d-a0c8de875c65", "242 只做单字段、表内多字段、跨表多字段、引用与顺序验证，输出 verified_bound_data；不得生成/修改数据、INSERT、ORM 或独立 ODS。"),
    "EAS-33": ("6575fe4a-a227-4b3f-8efd-da30f378930c", "251 仅事件 ORM 链消费与 241 相同的 230 操作闭包，生成不含业务值的受限 Python ORM；Foundation 禁止调用。"),
    "EAS-34": ("75f4e2a0-8865-42a9-a1aa-a6f1e7a95a84", "252 只验证并冻结 251 代码哈希；未知 API、动态执行、业务值、顺序/事务不一致必须拒绝；不承担独立 ODS。"),
    "EAS-35": ("136881b9-e32e-4526-80ee-a897371b6769", "260 只在数据库 copy 绑定、执行和回归。Foundation 仅调用 east-foundation-insert-compiler/v1 产生确定性参数化 INSERT；不得让 LLM 临时生成 SQL。"),
    "EAS-36": ("a56f6842-81f9-4174-a1c2-0168e1de9454", "210 负责会审与发布候选装配：接收 260 回归包后交 010；不得生成数据/ORM/自由 SQL 或绕过 010 写正式库。"),
    "EAS-39": ("a2cc21a2-7ce7-4490-baf1-77584bc65a7e", "事件联调固定为 210→220→230→{241→242,251→252}→260→210→010；必须证明 241/251 消费同一操作闭包且槽位可一一绑定。"),
    "EAS-40": ("76bccb15-d9f9-433b-9f00-62714437573d", "Foundation 联调固定为 210→220→241→242→260→210→010；230/251/252 调用数必须为零，260 使用固定编译器并验证拓扑、事务、回滚和字节级确定性。"),
    "EAS-41": ("c1355698-d46f-4b10-b8d0-5098804ecd22", "全链路必须分别覆盖事件数据、事件 ORM 与 Foundation，最终均经 260→210→010；仅 010 固定发布代码可提交正式库。"),
    "EAS-42": ("7e9b0311-2860-47b2-a858-db4caf8f8485", "职责收窄为消费 TRG-V1.0.0，冻结 Foundation 资格、profile 触发、快照缺失、最小基础池与扩容合同；不得重复构建引用图或引入操作闭包/ORM。"),
}

COMMON = """

{marker}

本节覆盖前文一切冲突表述。机器权威输入为 EAS-44 PR 中的 `config/v5-architecture.json`、`config/v5-package-catalog.json` 与 governance manifest；冻结事实版本为 CA-V0.3.0、TRG-V1.0.0。

{specific}

验收必须同时包含：输入版本/哈希、严格输出包合同、批准 Fixture、真实下游 Stub 消费、成功/异常/拒绝路径、未知字段与版本漂移、幂等/可复算、目录和敏感数据边界。仅文件存在不算通过；下游不可消费则返工；事实冲突、敏感数据、正式库写入或无法唯一判定则人工阻断。Git 只保存控制面，真实数据库/原始响应/日志留在本地运行数据面；参考目录只读。
"""


def run(*args: str, stdin: str | None = None) -> str:
    completed = subprocess.run(args, input=stdin, text=True, capture_output=True, check=True)
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changed = []
    for key, (issue_id, specific) in ISSUES.items():
        issue = json.loads(run("multica", "issue", "get", issue_id, "--output", "json"))
        description = issue.get("description") or ""
        if MARKER in description:
            continue
        changed.append(key)
        if args.apply:
            updated = description.rstrip() + COMMON.format(marker=MARKER, specific=specific)
            run("multica", "issue", "update", issue_id, "--description-stdin", stdin=updated)
    print(json.dumps({"apply": args.apply, "changed": changed, "count": len(changed)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
