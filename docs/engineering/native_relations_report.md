# Native relation recovery qualification — 2026-11-07

Milestone status: **PARTIAL.** The full-source campaign completed; its strict
quality gate is **FAIL**. This report concerns adaptive normalization, separately
from the earlier structured QA benchmark and official accuracy.

## Implemented behavior

- Complete native catalog rows can prove exact product name/code identity and
  product-scoped unit factors. Transaction rows, ambiguous names, duplicate or
  conflicting factors and unsupported conditional columns cannot authorize changes.
- Source quantity/unit stay intact. Verified conversions add separate base quantity,
  base unit and factor values with foreign source-row lineage. Independent audit
  reconstructs the receipts from raw rows and checks the resulting quantities.
- The qualified SQL projection exposes supported measures only. Incomplete joins
  cannot publish filtered partial totals. Existing SQL access controls remain enforced.
- Business recovery visits distinct marked tables in deterministic dependency order,
  accumulates accepted policies, shares actual HTTP budgets and stops on provider or
  budget failures while retaining the last audited artifact. No format retry,
  generated code or silent model/backend/thinking fallback was added.
- Explicit `readme_roles` configuration enables every supported exact positive
  README field declaration before model fallback. Unknown semantics stay unresolved.
- Candidate fact/lineage writes use static columnar DuckDB statements inside the
  original transaction; serialization, constraints and source coordinates are retained.
- Unicode domain validation skips clean keys and unrelated FK components. A viable
  repair still requires complete relevant-domain uniqueness/collision/FK checks.

## Verification already completed

Commands ran in the project virtual environment on Windows:

- `.venv/Scripts/python.exe -X utf8 -m pytest -q`: **937 passed in 308.47 seconds**.
- `uv run ruff check .` and `uv run ruff format --check .`: passed, 155 files formatted.
- `uv pip check`: 50 packages compatible.
- Relevant diff/staged-path/secret checks ran before each local implementation commit.

The deterministic suite covers native conversions, ambiguous catalogs, policy-forged
proofs, original/base measures, namespace consistency, audit tampering, SQL projection
restrictions, bounded recovery and Unicode key-component regressions. Test success
alone does not establish accuracy on dirty full-business data.

## Actual model experiment

Explicit profile: `google_gemma_4_26b_a4b_it.json`, model `gemma-4-26b-a4b-it`,
Google OpenAI-compatible endpoint, streaming, automatic typed tool selection,
provider-default thinking. This is experimental development inference.

Campaign `native-relations-gemma-v1` allows 20 actual HTTP attempts (4 smoke,
12 development, 4 final), concurrency 1, no transport retries. Five attempts were
used: one accepted dataset mapping, one minimal typed-tool health success,
two HTTP 500 failures and one HTTP 429 failure. Subsequent explicit experiments
used 1 RPM, below the initial 5 RPM ceiling. No quota was increased.

The two successful responses reported **13,128 input and 224 output tokens**.
Failed-request usage was unavailable; these are incomplete reported totals, not
exact billed consumption. This campaign made **zero NVIDIA NIM requests**.

The successful source-derived Excel slice contains bounded selected rows, not the
complete business. On the same pre-existing assertions:

| Case | Registry baseline | Accepted mapping + native proof |
| --- | --- | --- |
| Mixed money representations | Failed | Cell-level pass |
| Order inheritance | Failed | Cell-level pass |
| Named-product unit conversion | Blocked | Cell-level pass: 1 source unit produced 12 base units |
| Missing cost | Safety-only pass | Safety-only pass; no invented cost |

A proposed cost role for a price field was rejected by source validation. The
unit assertion implements an already frozen source expectation and was applied
to both artifacts; labels/source selection were not changed after inference.
No reference/QA answer was sent to preparation or the model. Other business
recovery was interrupted by provider failure, so no model-quality conclusion
can be drawn for that business.

## Measured storage bottleneck

