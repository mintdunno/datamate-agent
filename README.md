# DataMate Harness

An offline-first Vietnamese structured-data QA harness. Development follows
[AGENTS.md](AGENTS.md); competition sources are indexed in [docs/README.md](docs/README.md).

Stages 1–2 implement contracts, partial evaluation, structured tracing, and deterministic
CSV/Excel/SQLite preparation into DuckDB with provenance and content-addressed caching.
Agent SQL tools, PydanticAI/NVIDIA NIM integration, and training evaluation are not implemented yet.
Unrestricted Python execution is outside v0.1 scope.

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
See [Stage 2 preparation](docs/engineering/stage2_data_preparation.md) for contracts,
provenance queries, cache behavior, and limitations.
