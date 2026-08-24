# V5.1 合同与 Schema

本目录存放 V5.1 管道适配的合同和 schema 定义。

## 目录结构

- `question-input.schema.json` — V5.1 已有 question 输入包 schema
- `normalized-question.schema.json` — Agent 110 归一化输出包 schema
- `query-spec-from-question.schema.json` — Agent 140 的 question/000 到 canonical query-spec 兼容映射合同

## V5.1 管道合同链

```
question-input → 110(归一化) → normalized-question + 000(约束) → 140(查询规格) → 150(SQL) → 160(预检) → 170/180(复核) → 260(回归)
```

V5.1 合同复用 V5 治理层（`governance-manifest.schema.json`、`v5-package-catalog.schema.json`），
仅新增/修改 V5.1 特有的输入输出合同。
