# Milestone 1 execution evidence

Status: **PARTIAL — execution in progress**. This ledger will be finalized after
the bounded training checkpoints; it is not a completion claim.

## Implemented and verified components

Single-agent PydanticAI execution, scoped/restricted DuckDB SQL workers, lexical/fuzzy
schema/value lookup, rule pagination, strict evidence-backed output functions,
active-rulebook calendar helpers, progressive submission/typed traces, resumable
source/config-bound runs, persistent HTTP attempt/token accounting, explicit model
profiles, GPU resource preflight, serving plans and shared platform bootstrap.

CSV/Excel/SQLite preparation qualified **30/30 businesses, 339 tables and 3,997,042
data records**. Real qualification exposed Excel metadata/header/row-width and open
stream failures; generic fixes and synthetic regressions passed. Raw inputs were
not changed. The previous pipeline prepared 22/30; the repaired pipeline prepared all 30.

Latest complete test run: `uv run --locked pytest -q`: **300 passed in 17.27s**,
including preparation-plan integration, snapshot drift and dependency-bound resume.
Ruff lint/format and staged secret/path checks pass. `pip-audit` audited 44 packages:
no known vulnerabilities; the local unpublished package was explicitly skipped.
The initial audit needed a process-local certifi CA override because the host's
REQUESTS_CA_BUNDLE pointed to a missing PostgreSQL bundle; TLS was not disabled.

An independent Sol XHigh review reproduced CTE shadowing and unaliased
PIVOT/UNPIVOT/SUMMARIZE/DESCRIBE source bypasses. The selected-source authorization
fix and regression tests reject them. Resume now also binds contract/dependency
changes. No additional OS sandbox qualification is claimed.

## Evaluation and resource evidence so far

NIM compatibility probes consumed **24 actual HTTP attempts, 31,739 input tokens,
5,276 output tokens**. The final synthetic SQL probe returned number 5 correctly in
4 requests / 29.62 seconds; its expected value was not supplied to the model.
Earlier probes failed on output shape or an inappropriate metadata-column search.
IDs/types are now bound by output functions and business metadata is not model input.

Final suite manifest: `7b3b089a6e9803c1b2467c348877c2aa65b68e381e7a48c9be83c0ac5d2e3709`.
Seed 42: smoke 12/12 businesses, development 72/26 businesses, hard candidates 36/26
businesses, holdout 72/4 businesses. Development and holdout businesses are disjoint.
Smoke/development cover all eight available reference types. The holdout pool has
no table references. Domains are unknown without an explicit mapping; hard difficulty
is heuristic, not human qualification. The earlier draft suite was never evaluated.
Saved solver snapshots contain question/source hashes only; the evaluator manifest
and reference metadata do not enter solving.

Pre-improvement smoke baseline at `fd2212d`: **0 correct, 0 wrong, 12 abstain**,
provisional PA 0.0, no invalid/unresolved records. Runtime 447.80 seconds, mean37.32,
p9547.51 seconds; 12 invoked-tool errors. Failure categories: 7 model-behavior/output
validation failures and 5 cumulative-token limit failures. It consumed **71 HTTP
attempts, 214,119 input tokens and 36,239 output tokens**; every response reported usage.

Optimization round 1: a bounded deterministic initial evidence packet, scope/source
rule lookup and safe SQL feedback. Read-only triage directly established 24,586
cumulative tokens in one failed case, mixed rule paging and missing rule/SQL evidence
in early failures. This changes no model, thinking, sampling or resource budget.
All 308 deterministic tests pass (19.53s). The broad development run is deferred until
this execution blocker is checked, avoiding repetition of the already observed failure.
Comparable smoke verification and subsequent fixed/holdout metrics are pending.
No full 3,000-question evaluation has run. Defaults remain 25 RPM, 1,200 requests
per UTC day and 300 per experiment; no budget was increased.

## External qualification and unresolved gates

- NIM: actual synthetic tool/SQL/output round trip passed; training quality pending.
- Gemma 4 12B, Ornith 1.5 9B and Qwen3 14B: pinned profiles/parser research and
  synthetic configuration tests only. No local model weights/server inference ran.
- Windows host: RTX 3060 Ti 8 GiB; selected BF16 resource requirements fail preflight.
  No automatic quantization/model fallback or large download was attempted.
- Colab/Kaggle and Linux GPU serving: bootstrap/command contracts tested; actual
  platform qualification pending. Docker daemon unavailable; no image was tested.
- Exact official PA and some matching clauses remain unavailable. A direct read
  of the official challenge portal yielded no readable content; no formula was guessed.
  Reports retain unresolved coverage and provisional scoring labels.

## Local commits

- `73f63ba` configurable inference clients and persistent request budgets.
- `de27dcf` structured runtime and durable batches.
- `1cd2d03` generic Excel width/header/resource cleanup correction.
- `ba634e1` pinned model profiles and explicit GPU serving configuration.

Branch: `feat/milestone1-harness`. No merge, push, history rewrite or source-data deletion.
