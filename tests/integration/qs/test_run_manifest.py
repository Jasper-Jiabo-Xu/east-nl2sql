"""EAS-38 机器可读运行清单的合同、自哈希与不变量校验。

清单 `docs/reports/integration/qs/EAS-38-run-manifest.json` 必须：
- 满足 `fixtures/integration/qs/run-manifest.schema.json`；
- `content_sha256` 等于「除自身外全部字段」的规范 JSON SHA-256；
- 区分 `platform_agent_identity`（Multica Agent 持久化记录，Sol 冻结映射）、
  `repo_component_identity`（Git 控制面冻结文件逐字节 SHA-256）与
  `execution_mode=in_process_component_or_stub`；
- 000 登记为 `fixed_component`（agent_uuid=null、not_applicable_no_platform_agent_record）；
- 记录 issue/base/head/run/attempt、逐边 artifact 三元组、路由/重试/耗时，
  且 database_copy 与真实模型调用为不适用/未执行；
- 不写入运行数据面 locator、模型原始响应或敏感数据。
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from jsonschema import Draft202012Validator

from east_v5.governance import load_json, sha256

MANIFEST_PATH = ROOT / "docs" / "reports" / "integration" / "qs" / "EAS-38-run-manifest.json"
SCHEMA_PATH = ROOT / "fixtures" / "integration" / "qs" / "run-manifest.schema.json"

BASE = "4b83d9d513f01484c71b811512339fd7fe8943ec"
# 实现父提交：仅绑定未变的代码/Prompt/合同来源；最终 delivery head 由外部 DELIVERY-RECEIPT 绑定。
IMPLEMENTATION_PARENT_SHA = "0dfadf6393972d6e422384f8a127844c8e59072d"

# Sol 冻结映射：agent_id -> Multica Agent UUID（workspace 持久化记录权威）。
FROZEN_AGENT_UUIDS = {
    "120": "22533152-db59-4a1b-8d01-5f251c618e6b",
    "130": "32074806-be1d-45e0-becb-a03bb3737750",
    "140": "b90b55df-8d99-470e-b179-2028a3db58cc",
    "150": "42e84d5d-140d-43e1-bc57-71e0a55c9ba6",
    "160": "65ccd12e-3c3a-4574-a779-2f65c91cc2f6",
    "170": "2335a247-2257-4a50-9085-5e75dba034ad",
    "180": "9639f8aa-44fa-46ac-9373-ff4085d609fc",
    "110": "67f9cf29-cd45-4ef3-8c87-963fd3ff5898",
    "210": "9b8b6b0f-f4be-41d4-9aa7-9647542ddc78",
    "220": "94511939-5004-4f86-9f03-016d2484ff88",
}
SKILL_UUID = "e4b03e06-c351-449c-9211-e48dae737874"
SKILL_BOUND = {"110", "130", "140", "150", "160", "170"}

# agent_id -> (code 文件, prompt 文件)；用于复算冻结文件哈希并对比清单。
CODE_FILES = {
    "120": "src/east_v5/agents/east_120/extractor.py",
    "130": "src/east_v5/agents/east_130/extractor.py",
    "000": "src/east_v5/agents/east_000/extractor.py",
    "140": "src/east_v5/agents/east_140/extractor.py",
    "150": "src/east_v5/agents/east_150/extractor.py",
    "160": "src/east_v5/agents/160/precheck.py",
    "170": "src/east_v5/agents/170/review.py",
    "180": "src/east_v5/agents/east_180/reviewer.py",
    "110": "src/east_v5/agents/110/scheduler.py",
    "210": "src/east_v5/agents/210/scheduler.py",
    "220": "src/east_v5/agents/220/closure.py",
}
PROMPT_FILES = {pid: f"agents/{pid}/prompt.md" for pid in CODE_FILES}


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RunManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_json(MANIFEST_PATH)
        self.schema = load_json(SCHEMA_PATH)
        self.by_id = {p["agent_id"]: p for p in self.manifest["participants"]}

    def test_manifest_satisfies_contract(self):
        Draft202012Validator(self.schema).validate(self.manifest)

    def test_self_hash_is_canonical(self):
        body = {k: v for k, v in self.manifest.items() if k != "content_sha256"}
        self.assertEqual(self.manifest["content_sha256"], sha256(body))

    def test_issue_and_run_identity(self):
        issue = self.manifest["issue"]
        self.assertEqual(issue["key"], "EAS-38")
        self.assertEqual(issue["base_sha"], BASE)
        self.assertEqual(issue["implementation_parent_sha"], IMPLEMENTATION_PARENT_SHA)
        self.assertEqual(issue["delivery_head_binding"], "external_delivery_receipt")
        run = self.manifest["run"]
        self.assertEqual((run["run_id"], run["qa_id"], run["trace_id"]), ("run-qs", "QA-QS", "trace-qs"))
        self.assertEqual((run["attempt_no"], run["mode"]), (1, "question_sql"))

    def test_no_self_referencing_delivery_head(self):
        """最终 delivery head 不得写入 Git 文件内（会形成不可收敛自引用）。"""
        issue = self.manifest["issue"]
        self.assertNotIn("head_sha", issue)
        self.assertNotIn("delivery_head_sha", issue)
        self.assertEqual(issue["delivery_head_binding"], "external_delivery_receipt")

    def test_all_topology_edges_recorded(self):
        edges = self.manifest["edges"]
        self.assertEqual(len(edges), 12)
        for edge in edges:
            self.assertTrue(edge["producer"] and edge["consumer"])
            self.assertTrue(edge["consumption_conclusion"])
            self.assertEqual(set(edge["output_ref"]), {"artifact_id", "version", "content_hash"})
        self.assertEqual(edges[0]["from"], "010")
        self.assertEqual(edges[-1]["to"], "220")

    def test_routing_authority_frozen(self):
        gov = self.manifest["routing_governance"]
        self.assertEqual(gov["authority"], "110")
        self.assertEqual(gov["question_fact_omission_target"], "150")
        self.assertTrue(gov["review_suggestion_only"])
        self.assertEqual(gov["governance_before"], "EAS-41")
        routes = self.manifest["routing"]["error_routes"]
        self.assertEqual(routes["QUESTION_FACT_OMISSION"], "150")
        self.assertEqual(routes["FACT_PACKAGE_ERROR"], "120")
        self.assertEqual(routes["OBSERVABLE_MAPPING_ERROR"], "130")
        self.assertEqual(routes["QUERY_SPEC_ERROR"], "140")

    def test_non_applicable_statements_present(self):
        na = self.manifest["non_applicable"]
        self.assertIn("不适用", na["database_copy"])
        self.assertIn("未执行", na["real_model_calls"])

    def test_no_runtime_locator_or_sensitive_data(self):
        raw = json.dumps(self.manifest, ensure_ascii=False)
        self.assertNotIn("vnext/03_构建过程层", raw)
        self.assertNotIn("runtime_locator", raw)
        self.assertNotIn("semantic_review_report", raw)  # 不夹带模型原始响应
        for banned in (".env", "BEGIN PRIVATE", "password", "token"):
            self.assertNotIn(banned, raw.lower())

    def test_platform_agent_identity_matches_frozen_mapping(self):
        self.assertEqual(len(self.by_id), 11)
        for agent_id, uuid in FROZEN_AGENT_UUIDS.items():
            participant = self.by_id[agent_id]
            identity = participant["platform_agent_identity"]
            self.assertEqual(identity["kind"], "multica_agent", agent_id)
            self.assertEqual(identity["agent_uuid"], uuid, agent_id)
            self.assertEqual(participant["execution_mode"], "in_process_component_or_stub")

    def test_000_is_fixed_component_not_agent(self):
        identity = self.by_id["000"]["platform_agent_identity"]
        self.assertEqual(identity["kind"], "fixed_component")
        self.assertIsNone(identity["agent_uuid"])
        self.assertEqual(identity["not_applicable_reason"], "not_applicable_no_platform_agent_record")

    def test_skill_bindings_follow_frozen_set(self):
        for agent_id, participant in self.by_id.items():
            bindings = participant["platform_agent_identity"].get("skill_bindings", [])
            if agent_id in SKILL_BOUND:
                self.assertEqual(len(bindings), 1, agent_id)
                self.assertEqual(bindings[0]["skill_id"], SKILL_UUID, agent_id)
                self.assertEqual(bindings[0]["name"], "east-v5-test-driven-development", agent_id)
                self.assertTrue(bindings[0]["binding_enabled"], agent_id)
            elif agent_id == "000":
                self.assertNotIn("skill_bindings", participant["platform_agent_identity"])
            else:
                self.assertEqual(bindings, [], agent_id)

    def test_participant_code_and_prompt_hashes_match_frozen_files(self):
        for pid, code_rel in CODE_FILES.items():
            participant = self.by_id[pid]
            repo = participant["repo_component_identity"]
            self.assertEqual(repo["algorithm"], "sha256")
            # 仅绑定未变的代码/Prompt 来源实现父提交；不得伪装为最终 delivery head。
            self.assertEqual(repo["source_commit_sha"], IMPLEMENTATION_PARENT_SHA)
            self.assertNotIn("commit_sha", repo)
            code_hashes = {item["relative_path"]: item["sha256"] for item in repo["code_files"]}
            self.assertEqual(code_hashes[code_rel], _file_sha(ROOT / code_rel), pid)
            prompt_hashes = {item["relative_path"]: item["sha256"] for item in repo["prompt_files"]}
            self.assertEqual(prompt_hashes[PROMPT_FILES[pid]], _file_sha(ROOT / PROMPT_FILES[pid]), pid)

    def test_000_component_files_and_contracts_hashed(self):
        repo = self.by_id["000"]["repo_component_identity"]
        code_hashes = {item["relative_path"]: item["sha256"] for item in repo["code_files"]}
        self.assertIn("src/east_v5/agents/east_000/query_executor.py", code_hashes)
        self.assertIn("src/east_v5/agents/east_000/safety_gate.py", code_hashes)
        contract_hashes = {item["relative_path"]: item["sha256"] for item in repo["contract_files"]}
        for rel in ("contracts/packages/constraint-query-request.schema.json", "contracts/packages/constraint-asset-package.schema.json"):
            self.assertEqual(contract_hashes[rel], _file_sha(ROOT / rel), rel)


if __name__ == "__main__":
    unittest.main()
