"""Literal source declarations for identity and unit resolution."""

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256

import pytest

from datamate.preparation.catalog import accept_entity_links, compile_clauses, resolve_units
from datamate.preparation.models import (
    Binding,
    EvidenceRef,
    ResolvedBinding,
    SemanticBindings,
    SourceFile,
)


def world(tmp_path, lines):
    root = tmp_path / "train"
    root.mkdir()
    rulebook = root / "rulebook.md"
    rulebook.write_text("# Active rules\n", encoding="utf-8")
    readme = root / "data" / "shop" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    clauses = compile_clauses(
        (
            SourceFile(rulebook, "rulebook.md", sha256(rulebook.read_bytes()).hexdigest()),
            SourceFile(readme, "data/shop/README.md", sha256(readme.read_bytes()).hexdigest()),
        )
    )
    bindings = SemanticBindings(
        (),
        clauses,
        ("a" * 64,),
        ("table",),
        profile_sources=(("table", "a" * 64, "data/shop/sales.csv", "b" * 64),),
    )
    return bindings


def proposal(bindings, line, kind, value, **fields):
    clause = next(c for c in bindings.clauses if c.quote.strip() == line)
    return Binding(
        kind=kind,
        table_id="table",
        columns=("code",),
        value=value,
        profile_digest="a" * 64,
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
        **fields,
    )


def test_explicit_map_and_alias_preserve_names_without_name_only_merge(tmp_path):
    line = "entity_map[product]: sales:P-01 = catalog:SKU-1"
    alias = "entity_alias[product]: sales:P-01 = Red Shirt"
    bindings = world(tmp_path, [line, alias, "Red Shirt", "Red Shirt"])
    mapping = proposal(
        bindings,
        line,
        "entity",
        "sales:P-01",
        entity_target="catalog:SKU-1",
        entity_scope="product",
        entity_relation="explicit_map",
    )
    naming = proposal(
        bindings,
        alias,
        "entity",
        "sales:P-01",
        entity_target="Red Shirt",
        entity_scope="product",
        entity_relation="alias",
    )
    result = accept_entity_links((mapping, naming), bindings)
    assert len(result.accepted_links) == 1
    assert result.components == (("product", ("catalog:SKU-1", "sales:P-01")),)
    assert result.aliases == (("product", "sales:P-01", "Red Shirt"),)


def test_transitive_union_rechecks_full_native_codes(tmp_path):
    lines = [
        "entity_map[product]: sales:P-01 = catalog:SKU-1",
        "entity_map[product]: catalog:SKU-1 = archive:OLD",
        "entity_map[product]: archive:OLD = sales:P-02",
    ]
    bindings = world(tmp_path, lines)
    links = tuple(
        proposal(
            bindings,
            line,
            "entity",
            left,
            entity_target=right,
            entity_scope="product",
            entity_relation="explicit_map",
        )
        for line, left, right in zip(
            lines,
            ("sales:P-01", "catalog:SKU-1", "archive:OLD"),
            ("catalog:SKU-1", "archive:OLD", "sales:P-02"),
            strict=True,
        )
    )
    result = accept_entity_links(links, bindings)
    assert len(result.accepted_links) == 2
    assert result.conflicts == (links[2],)


def test_similarity_and_relation_keys_cannot_prove_identity(tmp_path):
    line = "relation_key[product]: sales:P-01 = sales:P-02"
    bindings = world(tmp_path, [line, "same display name and price"])
    relation = proposal(
        bindings,
        line,
        "entity",
        "sales:P-01",
        entity_target="sales:P-02",
        entity_scope="product",
        entity_relation="relation_key",
    )
    guessed = relation.model_copy(update={"entity_relation": "explicit_map"})
    result = accept_entity_links((relation, guessed), bindings)
    assert result.accepted_links == ()
    assert set(result.unresolved) == {relation, guessed}


