"""Structural exclusions need source declarations; visual merges have finite scope."""

from decimal import Decimal

import duckdb
import pytest

from datamate.preparation.catalog import compile_clauses, validate_bindings
from datamate.preparation.discovery import sha256_file
from datamate.preparation.models import Binding, EvidenceRef, SourceFile, SourceRow, SourceTable
from datamate.preparation.profiling import profile_table
from datamate.preparation.storage import create_storage, iter_raw_batches, stage_table


def structure_world(tmp_path, db, *, summary="30"):
    root = tmp_path / "train"
    folder = root / "data/shop"
    folder.mkdir(parents=True)
    book = root / "rulebook.md"
    book.write_text("# Active rules\nPreserve unknown data.\n", encoding="utf-8")
    readme = folder / "README.md"
    readme.write_text('note_marker: "Ghi chú:"\n\nsummary_marker: "Tổng cộng"\n', encoding="utf-8")
    clauses = compile_clauses(
        (
            SourceFile(book, "rulebook.md", sha256_file(book)),
            SourceFile(readme, "data/shop/README.md", sha256_file(readme)),
        )
    )
    headers = ("order_id", "product_id", "quantity", "line_net_amount")
    table = stage_table(
        db,
        SourceFile(folder / "x.xlsx", "data/shop/x.xlsx", "a" * 64),
        SourceTable(
            "sales",
            headers,
            1,
            [
                SourceRow(2, ("A", "Tổng hợp", 1, 10)),
                SourceRow(3, headers),
                SourceRow(4, ("B", "P2", 2, 20)),
                SourceRow(5, ("Ghi chú: nhập trễ", None, None, None)),
                SourceRow(6, ("Tổng cộng", None, None, summary if summary != "native" else 30)),
            ],
        ),
    )
    profile = profile_table(db, table.table_id)
    proposals = [
        Binding(
            kind="role",
            table_id=table.table_id,
            columns=(f"c{i:04d}",),
            value=name,
            profile_digest=profile.digest,
            evidence=(
                EvidenceRef(
                    source=profile.source,
                    sha256=profile.source_sha256,
                    locator=f"header:{table.table_id}/c{i:04d}",
                ),
            ),
        )
        for i, name in enumerate(headers, 1)
    ]
    for value, marker in (("note", "Ghi chú:"), ("summary", "Tổng cộng")):
        clause = next(c for c in clauses if c.quote.startswith(value + "_marker"))
        proposals.append(
            Binding(
                kind="structure",
                value=value,
                marker=marker,
                table_id=table.table_id,
                columns=("c0001",),
                profile_digest=profile.digest,
                evidence=(
                    EvidenceRef(
                        source=clause.source_document.source,
                        sha256=clause.source_document.sha256,
                        locator=f"clause:{clause.clause_id}",
                        line_start=clause.line_start,
                        line_end=clause.line_end,
                        quote_sha256=clause.quote_sha256,
                    ),
                ),
            )
        )
    bindings = validate_bindings(clauses, [profile], proposals)
    records = [r for batch in iter_raw_batches(db, table.table_id, 1000) for r in batch]
    return table, bindings, records


@pytest.mark.parametrize(("summary", "status"), [("1.000", "UNRESOLVED"), ("native", "ACCEPTED")])
def test_exact_headers_declared_notes_and_independently_checkable_summaries(
    tmp_path, summary, status
):
    from datamate.preparation.operations import classify_structure

    with duckdb.connect() as db:
        create_storage(db)
        table, bindings, records = structure_world(tmp_path, db, summary=summary)
        decisions = classify_structure(records, table, bindings)
        by_row = {
            record.source.number: decision
            for record, decision in zip(records, decisions, strict=True)
        }
        assert by_row[1].role == by_row[3].role == "header"
        assert by_row[2].role == by_row[4].role == "data"
        assert by_row[5].role == "note"
        assert by_row[6].role == "summary" and by_row[6].status == status
        assert by_row[6].anchor_scope == table.table_id
        assert db.execute("SELECT count(*) FROM staging.raw_rows").fetchone()[0] == 6


