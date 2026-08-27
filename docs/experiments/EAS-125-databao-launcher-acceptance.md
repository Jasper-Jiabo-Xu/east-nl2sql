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
endpoint observed query then submit request bodies; the native output was
`thread.code()` SQL. No network provider, S0 database, model response, or key
was persisted. A two-question test runs through both serial and concurrent
harness entry points: each question has a distinct `qa-<sha256-prefix>`
namespace and its own `attempt-01`. A transport retry for the first question
uses only its `attempt-02`; the next question still begins at its own
`attempt-01`. Clear question text is never used in a filesystem path.

### Verification

```text
complete `tests/experiments` with prepared runtime: 21/21
scripts/v5.py check: 399 passed, 8 runtime-dependent tests skipped
git diff --check: pass
secret scan of changed experiment paths: pass
```

### Unified harness boundary

`s0_harness` consumes the dual-axis matrix before it reads credentials,
worktrees, databases, or models. DeepEye, DataGallery, JoyDataAgent, ReFoRCE,
and AutoLink emit `S0_METHOD_INCOMPATIBLE` with zero calls; only Databao invokes
the isolated launcher and is converted into the common prediction fields
(SQL hashes, attempt, calls/tokens, latency, trace, and stable failure code).
The former pinned shaped-stub implementation has been removed. The Databao
fake-endpoint matrix fixes one model-unavailable call, one invalid-output call,
one timeout request, and at most three transport attempts; traces contain only
redacted digests. The harness callback condition records the dual-axis,
five-route fail-closed plus Databao multi-question consumption boundary rather
than implying six successful native routes.
