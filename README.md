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
opens questions, references, or .env. For explicit normalization, provide --plans
with table-key -> TablePlan JSON and current source-evidence fingerprints.
The solving CLI accepts --plans with a businesses -> table-key -> TablePlan bundle;
the same evidenced policies and their fingerprints apply to every compared model.
See [Stage 2 preparation](docs/engineering/stage2_data_preparation.md) for contracts,
provenance queries, cache behavior, and limitations.

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
