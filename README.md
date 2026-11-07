# DataMate Harness

An offline-first Vietnamese structured-data QA harness. Development follows
[AGENTS.md](AGENTS.md); competition sources are indexed in [docs/README.md](docs/README.md).

The structured-only harness uses one PydanticAI agent, DuckDB SQL, deterministic
calendar processing and lexical/fuzzy lookup. CSV/Excel/SQLite preparation preserves
raw records and provenance. Model/backend profiles select NIM or an OpenAI-compatible
local server without changing agent logic. Embeddings, rerankers and vector databases
are outside Milestone 1. Unrestricted Python execution is not exposed.

Implementation, synthetic verification and real model qualification are separate.
See the [Milestone 1 evidence report](docs/engineering/milestone1_report.md) for actual
runs and remaining acceptance gates; profiles alone do not establish model compatibility.

## Development

Use Python 3.13 and uv. Dependencies are pinned in `uv.lock`.

```powershell
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Tests use synthetic fixtures and do not require `.env`, datasets, or network access.
Source datasets, secrets, `.venv`, and generated `state/` are ignored; `docs/` is tracked.

## Implemented boundaries

- Strict question and nine submission types in `datamate.contracts`.
- Evaluator-only references/aliases, JSONL diagnostics, explicit split/version policy,
  per-question outcomes, and incomplete-result safeguards in `datamate.evaluation`.
- Versioned run metadata and question/tool/final-answer JSONL events in
  `datamate.observability`. Writes validate and flush; credentials and reference
  payloads have no fields in the runtime contracts.

See [evaluation semantics and unresolved cases](docs/engineering/evaluation_foundation.md)
and [trace/run contracts and Stage 2 handoff](docs/engineering/stage1_contracts.md).
The scorer deliberately reports unsupported matching cases as unresolved and emits
only a **provisional** PA when the entire batch is resolved. It does not reproduce
an independently verified official PA formula.

## Data preparation

~~~powershell
uv run --locked python -m datamate.preparation --split-root datasets/train --split-id train --business-id YOUR_BUSINESS --cache-root state/prepared
~~~

The CLI processes all records for that business and prints metadata only. It never
opens questions, references, or .env. Its default version-2 registry path validates
source-bound roles, applies registered operations, audits candidate facts and publishes
only qualified projections. `--policy` accepts a version-2 `PreparationPolicy`;
`--mode replay` requires a frozen plan through that policy. Unknown business semantics
remain `PARTIAL`. Registry and replay make no model requests. The `prepare_with_recovery` Python API connects registry preparation to bounded
Level-1 proposals and audited replay; the CLI remains explicitly registry/replay.
Automatic issue selection for arbitrary businesses and real-pilot qualification
remain pending.
`--plans` selects the older explicit `TablePlan` path for reproduction and does not
qualify an artifact for the restricted solving runtime.
See [Stage 2 preparation](docs/engineering/stage2_data_preparation.md) for contracts,
provenance queries, cache behavior, and limitations.

Adaptive normalization is being implemented on `feat/adaptive-normalization`.
The Python API `profile_business_sources` creates a source-only raw database and
profiles in a new workspace, with immutable input copies and supervised CPU
limits (2 GiB DuckDB, 8 GiB sampled process-tree RSS, 45 minutes, 10 GiB output/spill).
It accepts frozen `expected_sources` hashes and never loads QA labels or a model.
Its `RAW_PROFILE_ONLY_UNQUALIFIED` manifest is a diagnostic, not a query-ready
normalization artifact. Failed workers keep resource diagnostics and no success
manifest. Existing preparation CLI behavior remains available during integration.

## Character encoding and Unicode integrity

Version-2 preparation validates CSV decoding before publishing any rows. UTF-8,
UTF-8 BOM and UTF-16 BOM are recognized with strict decoding. Charset-normalizer
only proposes candidates; it cannot authorize a decoder. CP1258 can be selected
with sufficient deterministic Vietnamese evidence. Ambiguous CP1258/Windows-1252
and BOM-less UTF-16 require an explicit hint bound to the original file SHA-256:

```json
{
  "csv_encodings": {
    "data/shop/sales.csv": {
      "encoding": "cp1252",
      "source_sha256": "REPLACE_WITH_SOURCE_SHA256"
    }
  }
}
```

Add these fields to the preparation policy passed with `--policy`. A conflicting
BOM, changed hash, invalid byte sequence or invalid CSV rejects the input; no
`ignore`/`replace` decoding or guessed fallback is used. All records are checked,
including failures after the preview. A file whose encoding cannot be resolved
stops preparation because its schema and coverage are unknown.

The default `text_integrity` policy enables conservative mojibake recovery before
schema matching, NFKC/casefold and working-cell normalization. Ftfy supplies an
explanation; acceptance additionally requires a bounded, reversible, unambiguous
strict codec chain, removal of the detected corruption and an idempotent result.
CSV, Excel and SQLite text, headers, sheet/table labels, metadata and document
quotes are inspected. Raw bytes, raw values, physical identifiers and original
rule quotes remain unchanged. Derived labels and accepted working values feed
schema matching, calendar parsing and qualified SQL consistently.

`meta.text_integrity` records candidates, source coordinates/hashes, policy,
explanations and validation receipts. Existing transform events/lineage record
cell changes, and receipt changes invalidate the preparation audit/cache. Clean
inputs retain their raw identities and logical facts. Set
`"text_integrity": {"enabled": false}` to disable Unicode inspection/repair;
CSV decoding remains strict. `auto_repair: false` records candidates without
accepting repairs. Default limits are 8,192 characters and three encoding layers.

Identifiers and join keys require complete declared SQLite relation evidence,
injective domains, target uniqueness and agreement across all connected foreign
keys. Heuristic names only protect keys; they never authorize repairs. Composite,
implicit-target or otherwise unsupported key proofs remain unresolved. Known
issues affect dependent columns/projections; independent qualified scopes remain
available. Mixed text outside the reversible codec family, replacement characters,
invalid SQLite TEXT bytes and uncertain candidates retain their evidence without
a guessed repair. Formula expressions, paths, executable metadata and rule quotes
are report-only. The legacy `--plans` path does not apply Unicode overlays.

## Bounded adaptive preparation API

`datamate.preparation.orchestration.prepare_with_recovery` first creates/reuses a
registry artifact, then calls a trusted source-only issue selector. A selected
issue receives only its authoritative table bindings and bounded source evidence.
Live calls require `allow_model_calls=True`, an explicit model profile and a shared
`RequestBudget`. The API rejects rates above 40 RPM; target 25 RPM and concurrency 1
remain the development defaults. Registry/replay alone make no model requests.

The model proposes a delta of registered operations. The orchestrator preserves the
registry plan, merges new operations in the validator's prerequisite order and
builds each trial in an isolated directory on the destination cache volume.
Independent source verification and preservation of previously READY scopes are
required before atomic promotion. Rejected/modified trials cannot replace the
baseline. Deterministic replay artifacts may be reused; model answers are not cached.

A source selector and verifier are required callables, not a universal learned
cleaning policy. They must establish applicable rules, ambiguity/collision checks,
repair effectiveness and preservation without evaluator labels. A no-issue result
only describes that selector; it does not certify an entire business. PARTIAL
retains safe baseline output. This API currently handles one selected issue per
invocation; batch-level discovery/recovery and representative real-data qualification
remain separate work.

Each write-once run directory contains structured status, selected profile hash,
proposal/recovery traces, baseline/selected fingerprints, timings and actual ledger
accounting. Source files and existing caches stay unchanged. Model context limits,
format repair, transport retries and campaign/phase limits use existing components.
No generated Python, new framework or inference runtime is introduced.

## Offline normalization evaluation utilities

```bash
uv run --locked python -m datamate normalization-eval --help
uv run --locked python -m datamate normalization-eval report --manifest state/suites/normalization-v1/cases-v1.json --suite state/suites/normalization-v1/references-v1.json --policy config/normalization/pilot.json --offline
```

Use the actual frozen policy path for your campaign. `select`, `references`, `freeze`
and `report` call shared evaluator APIs without loading inference or provider modules.
Outputs are write-once and must remain outside source/dataset directories. Reports
show development aggregates and withhold checkpoint labels and diagnostics. These
utilities do not yet expose baseline/verification execution as CLI actions.

Prepared-artifact evaluation uses the private `artifact-observation-v1` contract.
It reads fixed source coordinates after preparation; assertion targets and expected
values never enter the preparation callback. Legacy reference files retain their
original serialization and digests. Migration is explicit, rechecks original source
proofs and produces a new version; missing artifact support abstains while retaining
verified denominators. No reference-coverage minimum is relaxed by migration.

Evaluator source assertions now have fixed targets for structural dispositions,
typed string/quantity preservation, integer amounts, month-scoped dates and derived
size attributes. `datamate.evaluation.source_assertions.revise_development_suite`
rechecks source witnesses before producing a new reference version; it accepts
only development cases and preserves the checkpoint. Unsupported source claims
stay unresolved. A single ambiguous comma amount needs a native integer price,
quantity and connected exact order/product keys; arbitrary agreement is insufficient.
Header proofs require declarations or a declared-file/header-vector/data witness.

Artifact observation never performs the candidate's missing transformation. A size
assertion requires an actual separate `size` field and preserved product identity;
missing fields abstain. Integral typed Decimal values serialize as integers without
rounding, while fractional values fail an integer assertion. Header classification
reads the candidate's disposition, rather than inferring success from raw metadata.
References and target addresses remain evaluator-only. Real adjudications stay in
ignored state, not source control. When reference assertions change, freeze a new
suite and compare both baseline and final against that same version; old metrics
cannot be carried over. The offline `normalization-eval report --suite ...` command
accepts the explicitly selected new reference file.

## Solving and bounded evaluation

Create evaluator-owned fixed suites once; never pass answers or the evaluator manifest
to solving. Defaults: smoke 12, development 72, heuristic hard cases 36, business-level
holdout 72. Seed, question IDs, coverage and all source hashes are saved under state.

```bash
uv run --locked python -m datamate suites --split-root datasets/train --output-dir state/suites/m1
uv run --locked python -m datamate run --profile config/experiments/nim_reasoning_2k.json --suite state/suites/m1/smoke.jsonl --split-root datasets/train --state-root state/prepared --output-dir state/runs/nim-smoke --env-file .env
uv run --locked python -m datamate report --suite state/suites/m1/smoke.jsonl --references datasets/train/answers.jsonl --rulebook datasets/train/rulebook.md --run-dir state/runs/nim-smoke --scoring-profile train_rulebook_v1
```

Only the report/suite commands import the evaluator. The solver receives question
text, as_of_date, and tools bound to the selected business. Strict output functions
bind IDs and answer types in code; final records use the official submission models.
Output is progressive, fsynced `submission.jsonl`, typed `traces.jsonl`, `run.json`,
and observed Stage 1 `metadata.json` when a Git revision is available. Files stay
outside the source split. Traces retain argument digests, bounded SQL text, actual
tool completion times and evidence IDs; raw tool rows, bound parameter values and
credentials are excluded. SQL literals and final answers may contain source data.
Protect the output directory with host permissions;
Unix mode 0600 does not establish a Windows per-user ACL.

Each question has request/tool/token/deadline limits; each SQL query has AST source
and function allowlists, a read-only restricted DuckDB worker, memory/time/row/byte
limits. This is defense in depth, not a separately qualified OS sandbox. Successful
business queries and exact output evidence are required; verification cannot prove
that every business rule or generated query interpretation is correct.
Automatic context preloading was removed after two bounded smoke qualifications
failed to improve correctness. The model discovers schemas and reads scoped rules
through tools. SQL recovery, source-linked evidence and response-finish diagnostics
remain. The explicit 2,048-output-token NIM experiment profile was tested after
repeated failures at the 1,024-token output cap. It does not change thinking mode
or total question budgets.
The 1,024-token base profile is retained for baseline reproduction. Neither profile
has met the training quality gate; use the evidence report before planning more API runs.

Request accounting is persisted in `state/nim_accounting.sqlite` by default. Every
HTTP attempt, including retries, counts. Defaults are 25 RPM, 1,200 requests per UTC
day and 300 per experiment. SDK retries are disabled; bounded transport backoff
honors Retry-After. Provider-reported usage is retained even when output validation
fails; absent usage is explicitly incomplete. Configure limits with CLI options;
generation/context/tool budgets live in model profiles. The PydanticAI cumulative
token threshold is checked between requests and can overshoot on the last exchange;
HTTP attempt caps remain strict. Do not raise budgets merely
to continue tuning. No prediction cache is shared between runs.

Explicit `--resume` requires matching inputs, profile, implementation, dependencies,
lockfile and budgets. Changed configurations need a new output directory/run; partial
JSONL tails require operator repair. Defaults reject suites above 100 questions;
`--max-questions` permits up to 500. Larger runs additionally require
`--allow-full-evaluation`, reserved for separately approved runs. Nothing automatically
runs all 3,000 questions. Preparing all source businesses is a separate operation.

Use `compare --left .../report.json --right .../report.json --axis model` for models
or `--axis harness` for harness changes. It rejects changed suites, scoring or budget
policies. Reports distinguish abstention, unresolved matching, conditional accuracy,
coverage-bound accuracy, provisional PA, latency, actual API consumption and throughput.
Small-sample intervals are descriptive and do not account for business clustering.
Hard-case difficulty is a lexical heuristic; domain labels stay unknown unless an
explicit mapping is supplied to the suite builder. These limits belong in conclusions.

## Local GPU and cloud notebooks

```bash
uv run --locked python -m datamate preflight --profile config/model_profiles/ornith_1_5_9b_vllm.json
uv run --locked python -m datamate serve --profile config/model_profiles/ornith_1_5_9b_vllm.json
python scripts/bootstrap_gpu.py --repo-root . --action plan
```

Serve only prints an argument plan unless `--launch` is supplied. Launch requires
Linux/WSL and resource preflight; remote artifact downloads additionally require
`--allow-model-download`. Put GPU inference dependencies in a separate environment
using `requirements/inference-vllm.txt`; core dependencies contain no torch/vLLM.
Pass its Python with `--runtime-python`. No Docker is required for development.
Selected revisions/parsers/dtype/quantization are preserved; no failure changes them.

Colab/Kaggle bootstrap calls the same CLI. Use platform secrets/environment variables
for LLM_API_KEY or LOCAL_LLM_API_KEY; `.env` is read only when explicitly supplied to
run. JSON profiles configure model IDs, backend, URLs, paths, sampling and GPU options.
Local device telemetry is sampled and labeled device-wide; remote provider VRAM and
model-loading time remain unmeasured. Actual notebook/server qualification requires
running on that platform. See [model evidence and setup](docs/engineering/model_compatibility.md).

### Source mapping diagnostics

Version-2 preparation records unmapped physical columns and unresolved transforms
in `meta.normalization_issues` and the catalog `issues` list. An unmapped role means
unknown semantic coverage, not proof that the value is corrupt. Diagnostics count
these issues without claiming that all dirty values have been detected. Exact
field declarations in a business README may prove roles; file-specific sections
cannot authorize a different file. Rejected supplied mappings do not hide native
header mappings, and their rejection remains visible in binding diagnostics.

`python -m datamate.preparation --adaptive-mapping` adds one source-validated role
proposal to registry preparation. It requires explicit `--profile`, `--ledger`,
`--run-id` and a fresh `--run-dir`; use the existing split/business/cache arguments.
Set the credential environment variable named by the profile, or supply an explicit
`--env-file`. `--table-id` optionally selects a marked physical table; otherwise
the first table with unmapped roles is selected. Default preparation remains offline.

Adaptive requests use the selected profile's `input_byte_limit`, `context_window`
and `max_tokens`; no fixed 24,000-byte or 2,048-output-token value is required.
The shared request ledger defaults to 25 RPM, 1,200 daily requests and a four-attempt
run ceiling. A mapping invocation sends at most one request with zero hidden retries;
`--phase smoke` or the default `development` consumes the existing campaign quota.
No model, backend, thinking mode or quantization fallback occurs. Source context
includes the full active rulebook and business README once per document, with
numbered source lines and short locally resolved citations. Bounded deterministic
raw samples include source row IDs and explicit omissions; other sampled tables
remain unverified relationship candidates, not accepted joins.
Oversized context refuses before HTTP. Accepted mappings regenerate deterministic
registry operations in an isolated candidate, require unchanged raw staging and no
READY-scope regression, and use the existing audited atomic promotion.

Inspect the run `report.json` for proposal/validation/usage details.
`MAPPING_ACCEPTED` confirms source-backed role changes; it does **not** mean the
business is qualified. Novel wording without registered distinguishing evidence
remains unresolved. A role proposal cannot manufacture unit rules or authorize
identity from name resemblance. Independently validated native source-row receipts
can establish product/unit relations as described below.

The default profiles retain their conservative budgets for reproducibility. To
allow a larger request, choose explicit values in a compatible model profile, for
example `input_byte_limit: 48000`, `context_window: 65536`, `max_tokens: 2048`.
Evidence construction uses that profile's byte budget, not a global 24 KB cap.
The final transport still checks the complete serialized request, including tools
and overhead, before any HTTP attempt. Its byte-based context estimate remains
conservative; exact model-tokenizer counting is not implemented by this change.
No prior run profile, model, reasoning mode, or budget is silently modified.

### Streaming and request deadlines

Profiles explicitly select `streaming` and declare `supports_streaming`; unsupported
streaming fails instead of falling back. Existing profiles remain nonstreaming.
`force_output_tool` and `strict_tool_schema` describe requested backend capabilities;
they must be qualified for the selected endpoint, not inferred from model size.
Default per-phase HTTP timeouts are connect15/read300/write30/pool10 seconds.
`request_deadline_seconds` defaults to360 and bounds one request including budget
wait and streamed body consumption. All are configurable, and token/request
budgets remain independent of timeout settings.

The shared ledger stores at most the last ten request timing traces in snapshots: rate
limiter wait, optional TCP/TLS timing, headers/body/first output timing, finish reason,
completion state and provider usage when reported. SSE chunks are forwarded immediately;
usage is recorded once at EOF/close. Missing usage stays unavailable. Partial streams
are not retried. Neither authorization headers nor generated/source text is stored in
these timing traces. Slow first output does not identify provider queue vs model
prefill/reasoning without additional provider metrics.

Profiles set `stream_usage_mode` to `final` (default) or `cumulative` to match
provider usage events. Cumulative mode uses the SDK replacement accounting so
repeated totals do not consume the token budget repeatedly. The Google Gemma
profile enables it; the transport removes the SDK vendor request extension
`stream_options.continuous_usage_stats` while retaining supported stream options.
The central request ledger still records each actual request once.

Mapping proposals stream only when the selected profile explicitly enables it.
The agent drains the response through usage/finish events before typed acceptance.
Reports distinguish transport timeout phases, deadline, provider errors, local
request limits, absent tool calls, malformed/schema-invalid output and truncation.
Validation diagnostics contain trusted field paths/error types, never rejected
values. Strict tool schemas and forced output tools are separate backend features;
unsupported parameters fail visibly and never trigger a silent fallback.
No automatic format retry is enabled in the bounded mapping qualification.

### Google AI Studio Gemma experiment

`config/model_profiles/google_gemma_4_26b_a4b_it.json` selects
`gemma-4-26b-a4b-it` through the Google OpenAI-compatible endpoint
`https://generativelanguage.googleapis.com/v1beta/openai/`. Set `LLM_API_KEY`
in an ignored `.env` and pass both `--profile` and `--env-file` explicitly. The
profile uses streaming and automatic tool selection; the qualification probe
returned HTTP500 for forced tool choice and valid typed output in automatic mode.
This does not establish that forced tool calling is universally unsupported.
Strict schemas are unqualified. No provider-specific thinking flag is sent, so
thinking behavior is the provider default. Raw text may include inline thought
markers; factual acceptance still requires the typed output tool and source checks.
Google requests use a separate provider ledger, not the exhausted NIM smoke quota.
This is an experimental cloud profile, not offline or competition qualification.


