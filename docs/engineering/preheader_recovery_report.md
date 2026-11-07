# Source-bound pre-header recovery: measured development slice

Date: 2026-11-07. Branch: `feat/source-mapping-recovery`.
Scope: train `biz_122`, `DonHang_ChiTiet_1.xlsx / T01-2025`.

**Latest checkpoint: the complete selected vertical slice PASSED.** Real Gemma
called SQL, received 5,516 verified source records and returned the typed numeric
answer `5516`. Two additional calls respected the user's 30 RPM / 16,000 TPM
constraints using serial requests spaced 61 seconds after response completion.
Earlier HTTP failures and budget stops below are historical checkpoints.
This is a source-observation diagnostic, not official QA accuracy or a complete
Milestone 1 acceptance result. It follows the historical
[source-field report](qualified_source_fields_report.md).

## Implementation and checks

Unresolved pre-header metadata now has a typed SML proposal route. The model
selects an observed record and key/value columns. Local validation reconstructs
source witnesses, validates physical position/header/shape, and retains raw data.
Independent audit reconstructs the receipt and verifies the solver-visible copy.
An accepted annotation does not establish transaction status or export coverage.

The solver now honors configured streaming. A live failure exposed repeated
cumulative usage totals being added by the SDK: 1,772 reported input tokens were
counted as 24,808, prematurely exhausting the unchanged 24,000-token limit.
`stream_usage_mode=cumulative` selects the installed SDK replacement accounting;
the transport strips its unsupported vendor request option. It does not rewrite
stream contents or change the model, thinking mode, backend or token budget.

A subsequent local preflight exposed a trace defect: qualified schema metadata
stores `rows` as an integer, whereas query results store a list. Trace counting
now counts only returned result lists. Two regressions use actual qualified
synthetic preparation and cover prefetched and model-requested schemas. Both
failed with the observed TypeError before the fix; the focused group then
passed 36 tests in 17.03 seconds. No live request was spent on that failure.

An optional `prefetch_source_context` uses existing bounded, traced rule/schema
tools. It excludes record counts and previous SQL results; numeric answers still
require SQL evidence. Its default remains false. Synthetic comparison preserved
the answer/source gate while reducing model exchanges from four to two.

## Real evidence

| Gate | Observed result |
| --- | --- |
| First real metadata proposal | HTTP 200, valid empty proposal; retained as failure |
| Metadata-only refinement | One source-bound annotation accepted by local validation |
| Full cold business preparation | PASS, `cache_hit=false`, 953.08 seconds |
| Raw records and lineage | 215,832 records accounted for; no missing/orphan records |
| Selected source SQL | 5,516 records; all 11 recorded-unit totals match raw-source checks |
| Initial real streamed solving | Stopped before tools by SDK usage overcount; preserved |
| Fixed real Agent SQL/answer | PASS after the bounded final confirmation below; prior HTTP 500 retained |

Preparation peak RSS was 2,522,812,416 bytes; peak disk was 1,017,027,732 bytes.
Preparation/resource measurements completed within the configured limits.
The source inventory and implementation hashes were checked around the cold run.
The raw source files were not changed.

Cold artifact fingerprint:
`2433195b3888c80fb86882eef6ec4cb056f6ef51e085c8cec44e47e75d47ab6e`.

The annotation retains `scope_semantics=UNRESOLVED`. Its phrase must not be applied
to every order as a status. Dates, inherited identifiers, ambiguous monetary
fields and other source sheets retain their independent qualification gates.
The selected result proves neither order counts nor official revenue/coverage.

## Actual offline quality gates

- `.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short`:
  **1,061 passed in 336.84 seconds**, after the trace fix.
- Targeted solver, cumulative-usage, inference-client, streaming and batch tests:
  **30 passed in 9.92 seconds**; these overlap the full suite.
- `.venv/Scripts/python.exe -X utf8 -m ruff check .`: passed.
- `.venv/Scripts/python.exe -X utf8 -m ruff format --check .`:
  passed, 167 files formatted.
- `uv pip check`: 50 installed packages compatible.
- Scoped read-only review found no actionable issue in the cumulative accounting
  and prefetch changes. An earlier metadata review found a copied-receipt integrity
  defect; its independent raw-source comparison and regression were added.

