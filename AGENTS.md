# Agent Instructions — DataMate Harness

> Repository-wide engineering rules for AI coding agents (Codex, Claude Code, and others).
> These instructions govern how changes are implemented, tested, documented, and committed.

## 1. Mission and priorities

Build a reliable, inspectable, dataset-agnostic **DataMate Harness** for Vietnamese structured-data question answering.

Prioritize, in order:

1. **Correctness and compliance** with the official competition documents.
2. **Traceability and reproducibility** of data transformations, tool calls, and answers.
3. **Readable, testable, maintainable code** that another engineer or coding agent can understand.
4. **Simplicity** over speculative abstractions and premature optimization.
5. **Measured performance**, not assumed performance.

Do not sacrifice correctness for a cleaner-looking architecture or a higher apparent answer rate.

## 2. Sources of truth

- Read `docs/README.md` first, if present, and follow its links to the official specifications in `docs/official/`.
- The **official Evaluation, Rule, Data, and Submission specifications** override engineering suggestions in this file or in prompts.
- Each dataset split has its **own `rulebook.md`**. Always load rules from the split being processed; never silently reuse training rules for another split.
- Business `README.md` files and actual source-data evidence provide dataset-specific context. Record unresolved ambiguities instead of inventing rules.
- Keep confirmed competition requirements distinct from design decisions, assumptions, and unverified interpretations.

## 3. Data handling and ground-truth isolation

- **During initial repository inspection**, inventory metadata and inspect only small, representative samples. Never dump large workbooks, all questions, or complete datasets into an agent's context.
- **At runtime**, data loaders must process all required records; inspection limits must not become runtime sampling limits.
- Treat source datasets as **read-only**. Store generated databases, caches, reports, and logs separately.
- Preserve raw values, source provenance, and a clear audit trail of normalization decisions.
- Do not silently drop duplicates, exclude records, or rewrite ambiguous values without a documented rule.
- **Ground truth belongs only to the evaluator.** Never expose reference answers or accepted aliases to the solving agent, its tools, or its runtime context.
- Never hardcode answers or branch on question IDs or business IDs to produce answers.
- Keep model prompts and tool outputs bounded; do not send unnecessary raw business data to a cloud API.

## 4. Architecture and scope

Use a **modular monolith** with explicit responsibilities:

- **Preparation:** file discovery, profiling, rules, normalization, provenance, and DuckDB tables/views.
- **Solving:** PydanticAI agent, typed tools, bounded SQL execution, and structured answers.
- **Verification:** format checks and independently verifiable business constraints.
- **Evaluation:** isolated reference answers, official scoring, per-question diagnostics.
- **Observability:** structured traces with run IDs, question IDs, relevant SQL, tool calls, errors, timing, and model usage when available.

Initial stack: Python, PydanticAI, Pydantic, DuckDB, Polars, openpyxl, RapidFuzz, pytest, Ruff, and uv. NVIDIA NIM is a **development-only** model provider; the final competition inference path must be fully offline and compliant with model eligibility rules.

Do **not** add embedding models, rerankers, vector databases, LangChain, LangGraph, MCP, multi-agent orchestration, distributed services, or arbitrary code execution to v0.1 without a documented, evidence-based requirement.

## 5. Python and naming conventions

- Follow PEP 8; use descriptive, domain-accurate names for packages, modules, classes, functions, variables, and configuration keys.
- Use `snake_case` for functions/variables/modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.
- Use type hints for public interfaces and for nontrivial internal logic. Prefer explicit Pydantic contracts at system boundaries.
- Keep functions focused. Avoid god classes, hidden global state, circular imports, duplicate logic, and abstractions with only hypothetical consumers.
- Prefer pure functions for parsing, normalization, validation, and scoring.
- Fail clearly on invalid input. Do not hide failures behind broad `except` clauses or silent fallbacks.
- Keep business-specific logic separate from generic framework/infrastructure code.
- Do not fabricate successful tool execution, test results, benchmarks, or verification claims.

## 6. Comments and docstrings

Write **source-code comments and docstrings in English**.