### Native product-scoped unit evidence

Registry preparation can derive unit receipts from complete source-table rows with
accepted identity, unit, factor and base-unit semantics. Exact README field declarations
recognize unit metadata; generic `name` fields and fuzzy identity matches do not establish
product identity. Cross-table name/code links require unique catalog rows and exact keys.
Transaction tables are excluded from catalog authority. Duplicate, conflicting, missing
or conditioned conversion evidence remains unresolved in the dependent quantity scope.
Unknown conversion columns are treated as unsupported scope, rather than silently ignored.

Native CSV, Excel and SQLite numeric factors follow the same lossless Decimal checks.
The registered `normalize_quantity` operation preserves original values and produces
separate `quantity_base`, `quantity_base_unit` and `quantity_conversion_factor` facts.
Foreign factor/product source rows are retained in binding evidence and fact lineage.
Policy files cannot supply an accepted native-proof flag. Source/profile/implementation
hashes invalidate these receipts and caches. Graph results and immutable step identities
are cached only inside the authorized operation state, never as model answers.

These receipts do not qualify an entire business or automatically establish source grain,
complete product linkage, temporal applicability, or query-time measure semantics.


Native catalog identity can derive a product code from an exact, unique name/code
source row without changing the original name. The exported product code stays in
the catalog namespace even when other explicit namespace mappings exist. A source-backed
named order-line grain can then publish a qualified projection. When every projected
row has a verified unit conversion, SQL also exposes `quantity_base` and
`quantity_base_unit`, alongside original `quantity`, `unit` and product names where
available. An incomplete product join cannot publish filtered partial order totals.

