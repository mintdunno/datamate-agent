# Character Encoding and Mojibake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. User authorized selective parallel implementation; parent owns integration, security and Git. Steps use checkbox syntax.

**Goal:** Deterministic source-bound decoding and audited mojibake repair across ingestion and qualified SQL.
**Architecture:** Small decoder and Unicode repair helpers, immutable raw staging plus accepted derived text. Reuse existing profile/binding/operation/audit boundaries and scoped qualification.
**Tech Stack:** Python 3.13, charset-normalizer, ftfy, DuckDB, Polars, pytest, Ruff, uv.
**Spec:** docs/superpowers/specs/2026-11-06-text-integrity-design.md

## Global Constraints
- No lossy decoding, source modification, LLM, datasets in context, merge/push/history rewrite.
- Preserve source hashes, physical identifiers, raw text and exact document proofs.
- Protected-key candidates need full-domain collision/relational proof, not heuristics alone.
- Partial failures qualify only unaffected scopes; retain denominators.

## Review Focus
- Late invalid bytes must fail strict decoding before publication (Task 1).
- Legitimate accented names/symbols and combining marks remain unchanged (Task 2).
- Header repair cannot masquerade as raw evidence; collision blocks only its dependencies (Task 3).
- Key repair cannot silently merge entities or invent relation evidence (Task 3).
- Tampered metadata receipt or cached policy cannot retain READY (Task 4).

### Task 1: Strict source decoding
Files: preparation/decoding.py, loaders.py, minimal SourceTable metadata, tests/test_decoding.py.
Interface: select_csv_decoder(path, encoding=None, expected_sha256=None) -> CsvDecodingDecision with encoding/source_sha256/reason/candidates; load_tables accepts trusted source-bound hint.
- [x] Write failing BOM, UTF16, CP1258/1252, ambiguity, late-error, CSV structure and source-drift tests; run RED.
- [x] Implement bounded candidate detection and full strict validation; freeze codec across iterator; preserve source values.
- [x] Run targeted tests, full regression, Ruff/diff/secret gates; commit independently.

### Task 2: Reversible Unicode candidates
Files: preparation/text_integrity.py, tests/test_text_integrity.py, pyproject.toml/uv.lock.
Interface: TextIntegrityPolicy; inspect_text(text, policy=None, protected=False) -> TextDecision (before/candidate/status/steps/reason/digest).
- [x] Write clean/Vietnamese/combining, single/multilayer, mixed, false-positive, lossy, protected and bounds tests; run RED.
- [x] Implement ftfy candidate generation with strict allowed reversible codec plan, no other cleanup; protected candidates stay unaccepted.
- [x] Run target tests, full regression and gates; commit dependency/core unit.

### Task 3: Source-bound derived text integration
Files: preparation/models.py, storage.py, profiling.py, catalog.py, operations.py, v2.py; integration tests.
Interface: one persisted integrity receipt relation with raw locators, decisions and acceptance proof; effective labels are separate from physical source labels. Working cells consume only accepted values.
- [x] Write RED integration tests for repaired header/value through SQL, raw identity/quotes, mixed formats, collision and unaffected scopes.
- [x] Stage raw first; generate receipts before semantic matching; validate derived header proof; apply working values consistently; preserve raw-equality dedup.
- [x] Generate protected-key candidates; accept only complete injective source-declared relation verification; otherwise record scoped unresolved.
- [x] Test SQL filtering, key domains, batch equivalence and raw snapshots; gates then commit.

### Task 4: Audit, reproducibility and final verification
Files: preparation/audit.py, diagnostics.py, pipeline.py, runtime/tools.py only if needed, README.md; regression tests.
- [x] Write RED tests for receipt tampering, missing receipts, cache policy/library drift and scoped publication.
- [x] Bind receipts to audit/qualification/cache; expose bounded diagnostics and source/derived provenance through existing catalogs.
- [x] Full pytest, Ruff check/format, dependency audit, staged diff/secrets; one fresh whole-branch review and one bounded fix pass.
- [x] Record actual checks, limitations and atomic Conventional Commits; no merge/push.

## Verification evidence

Full integration gate: `python -m pytest -q --tb=short`: 814 passed in 197.60s.
Ruff check and format: pass (138 files). Dependency audit: 49 dependencies, no known vulnerabilities.
One fresh review, one bounded fixpass; existing Windows audit-abort regression also fixed by closing the native SQLite schema connection.
No NIM calls, full training evaluation, source edits, merges or pushes. Coverage is synthetic fixtures and repository regressions; unseen production businesses remain unqualified until actually prepared/audited.
