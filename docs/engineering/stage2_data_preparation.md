# Stage 2 — deterministic data preparation

## Scope and entry point

prepare_business(split_root, business_id, cache_root, split_id=..., plans=...) prepares
one explicitly selected business. The CLI is python -m datamate.preparation; it
requires split-root, split-id, business-id and cache-root. Optional --plans reads
a JSON mapping from table keys to TablePlan objects. CLI output contains only counts,
cache status, fingerprints and an artifact path. No question solving/evaluation runs.

No source/business IDs select special logic. Each business receives a separate DB
and catalog. Only selected business CSV/XLSX/SQLite files and Markdown notes, the
active split rulebook and calendar enter discovery/hashing/snapshots. Root-level
questions, answers, environment files and unrelated businesses are never opened.
Unsupported business files and linked paths fail clearly rather than being followed.

## Loading and schema discovery

- CSV: UTF-8/BOM, bounded delimiter sniff (comma/semicolon/tab), quoted multiline
  records, strict malformed/ragged-row rejection. Ordinals are CSV logical records.
- Excel: streaming openpyxl, all worksheets/all data records, native booleans/dates,
  workbook epoch handling, original sheet/row/column positions, styles and formulas.
  Formulas/macros/external links are never executed. Missing formula caches are
  explicit unresolved events; they cannot be forward-filled as blank cells.
- Excel headers: deterministic heuristic in the first 20 rows, title/preamble retained.
  Header selection is marked inferred. Numeric-only/undetectable headers fail clearly;
  arbitrary spreadsheet layouts still require additional evidenced parsing support.
- SQLite: immutable read-only connection, query_only, trusted_schema off, extensions
  disabled. Visible/generated columns, declared types, native rowid/composite keys
  are retained. Virtual tables and uncheckpointed WAL/journal snapshots are rejected.

SourceTable/SourceRow are streaming contracts: consume a table's rows before advancing
its containing iterator. SQLite row ordinals are scan positions; record_key separately
retains available native identity. Excel values are library-decoded native values;
the immutable original file plus source hash remains the authoritative raw artifact.

Schema catalog maps opaque table IDs and c0001-style column IDs to source names,
headers, physical column positions, declared types, row counts and profiles. Generated
SQL never interpolates source headers/table names as executable SQL.

## Normalization and rules

Default auto policy preserves text and native scalar meanings; it never assumes
that a numeric-looking identifier or text amount is currency. Native floats use
decimal representation; unrepresentable/nonfinite values are flagged or rejected.
ColumnPlan explicitly selects integer/decimal/date/datetime/boolean/text interpretation,
locale, shorthand suffix policy or year context. Non-auto interpretation requires
Evidence(source, sha256, locator) bound to a current allowed source/document.

For example, a sales.csv source with worksheet/table name sales has a key like
data/shop/sales.csv::sales. Columns are identified by c0001, c0002, etc., keeping
duplicate/ambiguous original headers intact. Policies are data, never executable code.

TablePlan supports explicitly evidenced order grouping, first-row group inheritance
and exact within-order deduplication. Conflicting group metadata is preserved with
group_conflict; unknown formula-derived keys reset inheritance. Non-order duplicates
remain. Excluded duplicates keep raw records and duplicate_of links. These utilities
do not establish a universal valid-orders filter, recompute recorded sale-time COGS,
infer business joins, or truncate records by today's date.

Parse failures produce null plus parse_failed, never fabricated zero. Tagged raw
values, original formula metadata, row identity and policy/evidence remain available.
Typed DuckDB decimals preserve supported precision; precision >38 fails rather than
rounding silently. Aware datetimes use TIMESTAMPTZ; mixed aware/naive values remain text.
pytz is present because DuckDB's Python TIMESTAMPTZ reader requires it.

Rule catalog preserves Markdown sections, complete text, source digest and line
spans. Text is untrusted evidence. Lexical search is bounded by result count and
does not guarantee retrieval of all applicable general/industry clauses; Stage 3
must control context size and include rule dependencies.

## Database and provenance

- Business tables: typed normalized values plus _source_row_id and _source_row.
- raw_records: tagged raw/normalized values, source ordinal/native record keys,
  record kind (header/preamble/data/calendar), formulas, formats, events, exclusions.
- source_manifest: only allowed source paths and hashes.
- schema_catalog and rule_catalog: source-linked metadata and rule text.
- calendar_data: active split calendar with original header mapping in its catalog.

Join a business table's _source_row_id to raw_records.row_id, then its table_id
to schema_catalog.table_id for file/sheet/table and column evidence. Schema/profile
JSON is also exported in catalog.json. Profiles cover every data record using
bounded batches and Polars type aggregation. Distinct counts are exact only up to
the documented cap (1000); otherwise a lower bound is explicitly labelled.
Input rows = output rows + explicitly excluded rows is checked before publication.

## Cache integrity and concurrency

Fingerprint includes allowed file contents, split/business identity, policies/evidence,
implementation module contents, pipeline version, Python and dependency versions.
There is no train-rule fallback for another split. A cache hit verifies DB/catalog
checksums and completion metadata. Corrupt caches fail clearly and are not silently
used; repairing/removing generated artifacts is an explicit operator action.

A private source snapshot is hash-verified against the manifest before parsing;
this binds parsed bytes even if an original changes and changes back during the run.
Original dependencies are rechecked before publication. Snapshot files are temporary
and are not bundled into the published cache. Output inside the source split is
forbidden. Exclusive fingerprint locks prevent concurrent writers; foreign/stale
locks are never removed automatically. Complete artifacts are published by directory
rename; failed builds leave no complete cache. No input is modified or deleted.

## Stage 3 boundary and remaining limitations

Preparation executes only its own parameterized/generated SQL. It does not expose
an arbitrary SQL execution tool. Stage 3 must implement the process/filesystem
sandbox, query/resource limits, typed tools, provider adapter and runtime verification.
Only the prepared DB/catalog and allowed question context should enter that worker.
Structural import isolation is tested; it is not an OS permission guarantee.

Business-specific semantic mappings and ambiguous header/locale/entity decisions
remain explicit rather than inferred from ground truth. Whole-training accuracy,
offline inference budget and official scoring equivalence have not been measured.
No NVIDIA NIM calls or training evaluation are part of Stage 2.
