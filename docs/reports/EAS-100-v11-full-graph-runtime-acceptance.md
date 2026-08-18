## EAS-100 V11 完整运行时图验收报告

结论：通过本地合同验收；交付为独立 v11 identity，未修改 v6～v10。

冻结基线：`9b1fc884ab15aa3df3631ca0c2e9a94ce547091f`。

### 交付范围

- `skills/east-v5-runtime-bootstrap-v11/`：17 个真实 Agent 的运行时身份、000 固定组件 receipt、全图边表、v11 信封、fail-closed 预检与调度器。
- `tests/runtime/test_full_runtime_graph_v11.py`：真实控制器打包后执行 event 全图、Foundation 独立链、双审与数据/ORM 双屏障、17/17 零任务预检拒绝、未知输入拒绝、同 task 重放、受影响链重试；测试复制项目 `.gitignore` 后再提交/打包，覆盖 runtime 源码误忽略回归。
- `.gitignore`：增加 v11 `east_v5/runtime/` 反忽略规则，和 v2～v10 对齐；只放行 Skill 源码，不放行其他本地 runtime 数据面。

000 仅作为 `east-v5-fixed-component-receipt/v1` 固定组件验收，不存在 000 Agent task。调度器只从冻结图表派生后继，不接受调用方指定的下一跳；事件模式要求 170+180 后才从 110 进入 210，260 要求 242+252，Foundation 明确拒绝 230/251/252。

### 输入/输出哈希

| 工件 | SHA-256 |
| --- | --- |
| `SKILL.md` | `7ba4105ccd61df68868ff69116af05ff1c340d291370a13baf7b8c314a35a89f` |
| 全图配置 | `632f5dd42d8e8f44cfada4c879c1e69ccc6f02aa05d2f533f197cd800ed19472` |
| 图控制器 | `c031c387d6010efbf26f5d6e47c3319fda65b14ffb78aaed1dbb5dfd4d545ffd` |
| 入口脚本 | `4c5eda245d32f007ca14b7ce0bafa1aa7a240af1a7b3f21c6f900f76e10f87c7` |
| `.gitignore` | `9553c143662814f1f8517e026e3b0ecaca4689272e9cc87753fc909690608a1a` |
| v11 合同测试 | `4ff19804cfc5f26ff35c966c7838c43aff0bb986bac6ef790c469602e0587ba9` |

Skill 打包器在测试中从干净提交生成确定性 archive；manifest 绑定 17 个 prompt 指令哈希、Skill materialization identity、配置和候选 head。实际平台安装前的 `full-preflight` 还会检查 17/17 UUID/runtime/指令哈希/Skill 绑定、共享 0700 root 和 0600 binding marker；任意缺口不会创建业务 task。

### 测试

```text
python3 -m unittest tests.runtime.test_full_runtime_graph_v11 -v
4 tests, OK

python3 -m unittest discover -s tests/runtime -p 'test_*.py'
116 tests, OK

# 使用项目 .gitignore 的镜像提交；确认 graph_controller.py 位于 git tree 后执行
python3 skills/east-v5-runtime-bootstrap-v11/scripts/pack_skill.py --repo-root <clean-mirror> --head <mirror-head> --output <v11.skill.zip>
OK（manifest `7579b6d67d89b1f5ead2e24f88aa771fb19935828a024a9751736933569cc9bb`）

git diff --check
OK
```

风险与后续：本交付只提供 PR-ready 本地工件，未安装 v11 Skill、未创建任何业务 task、未写正式库。工程助理需在指定 head 创建 PR；Sol 合并并完成平台 17/17 安装、无业务写入 preflight 后，才可重启 EAS-41 真实端到端联调。