The audit reconstructs native relation receipts from raw rows, independently compares
derived product codes, and checks base quantities against source quantities and unit
proofs. Plain signed integer text is independently checked without locale guessing;
other textual numeric representations still require supported independent evidence.
Absence of base quantity columns means complete base-unit totals remain unavailable.


### Bounded recovery across a business

`datamate.preparation.business_recovery.prepare_business_with_recovery` schedules
source-marked mapping gaps on distinct tables, in deterministic dependency order.
Accepted policies accumulate; each table is attempted once, without transport or
format retries. Provider failures or exhausted shared budgets stop scheduling and
retain the last audited artifact. Mapping success is reported separately from business
qualification; unchanged unresolved issues remain visible.

The shared preparation CLI adds `--adaptive-business --max-tables 3` in place of
`--adaptive-mapping`. Supply explicit profile, ledger, run-id, run-dir, env-file and
request/rate limits. `--campaign-policy` accepts a `CampaignPolicy` JSON configuration;
`config/experiments/native_relations_google_campaign.json` fixes the approved experiment
at 20 requests (smoke 4, development 12, final 4). Existing campaign limits cannot be
reset by changing run IDs. Registry/replay modes remain offline and make no model calls.


Unicode finalization/audit materializes key domains only for viable repair candidates
and their complete native foreign-key component. Clean identifier columns and unrelated
components require no key-domain materialization. When a repair is considered, every
required domain value is still checked; no sampled uniqueness or FK proof is substituted.