def test_structure_operation_preserves_raw_and_never_excludes_by_total_keyword(tmp_path):
    from datamate.preparation.normalization import CleaningPlan, CleaningStep
    from datamate.preparation.operations import OperationState, execute_batch

    with duckdb.connect() as db:
        create_storage(db)
        table, bindings, records = structure_world(tmp_path, db, summary="1.000")
        step = CleaningStep(
            table_id=table.table_id,
            operation={"name": "classify_structure"},
            binding_ids=tuple(b.binding_id for b in bindings.accepted),
            evidence=tuple(e for b in bindings.accepted for e in b.evidence),
        )
        plan = CleaningPlan(
            bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=(step,)
        )
        with OperationState(db, table.table_id) as state:
            state.authorize(plan, bindings)
            result = execute_batch(records, step, state)
            assert {f.fact_id for f in result.facts} == {
                "row:" + records[i].record_id for i in (1, 3)
            }
            assert result.dispositions[-1].kind == "quarantined"
        assert records[1].source.values[1] == "Tổng hợp"


def test_partial_summary_scope_and_uncached_formula_cannot_claim_structure(tmp_path):
    from datamate.preparation.models import RawRecord
    from datamate.preparation.operations import classify_structure

    with duckdb.connect() as db:
        create_storage(db)
        table, bindings, records = structure_world(tmp_path, db, summary="native")
        subset = [records[i] for i in (1, 3, 5)]
        assert classify_structure(subset, table, bindings)[-1].status == "UNRESOLVED"
        formula = RawRecord(
            "uncached",
            table.table_id,
            SourceRow(7, (None, None, None, None), formulas=("=SUM(A1:A6)", None, None, None)),
            "data",
        )
        assert classify_structure([formula], table, bindings)[0].status == "UNRESOLVED"


def test_merged_fill_respects_explicit_vertical_span_across_single_row_batches(tmp_path):
    from datamate.preparation.models import ResolvedBinding, SemanticBindings
    from datamate.preparation.normalization import CleaningPlan, CleaningStep
    from datamate.preparation.operations import OperationState, execute_batch

    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db,
            SourceFile(tmp_path / "x.xlsx", "data/shop/x.xlsx", "a" * 64),
            SourceTable(
                "sales",
                ("order_id", "quantity"),
                1,
                [
                    SourceRow(7, ("A", Decimal("0.10"))),
                    SourceRow(8, (None, 1)),
                    SourceRow(9, (None, 2)),
                    SourceRow(10, (None, 3)),
                ],
                merged_ranges=((1, 7, 1, 9),),
            ),
        )
        profile = profile_table(db, table.table_id)
        ref = EvidenceRef(
            source=profile.source,
            sha256=profile.source_sha256,
            locator=f"header:{table.table_id}/c0001",
        )
        binding = Binding(
            kind="role",
            value="order_id",
            table_id=table.table_id,
            columns=("c0001",),
            profile_digest=profile.digest,
            evidence=(ref,),
        )
        bindings = SemanticBindings(
            (ResolvedBinding(binding, "ACCEPTED", "fixture"),),
            (),
            (profile.digest,),
            (table.table_id,),
        )
        step = CleaningStep(
            table_id=table.table_id,
            operation={"name": "fill_merged", "columns": ("c0001",)},
            binding_ids=(binding.binding_id,),
            evidence=(ref,),
        )
        plan = CleaningPlan(
            bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=(step,)
        )
        outputs = []
        with OperationState(db, table.table_id) as state:
            state.authorize(plan, bindings)
            for batch in iter_raw_batches(db, table.table_id, 1):
                outputs.extend(execute_batch(batch, step, state).facts)
        assert [fact.values["c0001"] for fact in outputs] == ["A", "A", "A", None]
        assert outputs[0].values["c0002"].as_tuple().exponent == -2
