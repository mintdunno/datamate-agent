# DataMate Harness

An offline-first Vietnamese structured-data QA harness. Development follows
[AGENTS.md](AGENTS.md); competition sources are indexed in [docs/README.md](docs/README.md).

Stage 1 implements contracts, partial evaluation, and structured tracing. Data ingestion, SQL tools,
PydanticAI/NVIDIA NIM integration, and training evaluation are not implemented yet.
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