`config/normalization/native_preparation.json` enables `readme_roles`: deterministic
discovery of every already-supported, exact positive field declaration before model
fallback. It uses the same file-scoped source validator as model-proposed mappings;
negations, ambiguity and unfamiliar meanings remain unresolved. Pass this file through
`--policy` or `PreparationPolicy(readme_roles=True)`. The default remains false for
legacy baseline reproduction; this is an explicit preparation setting, not a model
or backend fallback.


The current native-relation pilot has **Milestone 1 PARTIAL** status and a **FAIL**
strict five-business quality gate. Full-source execution, cell-level improvements,
SML evidence and remaining SQL/financial-contract gaps are recorded in the
[qualification report](docs/engineering/native_relations_report.md).


Financial mappings distinguish recorded `unit_price` from `unit_cost`, and line/order
amount/percentage discounts. A recorded price does not establish its unit basis or
qualify an official revenue measure. Percentage parsing distinguishes explicit source
percent points from native Excel ratio formats; unsupported formats stay unresolved.
An optional registered line-amount formula requires an exact source declaration,
matching quantity/price units and explicit discount dependencies. Its separate
`derived_line_net_amount` never overwrites an observed amount and performs no rounding.

Qualified source observations can now publish independently of the nine canonical
business scopes. Each source projection retains its complete source-row denominator
and withholds an uncertain field in full. Missing product identity therefore does
not hide unrelated audited names/quantities, and cannot authorize a JOIN. SQL on
these source projections currently supports a single-source SELECT with no joins,
CTEs, subqueries, window operations, casts or arithmetic. Quantity aggregation
requires grouping by the recorded unit or filtering one explicit string unit;
parameterized unit filters require grouping. Prices/costs with unknown unit basis
cannot be aggregated. Source observations do not qualify official revenue or
whole-business/time coverage. Raw values and source/projection lineage stay separate;
persisted receipts and exact view definitions reject post-audit changes.

