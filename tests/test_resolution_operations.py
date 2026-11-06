"""Source-backed registry resolution preserves physical fields and uncertainty."""

from dataclasses import replace
from decimal import Decimal, localcontext
from hashlib import sha256

import duckdb
import pytest

from datamate.preparation.catalog import compile_clauses, validate_bindings
from datamate.preparation.models import (
    Binding,
    EvidenceRef,
    RawRecord,
    SourceFile,
    SourceRow,
    SourceTable,
)
from datamate.preparation.normalization import CleaningPlan, CleaningStep
from datamate.preparation.operations import OperationState, execute_batch, validate_plan
from datamate.preparation.profiling import profile_table
from datamate.preparation.storage import create_storage, stage_table


def world(
    tmp_path, declarations, rows, headers=("product_id", "quantity", "unit", "quantity_base")
):
    book = tmp_path / "rulebook.md"
    book.write_text("# Active source rules\n", encoding="utf-8")
    readme = tmp_path / "data" / "shop" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("\n\n".join(declarations) + "\n", encoding="utf-8")
    clauses = compile_clauses(
        tuple(
            SourceFile(p, name, sha256(p.read_bytes()).hexdigest())
            for p, name in ((book, "rulebook.md"), (readme, "data/shop/README.md"))
        ),
        split_id="active",
    )
    db = duckdb.connect()
    create_storage(db)
    table = stage_table(
        db,
        SourceFile(tmp_path / "sales.csv", "data/shop/sales.csv", "a" * 64),
        SourceTable("sales", headers, 1, [SourceRow(i + 2, row) for i, row in enumerate(rows)]),
    )
    profile = profile_table(db, table.table_id)
    roles = tuple(
        Binding(
            kind="role",
            table_id=table.table_id,
            columns=(column,),
            value=header,
            profile_digest=profile.digest,
            evidence=(
                EvidenceRef(
                    source=profile.source,
                    sha256=profile.source_sha256,
                    locator=f"header:{table.table_id}/{column}",
                ),
            ),
        )
        for column, header in zip(table.columns, headers, strict=True)
    )
    graphs = []
    for declaration in declarations:
        clause = next(c for c in clauses if c.quote.strip() == declaration)
        evidence = EvidenceRef(
            source=clause.source_document.source,
            sha256=clause.source_document.sha256,
            locator=f"clause:{clause.clause_id}",
            line_start=clause.line_start,
            line_end=clause.line_end,
            quote_sha256=clause.quote_sha256,
        )
        prefix, value = declaration.split(": ", 1)
        fields = {}
        if prefix.startswith("unit_"):
            product = prefix.split("product=", 1)[1][:-1]
            base = prefix.startswith("unit_base")
            if not base:
                value, target = value.split(" = ")
                factor, target = target.split(" ", 1)
                fields.update(unit_target=target, unit_factor=Decimal(factor))
            fields.update(unit_product=product, unit_base=base)
            kind, column = "unit", "c0003"
            deps = (roles[0].binding_id, roles[2].binding_id)
        else:
            value, target = value.split(" = ")
            label, scope = prefix[:-1].split("[")
            fields.update(
                entity_target=target,
                entity_scope=scope,
                entity_relation={
                    "entity_map": "explicit_map",
                    "entity_alias": "alias",
                    "trusted_code": "trusted_code",
                    "relation_key": "relation_key",
                }[label],
            )
            kind, column = "entity", "c0001"
            deps = (roles[0].binding_id,)
        graphs.append(
            Binding(
                kind=kind,
                table_id=table.table_id,
                columns=(column,),
                value=value,
                profile_digest=profile.digest,
                evidence=(evidence,),
                dependencies=deps,
                **fields,
            )
        )
    bindings = validate_bindings(clauses, (profile,), (*roles, *graphs))
    batch = tuple(
        RawRecord(f"r{i}", table.table_id, SourceRow(i + 2, row), "data")
        for i, row in enumerate(rows)
    )
    return db, table, roles, tuple(graphs), bindings, batch


def step_for(table, bindings, operation, *, selected=None):
    selected = bindings.accepted if selected is None else selected
    return CleaningStep(
        table_id=table.table_id,
        operation=operation,
        binding_ids=tuple(b.binding_id for b in selected),
        evidence=tuple(dict.fromkeys(e for b in selected for e in b.evidence)),
    )


def run(db, table, bindings, batch, operation):
    step = step_for(table, bindings, operation)
    plan = CleaningPlan(
        bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=(step,)
    )
    with OperationState(db, table.table_id) as state:
        state.authorize(plan, bindings)
        return execute_batch(batch, step, state)