## Budget and external-call stop before the explicit retry

The same persistent run ledger records **four actual Google HTTP attempts** out
of five, at 1 RPM and concurrency one. Known reported usage is **21,742 input and
122 output tokens**. The third stream closed early, so these are observed totals,
not a complete billing assertion. No helper/transport retries, fallback, ledger
reset or increased request budget occurred. No NVIDIA NIM calls were made.

Automatic approval review initially rejected external dispatch because the specific
business payload/destination needed explicit permission. The user then explicitly
approved at most two remaining Google AI Studio requests with the documented
train context. The first continuation stopped locally on the trace defect above.
A separate ignored helper variable collision also stopped before HTTP; its source
and failure were preserved, corrected and its complete preflight passed offline.

The subsequent real request reached Google but returned **HTTP 500 in 1.43 seconds**.
No model output, usage or tool call was returned; the cause is unverified. This
is not evidence of an incorrect model answer. Source/code/profile/manifest hashes
were unchanged around the attempt. No automatic retry or fallback occurred.

The ledger has **one request remaining**. The diagnostic requires a SQL exchange
and a final answer exchange, so no further call was spent without sufficient
budget to finish that two-exchange path. The real fixed cumulative-usage path and
final Agent answer therefore remain unverified. The selected preparation/SQL gates
passed; the full vertical slice remains **BLOCKED**, not PASS.

The approved initial context was approximately 12,892 bytes of train rules,
README/schema/metadata. Later bounded tool outputs could contain business values.
No evaluator answers, 80-case labels, private-test records or credentials enter
the model context.

Ignored local evidence is under `state/runs/preheader-live-biz122-v2/`:
`live/preparation_receipt.json`, `live/proposal.json`, `live/cpu_gate.json`,
`live/solve_actual.json`, `live/solve_budget_after.json`,
`outbound-manifest.json`, `outbound-context-preview.json`,
`protocol-fix/` (local trace failure), `trace-fix/` (local helper failure), and
`trace-fix-v2/` (actual HTTP 500 and immutable before/after checks).
The completed 1,059-test log is `state/runs/preheader-protocol-release.log`.
The post-trace-fix full regression log is `state/runs/preheader-trace-release.log`.
No full QA evaluation, checkpoint reuse, GPU model download or Colab/Kaggle
qualification was performed in this diagnostic.

## Local implementation commits

- `4b87660`: source-bound pre-header annotations and independent audit.
- `61c9c13`: honor streaming profiles during solving.
- `c6047d9`: cumulative streaming usage accounting.
- `7f2dc5b`: optional bounded source evidence prefetch.
- `d6ce321`: trace qualified schemas without confusing metadata counts with result rows.

## User-requested single-request retry

The user explicitly requested another test after the HTTP 500. Exactly one
remaining request was sent with the same model, profile, question, source context,
streaming mode and ledger; no automatic retries or fallback were enabled.

- HTTP **200**, stream completed, finish reason `stop`, **12.19 seconds**.
- Gemma selected `execute_sql` and issued a source-table `SELECT count(*)`.
- Restricted SQL returned **5,516**, non-truncated and matching raw-source evidence.
- SDK and central ledger agree: **6,757 input / 82 output tokens** for this request;
  the cumulative accounting fix therefore also has real-stream evidence.
- Source files, implementation, model profile and prepared manifest stayed unchanged.
- Run accounting is **5/5 attempts**, zero remaining. Known reported cumulative
  usage is **28,499 input / 204 output tokens**; earlier incomplete/missing usage
  still prevents a complete billing assertion.

The agent hit its explicit one-request limit after executing SQL, so its stored
final output is abstention with `UsageLimitExceeded`. This is a request-budget
stop, not another HTTP 500 or a token-counting failure. A subsequent model exchange
is still needed for the final typed answer. The transport/model-tool/SQL checks
passed for this diagnostic; the complete answer path is not yet verified.

Ignored evidence: `state/runs/preheader-live-biz122-v2/http500-retry/` contains
`result.json`, `solve_actual.json`, request-budget snapshots and integrity checks.
No tracked source code changed and no full test suite was rerun for this retry.

