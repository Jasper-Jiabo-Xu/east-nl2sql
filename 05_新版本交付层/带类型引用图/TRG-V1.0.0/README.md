# EAS-43 带类型引用图重审与 Foundation 初次铺底范围

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

验收摘要：约束覆盖 470/470，字段 1963/1963，表 74/74，人工队列 0，状态 `PASS`。源多字段 SQLite SHA-256：`5e2753235bf1e47c9d015a05d28c45a20d17a26789d1c88498f80b6ec43577bb`。