def quantity_operation(**extra):
    return {
        "name": "normalize_quantity",
        "product_column": "c0001",
        "quantity_column": "c0002",
        "unit_column": "c0003",
        **extra,
    }


def test_literal_declarations_become_accepted_bindings_and_derive_scoped_values(tmp_path):
    lines = [
        "entity_map[product]: sales:P1 = catalog:SKU1",
        "unit_base[product=P1]: tablet",
        "unit_factor[product=P1]: box = 10 blister",
        "unit_factor[product=P1]: blister = 10 tablet",
    ]
    rows = [("P1", Decimal("2.00"), "box", Decimal("200.00")), ("P2", 2, "box", None)]
    db, table, roles, graphs, bindings, batch = world(tmp_path, lines, rows)
    with db:
        assert set(graphs) <= set(bindings.accepted)
        result = run(db, table, bindings, batch, quantity_operation(preconverted_column="c0004"))
        assert result.facts[0].values["quantity_base"] == Decimal("200.00")
        assert result.facts[0].values["quantity_base"].as_tuple().exponent == -2
        assert result.facts[0].values["quantity_base_unit"] == "tablet"
        assert result.facts[0].values["quantity_conversion_factor"] == Decimal("100")
        assert "quantity_base" not in result.facts[1].values
        assert [e.status for e in result.events] == ["APPLIED", "UNRESOLVED"]
        assert [tuple(f.values[c] for c in table.columns) for f in result.facts] == rows
        assert batch[0].source.values == rows[0]
        assert any(e.column_id == "quantity_base" for e in result.lineage)
        assert {"c0001", "c0002", "c0003", "c0004"} <= {
            e.column_id for e in result.lineage if e.step_id == result.events[0].step_id
        }
        entity = run(
            db,
            table,
            bindings,
            batch,
            {
                "name": "resolve_entities",
                "columns": ("c0001",),
                "scope": "product",
                "namespace": "sales",
            },
        )
        assert entity.facts[0].values["entity_id_c0001"] == "catalog:SKU1"
        assert "entity_id_c0001" not in entity.facts[1].values
        assert entity.facts[0].values["c0001"] == "P1"


@pytest.mark.parametrize(
    ("lines", "unit"),
    [
        (
            [
                "unit_base[product=P1]: tablet",
                "unit_factor[product=P1]: box = 10 tablet",
                "unit_factor[product=P1]: box = 9 tablet",
            ],
            "box",
        ),
        (["unit_base[product=P1]: tablet", "unit_factor[product=P1]: tablet = 3 box"], "box"),
        (["unit_base[product=P1]: tablet"], "unknown"),
    ],
)
def test_conflicts_nonterminating_and_unknown_units_do_not_materialize(tmp_path, lines, unit):
    db, table, _, _, bindings, batch = world(tmp_path, lines, [("P1", 2, unit, None)])
    with db:
        result = run(db, table, bindings, batch, quantity_operation())
        assert "quantity_base" not in result.facts[0].values
        assert result.events[0].status == "UNRESOLVED"


def test_pack_sizes_do_not_cross_products_and_preconverted_mismatch_stays_raw(tmp_path):
    lines = [
        "unit_base[product=CANS]: can",
        "unit_factor[product=CANS]: pack = 24 can",
        "unit_base[product=BOTTLES]: bottle",
        "unit_factor[product=BOTTLES]: pack = 20 bottle",
    ]
    rows = [("CANS", 1, "pack", 20), ("BOTTLES", 1, "pack", 20), ("CANS", 1, "bottle", 20)]
    db, table, _, _, bindings, batch = world(tmp_path, lines, rows)
    with db:
        result = run(db, table, bindings, batch, quantity_operation(preconverted_column="c0004"))
        assert [f.values.get("quantity_base") for f in result.facts] == [None, Decimal("20"), None]
        assert [e.status for e in result.events] == ["UNRESOLVED", "APPLIED", "UNRESOLVED"]
        assert "mismatch" in result.events[0].reason
        assert [tuple(f.values[c] for c in table.columns) for f in result.facts] == rows


@pytest.mark.parametrize("quantity", [True, 1.5, "2", Decimal("NaN"), Decimal("1E39")])
def test_only_supported_integer_or_decimal_quantities_convert(tmp_path, quantity):
    db, table, _, _, bindings, batch = world(
        tmp_path, ["unit_base[product=P1]: can"], [("P1", quantity, "can", None)]
    )
    with db:
        result = run(db, table, bindings, batch, quantity_operation())
        assert "quantity_base" not in result.facts[0].values
        assert result.events[0].status == "UNRESOLVED"


