## IMPLEMENTATION-HANDOFF

- Issue: EAS-120
- Scope: Stage 1B synthetic S0 smoke only. No real questions, Gold SQL, model responses, database files, credentials or CoreBank recoverable data are included.
- Contracts: `contracts/experiments/*.schema.json`
- Fixture: `fixtures/experiments/s0_synthetic/`
- Runner: `scripts/east_baseline_harness.py`
- Callback condition: `SIX_BASELINE_COMMON_HARNESS_CONCURRENCY6_STATIC_SMOKE_PASS`

Databao upstream pin is now uniquely frozen for Stage 1B in `databao_upstream_pin.json` and in the baseline run manifest. The previous synthetic Databao pin is no longer used as source evidence.

## Run Matrix

| Baseline | Stage 1 source pin | Model contract | Synthetic smoke |
| --- | --- | --- | --- |
| DeepEye-SQL | `synthetic-stage1b-deepeye-sql` | `synthetic-local-v1` + `local://synthetic` | pass |
| DataGallery-Text2SQL | `synthetic-stage1b-datagallery-text2sql` | `synthetic-local-v1` + `local://synthetic` | pass |
| JoyDataAgent-SQL | `synthetic-stage1b-joydataagent-sql` | `synthetic-local-v1` + `local://synthetic` | pass |
| Databao Agent | `6a5c676a4e93e52b814459dcb8e48911105cbe33` from `https://github.com/JetBrains/databao-agent.git` | `synthetic-local-v1` + `local://synthetic` | pass |
| ReFoRCE | `synthetic-stage1b-reforce` | `synthetic-local-v1` + `local://synthetic` | pass |
| AutoLink | `synthetic-stage1b-autolink` | `synthetic-local-v1` + `local://synthetic` | pass |

## Evidence Summary

- Hard rules hash: `d00fa6028ff729f2776a7e779db2f530bcba8fba892765c8aa08c6e0aee6b463`
- Concurrent run: 6 baselines, 2 synthetic questions, 12 candidates, 0 failures, cache namespace isolation true, trace isolation true.
- Serial run: 6 baselines, 2 synthetic questions, 12 candidates, 0 failures, cache namespace isolation true, trace isolation true.
- Candidate collection hash for both modes: `850a1195e86972f33afe8f0ad42e85c6a6b602f4eff150e009aa51a0bf328c3d`
- Fail-closed evidence: `fail_closed_evidence.json` covers bad input, leakage field, unknown model ID, missing endpoint, budget exceeded, timeout, illegal SQL output, duplicate attempt, cache namespace collision and EAS-118 forbidden mutation.
- EAS-118 downstream contract consumption: `eas118_contract_consumption_receipt.json`, 0 model calls, no SQL generation, S0 package hash `857bce76268890b9a14fffaa278ffbfb5dd41d27f6b549075a2bce923548c69d`, forbidden `gold_sql` mutation rejected as `S0_LEAKAGE_FIELD`.
- Protocol deviation: missing `PREPARED-WORKSPACE-RECEIPT` is recorded as a deviation only. No retroactive receipt was fabricated.
- Verification: `python3 -m unittest tests.experiments.test_s0_harness`; `python3 scripts/east_baseline_harness.py validate ...`; `python3 scripts/east_baseline_harness.py consume-s0 ...`; `python3 scripts/v5.py check`; `git diff --check`.