Historical source-field evidence is recorded in the
[source-field report](docs/engineering/qualified_source_fields_report.md).
The [pre-header recovery report](docs/engineering/preheader_recovery_report.md)
records the subsequent cold preparation, SQL checks and remaining live-solve gate.


Unresolved pre-header records are marked as unresolved_preheader_metadata and
scheduled before column-role gaps. The SML receives bounded source witnesses and
a metadata-only output schema. It may select a key/value annotation, while local
validation checks the exact source/profile, pre-header position, established
header, populated cells and raw witnesses. Accepted annotations retain source
provenance and unresolved scope semantics; they never set transaction statuses
or establish whole-business coverage. Invalid proposals retain the prior artifact.
Independent audit rebuilds annotation receipts and checks the copy exposed to SQL
tools before source-field publication.


The solving runtime and batch runner honor the model profile's streaming flag
through PydanticAI streamed tool calls. Streaming uses the existing request/token
ledger, deadlines and per-question limits; it does not switch models or backends.

`solve_question(..., prefetch_source_context=True)` optionally loads bounded split
and business rules plus schemas for at most two allowed table IDs explicitly
named in the question. It uses the existing traced tools and discloses omitted
rules. Record counts and previous query answers are excluded; a successful SQL
query remains mandatory for numeric output. The default is `False`.
