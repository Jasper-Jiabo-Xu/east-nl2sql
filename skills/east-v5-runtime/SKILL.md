---
name: east-v5-runtime
description: 执行 EAST NL2SQL V5 事件双通路或 Foundation 链时使用，强制校验机器架构、包合同、模式边界和写前拒绝。
---

# EAST V5 Runtime

1. 读取 `config/v5-architecture.json` 和 `config/v5-package-catalog.json`，校验 manifest 与输入哈希。
2. 根据 `mode` 选择唯一合法链；Foundation 不得出现 230、251、252、ORM、操作闭包或独立 ODS 资产。
3. 事件模式下，230 的唯一操作闭包必须由 241 与 251 共同消费。
4. 241 才能生成/修改绑定数据；242 只验证。251 只生成无业务值 ORM；252 只验证并冻结哈希。
5. 260 只操作正式库 copy；Foundation 仅调用固定编译器生成参数化 INSERT。010 之外不得提交正式库。
6. 未知字段、版本漂移、哈希漂移、非法路径、EVENT_OWNED Foundation 写入、依赖缺失或环依赖均在任何写入前拒绝。
