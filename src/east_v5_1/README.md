# EAST V5.1 — 已有 question 场景的管道适配

## 目标

V5.1 适配"已有 question"的场景，简化管道入口，以 question 为起点重新生成高质量 SQL 和 data。

## 管道变化

### V5 原路径（从处罚原文出发）

```
监管处罚原文 → 120 → 130 → 140 → 150 → 160 → 170/180
```

### V5.1 新路径（已有 question 出发）

```
已有question(+人工审核修正)
  → 110(问题归一化/意图提取) [升级：增加 question 解析、歧义检测]
  → 000(约束检索) [复用，已加脱敏过滤]
  → 140(查询规格) [适配：输入源从 120+130 改为 110+000]
  → 150(SQL生成) [复用]
  → 160(预检) [复用]
  → 170/180(复核) [复用]
  → 260(回归验证)
```

## 关键变化

1. **120 可跳过**：已有 question 已编码监管意图，不需要从处罚原文重新提取事实
2. **110 升级为入口**：原职责是 query binding，新增 question 归一化
   - 解析 question 中的表名、字段名、谓词条件、返回字段
   - 检测共性问题（对公/个人是否明确、借据/分户账/明细是否清晰、返回字段是否完整）
   - 有歧义时标注并提示人工修正
3. **140 输入源变化**：从 120+130 改为 110(归一化后) + 000(约束)
4. **130 角色可选**：不再从 120 处罚事实映射，而是将 question 中的隐含事实映射为可观察事实

## 复用关系

V5.1 复用 V5 的治理层（`east_v5.governance`、`east_v5.artifacts`）和已有 Agent 实现
（000/110/140/150/160/170/180/210/220/241/242/260 等），但新增/修改入口逻辑。

## 工程边界

- V5 代码、合同、测试保持原样，不修改 `src/east_v5/`
- V5.1 代码放 `src/east_v5_1/`
- V5.1 合同、schema 放 `contracts/v5_1/`
- 提交前 `python3 scripts/v5.py check` 通过

## 两阶段 Gold（EAS-105）

`gold_lifecycle.py` 是 V5.1 的只读控制面：语义通过后的产物只能为
`semantic_candidate`；同一 lineage 的真实 V5 `260 database_copy_regression`
通过后才可为 `execution_confirmed`；同时消费真实 V5 `210 release_candidate`
和 `010 release_receipt` 后才可为 `formal_released`。它不会执行或伪造
260，也不会写正式库。

六路 baseline 首轮候选仅以 opaque ID 和 SQL/锁定证据哈希保存，按
`candidate_set_hash` 锁定。260 后缺陷通过稳定的 adjudication 路由回到
150（经 110）、241、251 或 `blocked_manual`；SQL 路由要求新 attempt，并
使先前的解释、闭包和执行证据失效。
