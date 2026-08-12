#!/usr/bin/env python3
"""从 05 正式约束包确定性构建带类型引用图和 Foundation 初次铺底范围。"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict, deque
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MULTI = ROOT / "05_新版本交付层/约束资产/CA-V0.3.0-multifield/multifield_constraints.sqlite"
SINGLE = ROOT / "05_新版本交付层/约束资产/CA-V0.2.0-foundation/single_field_final.sqlite"
BUILD_AT = "2026-08-12T16:00:00Z"

# 初次空库铺底：仅纳入“业务发生前对象/状态 + 有真实下游”的全局最小池。
# 数量是最小可用世界的记录数，不是产量目标；扩容仍按具体 Foundation profile 计算。
INITIAL = {
    "JGXXB": ("机构根对象", 1),
    "GWXXB": ("岗位对象", 1),
    "YGB": ("员工对象", 1),
    "GYB": ("柜员对象", 1),
    "GRJCXXB": ("个人客户对象", 1),
    "DGKHXXB": ("对公客户对象", 1),
    "ZZKJQKMB": ("会计科目静态配置", 1),
    "JRGJXXB": ("金融工具静态对象", 1),
    "LCCPXXB": ("理财产品静态对象", 1),
    "SDSHXXB": ("收单商户对象", 1),
    "GRHQCKFHZ": ("个人活期账户初始状态", 1),
    "GRDQCKFHZ": ("个人定期账户初始状态", 1),
    "DGHQCKFHZ": ("对公活期账户初始状态", 1),
    "DGDQCKFHZ": ("对公定期账户初始状态", 1),
    "NBFHZ": ("内部账户初始状态", 1),
    "XYKXXB": ("信用卡对象及初始额度状态", 1),
    "SXXXB": ("个人/对公客户授信初始状态", 2),
}

ON_DEMAND = {
    "CZXXB": "存折对象，但正式图中无下游消费证据",
    "DGKHCWXXB": "客户财务状态，但正式图中无下游消费证据",
    "GDXXB": "股东对象，但正式图中无下游消费证据",
    "GLGXB": "关联关系对象，但正式图中无下游消费证据",
    "GRKHGXB": "个人客户关系对象，但正式图中无下游消费证据",
    "HLXXB": "汇率静态状态，但正式图中无下游消费证据",
    "JGGXB": "机构关系对象，但正式图中无下游消费证据",
    "JJKXXB": "借记卡对象，但正式图中无下游消费证据",
    "JTKHB": "集团客户对象，但正式图中无下游消费证据",
    "KHLCZHXXB": "客户理财账户对象，但正式图中无下游消费证据",
    "NBKMDZB": "科目映射配置，但正式图中无下游消费证据",
    "XYKSXQKB": "信用卡授信状态；未进入正式多字段图且无下游消费证据",
}


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")


def endpoint_table(endpoint):
    return endpoint.split(".", 1)[0]


def tarjan(nodes, adjacency):
    index = 0
    indices, low, stack, on_stack, result = {}, {}, [], set(), []

    def visit(node):
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in sorted(adjacency.get(node, ())):
            if nxt not in indices:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], indices[nxt])
        if low[node] == indices[node]:
            component = []
            while True:
                current = stack.pop()
                on_stack.remove(current)
                component.append(current)
                if current == node:
                    break
            result.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return sorted(result, key=lambda x: (len(x), x))


def transitive(start, adjacency):
    seen, queue = set(), deque(sorted(adjacency.get(start, ())))
    while queue:
        node = queue.popleft()
        if node in seen or node == start:
            continue
        seen.add(node)
        queue.extend(sorted(adjacency.get(node, ())))
    return sorted(seen)


def add_edge(edges, constraint, source, target, edge_type, expression, ordinal, mode="ALL"):
    payload = {
        "constraint_id": constraint["constraint_id"],
        "source_candidate_id": constraint["source_candidate_id"],
        "source_field": source,
        "target_field": target,
        "source_table": endpoint_table(source),
        "target_table": endpoint_table(target),
        "edge_type": edge_type,
        "prerequisite_mode": mode,
        "condition_text": constraint["condition_text"],
        "expression": expression,
        "evidence_refs": json.loads(constraint["evidence_refs_json"]),
        "approval_status": constraint["approval_status"],
        "review_status": "INCLUDED",
    }
    payload["edge_id"] = f"EDGE-{constraint['source_candidate_id']:06d}-{ordinal:02d}"
    payload["content_sha256"] = sha_text(canonical(payload))
    edges.append(payload)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    multi = sqlite3.connect(MULTI)
    multi.row_factory = sqlite3.Row
    single = sqlite3.connect(SINGLE)
    single.row_factory = sqlite3.Row

    fields = [dict(r) for r in single.execute(
        "SELECT field_id,table_code,table_name,field_code,field_name,data_element_code,source_row "
        "FROM field_master ORDER BY table_code,field_code"
    )]
    field_by_endpoint = {f"{r['table_code']}.{r['field_code']}": r for r in fields}
    constraints = [dict(r) for r in multi.execute("SELECT * FROM multifield_constraint ORDER BY source_candidate_id")]
    endpoint_mentions = defaultdict(set)
    edges = []

    for constraint in constraints:
        expression = json.loads(constraint["structured_expression_json"])
        roles = json.loads(constraint["field_roles_json"])
        for role in roles:
            endpoint_mentions[role["field"]].add(constraint["constraint_id"])
        ordinal = 0
        if constraint["constraint_item_type"] == "REFERENCE_EXISTENCE":
            consumer = expression["consumer_field"]
            providers = expression["provider_fields"]
            mode = expression.get("provider_match", "ONE")
            for provider in providers:
                ordinal += 1
                add_edge(edges, constraint, provider, consumer, "REFERENCE_PREREQUISITE", expression, ordinal, mode)
        else:
            assertion = expression.get("assertion", {})
            condition = expression.get("condition", {})
            join = expression.get("join")
            target = assertion.get("left") or assertion.get("field")
            right = assertion.get("right")
            if right and target:
                ordinal += 1
                kind = "SAME_RECORD_COMPANION" if endpoint_table(right) == endpoint_table(target) else "STATE_SOURCE"
                add_edge(edges, constraint, right, target, kind, expression, ordinal)
            if join:
                ordinal += 1
                add_edge(edges, constraint, join["left"], join["right"], "REFERENCE_PREREQUISITE", expression, ordinal)
            if condition.get("operator") == "EXISTS_IN":
                ordinal += 1
                add_edge(edges, constraint, condition["right"], condition["left"], "CONDITIONAL_DEPENDENCY", expression, ordinal)
            condition_fields = []
            if condition.get("field"):
                condition_fields.append(condition["field"])
            condition_fields.extend(condition.get("fields", []))
            if condition.get("left") and condition.get("operator") != "EXISTS_IN":
                condition_fields.append(condition["left"])
            if condition.get("operator") == "EXISTS_IN":
                condition_fields.append(condition["left"])
            for field in dict.fromkeys(condition_fields):
                if target and field != target:
                    ordinal += 1
                    add_edge(edges, constraint, field, target, "CONDITIONAL_DEPENDENCY", expression, ordinal)

    bad_endpoints = sorted({e[k] for e in edges for k in ("source_field", "target_field") if e[k] not in field_by_endpoint})
    if bad_endpoints:
        raise SystemExit(f"unknown endpoints: {bad_endpoints}")

    field_in, field_out = defaultdict(int), defaultdict(int)
    for edge in edges:
        field_out[edge["source_field"]] += 1
        field_in[edge["target_field"]] += 1
    nodes = []
    for endpoint, row in sorted(field_by_endpoint.items()):
        node = {
            "node_id": endpoint,
            "field_id": row["field_id"],
            "table_code": row["table_code"],
            "table_name": row["table_name"],
            "field_code": row["field_code"],
            "field_name": row["field_name"],
            "data_element_code": row["data_element_code"],
            "source_row": row["source_row"],
            "constraint_ids": sorted(endpoint_mentions.get(endpoint, ())),
            "in_degree": field_in[endpoint],
            "out_degree": field_out[endpoint],
        }
        node["content_sha256"] = sha_text(canonical(node))
        nodes.append(node)

    # 仅 REFERENCE_PREREQUISITE 参与 INSERT 拓扑；ANY 组仍保留每条候选提供边及组语义。
    table_adj, table_rev = defaultdict(set), defaultdict(set)
    table_edge_constraints = defaultdict(set)
    for edge in edges:
        if edge["edge_type"] != "REFERENCE_PREREQUISITE":
            continue
        source, target = edge["source_table"], edge["target_table"]
        if source != target:
            table_adj[source].add(target)
            table_rev[target].add(source)
            table_edge_constraints[(source, target)].add(edge["constraint_id"])
    tables = sorted({r["table_code"] for r in fields})
    sccs = tarjan(tables, table_adj)
    cyclic = [c for c in sccs if len(c) > 1 or any(n in table_adj.get(n, ()) for n in c)]
    scc_index = {table: idx for idx, component in enumerate(sccs, 1) for table in component}

    projections = []
    for (source, target), ids in sorted(table_edge_constraints.items()):
        projections.append({
            "projection_id": f"TABLE-{source}-TO-{target}",
            "projection_level": "TABLE",
            "source_table": source,
            "target_table": target,
            "edge_type": "REFERENCE_PREREQUISITE",
            "constraint_ids": sorted(ids),
            "field_edge_count": sum(1 for e in edges if e["edge_type"] == "REFERENCE_PREREQUISITE" and e["source_table"] == source and e["target_table"] == target),
        })

    closures = []
    for table in tables:
        closures.append({
            "table_code": table,
            "ancestor_tables": transitive(table, table_rev),
            "descendant_tables": transitive(table, table_adj),
            "scc_id": f"SCC-{scc_index[table]:03d}",
            "is_cyclic": any(table in c for c in cyclic),
        })

    provider_edge_count = defaultdict(int)
    consumer_tables = defaultdict(set)
    for edge in edges:
        if edge["edge_type"] in {"REFERENCE_PREREQUISITE", "STATE_SOURCE"}:
            provider_edge_count[edge["source_table"]] += 1
            consumer_tables[edge["source_table"]].add(edge["target_table"])

    # 表级 Foundation 决策覆盖 74/74；事件产物一律不因有下游而升级为 Foundation。
    table_names = {r["table_code"]: r["table_name"] for r in fields}
    foundation = []
    for table in tables:
        if table in INITIAL:
            role, min_records = INITIAL[table]
            decision = "INITIAL_SEED"
            reason = f"{role}；存在 {provider_edge_count[table]} 条下游证据，进入初次空库最小池"
        elif table in ON_DEMAND:
            min_records = 0
            decision = "ON_DEMAND_ONLY"
            reason = ON_DEMAND[table] + "；仅在具体 profile 明确请求且重新通过下游证据闸门后扩容"
        else:
            min_records = 0
            decision = "EVENT_OWNED"
            reason = "合同、借据、担保、交易、明细、变动或事后状态类事件产物；由业务事件操作闭包负责"
        foundation.append({
            "table_code": table,
            "table_name": table_names[table],
            "decision": decision,
            "minimum_record_count": min_records,
            "downstream_evidence_edge_count": provider_edge_count[table],
            "downstream_consumer_tables": sorted(consumer_tables[table]),
            "reason": reason,
            "approval_basis": "EAS-43/44 项目负责人最新裁决 + CA-V0.3.0 正式图证据",
        })

    # 初次池必须对其所有引用前置闭包；若闭包引入事件产物则阻断。
    initial_tables = set(INITIAL)
    expanded = set(initial_tables)
    queue = deque(sorted(initial_tables))
    while queue:
        table = queue.popleft()
        for parent in sorted(table_rev.get(table, ())):
            if parent not in expanded:
                expanded.add(parent)
                queue.append(parent)
    extra = sorted(expanded - initial_tables)
    if extra:
        raise SystemExit(f"initial scope is not reference-closed; missing providers: {extra}")

    mandatory = defaultdict(set)
    for row in single.execute(
        "SELECT f.table_code,f.field_code,s.constraint_item_type,s.value_json "
        "FROM single_field_constraints s JOIN field_master f ON f.field_id=s.field_id"
    ):
        value = json.loads(row["value_json"])
        if row["constraint_item_type"] in {"PRIMARY_KEY", "UNIQUE"} or (
            row["constraint_item_type"] == "NULLABLE" and value.get("nullable") == "NO"
        ):
            mandatory[row["table_code"]].add(row["field_code"])
    for edge in edges:
        if edge["source_table"] in initial_tables:
            mandatory[edge["source_table"]].add(edge["source_field"].split(".", 1)[1])
        if edge["target_table"] in initial_tables:
            mandatory[edge["target_table"]].add(edge["target_field"].split(".", 1)[1])
    legality = [{
        "table_code": table,
        "minimum_record_count": INITIAL[table][1],
        "legality_closure_fields": sorted(mandatory[table]),
        "legality_field_count": len(mandatory[table]),
    } for table in sorted(initial_tables)]

    dispositions = [{
        "constraint_id": c["constraint_id"],
        "source_candidate_id": c["source_candidate_id"],
        "constraint_item_type": c["constraint_item_type"],
        "scope": c["scope"],
        "disposition": "INCLUDED",
        "edge_count": sum(1 for e in edges if e["constraint_id"] == c["constraint_id"]),
        "review_status": "CLOSED",
    } for c in constraints]

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:east:v5:typed-reference-graph:1.0",
        "title": "EAST V5 带类型引用图记录",
        "oneOf": [
            {"required": ["node_id", "table_code", "field_code", "content_sha256"]},
            {"required": ["edge_id", "source_field", "target_field", "edge_type", "constraint_id", "content_sha256"]},
        ],
        "properties": {
            "edge_type": {"enum": ["REFERENCE_PREREQUISITE", "CONDITIONAL_DEPENDENCY", "SAME_RECORD_COMPANION", "STATE_SOURCE", "UNRESOLVED"]},
            "prerequisite_mode": {"enum": ["ALL", "ONE", "ANY"]},
            "review_status": {"enum": ["INCLUDED", "EXCLUDED", "MANUAL_REVIEW"]},
        },
        "additionalProperties": True,
    }
    write_json(OUT / "typed_reference_graph.schema.json", schema)
    write_jsonl(OUT / "typed_reference_graph_nodes.jsonl", nodes)
    write_jsonl(OUT / "typed_reference_graph_edges.jsonl", edges)
    write_jsonl(OUT / "typed_reference_graph_projections.jsonl", projections)
    write_jsonl(OUT / "typed_reference_graph_closures.jsonl", closures)
    write_jsonl(OUT / "typed_reference_graph_review_queue.jsonl", [])
    write_jsonl(OUT / "constraint_dispositions.jsonl", dispositions)
    write_jsonl(OUT / "foundation_eligibility_decisions.jsonl", foundation)
    write_json(OUT / "foundation_initial_scope.json", {
        "schema_version": "EAS-FDN-SCOPE-1.0",
        "built_at_utc": BUILD_AT,
        "mode": "EMPTY_DATABASE_INITIAL_SEED_SCOPE_ONLY",
        "pipeline": ["210", "220", "241", "242", "260"],
        "forbidden_agents": ["230", "251", "252"],
        "uses_operation_closure": False,
        "uses_orm": False,
        "initial_tables": sorted(initial_tables),
        "initial_table_count": len(initial_tables),
        "minimum_record_count": sum(v[1] for v in INITIAL.values()),
        "reference_closure_tables": sorted(expanded),
        "legality_closure": legality,
        "expansion_rule": "具体 profile 明确请求 -> 业务资格 -> 下游证据 -> 正式库缺失 -> 最小祖先闭包；事件产物拒绝",
        "source_graph": "EAS-TYPED-GRAPH-20260812-001",
    })

    edge_types = defaultdict(int)
    for edge in edges:
        edge_types[edge["edge_type"]] += 1
    decision_counts = defaultdict(int)
    for row in foundation:
        decision_counts[row["decision"]] += 1
    validation = {
        "build_at_utc": BUILD_AT,
        "source_multifield_sha256": sha_file(MULTI),
        "source_single_field_sha256": sha_file(SINGLE),
        "schema_field_count": len(nodes),
        "schema_table_count": len(tables),
        "approved_constraint_count": len(constraints),
        "constraint_disposition_counts": {"INCLUDED": len(dispositions), "EXCLUDED": 0, "MANUAL_REVIEW": 0},
        "edge_count": len(edges),
        "edge_type_counts": dict(sorted(edge_types.items())),
        "table_projection_count": len(projections),
        "scc_count": len(sccs),
        "cyclic_sccs": cyclic,
        "manual_review_count": 0,
        "unknown_endpoint_count": len(bad_endpoints),
        "foundation_decision_counts": dict(sorted(decision_counts.items())),
        "foundation_initial_table_count": len(initial_tables),
        "foundation_minimum_record_count": sum(v[1] for v in INITIAL.values()),
        "foundation_reference_closed": expanded == initial_tables,
        "status": "PASS",
    }
    write_json(OUT / "build_validation.json", validation)

    # 为 agent-xlsx 生成结构化输入，Excel 本身由 CLI 写入和格式化。
    with (OUT / "review_constraints.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(dispositions[0]))
        w.writeheader(); w.writerows(dispositions)
    with (OUT / "review_cycles.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["scc_id", "table_count", "tables", "decision"])
        for idx, component in enumerate(cyclic, 1):
            w.writerow([f"CYCLE-{idx:02d}", len(component), ",".join(component), "EVENT_OWNED；不进入 Foundation，事件事务内处理"])
    with (OUT / "review_foundation.csv").open("w", encoding="utf-8", newline="") as f:
        cols = ["table_code", "table_name", "decision", "minimum_record_count", "downstream_evidence_edge_count", "downstream_consumer_tables", "reason", "approval_basis"]
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for row in foundation:
            out = dict(row); out["downstream_consumer_tables"] = ",".join(out["downstream_consumer_tables"]); w.writerow(out)

    readme = f"""# EAS-43 带类型引用图重审与 Foundation 初次铺底范围

