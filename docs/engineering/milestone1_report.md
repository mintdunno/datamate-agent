# Milestone 1 execution evidence

Status: **PARTIAL — bounded execution stopped at the acceptance limits**.
Engineering implementation is verified below; training quality and actual multi-SLM/GPU
qualification did not meet the requested acceptance gates. No completion claim is made.

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

Latest complete test run: `uv run --locked pytest -q`: **307 passed in 17.79s**,
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
The round-1 revision passed 308 deterministic tests (19.53s); its measured result
follows. Test counts change when discarded experimental code/tests are removed.
Round 1 verification: 0 correct, 1 wrong, 11 abstain; provisional PA -0.0833.
It used 32 requests / 173,989 input / 23,775 output tokens in 369.79 seconds,
with two tool errors. This is a resource improvement, **not an accepted quality
improvement**. Five one-request failures each consumed exactly1,024 output tokens.
The sole unanswerable prediction was wrong; no claim of successful absence verification.

Qualification round 2 explicitly selects `config/experiments/nim_reasoning_2k.json`:
same model/backend/thinking/sampling and 24k total-token budget per question, with
2,048 output tokens instead of1,024. Request/day/run/context limits are unchanged.
Generation-budget differences preclude treating this as a pure harness comparison.
SQL lexer failures now produce bounded repair feedback; finish reasons are recorded
without response text, headers or credentials.
Round 2: **0 correct, 1 wrong, 11 abstain**, provisional PA -0.0833, 49 HTTP
attempts, 265,209 reported input tokens and 39,612 reported output tokens in
590.49 seconds. Usage was present for 48/49 responses; totals are incomplete.
All 49 response metadata records were observed: 48 tool_calls, one not_provided,
no reported length finish reason. Ten questions hit usage limits and one the context
byte guard. The larger output cap passed this diagnostic check; correctness did not improve.

**Stop decision:** two consecutive major rounds without meaningful correctness
improvement. General optimization stopped. Automatic context preloading was removed
in `def7144`; safe SQL repair/lexer handling, scoped rule lookup, evidence tracing
and response diagnostics were retained. Frozen final checkpoints use the explicitly
qualified 2,048-output-token profile, unchanged 24k cumulative tokens/question,
eight model requests/question, 16 tools, 120 seconds/question and API budgets.
Development and locked holdout executed concurrently in separate run directories
with the shared process-safe limiter. Latency is therefore not isolated performance
qualification. No holdout examples are used for tuning. Their aggregate results
are recorded below with budget-limited coverage. Both stopped with explicit
`budget_exhausted` status and CLI exit code 1; neither crashed or silently retried forever.
No full 3,000-question evaluation has run. Defaults remain 25 RPM, 1,200 requests
per UTC day and 300 per experiment; no budget was increased.

## Frozen final checkpoints

| Run | HTTP-attempted questions / fixed suite | Attempted businesses | Correct / wrong / abstain on full suite | Missing predictions | HTTP attempts | Reported input / output tokens |
| --- | --- | --- | --- | --- | --- | --- |
| Development | 45 / 72 | 26 / 26 | 0 / 0 / 72 | 27 | 300 | 896,690 / 171,473 |
| Locked holdout | 44 / 72 | 4 / 4 | 0 / 0 / 72 | 28 | 300 | 842,522 / 156,930 |

All attempted questions also abstained. Missing predictions are scored as abstain;
72 resolved outcomes therefore does **not** mean 72 questions ran. There are no
invalid records or unresolved outcomes in these reports. Provisional known-correct
accuracy and provisional PA are both 0; abstention is 100%. Conditional accuracy
is undefined because no substantive answer survived verification. These results do
not establish generalization, a useful answer rate or successful value-matching
coverage. No before/after improvement on development or holdout is claimed.

Development latency: total 2,744.90 seconds, mean 61.00, p50 60.31, p95 92.42;
throughput 0.01639 completed traced questions/second; 43 invoked-tool errors.
Holdout latency: total 2,591.48 seconds, mean 58.90, p50 59.26, p95 92.75;
throughput 0.01698 questions/second; 46 invoked-tool errors. Runs overlapped,
so these are observed shared-limit measurements, not isolated backend benchmarks.

Question failure categories:

| Code | Development | Holdout |
| --- | --- | --- |
| usage_limit_exceeded | 32 | 26 |
| tool_retry_exhausted | 9 | 6 |
| split_business_rules_required | 1 | 6 |
| exact_scalar_answer_required | 2 | 3 |
| output_retry_exhausted | 0 | 2 |
| budget_exhausted | 1 | 1 |

The dominant measured blocker is repeated tool/context work exhausting resource
budgets: 52/58 usage-limit failures reported at least 24,000 tokens; 17/58 had at
least eight HTTP attempts (groups overlap). The PydanticAI cumulative token limit
is checked between requests, so 24,000 is a stop threshold, not a strict consumption
ceiling. Final usage-limit traces reached 30,540 reported tokens. Hard HTTP attempt
caps and request/output size bounds remain enforced. Other failures involve tool
repair, missing rule-source evidence and output validation. These are not all attributed to model reasoning. No further
prompt/tool/agent changes were made from holdout results. The hard suite was built
but not run; neither 200–500 nor full 3,000 evaluation was attempted.

