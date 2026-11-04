# Agent Instructions — DataMate Harness

Repository-wide rules for AI coding agents. Apply these to implementation, testing, documentation, and local Git operations. Prefer the smallest verified change over speculative engineering.

## 1. Mission and priorities

Build a reliable, inspectable, dataset-agnostic harness for Vietnamese structured-data question answering. Prioritize, in order:

1. Correctness and compliance with official competition requirements.
2. Traceability and reproducibility of transformations, tool calls, and answers.
3. Readable, testable, maintainable code.
4. Simple architecture before speculative abstractions.
5. Performance demonstrated by measurement.

Never trade correctness for a higher apparent answer rate.

## 2. Sources of truth

- Read `docs/README.md` first when present, then relevant official documents in `docs/official/`.
- Official Evaluation, Rule, Data, and Submission specifications override suggestions here and in task prompts. Flag conflicts rather than silently choosing an interpretation.
- Load the `rulebook.md` belonging to **each dataset split** at runtime; never apply training rules to a different split by default.
- Use each business's `README.md` and source records as evidence for dataset-specific semantics. Record unresolved ambiguities; never invent business rules.
- Separate confirmed requirements from implementation choices and unverified assumptions.

## 3. Data integrity and isolation

- During repository inspection, inventory files and read only bounded, representative samples. Do not dump whole workbooks, datasets, or question sets into model context. **Runtime loaders must still process every required record.**
- Keep source data read-only. Write normalized tables, caches, logs, reports, and generated databases elsewhere.
- Preserve raw values and provenance (source file/sheet/row where applicable). Make normalization and exclusions auditable; never silently alter ambiguous values or remove duplicates.
- Keep reference answers and accepted aliases exclusively in the evaluator. The solving agent, its prompts, tools, and runtime must never access ground truth.
- Never hardcode answers or special-case question IDs/business IDs to improve scores.
- Bound SQL execution and tool output. Do not leak secrets or unnecessary business data into prompts, traces, commits, or development cloud APIs.

## 4. Architecture and technology

Use a **modular monolith** with clear boundaries:

- **Preparation:** discovery, profiling, split rules, normalization, provenance, DuckDB tables/views.
- **Solving:** PydanticAI, typed tools, restricted SQL execution, structured answers.
- **Verification:** output contracts and independently checkable business constraints.
- **Evaluation:** isolated ground truth, official scoring, per-question diagnostics.
- **Observability:** structured traces for run/question IDs, SQL, tool calls, errors, timings, and available model usage.

Initial stack: Python, PydanticAI, Pydantic, DuckDB, Polars, openpyxl, RapidFuzz, pytest, Ruff, uv. NVIDIA NIM is **development-only**; final competition inference must run fully offline and satisfy official model restrictions.

Do not introduce embeddings, rerankers, vector databases, LangChain/LangGraph, MCP, multi-agent systems, distributed services, or arbitrary code execution into v0.1 without a documented need supported by evidence.

## 5. Code and documentation standards

- Follow PEP 8 and repository tooling. Name functions/modules/variables in `snake_case`, classes in `PascalCase`, and constants in `UPPER_SNAKE_CASE`.
- Use descriptive domain names, public-interface type hints, and explicit Pydantic contracts at boundaries.
- Prefer focused functions and pure parsing, normalization, validation, and scoring logic. Separate business rules from infrastructure.
- Avoid god classes, circular imports, silent fallbacks, broad exception swallowing, unnecessary indirection, and premature abstractions.
- Write comments and docstrings **in English**. Explain non-obvious _why_, invariants, and relevant competition-rule sources; do not narrate trivial code. Keep public non-obvious APIs and agent tools documented without boilerplate.
- Update `README.md` for user-visible setup, configuration, commands, and limitations. Use brief ADRs in `docs/decisions/` only for consequential architectural choices.
- Avoid duplicating instructions across `AGENTS.md`, `CLAUDE.md`, and prompts; link here instead.

## 6. Tests and verification

- Unit-test parsing, normalization, schema mapping, answer serialization, and official scoring.
- Integration-test data loading, DuckDB tools, and representative end-to-end solving paths.
- Use small synthetic fixtures for deterministic edge cases; keep evaluator references inaccessible to the solver. Add regression tests for bug fixes when practical.
- Before committing, run relevant checks, normally `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest`, or justified targeted equivalents.
- Report **actual commands and results**. Distinguish offline tests from untested external API integrations. Never claim a test, tool call, benchmark, or verification succeeded unless it ran.

## 7. Git and branch workflow

Agents may create, inspect, switch, and commit to **local feature branches** without asking each time. Keep `main` stable; use short-lived names such as `feat/data-ingestion`, `fix/sql-validation`, `refactor/agent-runtime`, or `test/ranking`. One branch per meaningful task, not per commit.

For each task:

1. Inspect `git status`, current branch, existing changes, and relevant documentation/code. Preserve unrelated user work.
2. Briefly state the change scope, affected interfaces, and acceptance criteria; then implement rather than stopping at a plan.
3. Make a small coherent change; verify with relevant tests and lint/format checks.
4. Review `git diff` and stage **only relevant paths** (avoid blind `git add .`). Check staged content for secrets, `.env`, source datasets, generated databases, caches, and private logs.
5. Commit logical, independently reviewable units using **Conventional Commits**, e.g. `feat(data): preserve Excel provenance`. Use a body for important trade-offs.
6. Report branch name, changed behavior, executed checks/results, remaining risks, and real commit hashes.

**Safety:** Never discard user changes, fabricate Git metadata, override author identity, rewrite history, force-push, push remotely, or merge into `main` without explicit authorization. Do not delete unmerged branches or remote branches without approval. After an authorized merge, verify the merged state; safely delete a merged local branch only when appropriate. For parallel agents, use separate Git worktrees; never concurrently edit the same working tree.

## 8. Definition of done

A task is complete only when the requested behavior is implemented, relevant verification has actually run, documentation is current, and local changes are traceable. Clearly label work as **implemented**, **tested**, **evaluated**, or **proposed**; report blockers honestly instead of claiming completion.