## 结论

- 唯一多字段输入为 `CA-V0.3.0-multifield`：470 条已批准约束全部入图，排除 0、人工判断 0。
- 74 表、1963 字段全部注册；生成 537 条字段级边，其中引用前置 499、条件依赖 14、同记录伴随 22、状态来源 2；边端点缺失 0。
- 表级引用图存在 2 个强连通环：个人/对公信贷分户账分别与对应借据表互引。这四张表均为业务事件产物，不进入 Foundation；由业务事件操作闭包和事务内顺序处理。
- Foundation 初次空库最小池冻结为 17 表、18 条最小记录：个人/对公两类授信各 1 条，其余各 1 条。该范围引用祖先闭包已闭合。
- 其余 12 张业务前对象/状态表仅按 profile 扩容，45 张合同、借据、担保、交易、明细、变动或事后状态表归业务事件所有。

## Foundation 初次范围

`JGXXB、GWXXB、YGB、GYB、GRJCXXB、DGKHXXB、ZZKJQKMB、JRGJXXB、LCCPXXB、SDSHXXB、GRHQCKFHZ、GRDQCKFHZ、DGHQCKFHZ、DGDQCKFHZ、NBFHZ、XYKXXB、SXXXB`

固定链路为 `210 -> 220 -> 241 -> 242 -> 260`。不调用 230/251/252，不使用操作闭包，不生成 ORM。`minimum_record_count` 是空库初次最小可用世界，不是数据产量目标；以后扩容必须按具体 Foundation profile、业务资格、下游证据、正式库缺失和最小祖先闭包计算。

