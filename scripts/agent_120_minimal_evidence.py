#!/usr/bin/env python3
"""Minimal runtime evidence for Agent 120."""
import json, sys, hashlib
from pathlib import Path

import os
ROOT = Path(os.environ.get("V5_REPO_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / "src"))

from east_v5.agents.east_120.extractor import FactExtractor

fixture = json.loads((ROOT / "fixtures/penalty/matched.json").read_text())
extractor = FactExtractor(ROOT)

# 1. Input validation
extractor.validate_input(fixture)
print("INPUT_VALIDATION: PASS (PENALTY-SOURCE-PACKAGE schema + hard rules)")

# 2. Deterministic extraction
result = extractor.extract(fixture)
print(f"EXTRACTION: {len(result['source_facts'])} facts extracted")

# 3. Output validation
extractor.validate_output(result, fixture)
print("OUTPUT_VALIDATION: PASS (PENALTY-FACT-PACKAGE schema + cross-checks)")

# 4. Fact summary
for f in result["source_facts"]:
    txt = f["original_text"][:60].replace("\n", " ")
    print(f"  {f['penalty_fact_id']} | {f['fact_type']:25s} | must_preserve={f['must_preserve_in_question']:12s} | {txt}")

# 5. Uncertainties
for u in result["uncertainties"]:
    print(f"  uncertainty: type={u['type']} review={u['needs_human_review']}")

# 6. Content hash
content = json.dumps(result, sort_keys=True, ensure_ascii=False)
content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
print(f"CONTENT_HASH: {content_hash}")

# 7. list_only fixture test
fixture2 = json.loads((ROOT / "fixtures/penalty/list_only.json").read_text())
extractor.validate_input(fixture2)
result2 = extractor.extract(fixture2)
extractor.validate_output(result2, fixture2)
print(f"LIST_ONLY_TEST: {len(result2['source_facts'])} facts, {len(result2['uncertainties'])} uncertainties")

# 8. text_only fixture test
fixture3 = json.loads((ROOT / "fixtures/penalty/text_only.json").read_text())
extractor.validate_input(fixture3)
result3 = extractor.extract(fixture3)
extractor.validate_output(result3, fixture3)
print(f"TEXT_ONLY_TEST: {len(result3['source_facts'])} facts, {len(result3['uncertainties'])} uncertainties")

print("ALL_MINIMAL_RUNTIME_TESTS: PASS")