Repeated bounded CPU stack samples from the first full-source attempt identified
row-at-a-time `executemany` calls in candidate lineage/fact storage. That run was
interrupted and recorded as **not qualifying**, without deleting generated artifacts
or consuming the checkpoint. One synthetic benchmark compared exact results,
Unicode/JSON/null handling, constraint errors and transaction rollback. At 10,000
rows per table, combined insertion time was 15.265 seconds versus 0.121 seconds
(approximately 126 times faster). This is an in-memory microbenchmark, not a
full-business latency claim. The narrowly scoped replacement passed 27 focused
integration/storage tests before commit; its subsequent complete suite passed 935 tests.
A second sampled bottleneck was per-record receipt lookup on clean rows. Batch
retrieval preserves unique-locator pagination, original ordered events and stale
raw-value guards. It passed 71 targeted tests, including 600 receipts crossing the
500-row page boundary. That interrupted run is also recorded as not qualifying.
The final 937-test suite passed after both corrections. The final cold pass uses
both corrections; no further performance tuning is planned
in this campaign.

## Full-source campaign

Frozen development: four businesses, 80 normalization cases. Locked SQLite
checkpoint: one business, 20 cases. These are source-cell/relationship checks,
not training QA questions. Preparation receives business inputs only, never
case locations, references or expected values. Windows spawn children cannot
load the evaluator references through entrypoint imports.

The paired CPU comparison was fixed before results: legacy `readme_roles=false`
baseline versus explicit `readme_roles=true` final. All other source/config/code
identities are hashed. The fresh run is `qualification-v3`; its baseline started
with the final deterministic test suite running concurrently, so CPU wall time
is descriptive and cannot support a controlled latency improvement claim. Cold
workers retain the existing 45-minute, 10-GiB-disk ceiling, with 4-GiB RSS per worker and concurrency 2. A cache hit cannot qualify
as fresh full CPU audit. The source-bound checkpoint ledger permits one use;
a failed attempt also consumes it. No checkpoint-driven tuning is allowed.

The cold baseline completed all four businesses within the existing limits:

| Business | Elapsed seconds | Peak RSS bytes | Peak disk bytes |
| --- | ---: | ---: | ---: |
| biz_124 | 709.317 | 1,868,996,608 | 650,360,323 |
| biz_106 | 734.847 | 2,171,871,232 | 714,261,465 |
| biz_120 | 517.734 | 1,585,565,696 | 507,404,616 |
| biz_122 | 424.012 | 1,344,929,792 | 398,734,511 |

Baseline: **FAIL**. The frozen suite contains 80 cases, of which 34 have verified
determinate expectations: 22 correct, 5 incorrect commitments and 7 uncommitted.
Across all 80 cases there were 50 abstentions and 3 quarantines. The other 46
references are not silently treated as proven truth or removed from the suite.
Verified determinate success is 22/34 (64.71%); committed correctness is 22/27
(81.48%). These are normalization metrics, not official QA accuracy or PA.

The final cold pass completed all five businesses, with complete resource
measurements, no ready-cache reuse and source-bound audit receipts:

| Business | Elapsed seconds | Peak RSS bytes | Peak disk bytes |
| --- | ---: | ---: | ---: |
| biz_124 | 728.543 | 1,992,118,272 | 696,242,134 |
| biz_106 | 745.526 | 2,242,547,712 | 771,688,748 |
| biz_120 | 805.911 | 1,764,536,320 | 544,764,063 |
| biz_122 | 795.696 | 2,476,081,152 | 918,850,672 |
| biz_123 | 455.689 | 1,825,087,488 | 649,692,432 |

| Development metric | Baseline | Frozen final |
| --- | ---: | ---: |
| Total frozen cases | 80 | 80 |
| Verified determinate denominator | 34 | 34 |
| Correct determinate results | 22 | 27 |
| Incorrect commitments | 5 | 1 |
| Determinate success rate | 64.71% | 79.41% |
| Correctness among determinate commitments | 81.48% | 96.43% |
| Abstentions across all 80 cases | 50 | 49 |
| Quarantines across all 80 cases | 3 | 3 |
| Strict qualification | FAIL | FAIL |

