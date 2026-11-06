"""Registered operations keep raw types/identity and require source-bound plans."""

import importlib
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from datamate.preparation.models import RawRecord, SourceRow


def api():
    return importlib.import_module("datamate.preparation.operations")


def test_batch_roundtrip_preserves_native_values_decimal_scale_and_identity():
    module = api()
    from datamate.preparation.normalization import CleaningStep

    values = (Decimal("0.10"), None, "001", date(2025, 1, 2), b"\x00", "", True)
    record = RawRecord("record-1", "table-1", SourceRow(2, values), "data")
    step = CleaningStep(table_id="table-1", operation={"name": "preserve_raw"})
    with duckdb.connect() as db, module.OperationState(db, "table-1") as state:
        result = module.execute_batch([record], step, state)
        output = tuple(result.facts[0].values.values())
        assert output == values
        assert all(type(a) is type(b) for a, b in zip(output, values, strict=True))
        assert output[0].as_tuple().exponent == -2
        assert record.source.values == values
        assert {edge.record_id for edge in result.lineage} == {"record-1"}
        assert result.dispositions[0].kind == "retained"
        assert db.execute("SELECT 1").fetchone() == (1,)
    # OperationState closes only its own state, never the caller's connection.


def test_unregistered_operation_or_callable_parameters_fail_closed():
    from datamate.preparation.normalization import CleaningStep

    for operation in (
        {"name": "exec", "code": "print(1)"},
        {"name": "preserve_raw", "callback": lambda: None},
        {"name": "preserve_raw", "version": "unknown"},
    ):
        with pytest.raises(ValueError):
            CleaningStep(table_id="t", operation=operation)


def test_structural_guess_is_quarantined_until_a_registered_classifier_proves_it():
    module = api()
    from datamate.preparation.normalization import CleaningStep

    record = RawRecord("r", "t", SourceRow(1, ("Maybe a header",)), "header")
    with duckdb.connect() as db, module.OperationState(db, "t") as state:
        result = module.execute_batch(
            [record], CleaningStep(table_id="t", operation={"name": "preserve_raw"}), state
        )
        assert not result.facts
        assert result.dispositions[0].kind == "quarantined"


@pytest.mark.parametrize(
    ("values", "expected", "unresolved"),
    [
        (
            [Decimal("0.10"), None, " 001 ", date(2025, 1, 2), b"\x00", ""],
            [Decimal("0.10"), None, "001", date(2025, 1, 2), b"\x00", ""],
            3,
        ),
        ([None, "", " 001 "], [None, "", "001"], 0),
    ],
)
def test_mixed_text_operation_never_coerces_non_text_and_requires_authorization(
    values, expected, unresolved
):
    module = api()
    from datamate.preparation.models import Binding, EvidenceRef, ResolvedBinding, SemanticBindings
    from datamate.preparation.normalization import CleaningPlan, CleaningStep

    evidence = EvidenceRef(source="data/shop/x.csv", sha256="a" * 64, locator="header:t/c0001")
    binding = Binding(
        kind="role",
        table_id="t",
        columns=("c0001",),
        value="product_id",
        profile_digest="b" * 64,
        evidence=(evidence,),
    )
    bindings = SemanticBindings(
        (ResolvedBinding(binding, "ACCEPTED", "synthetic", (), "v1"),), (), ("b" * 64,), ("t",)
    )
    step = CleaningStep(
        table_id="t",
        operation={"name": "trim_text", "columns": ("c0001",)},
        binding_ids=(binding.binding_id,),
        evidence=(evidence,),
    )
    plan = CleaningPlan(bindings_digest=bindings.digest, profile_digests=("b" * 64,), steps=(step,))
    batch = [
        RawRecord(f"r{i}", "t", SourceRow(i + 1, (value,)), "data")
        for i, value in enumerate(values)
    ]
    with duckdb.connect() as db:
        state = module.OperationState(db, "t")
        with pytest.raises(ValueError, match="authorized"):
            module.execute_batch(batch, step, state)
        state.authorize(plan, bindings)
        result = module.execute_batch(batch, step, state)
        outputs = [f.values["c0001"] for f in result.facts]
        assert outputs == expected
        if isinstance(outputs[0], Decimal):
            assert outputs[0].as_tuple().exponent == -2
        assert [record.source.values[0] for record in batch] == values
        assert sum(event.status == "UNRESOLVED" for event in result.events) == unresolved
        stale = plan.model_copy(update={"bindings_digest": "f" * 64})
        with pytest.raises(ValueError, match="binding"):
            state.authorize(stale, bindings)
        with pytest.raises(ValueError, match="authorized"):
            module.execute_batch(batch, step, state)
        state.close()
        assert db.execute("SELECT 1").fetchone() == (1,)
        state.close()