## Total NIM accounting

All probes and training runs used **776 actual HTTP attempts**, below the configured
1,200/day development cap. Each final checkpoint stopped at 300, target RPM stayed 25,
and no request/day/run budget was raised. The ledger contains 774 responses with
usage: **2,424,268 reported input tokens + 433,305 reported output tokens**.
Two attempts lack usage (one in round 2, one in final development); these token sums
are incomplete, not exact billed consumption. Final development has 299/300 usage
responses; holdout has 300/300. All final 600 response-metadata records were captured:
development 297 tool_calls / 2 stop / 1 not_provided; holdout 297 tool_calls / 3 stop.
No final response reported a length finish reason. Provider-hosted weights have no
immutable revision selector, and inference VRAM is unavailable from this endpoint.

## External qualification and unresolved gates

- NIM: actual synthetic tool/SQL/output round trip passed; training quality failed.
  Frozen final checkpoints completed within their caps; no quality improvement was established.
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
- `fd2212d` fixed source-bound suites, benchmark reporting and portable CLI.
- `4db8fce` bounded evidence experiment and SQL recovery (automatic preload later removed).
- `9b65727` SQL lexer recovery and response finish-reason accounting.
- `def7144` remove ineffective automatic context preload; 307 tests passed.

Branch: `feat/milestone1-harness`. No merge, push, history rewrite or source-data deletion.

## Acceptance limits and reproducibility

Deterministic tests qualify contracts, preparation, SQL safety, request accounting,
source-bound resume, suite isolation and CLI behavior. They do not establish
business-question accuracy or actual local model/server compatibility. A valid
abstain is safe failure handling, not evidence of a useful competition solution.

The same fixed smoke suite supports the baseline/round-1/round-2 comparison above.
There is no pre-improvement development/holdout baseline, and no claim of a paired
before/after improvement on those suites. The final checkpoints measure one frozen
revision (`def7144`) and one explicit NIM profile. No cross-model ranking is possible
without real local-model runs. NIM does not expose measured inference VRAM here.

Artifact locations are ignored deliberately: `state/suites/m1-fixed-v1/manifest.json`,
label-free suite JSONL/snapshots, `state/preparation_qualification.json`,
`state/security/pip-audit.json`, `state/nim_accounting.sqlite`, and each run's
`submission.jsonl`, `traces.jsonl`, `run.json`, `metadata.json`, `report.json`.
Results and SQL traces may contain source data; they are not committed to Git.
Reproduce on the recorded checkout/data fingerprint with the shared CLI, selected
profile and explicit budgets. Do not rerun locked holdout for targeted tuning.

Remaining known limits include heuristic Excel header discovery; no universal
business semantic mapping; evidence/source checks that cannot prove complete
rule reading, join correctness or absence; unresolved official PA/matching clauses;
no measured quantized model fit; and unverified Linux/WSL/Colab/Kaggle model serving.
The SQL worker is restricted DuckDB in a disposable process, not an independently
qualified OS sandbox. Raw source files were not edited or deleted.

## Gate ledger

| Gate | Evidence / limitation |
| --- | --- |
| Structured-only architecture | One PydanticAI agent, deterministic preparation, DuckDB/SQL and lexical/fuzzy tools; no embedding, reranker or vector store. |
| Data coverage | All 30 businesses, all three input formats; 339 tables / 3,997,042 records qualified. |
| Contract / safety checks | 307 deterministic tests, Ruff, known-secret/diff checks; SQL bypass regressions and evaluator isolation included. |
| End-to-end model execution | Real NIM synthetic SQL/tool/output round trip; training checkpoints persisted separately. |
| Reliability | Finite request/token/tool/deadline/SQL limits, durable progress, central HTTP accounting and explicit resource failures. |
| Training quality | Not passed: smoke baseline and both improvement rounds established no correctness improvement. Final checkpoints also yielded no correct answers and stopped short of full question coverage. |
| Official scoring | Partial: source-confirmed train semantics only; PA remains provisional and unsupported cases unresolved. |
| Multi-model benchmark | Runner/configuration boundary implemented; only NIM ran. No measured multi-SLM comparison. |
| Gemma / Ornith / Qwen | Researched pinned profiles/configuration tests; no weights/server inference qualification. |
| Cloud GPU portability | Shared bootstrap/CLI and capacity preflight implemented; Colab/Kaggle and Linux/WSL model serving unverified. |
| Final competition packaging | Docker daemon unavailable; final offline image/runtime/model eligibility needs separate actual qualification. |

Overall acceptance cannot be PASS while the quality, multi-SLM and actual GPU
qualification evidence is missing. Stopping after bounded unsuccessful improvement
rounds is deliberate; it does not turn an unmet gate into success.