def test_source_code_whitespace_and_alias_collision_stay_unresolved(tmp_path):
    lines = [
        "entity_map[product]: sales:P-01  = catalog:SKU-1",
        "entity_alias[product]: sales:P-01 = Red Shirt",
        "entity_alias[product]: sales:P-02 = Red Shirt",
    ]
    bindings = world(tmp_path, lines)
    padded = proposal(
        bindings,
        lines[0],
        "entity",
        "sales:P-01 ",
        entity_target="catalog:SKU-1",
        entity_scope="product",
        entity_relation="explicit_map",
    )
    first = proposal(
        bindings,
        lines[1],
        "entity",
        "sales:P-01",
        entity_target="Red Shirt",
        entity_scope="product",
        entity_relation="alias",
    )
    second = proposal(
        bindings,
        lines[2],
        "entity",
        "sales:P-02",
        entity_target="Red Shirt",
        entity_scope="product",
        entity_relation="alias",
    )
    result = accept_entity_links((padded, first, second), bindings)
    assert result.accepted_links == ()
    assert result.unresolved == (padded,)
    assert result.ambiguous_aliases == (("product", "Red Shirt"),)
    assert result.aliases == (
        ("product", "sales:P-01", "Red Shirt"),
        ("product", "sales:P-02", "Red Shirt"),
    )


def test_stale_clause_hash_cannot_prove_identity(tmp_path):
    line = "trusted_code[customer]: crm:C123 = billing:C123"
    bindings = world(tmp_path, [line])
    link = proposal(
        bindings,
        line,
        "entity",
        "crm:C123",
        entity_target="billing:C123",
        entity_scope="customer",
        entity_relation="trusted_code",
    )
    stale = link.model_copy(
        update={"evidence": (link.evidence[0].model_copy(update={"sha256": "0" * 64}),)}
    )
    assert accept_entity_links((link,), bindings).accepted_links == (link,)
    assert accept_entity_links((stale,), bindings).unresolved == (stale,)


def test_other_business_readme_cannot_prove_mapping(tmp_path):
    line = "entity_map[product]: sales:A = catalog:Z"
    bindings = world(tmp_path, [line])
    link = proposal(
        bindings,
        line,
        "entity",
        "sales:A",
        entity_target="catalog:Z",
        entity_scope="product",
        entity_relation="explicit_map",
    )
    other_business = replace(
        bindings,
        profile_sources=(("table", "a" * 64, "data/other/sales.csv", "b" * 64),),
    )
    assert accept_entity_links((link,), other_business).unresolved == (link,)


def test_identity_components_are_separate_across_scopes(tmp_path):
    lines = [
        "entity_map[product]: sales:A = catalog:Z",
        "entity_map[customer]: catalog:Z = sales:B",
    ]
    bindings = world(tmp_path, lines)
    first = proposal(
        bindings,
        lines[0],
        "entity",
        "sales:A",
        entity_target="catalog:Z",
        entity_scope="product",
        entity_relation="explicit_map",
    )
    second = proposal(
        bindings,
        lines[1],
        "entity",
        "catalog:Z",
        entity_target="sales:B",
        entity_scope="customer",
        entity_relation="explicit_map",
    )
    result = accept_entity_links((first, second), bindings)
    assert result.accepted_links == (first, second)
    assert result.components == (
        ("customer", ("catalog:Z", "sales:B")),
        ("product", ("catalog:Z", "sales:A")),
    )


