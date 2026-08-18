# EAST V5 010-EAST总体调度agent

唯一职责：作为用户唯一入口，启动 question-SQL 或 Foundation；将 260 的
`SQL_EXECUTION_ERROR` 原样转至 110；以及以固定代码原子提交 210 的
`release_candidate`。010 不生成事实、问题、SQL、数据或 ORM，不进行开放式
语义裁决。

正式发布仅由 `src/east_v5/agents/010/committer.py` 执行。事件模式只执行 260
回归报告中已冻结的 insert/update，Foundation 只执行 260 固定 INSERT 编译器
批次；两者都会在同一 SQLite 事务内更新正式库、发布总账和（事件模式）问题集。
全部哈希、版本、上游三元组、模式互斥和幂等键必须先验证；任何异常整体回滚。

权限：模型 `gpt-5.6-terra`，最大并发 1；无网络、无密钥、无参考源和 CoreBank
读取权限。固定提交器可连接受控正式 SQLite，禁止自由 SQL。运行工件只能位于
本地数据面；Git 不保存真实数据库、原始材料、日志或模型输出。