- Include concise docstrings for public classes, public non-obvious functions, agent tools, and code with important business rules or invariants.
- Default to **one clear sentence or 1–5 short lines**. Add Google-style `Args`, `Returns`, or `Raises` sections only when they clarify non-obvious contracts.
- Explain **why** a rule or design decision exists, not the obvious mechanics of a line of Python.
- Cite the source rule or document location in comments when implementing sensitive competition semantics.
- Keep comments accurate when behavior changes. Remove stale, redundant, or boilerplate comments.
- Do not add a docstring to every trivial private helper merely to satisfy a quota.

Example:

```python
def parse_vnd_amount(value: str) -> Decimal:
    """Parse a supported VND amount without guessing ambiguous formats.

    Raises:
        ValueError: If the format cannot be interpreted safely.
    """
    ...
```

## 7. Tests and quality gates

- Write focused unit tests for normalization, schema mapping, answer serialization, and official scoring.
- Add integration tests for DuckDB tools, data loading, and representative end-to-end agent workflows.
- Use small synthetic fixtures for deterministic edge cases. Keep real training answers outside runtime test fixtures accessible to the solving agent.
- When fixing a bug, add a regression test that fails before the fix when practical.
- Before committing, run the relevant tests and quality checks, e.g. `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest` (or an appropriately scoped test command).
- Report any checks you could not run, along with the reason. Never claim a check passed without executing it.
- If an external API is unavailable, clearly separate offline/unit test results from untested live integration behavior.

## 8. Git workflow: small, meaningful, traceable commits

Use **Conventional Commits**:

```text
<type>(<scope>): <short, imperative summary>
```

Typical types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `build`.

Good examples:

```text
chore: initialize uv, Ruff, and pytest configuration
feat(data): discover dataset splits and business files
feat(data): preserve provenance during Excel ingestion
feat(agent): integrate PydanticAI tool calling
fix(eval): apply the documented ranking tie-break rule
```

Commit discipline:

1. Break work into **logical, independently reviewable changes**. A milestone may need multiple commits; do not force one commit per stage or one commit per file.
2. Before staging, inspect `git status` and `git diff`. Preserve unrelated user changes.
3. Stage only relevant paths. **Avoid blind `git add .`**.
4. Check staged changes for API keys, `.env`, raw datasets, generated DuckDB files, large caches, and private logs; these must not be committed unless explicitly intended and safe.
5. Run relevant tests and checks; fix failures before committing, or explicitly document a verified blocker instead of misrepresenting the result.
6. Write a precise commit subject. Add a body for non-obvious reasons, trade-offs, behavior changes, or migrations.
7. Record the real commit hash in the stage summary. Never invent hashes or claim a commit was made if it was not.

Do not rewrite existing history, force-push, or push to a remote without explicit user authorization. Do not invent Git author identity or override repository settings to fake an author.

## 9. Implementation workflow

For each stage:

1. **Inspect:** read only the relevant docs and code; inspect training data with strict output limits.
2. **Plan briefly:** state the intended files, interfaces, and acceptance criteria.
3. **Implement:** make the smallest complete, coherent change.
4. **Verify:** run focused tests, lint/format checks, and any relevant integration check.
5. **Commit:** create a logical commit if the repository is initialized and commits are possible.
6. **Report:** summarize what changed, actual checks and results, remaining risks, and the real commit hash.

Do not stop at a plan when implementation is requested. Do not perform a large unverified rewrite just to finish a milestone faster.

## 10. Documentation and decisions

- Keep `README.md` accurate: prerequisites, installation, configuration, run commands, testing, and known limitations.
- Document public interfaces, data contracts, and non-obvious operational requirements near the relevant code.
- For consequential architecture choices, add a short Architecture Decision Record in `docs/decisions/` with **Context**, **Decision**, and **Consequences**. Do not create ADRs for routine changes.
- Avoid duplicating long instructions across `AGENTS.md`, `CLAUDE.md`, and prompts. If `CLAUDE.md` is used, link to this file.
- Prefer concise Markdown headings, lists, tables, and fenced code blocks; keep documentation easy to scan.

## 11. Done means verified

A change is complete only when its behavior is implemented, relevant tests have actually run, documentation is accurate, and the Git history is traceable. Distinguish **implemented**, **tested**, **evaluated**, and **proposed** work in every report.
