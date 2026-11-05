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