def test_multihop_unit_factor_is_product_scoped(tmp_path):
    lines = [
        "unit_base[product=P1]: tablet",
        "unit_factor[product=P1]: box = 10 blister",
        "unit_factor[product=P1]: blister = 10 tablet",
    ]
    bindings = world(tmp_path, lines)
    accepted = tuple(
        proposal(
            bindings,
            line,
            "unit",
            value,
            unit_product="P1",
            unit_target=target,
            unit_factor=factor,
            unit_base=base,
        )
        for line, value, target, factor, base in (
            (lines[0], "tablet", None, None, True),
            (lines[1], "box", "blister", Decimal("10"), False),
            (lines[2], "blister", "tablet", Decimal("10"), False),
        )
    )
    bindings = replace(
        bindings, entries=tuple(ResolvedBinding(p, "ACCEPTED", "test") for p in accepted)
    )
    assert resolve_units(bindings, product_id="P1", unit="box").factor == Decimal("100")
    assert len(resolve_units(bindings, product_id="P1", unit="box").evidence) == 3
    assert resolve_units(bindings, product_id="P2", unit="box").factor is None


def test_conflicting_unit_cycle_blocks_scope(tmp_path):
    lines = [
        "unit_base[product=P1]: tablet",
        "unit_factor[product=P1]: box = 10 blister",
        "unit_factor[product=P1]: blister = 10 tablet",
        "unit_factor[product=P1]: box = 99 tablet",
    ]
    bindings = world(tmp_path, lines)
    values = (
        ("tablet", None, None, True),
        ("box", "blister", Decimal("10"), False),
        ("blister", "tablet", Decimal("10"), False),
        ("box", "tablet", Decimal("99"), False),
    )
    entries = tuple(
        ResolvedBinding(
            proposal(
                bindings,
                line,
                "unit",
                value,
                unit_product="P1",
                unit_target=target,
                unit_factor=factor,
                unit_base=base,
            ),
            "ACCEPTED",
            "test",
        )
        for line, (value, target, factor, base) in zip(lines, values, strict=True)
    )
    bindings = replace(bindings, entries=entries)
    assert resolve_units(bindings, product_id="P1", unit="box").status == "CONFLICT"


def test_factor_without_declared_base_stays_unknown(tmp_path):
    line = "unit_factor[product=P1]: carton = 24 can"
    bindings = world(tmp_path, [line])
    edge = proposal(
        bindings,
        line,
        "unit",
        "carton",
        unit_product="P1",
        unit_target="can",
        unit_factor=Decimal("24"),
        unit_base=False,
    )
    bindings = replace(bindings, entries=(ResolvedBinding(edge, "ACCEPTED", "model assertion"),))
    assert resolve_units(bindings, product_id="P1", unit="carton").factor is None


def test_disconnected_conflicting_cycle_blocks_product_scope(tmp_path):
    lines = [
        "unit_base[product=P1]: tablet",
        "unit_factor[product=P1]: box = 2 tray",
        "unit_factor[product=P1]: tray = 2 pack",
        "unit_factor[product=P1]: box = 5 pack",
    ]
    bindings = world(tmp_path, lines)
    values = (
        ("tablet", None, None, True),
        ("box", "tray", Decimal("2"), False),
        ("tray", "pack", Decimal("2"), False),
        ("box", "pack", Decimal("5"), False),
    )
    entries = tuple(
        ResolvedBinding(
            proposal(
                bindings,
                line,
                "unit",
                value,
                unit_product="P1",
                unit_target=target,
                unit_factor=factor,
                unit_base=base,
            ),
            "ACCEPTED",
            "test",
        )
        for line, (value, target, factor, base) in zip(lines, values, strict=True)
    )
    bindings = replace(bindings, entries=entries)
    assert resolve_units(bindings, product_id="P1", unit="tablet").status == "CONFLICT"


def test_preconverted_quantity_mismatch_does_not_mutate_values(tmp_path):
    from datamate.preparation.catalog import reconcile_quantity

    result = reconcile_quantity(Decimal("24"), Decimal("20"), Decimal("1"))
    assert result.raw_quantity == Decimal("24")
    assert result.base_quantity == Decimal("20")
    assert result.expected_base_quantity == Decimal("24")
    assert result.mismatch is True


