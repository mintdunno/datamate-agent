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