def test_conversion_retains_exact_high_precision_under_small_ambient_context(tmp_path):
    value = Decimal("123456789012345678901234567890.1234567")
    db, table, _, _, bindings, batch = world(
        tmp_path,
        ["unit_base[product=P1]: can", "unit_factor[product=P1]: pack = 2 can"],
        [("P1", value, "pack", None)],
    )
    with db, localcontext() as context:
        context.prec = 6
        result = run(db, table, bindings, batch, quantity_operation())
        assert result.facts[0].values["quantity_base"] == Decimal(
            "246913578024691357802469135780.2469134"
        )
        assert result.facts[0].values["c0002"].as_tuple() == value.as_tuple()


def test_alias_collisions_and_entire_transitive_conflicts_remain_unresolved(tmp_path):
    lines = [
        "entity_map[product]: sales:A = catalog:X",
        "entity_map[product]: catalog:X = archive:Z",
        "entity_map[product]: archive:Z = sales:B",
        "entity_alias[product]: sales:A = Shirt",
        "entity_alias[product]: sales:B = Shirt",
    ]
    db, table, _, _, bindings, batch = world(
        tmp_path, lines, [("A", 1, "can", None), ("B", 1, "can", None)]
    )
    with db:
        result = run(
            db,
            table,
            bindings,
            batch,
            {
                "name": "resolve_entities",
                "columns": ("c0001",),
                "scope": "product",
                "namespace": "sales",
            },
        )
        assert all("entity_id_c0001" not in f.values for f in result.facts)
        assert all(e.status == "UNRESOLVED" for e in result.events)


@pytest.mark.parametrize("collision", [False, True])
def test_explicit_source_aliases_require_name_role_and_unique_component(tmp_path, collision):
    lines = ["entity_alias[product]: sales:A = Shirt"]
    if collision:
        lines.append("entity_alias[product]: sales:B = Shirt")
    db, table, _, _, bindings, batch = world(
        tmp_path,
        lines,
        [("Shirt", 1, "can", None)],
        headers=("product_name", "quantity", "unit", "quantity_base"),
    )
    with db:
        result = run(
            db,
            table,
            bindings,
            batch,
            {
                "name": "resolve_entities",
                "columns": ("c0001",),
                "scope": "product",
                "namespace": "sales",
                "input_kind": "alias",
            },
        )
        assert result.facts[0].values.get("entity_id_c0001") == (None if collision else "sales:A")
        assert result.facts[0].values["c0001"] == "Shirt"


def test_plan_requires_current_proof_roles_dependencies_and_ordering(tmp_path):
    db, table, roles, graphs, bindings, batch = world(
        tmp_path, ["unit_base[product=P1]: can"], [("P1", 2, "can", None)]
    )
    with db:
        step = step_for(table, bindings, quantity_operation())
        parse = step_for(
            table,
            bindings,
            {"name": "parse_values", "columns": ("c0002",), "role": "quantity"},
            selected=(roles[1],),
        )
        plan = CleaningPlan(
            bindings_digest=bindings.digest,
            profile_digests=bindings.profile_digests,
            steps=(step, parse),
        )
        with pytest.raises(ValueError, match="order"):
            validate_plan(plan, bindings)
        for changed in (
            step.model_copy(
                update={
                    "binding_ids": tuple(b.binding_id for b in bindings.accepted if b != roles[0])
                }
            ),
            step.model_copy(update={"evidence": roles[0].evidence}),
            step.model_copy(
                update={"operation": step.operation.model_copy(update={"unit_column": "c0002"})}
            ),
        ):
            with pytest.raises(ValueError):
                validate_plan(plan.model_copy(update={"steps": (changed,)}), bindings)
        forged = graphs[0].model_copy(
            update={
                "evidence": (graphs[0].evidence[0].model_copy(update={"quote_sha256": "0" * 64}),)
            }
        )
        proposals = (*roles, forged)
        profile = profile_table(db, table.table_id)
        stale = validate_bindings(bindings.clauses, (profile,), proposals)
        assert forged not in stale.accepted
        # A forged ACCEPTED flag cannot replace a current literal source witness.
        asserted = replace(
            bindings,
            entries=tuple(
                replace(e, binding=forged) if e.binding == graphs[0] else e
                for e in bindings.entries
            ),
        )
        result = run(db, table, asserted, batch, quantity_operation())
        assert "quantity_base" not in result.facts[0].values