## Complete real SQL-to-answer confirmation

The user supplied the provider constraints of **30 RPM and 16,000 TPM** when
continuing the proposed two-call confirmation. The same ledger and immutable
campaign were retained; the run ceiling was extended explicitly from five to
seven for this bounded confirmation. No previous attempt was removed or reset.

Real execution passed all selected diagnostic gates:

1. Gemma called `execute_sql` with the selected source-table `SELECT count(*)`.
2. Restricted SQL returned `5516` without truncation, matching raw-source evidence.
3. The same agent consumed the tool result and returned the typed final answer
   `{"type":"number","answer":5516}` with the bound diagnostic ID.
4. Source files, implementation, model profile, cold manifest and helper hashes
   stayed unchanged throughout the run.

| Request | HTTP / stream | Input tokens | Output tokens |
| --- | --- | ---: | ---: |
| SQL tool selection | 200 / completed | 6,757 | 82 |
| Final typed answer | 200 / completed | 7,391 | 21 |

Total for this confirmation: **2 requests, 14,148 input / 103 output tokens**.
SDK and persistent ledger token totals match. Agent elapsed time was **81.85
seconds**, including rate waiting. The second request began **61.008 seconds
after the first response finished**, or 75.076 seconds between request starts.
Observed consumption stayed below 16,000 tokens even counting input and output
together. No automated retry, model/backend fallback, thinking-mode change or
output-cap change occurred.

The configured local rate was 1 RPM, below the 30 RPM provider ceiling. The core
accounting currently has no generic TPM limiter: this completion used an explicit
serial cadence for this known diagnostic, not qualification of arbitrary prompts
or concurrent workloads against a TPM limit.

The ledger now retains **7 actual attempts** with **42,647 known input / 307 known
output tokens** across the complete experiment. Earlier missing/incomplete usage
still prevents a full billing assertion. The run stops here; no larger QA suite
or full training evaluation was started. This PASS applies to the selected source
observation slice, not official QA accuracy or complete Milestone 1 acceptance.

Ignored evidence: `state/runs/preheader-live-biz122-v2/final-answer/` contains
`result.json`, `solve_actual.json`, budget snapshots, the revision receipt and
all successful after-run integrity checks. No tracked source changed; the earlier
1,061-test and Ruff results remain historical checks of this unchanged code.

## Bounded final smoke conclusion requested by the user

The user requested a small test followed by a conclusion, without another full
five-business pass. The fixed selection was recorded before execution: 26 test
functions, expanding to 44 parametrized tests, on revision `e28f71b403e05ee84be4b2ea87ac0f4e8785229b`.

Actual command: `.venv/Scripts/python.exe -X utf8 -m pytest -q <the 26 fixed node IDs
in state/runs/m1-small-final-20261107/manifest.json> --tb=short`.
Result: **44 passed in 42.30 seconds**. Both `.venv/Scripts/python.exe -X utf8 -m ruff
check .` and `.venv/Scripts/python.exe -X utf8 -m ruff format --check .` passed
(167 files formatted). No source-code edit or additional fix was needed.

The tests exercised synthetic CSV/Excel/SQLite loading, text repair and protected
keys, order-scoped fill/dedup, ambiguous money and source dates, calendar gaps,
explicit amount formulas, proven/ambiguous product-unit relations, accepted/rejected
metadata, source-field publication, restricted SQL bypass refusals, real qualified
schema tracing, rollback/provider failure, and mocked cumulative stream accounting.
The HTTP/model components in this new small set were mocked. No new live API request,
full five-business preparation, checkpoint replay or full QA evaluation occurred.
The real Google run ledger remains at seven attempts. Its previously verified
SQL-to-answer result remains `5516`; it is separate live evidence, not a new live
run in this small test.

**Conclusion: the selected cleaning/recovery/SQL smoke checks PASS; the run found
no new defect requiring a fix. Stop this fix/test round here.** This bounded result
does not replace the approved five-business/reference-coverage gates, so it does
not establish complete Milestone 1 acceptance. No quality gate or frozen reference
was changed. Artifacts: `state/runs/m1-small-final-20261107/manifest.json`,
`pytest.log` and `test-result.json`.
