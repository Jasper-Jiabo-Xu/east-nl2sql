## EAS-19 本地验收报告

本交付提供 DATABASE-READ-SNAPSHOT、260 独立 SQLite copy 事务与 010 固定发布接口。所有运行中的 SQLite、快照、copy、回执均位于 `V5_RUNTIME_ROOT`；Git 仅保存代码、JSON Schema、脱敏测试 Fixture 与本报告。

### 合同映射

- 241：结构化 SELECT DSL、表/列白名单、参数绑定、行数上限、SQLite progress 超时、snapshot SHA-256 核验。
- 260：以只读 snapshot 创建独立 copy；仅接受预验证的 INSERT/UPDATE/DELETE 参数批次；单事务提交，失败删除 copy 并报告回滚。
- 010：仅 `publish_release` 可写 `05_新版本交付层/formal.sqlite`；批准、候选哈希、源库哈希、目标版本、幂等键和正式基线版本均必须一致。

### 可复算证据

运行命令：`PYTHONPATH=src python -m unittest discover -s tests -v`。

测试覆盖：快照版本/哈希映射、原库隔离和只读；241 的白名单/未知字段拒绝；260 成功、失败回滚、非法 SQL 与基线冲突；010 幂等、版本冲突、哈希漂移、未批准候选与回执写入失败回滚。真实数据库不参与仓库测试或提交。

### 风险与人工门槛

发布入口不自行触发：调用方仍须提供独立审核已批准的 210 候选和 260 回归哈希。SQLite 进程被强制杀死、宿主磁盘损坏等 OS 级故障依赖 SQLite 原子提交与下次哈希核验；生产接入前应在冻结的 `V5_RUNTIME_ROOT` 完成恢复演练。
