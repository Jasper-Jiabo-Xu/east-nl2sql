## EAS-125 Databao native launcher acceptance

### Scope

This package implements the one currently runnable route in the frozen six-route
matrix: **Databao Agent**. It neither runs real S0 nor calls a real provider.
The other five routes remain classified as non-runnable and must fail closed
before any key, database, or model action.

### Evidence and launch contract

- Frozen upstream revision: `6a5c676a4e93e52b814459dcb8e48911105cbe33`.
- The launcher verifies that revision plus the SHA-256 values for the benchmark
  source-chain reference, `agent`, `Thread.code`, Lighthouse result extraction,
  and the LLM configuration source before importing Databao.
- Input is closed to `qa_id`, question, public schema, and a regular non-link
  read-only DuckDB file. Gold SQL, answers, business events, private KB, and
  write-database fields cannot enter the contract.
- The source API chain is `bao.agent(...).thread().ask(question)` followed by
  `thread.code()`. Databao itself re-attaches the supplied DuckDB database as
  `READ_ONLY`; EAST does not supply prompts, retrieval, SQL, or execution
  semantics.
- `DEEPSEEK_API_KEY` is read only at runtime and temporarily mapped to the
  upstream OpenAI-compatible client environment. It is never written to the
  request, output, contract, or fake-endpoint body.

### Hermetic consumption result

Using the prepared Python 3.11 environment and pinned source tree, the test
created a one-table irreversible fixture database and launched the actual
upstream Thread API against a local loopback OpenAI-compatible endpoint. The
endpoint observed two request bodies (query then submit); the native output was
`thread.code()` SQL. No network provider, S0 database, model response, or key
was persisted.

### Verification

```text
test_databao_launcher + test_route_compatibility_matrix: 4/4
test_s0_harness + test_autolink_overlay + route matrix: 23/23
scripts/v5.py check: pass
git diff --check: pass
secret scan of changed experiment paths: pass
```

### Residual boundary

The launcher is an independently callable closed native contract. It is not
yet wired back into the earlier generic six-route harness; that harness's
shaped-stub path is not evidence of native completion. Review and integration
must preserve the dual-axis matrix and remove or fail-close that obsolete path.
