# Qualified source fields — 2026-11-07

Historical checkpoint: see the subsequent [pre-header recovery report](preheader_recovery_report.md) for the metadata integration and fresh preparation results.

Status: **PARTIAL**. The original sales-sheet canary is **BLOCKED**; four
supplemental source-record SQL probes pass. This does not complete Milestone 1.

## Implemented and independently checked

Financial contracts distinguish recorded unit price/cost and line/order discounts.
Source-declared same-unit line formulas produce separate private candidates without
rounding or overwriting recorded amounts. A rational-arithmetic audit independently
recomputes those candidates and detects corruption.

One code-named projection per eligible source table exposes accepted fields plus
fact/table provenance. An uncertain field is withheld in full, retaining every
source data record in the denominator. Unknown record structure blocks that table,
not unrelated qualified tables. Audits independently check raw values, denominator,
non-data dispositions, underlying field lineage and exact projection lineage.
Accepted Unicode overlays require the existing source-bound text audit.

Canonical scopes keep their existing gates. Source SQL uses a deliberately limited
single-source SELECT: no joins, CTEs/subqueries, windows, casts or arithmetic.
Quantity aggregation requires one recorded-unit grouping or explicit string-unit
predicate; parameterized unit filters require grouping. Numeric predicates cannot
coerce different textual units into one. Price/cost/factor/percentage aggregates
remain unqualified. Views select only audited candidate fields; changed views,
values, profiles or policies invalidate access. Proof internals stay outside model
tool metadata. The existing SQL AST, read-only process and resource limits remain.

## Actual checks

- `.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short`:
  **1,042 passed in 314.16 seconds**.
- After adding three independent pre-publication coverage/lineage regressions,
  `.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_source_field_runtime.py
  tests/test_derived_line_amounts.py --tb=short`: **31 passed in 8.94 seconds**.
  This focused run overlaps the full suite; these counts must not be added.
- `uv run --locked ruff check .`, `uv run --locked ruff format --check .`:
  passed (162 files formatted).
- `uv pip check`: 50 installed packages compatible.
- A read-only review identified four defects: unit coercion, non-data coverage,
  orphan/missing lineage and repeated full-artifact hashing. All four were fixed
  and the fixes checked. Runtime verifies the full source receipt once per load.

## Real development-business probes

A copy of the complete frozen `biz_122` prepared artifact was reprojected and
audited. Source hashes were matched against the original manifest and rechecked
around the supplemental SQL probes. The original artifact remains unchanged.
This is **re-auditing existing preparation**, not cold ingestion or full validation
of the new financial operations against freshly loaded Excel files. Old derived
native identities remain private and cannot authorize source joins. No evaluator
labels or QA answers entered preparation or SQL tools.

Original artifact fingerprint:
`edea91b1612a8fd7c8519235e147ee32747cd3026b4633847b287874cee5b6bf`.

The planned `DonHang_ChiTiet_1.xlsx / T01-2025` canary is still blocked. Its quantity,
product-name and unit columns passed field checks, but a quarantined pre-header
pair, `Trạng thái đơn hàng | Đặt hàng (Đã duyệt)`, lacks an accepted metadata
interpretation. It may describe an export filter and cannot be silently discarded.
Date, inherited identifiers/customer values and ambiguous monetary columns also
remain withheld by their independent source checks. The canary driver stopped
when this target was not published; the failure was preserved.

Separate supplemental `COUNT(*)` probes compared SQL results with independently
counted raw data records. They do not replace the failed planned sales canary:

| Source / sheet | Raw records | Qualified SQL count |
| --- | ---: | ---: |
| DonHang_Lo_1.xlsx / Sheet1 | 100,000 | 100,000 |
| DonHang_Lo_2.xlsx / Sheet1 | 5,799 | 5,799 |
| DanhSachQuyDoi.xlsx / Sheet1 | 83 | 83 |
| DanhSachSanPham.xlsx / Sheet1 | 75 | 75 |

No source-quantity aggregate was asserted for lot tables lacking a qualified unit.
These are source-observation checks, not order counts, official revenue, QA
accuracy, Penalized Accuracy or evidence of whole-business completeness.

Ignored evidence: `state/runs/source-fields-biz122/probe-result.json`,
`source-fields-release-tests.log` and the source-field integration logs.

## Remaining work and resource stop

The real code-to-SML-to-sales-SQL canary has **not passed**. Registered metadata
interpretation and issue-specific recovery still need integration; the older role
mapping route cannot resolve arbitrary metadata, numeric formats or missing unit
basis. Derived line candidates are audited privately but not published as official
measures. Full fresh financial preparation and model verification remain pending.

The CPU precondition failed, so no new Google/Gemma or NVIDIA NIM requests were
made. The approved cap of five new attempts was not spent; existing campaign,
rate and daily ceilings were not increased. No QA benchmark, full training
iteration, checkpoint reuse, Colab/Kaggle qualification or model fallback occurred.
The frozen normalization labels/checkpoint were left unchanged.
