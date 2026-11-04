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

## Execution record

- b7efe06: allowlisted discovery, shared streaming contracts, dependencies; 141 tests.
- 461c43d: loaders and evidenced normalization; 170 tests for completed units
  (the then-unimplemented preparation integration test was explicitly excluded).
- 4863759: native SQLite keys and unresolved formula handling; RED/GREEN regressions.
- d6496f5: DuckDB, profiling, rule/schema catalogs and snapshot-bound cache.
- CLI/documentation unit: final hash is reported in the completion reply.
- Full suite before final documentation: 194 passed; Ruff check/format passed.
- Independent Sol XHigh review: no remaining Important findings after fixes;
  reviewer independently ran 56 targeted tests and exact Decimal storage checks.

Bounded real-data preparation smoke (not question solving or evaluation):

| Business | Source formats | Tables | Input/output records | Repeat cache hit |
|---|---|---:|---:|---|
| biz_123 | SQLite | 17 | 268911 / 268911 | yes |
| biz_124 | XLSX + CSV | 13 | 305973 / 305973 | yes |

Both use the conservative default policy; no records were excluded. Semantic
normalization policies were verified with synthetic fixtures, not inferred from labels.
Each run verified source stability and artifact integrity; no answers/questions
entered discovery, hashing, snapshots, catalogs or databases.

Rulings:

- Preserve ambiguous text and require source-bound normalization policies. Cost:
  Stage 3 must resolve semantic mappings rather than assume every column is numeric.
- Reject unsupported/ambiguous layouts and live SQLite WAL/journal snapshots.
  Cost: additional format support or a checkpointed input copy may be needed later.
- Keep private snapshots until parsing completes, then remove them before publishing.
  Cost: temporary disk duplication and content hashing during cache preparation.
- Cap exact profile cardinality at 1000, then report a lower bound. Cost: no exact
  high-cardinality statistic without a later explicit query.
- Custom Role activation was not available in the exposed spawn interface
  (agent_role=null, inherited Sol High on the unoverridden check). Use explicit
  Terra Medium implementation / Sol High audit / Sol XHigh review as requested.
  The logical repository root used for all work is D:/Github Sources/datamate-agent.
