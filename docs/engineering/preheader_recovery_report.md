# Source-bound pre-header recovery: measured development slice

Date: 2026-11-07. Branch: `feat/source-mapping-recovery`.
Scope: train `biz_122`, `DonHang_ChiTiet_1.xlsx / T01-2025`.
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
| Fixed real Agent SQL/answer | BLOCKED: Google endpoint returned HTTP 500 before any tool call |

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

## Budget and external-call stop

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
