# EAS-44 迁移矩阵

| 资产/职责 | 旧口径风险 | V5 冻结口径 | 机器入口 |
|---|---|---|---|
| 230 操作闭包 | 仅面向 251 | 同一包同时供 241/251 | `config/v5-package-catalog.json` |
| 241 数据生成 | 缺少事件操作闭包 | 事件消费 220+230+snapshot；Foundation 不消费 230 | `config/v5-architecture.json` |
| 242 验证 | 可能混入生成/INSERT | 只验证数据与约束 | Agent/Prompt 合同 |
| 251 ORM | 可能绑定业务数据 | 仅结构化受限代码，不含业务值 | Agent/Prompt 合同 |
| 252 验证 | 独立 ODS 职责 | AST/API/事务/哈希/dry-run/顺序 | Agent/Prompt 合同 |
| 260 Foundation | ORM 或自由 SQL | 固定、版本化、确定性 INSERT 编译器 | `src/east_v5/foundation/compiler.py` |
| 历史生成器 | 仍可能被引用 | 运行时禁止，残留扫描拒绝 | `scripts/v5.py check` |

受影响 Issue 合同以 2026-08-11 人工裁决为最高优先级。本次迁移目标：EAS-13、16、18、20、29—36、39—42；EAS-45 负责后续统一调度与最终验收。
