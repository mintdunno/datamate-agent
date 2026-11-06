"""Streaming grouping follows active rules and never deduplicates an ordinary ledger."""

import duckdb
import pytest

from datamate.preparation.catalog import compile_clauses, validate_bindings
from datamate.preparation.discovery import sha256_file
from datamate.preparation.models import Binding, EvidenceRef, SourceFile, SourceRow, SourceTable
from datamate.preparation.normalization import CleaningPlan, CleaningStep
from datamate.preparation.operations import OperationState, execute_batch
from datamate.preparation.profiling import profile_table
from datamate.preparation.storage import create_storage, iter_raw_batches, stage_table

GROUP_RULE = (
    "| Ô để trống ở các dòng sau của cùng một nhóm dòng | giống ô đó ở dòng đầu nhóm: khách, "
    "ghi chú, trạng thái của đơn áp cho mọi dòng của đơn. Không phải thiếu dữ liệu, "
    "không có nghĩa là khách lẻ hay đơn hoàn thành |"
)
DUP_RULE = (
    "| Hai dòng của cùng một đơn giống nhau ở mọi ô | lỗi xuất file, tính một lần. "
    "Hai dòng giống nhau của hai đơn khác nhau là hai lần bán, tính cả hai |"
)


def group_world(tmp_path, db):
    tmp_path.mkdir(parents=True)
    book = tmp_path / "rulebook.md"
    book.write_text(GROUP_RULE + "\n" + DUP_RULE + "\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text('note_marker: "Ghi chú:"\n', encoding="utf-8")
    clauses = compile_clauses(
        (
            SourceFile(book, "rulebook.md", sha256_file(book)),
            SourceFile(readme, "README.md", sha256_file(readme)),
        )
    )
    headers = ("order_id", "product_id", "quantity", "line_net_amount", "customer_id")
    rows = [
        ("A", "P1", 1, 10, "C1"),
        (None, "P2", 1, 20, None),
        (None, "P2", 1, 20, None),
        ("A", "P2", 2, 20, None),
        ("B", "P1", 1, 10, None),
        ("B", "P1", 1, 10, None),
        ("Ghi chú: divider", None, None, None, None),
        (None, "P3", 1, 30, None),
        ("C", "P4", 1, 40, "C3"),
        (None, "P5", 1, 50, None),
    ]
    table = stage_table(
        db,
        SourceFile(tmp_path / "x.xlsx", "data/shop/x.xlsx", "a" * 64),
        SourceTable("sales", headers, 1, [SourceRow(i + 2, tuple(v)) for i, v in enumerate(rows)]),
    )
    profile = profile_table(db, table.table_id)
    proposals = [
        Binding(
            kind="role",
            value=name,
            table_id=table.table_id,
            columns=(f"c{i:04d}",),
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

    def witness(clause):
        return EvidenceRef(
            source=clause.source_document.source,
            sha256=clause.source_document.sha256,
            locator=f"clause:{clause.clause_id}",
            line_start=clause.line_start,
            line_end=clause.line_end,
            quote_sha256=clause.quote_sha256,
        )

    marker = next(c for c in clauses if c.quote.startswith("note_marker"))
    proposals.append(
        Binding(
            kind="structure",
            value="note",
            marker="Ghi chú:",
            table_id=table.table_id,
            columns=("c0001",),
            profile_digest=profile.digest,
            evidence=(witness(marker),),
        )
    )
    proposals.append(
        Binding(
            kind="grain",
            value="order_line",
            table_id=table.table_id,
            columns=table.columns,
            profile_digest=profile.digest,
            evidence=proposals[0].evidence,
            dependencies=tuple(b.binding_id for b in proposals if b.kind == "role"),
        )
    )
    bindings = validate_bindings(clauses, [profile], proposals)
    ids = tuple(b.binding_id for b in bindings.accepted)
    common = tuple(e for b in bindings.accepted for e in b.evidence)
    steps = (
        CleaningStep(
            table_id=table.table_id,
            operation={"name": "classify_structure"},
            binding_ids=ids,
            evidence=common,
        ),
        CleaningStep(
            table_id=table.table_id,
            operation={"name": "inherit_group", "group_key": "c0001", "columns": ("c0005",)},
            binding_ids=ids,
            evidence=common + (witness(next(c for c in clauses if "Ô để trống" in c.quote)),),
        ),
        CleaningStep(
            table_id=table.table_id,
            operation={"name": "deduplicate_order", "group_key": "c0001"},
            binding_ids=ids,
            evidence=common + (witness(next(c for c in clauses if "Hai dòng" in c.quote)),),
        ),
    )
    plan = CleaningPlan(
        bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=steps
    )
    return table, bindings, plan


def test_fill_dedup_and_boundaries_are_chunk_invariant(tmp_path):
    outputs = []
    for size in (1, 7, 1000):
        with duckdb.connect() as db:
            create_storage(db)
            table, bindings, plan = group_world(tmp_path / str(size), db)
            facts = []
            dispositions = []
            with OperationState(db, table.table_id) as state:
                state.authorize(plan, bindings)
                for batch in iter_raw_batches(db, table.table_id, size):
                    for step in plan.steps:
                        result = execute_batch(batch, step, state)
                    facts.extend(result.facts)
                    dispositions.extend(result.dispositions)
                    assert all(
                        edge.fact_id in {f.fact_id for f in facts} for edge in result.lineage
                    )
            outputs.append(
                (
                    [(f.fact_id, f.values) for f in facts],
                    [(d.record_id, d.kind, d.duplicate_of) for d in dispositions],
                )
            )
            assert len(dispositions) == table.raw_record_count
            assert sum(d.kind == "duplicate" for d in dispositions) == 2
            values = [f.values for f in facts]
            assert values[1]["c0005"] == "C1"
            assert next(v for v in values if v["c0001"] == "B")["c0005"] is None
            assert next(v for v in values if v["c0002"] == "P3")["c0001"] is None
            assert values[-1]["c0005"] == "C3"
    assert outputs[0] == outputs[1] == outputs[2]


def test_ledger_or_missing_active_rule_cannot_authorize_order_dedup(tmp_path):
    from dataclasses import replace

    from datamate.preparation.operations import validate_plan

    with duckdb.connect() as db:
        create_storage(db)
        _, bindings, plan = group_world(tmp_path / "ledger", db)
        without_grain = replace(
            bindings, entries=tuple(e for e in bindings.entries if e.binding.kind != "grain")
        )
        step = plan.steps[-1].model_copy(
            update={"binding_ids": tuple(b.binding_id for b in without_grain.accepted)}
        )
        bad = plan.model_copy(update={"bindings_digest": without_grain.digest, "steps": (step,)})
        with pytest.raises(ValueError, match="grain"):
            validate_plan(bad, without_grain)
        missing = replace(bindings, clauses=())
        bad = plan.model_copy(update={"bindings_digest": missing.digest})
        with pytest.raises(ValueError):
            validate_plan(bad, missing)


def test_duplicate_links_and_collapsed_lineage_are_persistable(tmp_path):
    from datamate.preparation.storage import store_disposition, store_fact, store_lineage

    with duckdb.connect() as db:
        create_storage(db)
        table, bindings, plan = group_world(tmp_path / "persist", db)
        with OperationState(db, table.table_id) as state:
            state.authorize(plan, bindings)
            for batch in iter_raw_batches(db, table.table_id, 1):
                for step in plan.steps:
                    result = execute_batch(batch, step, state)
                for fact in result.facts:
                    store_fact(db, fact)
                for disposition in result.dispositions:
                    store_disposition(db, disposition)
                for edge in result.lineage:
                    store_lineage(db, edge)
        assert (
            db.execute(
                "SELECT count(*) FROM meta.dispositions WHERE duplicate_of IS NOT NULL"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT count(*) FROM meta.lineage l JOIN meta.dispositions d "
                "ON l.record_id=d.record_id WHERE d.kind='duplicate'"
            ).fetchone()[0]
            > 0
        )


def test_formula_difference_and_explicit_boundary_reset_preserve_records(tmp_path):
    from dataclasses import replace

    from datamate.preparation.models import RawRecord

    with duckdb.connect() as db:
        create_storage(db)
        table, bindings, plan = group_world(tmp_path / "boundaries", db)
        source = next(
            r for b in iter_raw_batches(db, table.table_id, 1000) for r in b if r.kind == "data"
        )
        first = RawRecord(
            "first", table.table_id, replace(source.source, formulas=("=1+1",)), "data"
        )
        second = RawRecord(
            "second", table.table_id, replace(source.source, formulas=("=2",)), "data"
        )
        only_dup = plan.model_copy(update={"steps": (plan.steps[-1],)})
        with OperationState(db, table.table_id) as state:
            state.authorize(only_dup, bindings)
            assert len(execute_batch([first, second], only_dup.steps[0], state).facts) == 2
            state.reset_boundary("new-source")
            assert len(execute_batch([first], only_dup.steps[0], state).facts) == 1
