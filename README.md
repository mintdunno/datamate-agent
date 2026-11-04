# DataMate Harness

An offline-first Vietnamese structured-data QA harness. Development follows
[AGENTS.md](AGENTS.md); competition sources are indexed in [docs/README.md](docs/README.md).

Stage 1 establishes contracts and evaluation foundations. Data ingestion, SQL tools,
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