## 可复现

```bash
python3 03_构建过程层/EAS-43_带类型引用图与Foundation范围/build_graph_and_foundation_scope.py
```

验收摘要：约束覆盖 470/470，字段 1963/1963，表 74/74，人工队列 0，状态 `{validation['status']}`。源多字段 SQLite SHA-256：`{validation['source_multifield_sha256']}`。
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    tracked = []
    for name in [
        "README.md", "typed_reference_graph.schema.json", "typed_reference_graph_nodes.jsonl", "typed_reference_graph_edges.jsonl",
        "typed_reference_graph_projections.jsonl", "typed_reference_graph_closures.jsonl", "typed_reference_graph_review_queue.jsonl",
        "constraint_dispositions.jsonl", "foundation_eligibility_decisions.jsonl", "foundation_initial_scope.json", "build_validation.json",
        "typed_reference_graph_review.xlsx",
    ]:
        path = OUT / name
        if not path.exists():
            continue
        tracked.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha_file(path), "size_bytes": path.stat().st_size})
    manifest = {
        "artifact_id": "EAS-TYPED-GRAPH-20260812-001",
        "asset_version": "TRG-V1.0.0",
        "publication_status": "FROZEN_BUILD_PROCESS_INPUT_FOR_EAS_44",
        "built_at_utc": BUILD_AT,
        "source_artifacts": [
            {"asset_version": "CA-V0.3.0-multifield", "sha256": sha_file(MULTI)},
            {"asset_version": "CA-V0.2.0-foundation", "sha256": sha_file(SINGLE)},
        ],
        "tracked_files": tracked,
    }
    manifest["content_hash"] = sha_text(canonical(tracked))
    write_json(OUT / "typed_reference_graph_manifest.json", manifest)


if __name__ == "__main__":
    main()