def test_24_can_and_20_bottle_packs_remain_product_specific(tmp_path):
    from datamate.preparation.catalog import reconcile_quantity

    lines = [
        "unit_base[product=CANS]: can",
        "unit_factor[product=CANS]: pack = 24 can",
        "unit_base[product=BOTTLES]: bottle",
        "unit_factor[product=BOTTLES]: pack = 20 bottle",
    ]
    bindings = world(tmp_path, lines)
    declarations = (
        ("CANS", "can", None, None, True),
        ("CANS", "pack", "can", Decimal("24"), False),
        ("BOTTLES", "bottle", None, None, True),
        ("BOTTLES", "pack", "bottle", Decimal("20"), False),
    )
    entries = tuple(
        ResolvedBinding(
            proposal(
                bindings,
                line,
                "unit",
                value,
                unit_product=product,
                unit_target=target,
                unit_factor=factor,
                unit_base=base,
            ),
            "ACCEPTED",
            "model assertion",
        )
        for line, (product, value, target, factor, base) in zip(lines, declarations, strict=True)
    )
    bindings = replace(bindings, entries=entries)
    cans = resolve_units(bindings, product_id="CANS", unit="pack")
    bottles = resolve_units(bindings, product_id="BOTTLES", unit="pack")
    assert (cans.factor, cans.base_unit) == (Decimal("24"), "can")
    assert (bottles.factor, bottles.base_unit) == (Decimal("20"), "bottle")
    assert resolve_units(bindings, product_id="CANS", unit="bottle").factor is None
    assert reconcile_quantity(Decimal("1"), Decimal("20"), cans.factor).mismatch is True


def test_unit_fields_cannot_attach_to_other_binding_kind(tmp_path):
    bindings = world(tmp_path, ["unrelated declaration"])
    with pytest.raises(ValueError, match="unit fields"):
        proposal(bindings, "unrelated declaration", "role", "quantity", unit_base=False)


def test_unit_factors_do_not_round_to_ambient_decimal_context(tmp_path):
    from decimal import localcontext

    from datamate.preparation.catalog import reconcile_quantity

    factor = Decimal("12345678901234567890123456789012345678")
    lines = ["unit_base[product=P1]: tablet", f"unit_factor[product=P1]: box = {factor} tablet"]
    bindings = world(tmp_path, lines)
    base = proposal(bindings, lines[0], "unit", "tablet", unit_product="P1", unit_base=True)
    edge = proposal(
        bindings,
        lines[1],
        "unit",
        "box",
        unit_product="P1",
        unit_base=False,
        unit_target="tablet",
        unit_factor=factor,
    )
    bindings = replace(
        bindings, entries=tuple(ResolvedBinding(p, "ACCEPTED", "fixture") for p in (base, edge))
    )
    with localcontext() as context:
        context.prec = 7
        assert resolve_units(bindings, product_id="P1", unit="box").factor == factor
        result = reconcile_quantity(
            Decimal("123456789012345678901234567890.10"),
            Decimal("1234567890123456789012345678901.00"),
            Decimal("10"),
        )
        assert result.mismatch is False
        assert result.expected_base_quantity.as_tuple().exponent == -2


def test_nonterminating_inverse_factor_is_unresolved_instead_of_rounded(tmp_path):
    lines = ["unit_base[product=P1]: tablet", "unit_factor[product=P1]: tablet = 3 box"]
    bindings = world(tmp_path, lines)
    base = proposal(bindings, lines[0], "unit", "tablet", unit_product="P1", unit_base=True)
    edge = proposal(
        bindings,
        lines[1],
        "unit",
        "tablet",
        unit_product="P1",
        unit_base=False,
        unit_target="box",
        unit_factor=Decimal("3"),
    )
    bindings = replace(
        bindings, entries=tuple(ResolvedBinding(p, "ACCEPTED", "fixture") for p in (base, edge))
    )
    result = resolve_units(bindings, product_id="P1", unit="box")
    assert result.status == "UNRESOLVED" and result.factor is None