Paired development comparison: 22 cases correct in both, five newly correct,
zero previously correct cases lost, seven correct in neither. The one remaining
incorrect commitment is a **development header disposition**: the frozen reference
expects structural, while preparation quarantines it. This does not establish a
wrong financial calculation, but it still fails the exact contract. No case IDs,
labels or expectations were changed after results.

The locked SQLite checkpoint ran once: **20 cases, four verified determinate**,
two correct commitments, two determinate noncommitments, no incorrect commitment;
17 abstentions and one quarantine across the complete 20. This is 2/4 success,
not 2/20 accuracy. Individual checkpoint failures were not inspected or used to tune.
The ledger has one SUCCEEDED claim: execution completed; this is not a quality PASS.

The small, selected source-check sample and limited verified denominators cannot
establish general QA accuracy, PA, or broad generalization. Two development
businesses have only six verified determinate cases each and the checkpoint has
four, below the policy minimum of ten per business. These coverage gates stay unmet.
The mandatory capability flag remains false. No gate or denominator was relaxed.

## Remaining verified blockers and stop decision

- **All five full artifacts publish zero factual SQL projections; each has nine
  UNRESOLVED business scopes.** Preserved/normalized candidate values in meta.facts
  do not make them available to the production solver. Calendar qualification is
  separate. Full-file execution/audit therefore does not establish business readiness.
- Development metadata distinguishes causes: biz_124 has only six accepted roles
  across multiple tables and no grain/descriptors. biz_106 has an order-header grain
  but its 78,847 total values remain strings; every corresponding parse event reports
  ambiguous or missing distinguishing context. Numeric projection therefore refuses.
  Its detail tables lack an accepted line_net_amount role. Product-name linkage is
  not the immediate gate in those tables because product IDs already exist.
- The mapping/recognition contracts lack **sales unit_price and discount roles**.
  There is no registered amount derivation from explicit quantity/price/discount
  semantics. Existing amounts can be parsed, and SQL arithmetic works once columns
  are published. Additional normalized source fields are otherwise unavailable behind
  the nine fixed canonical scopes. These are harness coverage limits, not evidence
  that a different model would automatically solve the data.
- Whole-business SML recovery remains unqualified. The actual positive model evidence
  covers one source-derived sliced table; full-source paired runs above are registry
  CPU runs, not end-to-end AI runs on five businesses. HTTP 500/429 failures do not
  establish model reasoning quality, and unchanged failures were not repeatedly retried.

Keep the source-proof, explicit README option and two measured storage optimizations.
The paired five-case development improvement comes from enabling supported README
roles on the same core; the native-relation improvement is demonstrated separately
on the fixed sliced examples. The performance microbenchmarks establish storage
cost reductions, independently of answer correctness. Retain the rejected price-as-cost
proposal and quarantined uncertain data rather than inventing semantics. The README
option stays explicit; the legacy default is unchanged. No source-specific fixes,
heuristic money-format choices or checkpoint-driven optimization were added.

Stop this bounded campaign with **Milestone 1 PARTIAL**, rather than claim completion
or broaden contracts during a frozen run. The required remaining work concerns
source-supported financial semantics, independently qualified dynamic source fields
and real SML-flow qualification. A future revision needs fresh development evidence
and an appropriate unexposed checkpoint; this source-bound claim is not resettable.

Original source inventories/bytes and all frozen code/config hashes were checked
again after the run and match. Engineering checks pass; strict normalization quality
and whole-business solving readiness do not. No new full training QA evaluation,
local model loading or Colab/Kaggle/GPU qualification occurred.

## Local commits

- `8ba9dca` native product-scoped unit evidence.
- `4b456ba` native product links, independent audit and qualified unit facts.
- `cbe408c` bounded multi-table recovery with shared request accounting.
- `6f5d3eb` candidate-scoped Unicode key-domain materialization.
- `2e5694b` exact README roles before model fallback.
- `92c22fc` columnar candidate writes with serialization/rollback regressions.
- `87f8fe3` batch Unicode receipt lookup with ordered-state/pagination regressions.

Branch: `feat/source-mapping-recovery`. No merge, push or history rewrite.
Generated evidence remains ignored under
`state/runs/normalization/native-relations-v1/`; raw sources remain read-only.
