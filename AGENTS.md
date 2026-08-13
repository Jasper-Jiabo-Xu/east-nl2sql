# EAST NL2SQL V5 工程边界

本仓库是 V5 Git 控制面。只提交代码、合同、脱敏 fixture、测试、文档和 manifest 模板；不得提交运行数据、真实数据库、模型原始响应、日志、缓存、密钥或 `.env`。

- `V5_REPO_ROOT`：本 checkout 的绝对根目录，只读输入及代码输出；不得放置运行期制品。
- `V5_RUNTIME_ROOT`：Git 之外的绝对本地运行数据面。V5 制品定位为 `vnext/03_构建过程层/issues/{issue}/{run_id}/{attempt}`。
- `V5_REFERENCE_ROOT`：只读参考源，只能读取；禁止作为 repo 或 runtime 根，禁止写入。

V5 仅使用 `src/east_v5` 的治理实现。旧目录仅为参考，禁止直接复制其生成器、字段策略、表级装配器或 registry。运行 `python3 scripts/v5.py check` 后再提交。