def test_entity_resolution_cannot_use_an_omitted_transitive_evidence_edge(tmp_path):
    lines = [
        "entity_map[product]: sales:A = catalog:X",
        "entity_map[product]: catalog:X = archive:Z",
    ]
    db, table, roles, graphs, bindings, batch = world(tmp_path, lines, [("A", 1, "can", None)])
    with db:
        step = step_for(
            table,
            bindings,
            {
                "name": "resolve_entities",
                "columns": ("c0001",),
                "scope": "product",
                "namespace": "sales",
            },
            selected=(*roles, graphs[0]),
        )
        plan = CleaningPlan(
            bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=(step,)
        )
        with OperationState(db, table.table_id) as state:
            state.authorize(plan, bindings)
            result = execute_batch(batch, step, state)
        assert "entity_id_c0001" not in result.facts[0].values
        assert result.events[0].status == "UNRESOLVED"


def test_quantity_conflict_cannot_be_hidden_by_a_binding_conflict_flag(tmp_path):
    lines = [
        "unit_base[product=P1]: can",
        "unit_factor[product=P1]: pack = 24 can",
        "unit_factor[product=P1]: pack = 20 can",
    ]
    db, table, _, graphs, bindings, batch = world(tmp_path, lines, [("P1", 1, "pack", None)])
    with db:
        bindings = replace(
            bindings,
            entries=tuple(
                replace(e, status="CONFLICT") if e.binding == graphs[-1] else e
                for e in bindings.entries
            ),
        )
        result = run(db, table, bindings, batch, quantity_operation())
        assert "quantity_base" not in result.facts[0].values
        assert result.events[0].status == "UNRESOLVED"


@pytest.mark.parametrize("batch_size", [1, 7, 1000])
def test_registry_resolution_is_batch_invariant_and_never_opens_http(
    tmp_path, monkeypatch, batch_size
):
    import socket

    import httpx

    def network_denied(*args, **kwargs):
        pytest.fail("registry resolution attempted network access")

    monkeypatch.setattr(socket.socket, "connect", network_denied)
    monkeypatch.setattr(httpx.Client, "send", network_denied)
    monkeypatch.setattr(httpx.AsyncClient, "send", network_denied)
    rows = [("P1", number, "pack", None) for number in range(-2, 8)]
    db, table, _, _, bindings, batch = world(
        tmp_path, ["unit_base[product=P1]: can", "unit_factor[product=P1]: pack = 24 can"], rows
    )
    with db:
        step = step_for(table, bindings, quantity_operation())
        plan = CleaningPlan(
            bindings_digest=bindings.digest, profile_digests=bindings.profile_digests, steps=(step,)
        )
        values = []
        dispositions = []
        with OperationState(db, table.table_id) as state:
            state.authorize(plan, bindings)
            for start in range(0, len(batch), batch_size):
                result = execute_batch(batch[start : start + batch_size], step, state)
                values.extend(f.values["quantity_base"] for f in result.facts)
                dispositions.extend(d.kind for d in result.dispositions)
        assert values == [
            Decimal("-48"),
            Decimal("-24"),
            Decimal("0"),
            Decimal("24"),
            Decimal("48"),
            Decimal("72"),
            Decimal("96"),
            Decimal("120"),
            Decimal("144"),
            Decimal("168"),
        ]
        assert dispositions == ["retained"] * 10


def test_trimmed_code_similarity_cannot_become_identity_proof(tmp_path):
    db, table, roles, _, bindings, batch = world(
        tmp_path,
        ["entity_map[product]: sales:001 = catalog:SKU1"],
        [(" 001 ", 1, "can", None), ("001", 1, "can", None), ("1", 1, "can", None)],
    )
    with db:
        trim = step_for(
            table, bindings, {"name": "trim_text", "columns": ("c0001",)}, selected=(roles[0],)
        )
        resolve = step_for(
            table,
            bindings,
            {
                "name": "resolve_entities",
                "columns": ("c0001",),
                "scope": "product",
                "namespace": "sales",
            },
        )
        plan = CleaningPlan(
            bindings_digest=bindings.digest,
            profile_digests=bindings.profile_digests,
            steps=(trim, resolve),
        )
        with OperationState(db, table.table_id) as state:
            state.authorize(plan, bindings)
            execute_batch(batch, trim, state)
            result = execute_batch(batch, resolve, state)
        assert [f.values.get("entity_id_c0001") for f in result.facts] == [
            None,
            "catalog:SKU1",
            None,
        ]
        assert [r.source.values[0] for r in batch] == [" 001 ", "001", "1"]
