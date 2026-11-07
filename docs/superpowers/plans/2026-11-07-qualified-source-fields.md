# biz_122 Qualified Source Fields Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. User approved execution on 2026-11-07; continue inline with selective read-only review, without another approval handoff.

**Goal:** Execute one real development-business flow from preparation through bounded SML recovery to independently checked SQL output.

**Architecture:** Retain the nine canonical scopes and READY gate. Add independently audited per-source field projections, with complete source-row denominators and withheld uncertain fields. Extend existing typed financial roles/operators and issue recovery; reuse PydanticAI, artifact receipts and the shared request ledger.

**Tech Stack:** Python, Pydantic, DuckDB, Polars, pytest, Ruff, uv; current explicit Google Gemma profile.

**Spec:** ../specs/2026-11-04-adaptive-normalization-design.md and the user's approved four-step vertical-slice proposal (2026-11-07).

## Global constraints

- Work in the existing authorized feature checkout/branch; raw inputs remain immutable.
- No new framework, Pandas, embedding, vector search, reranking or arbitrary model code.
- Only biz_122 development data; no QA answers/reference labels in preparation/solver or changes to the frozen 80-case labels.
- Do not rearm/reuse the locked SQLite checkpoint as unseen evidence.
- At most five NEW actual Google HTTP attempts, shared across recovery and solving; existing campaign 20 ceiling remains, RPM 1 (never over 40), concurrency 1, no hidden retries/fallback.
- Run focused regressions during implementation; one complete suite at final integration. No automatic five-business/full-QA rerun.
- Main owns security-critical implementation, integration and local Git; agents inspect read-only or propose isolated new files only.

## Review focus

- Incomplete product identity cannot authorize a join, yet must not block unrelated verified source fields.
- Every source row is accounted for; unknown dispositions/null measures cannot silently shrink totals.
- Failed numeric values are withheld entirely, never exposed as strings for CAST/TRY_CAST cleanup bypass.
- Source observations do not assert official revenue, base quantities, or complete business/time coverage.
- Observed line amounts stay intact; unsupported discount formulas, unit bases and rounding remain unresolved.

## Task 1 — Financial contracts and exact arithmetic

Files: preparation/catalog.py, mapping.py, normalization.py, operations.py, v2.py; focused financial helper/tests if useful.

- [ ] Add failing tests for distinct recorded unit price, line/order amount/percent discounts, source-file scoping and wrong-cost rejection.
- [ ] Extend registered semantic roles and model schema consistently; recorded price has no inferred unit basis.
- [ ] Register only typed, source-declared line-amount formulas with qualified dependencies; preserve observed amounts. No implicit zero discount, guessed rate, rounding or conversion.
- [ ] Independently recompute derived amounts in audits; run focused tests/Ruff/diff and commit.

## Task 2 — Independent per-source field publication

Files: preparation/projection.py, audit.py, v2.py, runtime/tools.py; one focused source-fields module if existing files need separation.
Interfaces: project_source_fields(db, bindings) builds trusted source-record descriptors; audit_source_fields(db, bindings) emits normal AuditFindings; existing receipts/catalog publication remain authoritative.

- [ ] Add regressions: unresolved product ID vs usable other fields; complete row coverage; numeric/null withholding; tamper/stale receipts; unapproved joins and SQL cast/alias/CTE bypasses.
- [ ] Build one code-named descriptor per eligible source table, exposing only accepted, independently checked fields plus provenance. Block unknown source records; never drop them into a false complete aggregate.
- [ ] Extend state digest, qualification/publication and catalog validation for the trusted descriptor kind; keep canonical qualification unchanged.
- [ ] Default-deny relationships/joins on source projections without audited relationship receipts; deny numeric casts/arithmetic of source text and unsupported quantity/price measure use. Preserve AST/read-only/resource restrictions.
- [ ] Run focused source/SQL security tests, review diff and commit.

## Task 3 — Typed, issue-specific SML recovery

Files: preparation/mapping.py, orchestration.py, business_recovery.py; existing adaptive contracts reused where possible.

- [ ] Test routing for supported role/format/grain issues, explicit refusal for unsupported identity/structure issues, cumulative policies, one unchanged issue attempt, rollback and common accounting.
- [ ] Extend model proposals only to registered interpretations with exact source citations; the model cannot author an accepted proof or bypass missing source semantics.
- [ ] Retain last audited artifact on validation/provider/budget failure; run focused tests/Ruff/diff and commit.

## Task 4 — Real single-business verification

Artifacts: ignored state/runs; concise README/evidence update.

- [ ] Freeze source/config/code identities and a deterministic source-only canary before model inference; no evaluator reference enters the solver.
- [ ] Run preparation on complete biz_122 records, or an explicitly reported re-audit of unchanged deterministic artifacts if supported. Never mislabel cache/replay as cold.
- [ ] Run direct independently checked source SQL, then the real SML proposal/validation path and a PydanticAI SQL-answer canary under the shared five-attempt cap.
- [ ] Test insufficient-evidence abstention and rollback; record which paths used real vs mocked models.
- [ ] Run final pytest/Ruff/dependency/diff/secret checks, atomic commits and report exact outcomes. A provider/data-evidence blocker yields honest PARTIAL, not a fabricated pass.