def test_stale_plan_is_rejected_and_state_boundary_reset_clears_working_values():
    module = api()
    from datamate.preparation.models import SemanticBindings
    from datamate.preparation.normalization import CleaningPlan, CleaningStep

    bindings = SemanticBindings((), (), ("a" * 64,), ("t",))
    step = CleaningStep(table_id="t", operation={"name": "preserve_raw"})
    plan = CleaningPlan(bindings_digest="f" * 64, profile_digests=("a" * 64,), steps=(step,))
    with pytest.raises(ValueError, match="binding"):
        module.validate_plan(plan, bindings)
    with duckdb.connect() as db, module.OperationState(db, "t") as state:
        first = RawRecord("r", "t", SourceRow(1, ("one",)), "data")
        module.execute_batch([first], step, state)
        state.reset_boundary("other")
        second = RawRecord("r", "t", SourceRow(1, ("two",)), "data")
        assert module.execute_batch([second], step, state).facts[0].values["c0001"] == "two"


def test_parse_operation_commits_unambiguous_values_and_preserves_ambiguous_raw():
    from datamate.preparation.models import Binding, EvidenceRef, ResolvedBinding, SemanticBindings
    from datamate.preparation.normalization import CleaningPlan, CleaningStep

    module = api()
    witness = EvidenceRef(source="data/shop/x.csv", sha256="a" * 64, locator="header:t/c0001")
    binding = Binding(
        kind="role",
        table_id="t",
        columns=("c0001",),
        value="line_net_amount",
        profile_digest="b" * 64,
        evidence=(witness,),
    )
    bindings = SemanticBindings(
        (ResolvedBinding(binding, "ACCEPTED", "fixture", (), "v1"),), (), ("b" * 64,), ("t",)
    )
    step = CleaningStep(
        table_id="t",
        operation={"name": "parse_values", "columns": ("c0001",), "role": "money"},
        binding_ids=(binding.binding_id,),
        evidence=(witness,),
    )
    plan = CleaningPlan(
        bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=(step,)
    )
    batch = [
        RawRecord(f"r{i}", "t", SourceRow(i + 1, (v,)), "data")
        for i, v in enumerate(("532.000đ", "1.000", Decimal("0.10"), "9" * 39))
    ]
    with duckdb.connect() as db, module.OperationState(db, "t") as state:
        state.authorize(plan, bindings)
        result = module.execute_batch(batch, step, state)
        assert [f.values["c0001"] for f in result.facts] == [
            Decimal("532000"),
            "1.000",
            Decimal("0.10"),
            "9" * 39,
        ]
        assert [e.status for e in result.events] == [
            "APPLIED",
            "UNRESOLVED",
            "APPLIED",
            "UNSUPPORTED",
        ]
        assert batch[0].source.values == ("532.000đ",)


@pytest.mark.parametrize("source_year_rule", [True, False])
def test_date_operation_requires_active_sheet_year_rule_and_workbook_date_style(
    tmp_path, source_year_rule
):
    from datetime import datetime

    from datamate.preparation.catalog import compile_clauses, validate_bindings
    from datamate.preparation.discovery import sha256_file
    from datamate.preparation.models import Binding, EvidenceRef, SourceFile, SourceTable
    from datamate.preparation.normalization import CleaningPlan, CleaningStep
    from datamate.preparation.profiling import profile_table
    from datamate.preparation.storage import create_storage, iter_raw_batches, stage_table

    module = api()
    book = tmp_path / "rulebook.md"
    book.write_text(
        "| Ngày không ghi năm | năm của sheet chứa dòng đó |\n"
        if source_year_rule
        else "# Different active book\nNo source-year declaration.\n",
        encoding="utf-8",
    )
    clauses = compile_clauses(
        (SourceFile(book, "rulebook.md", sha256_file(book)),), split_id="active"
    )
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db,
            SourceFile(tmp_path / "x.xlsx", "data/shop/x.xlsx", "a" * 64),
            SourceTable(
                "Sales 2025",
                ("ordered_at",),
                1,
                [
                    SourceRow(2, ("3/6",), formats=("General",)),
                    SourceRow(3, ("1.5",), formats=("General",)),
                    SourceRow(4, ("1.5",), formats=("yyyy-mm-dd hh:mm",)),
                ],
                workbook_epoch=datetime(1904, 1, 1),
            ),
        )
        profile = profile_table(db, table.table_id)
        ref = EvidenceRef(
            source=profile.source,
            sha256=profile.source_sha256,
            locator=f"header:{table.table_id}/c0001",
        )
        role = Binding(
            kind="role",
            table_id=table.table_id,
            columns=("c0001",),
            value="ordered_at",
            profile_digest=profile.digest,
            evidence=(ref,),
        )
        bindings = validate_bindings(clauses, [profile], [role])
        step = CleaningStep(
            table_id=table.table_id,
            operation={"name": "parse_values", "role": "datetime", "columns": ("c0001",)},
            binding_ids=(role.binding_id,),
            evidence=(ref,),
        )
        plan = CleaningPlan(
            bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=(step,)
        )
        batch = [
            r for b in iter_raw_batches(db, table.table_id, 1000) for r in b if r.kind == "data"
        ]
        with module.OperationState(db, table.table_id) as state:
            state.authorize(plan, bindings)
            values = [f.values["c0001"] for f in module.execute_batch(batch, step, state).facts]
            assert values == [
                date(2025, 6, 3) if source_year_rule else "3/6",
                "1.5",
                datetime(1904, 1, 2, 12),
            ]
