# Native Relations and Bounded Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. The user approved inline execution on 2026-11-07; do not add another approval handoff.

**Goal:** Turn source-backed column mappings into verified native product/unit relations, bounded recovery and qualified SQL evidence.

**Architecture:** Extend existing registry preparation and registered operations. Read all relevant source rows deterministically; models propose interpretations only. Keep raw source snapshots, independent audit, scoped qualification and atomic publication.

**Tech Stack:** Python, Pydantic, DuckDB, Polars, pytest, Ruff, uv; existing Google Gemma compatible profile.

**Spec:** ../specs/2026-11-04-adaptive-normalization-design.md plus the user's approved six-step proposal dated 2026-11-07.

## Global constraints

- No source data writes, evaluator/QA input to preparation, arbitrary code, embeddings or architecture rewrite.
- Reuse current feature branch and checkout; parallel agents are read-only. Main owns code, security decisions, integration and Git.
- New Google campaign: at most 20 actual HTTP attempts, smoke 4/development 12/final 4, 5 RPM, concurrency 1, no transport retries or silent model fallback. Preserve prior ledger.
- Four development businesses; freeze before the single SQLite checkpoint. No full training QA evaluation or checkpoint-driven tuning.
- Run small regressions first, full pytest/Ruff at final integration. Baseline HEAD 66cb1f2: 889 tests passed in 226.61 seconds.
- At most two bounded improvement rounds after baseline. Unmet required gates remain PARTIAL.

## Review focus

- Native unit evidence must cite actual current source rows, not generated declaration text.
- Same names with multiple IDs, duplicate/conflicting conversions and incomplete joins must not authorize identity or quantity changes.
- Source/base quantities stay distinct; preserve price semantics and prevent double conversion.
- No false READY, dropped denominators or partially filtered totals.
- Model/API errors and exhausted budgets preserve the baseline and stop repeated unchanged issues.

## Task 1 — Native source relations

Files: preparation/native_relations.py (new), catalog.py, v2.py, tests/test_native_relations.py (new).
Interface: enrich_native_relations(db, profiles, bindings) -> SemanticBindings, deriving only source-checked entries with original row citations.
- [x] Add failing integration tests for native conversion rows, product-scoped factors, ambiguity, duplicate/conflicting rows and policy-forged proof rejection.
- [x] Extend exact README role declarations and source-native relation validation; preserve legacy literal declarations.
- [x] Reuse resolve_units and registered quantity operation; audit source preservation and invalidation.
- [x] Run focused tests/Ruff/diff; commit feat(preparation): validate native product and unit relations.

## Task 2 — Derived facts and SQL qualification

Files: operations.py, v2.py, projection.py, audit.py as required; qualified-runtime regressions.
- [x] Test proven name-to-code linkage and source/base quantity separation; reject missing/ambiguous linkage and double conversion.
- [x] Register only validated dependent operations and expose supported facts with complete provenance; preserve unresolved scope blocking.
- [x] Verify usable qualified projection and forbidden access/partial-total refusals.
- [x] Run focused checks and commit the independently verified unit.

## Task 3 — Bounded business recovery

Files: orchestration.py, existing preparation CLI, mapping.py and adaptive-flow tests.
- [x] Test multi-table progress, one selected unchanged issue attempted once, total request cap, failure retention and offline registry mode.
- [x] Add a bounded dependency-ordered wrapper around current mapping/registered-operation recovery; record issue reasons and outcomes.
- [x] Run focused checks and commit feat(preparation): bound business recovery by issue and request budgets.

## Task 4 — Qualification campaign and report

Files: ignored state campaign artifacts; README and concise engineering evidence report.
- [ ] Freeze source/case/profile/revision identities and baseline; use existing evaluator references only outside preparation.
- [ ] Run deterministic regressions before consuming Google requests; bounded smoke/development, freeze then one checkpoint.
- [ ] Audit full selected source records within existing resource limits. Record exceptions/time/resource stops honestly.
- [ ] Run final pytest/Ruff/dependency checks, inspect staged paths and secrets, commit only relevant source/docs/tests.
- [ ] Report before/after cases, actual HTTP attempts/provider token usage, unchanged sources, unresolved scopes and PASS/PARTIAL status.
