# Stage 2 execution plan

Approved scope: CSV/Excel/SQLite ingestion, deterministic preparation, provenance,
profiling, DuckDB, schema/rule catalog, and content-addressed cache.
No model calls, training evaluation, merge, push, or source-data writes.

## Units

1. Source discovery and streaming loader contracts; isolated loader implementation.
2. Deterministic normalization policies with source evidence and explicit ambiguity.
3. DuckDB storage, profiles, rule/schema catalogs, transactional cache publication.
4. Synthetic regressions, bounded two-business preparation smoke, documentation/review.

## Interfaces and decisions

- load_tables(Path) yields SourceTable(name, headers, header_row, rows, preamble);
  SourceRow(number, values, formulas, formats) retains source coordinates and raw types.
- Only files in the selected business plus its README, active split rulebook and
  calendar enter preparation; labels/questions/secrets are never discovered or hashed.
- Generated SQL uses opaque table/column IDs and quoted identifiers; no LLM SQL
  executes in this stage. Runtime SQL sandbox belongs to Stage 3.
- Preserve raw rows; native scalar normalization is automatic. Locale/date/group
  transformations require explicit policies and evidence. Profiles suggest types,
  never infer business semantics from question IDs or reference answers.
- Cache includes content hashes, policy, tool versions; incomplete/corrupt caches
  fail clearly. Raw inputs and generated outputs must be disjoint.

## Gates

Tests first, synthetic fixtures, full pytest/Ruff and diff review before each commit.
Test traversal/symlinks, malformed files, header offsets, cached formulas, mixed dates,
numeric ambiguity, duplicate scope, provenance, cache dependency changes and isolation.
Independent Sol XHigh reviewer checks high-risk normalization/cache/storage boundaries.
