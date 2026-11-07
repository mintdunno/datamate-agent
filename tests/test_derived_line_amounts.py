"""Source declarations, rather than arithmetic fit, authorize derived line amounts."""

import json

import duckdb

from datamate.preparation.catalog import compile_clauses
from datamate.preparation.discovery import sha256_file
from datamate.preparation.loaders import load_tables
from datamate.preparation.models import Binding, EvidenceRef, SourceFile
from datamate.preparation.profiling import profile_table
from datamate.preparation.storage import create_storage, stage_table
from datamate.preparation.v2 import _source_roles, build_candidate, registry_plan


def formula_world(tmp_path, *, basis="source_quantity_unit", observed=False):
    shop = tmp_path / "data/shop"
    shop.mkdir(parents=True)
    rules = tmp_path / "rulebook.md"
    rules.write_text(
        "# Rules\nline_net_amount = quantity * unit_price - line_discount_amount; "
        f"price_basis: {basis}; rounding: none\n",
        encoding="utf-8",
    )
    path = shop / "sales.csv"
    extra = ",line_net_amount" if observed else ""
    path.write_text(
        f"quantity,unit_price,line_discount_amount{extra}\n2,100,10"
        + (",190" if observed else "")
        + "\n",
        encoding="utf-8",
    )
    source = SourceFile(path, "data/shop/sales.csv", sha256_file(path))
    clauses = compile_clauses(
        (SourceFile(rules, "rulebook.md", sha256_file(rules)),), split_id="train"
    )
    db = duckdb.connect()
    create_storage(db)
    table = stage_table(db, source, next(load_tables(path)))
    profile = profile_table(db, table.table_id)
    base = _source_roles([profile], clauses, ())
    roles = tuple(b for b in base.accepted if b.kind == "role")
    clause = next(c for c in clauses if "line_net_amount =" in c.quote)
    formula = Binding(
        kind="measure",
        table_id=table.table_id,
        columns=("c0001", "c0002", "c0003"),
        value="line_amount:minus_line_discount",
        profile_digest=profile.digest,
        dependencies=tuple(
            b.binding_id
            for b in roles
            if b.value in {"quantity", "unit_price", "line_discount_amount"}
        ),
        evidence=(
            EvidenceRef(
                source="rulebook.md",
                sha256=clause.source_document.sha256,
                locator=f"clause:{clause.clause_id}",
                line_start=clause.line_start,
                line_end=clause.line_end,
                quote_sha256=clause.quote_sha256,
            ),
        ),
    )
    bindings = _source_roles([profile], clauses, (formula,))
    db.execute(
        "CREATE TABLE meta.transform_events(record_id VARCHAR,column_id VARCHAR,step_id VARCHAR,"
        "status VARCHAR,before_json JSON,after_json JSON,reason VARCHAR)"
    )
    return db, bindings


def test_source_declared_formula_produces_separate_derived_value(tmp_path):
    db, bindings = formula_world(tmp_path)
    with db:
        assert any(b.kind == "measure" for b in bindings.accepted)
        plan = registry_plan(bindings)
        assert any(s.operation.name == "derive_line_amount" for s in plan.steps)
        build_candidate(db, plan, bindings)
        row = json.loads(
            db.execute(
                "SELECT values_json FROM meta.facts WHERE table_role='physical_row'"
            ).fetchone()[0]
        )
        assert row["derived_line_net_amount"]["value"] == "190"
        assert row["c0001"]["value"] == "2"
        assert row["c0002"]["value"] == "100"


def test_formula_with_unproven_price_basis_cannot_authorize_derivation(tmp_path):
    db, bindings = formula_world(tmp_path, basis="base_unit")
    with db:
        assert not any(b.kind == "measure" for b in bindings.accepted)
        assert not any(
            s.operation.name == "derive_line_amount" for s in registry_plan(bindings).steps
        )


def test_existing_source_amount_is_not_replaced_by_derivation(tmp_path):
    db, bindings = formula_world(tmp_path, observed=True)
    with db:
        assert not any(
            s.operation.name == "derive_line_amount" for s in registry_plan(bindings).steps
        )


def test_independent_formula_audit_detects_candidate_corruption(tmp_path):
    from datamate.preparation.audit import audit_prepared

    db, bindings = formula_world(tmp_path)
    with db:
        build_candidate(db, registry_plan(bindings), bindings)
        good = audit_prepared(db, bindings)
        assert (
            next(f.status for f in good.findings if f.check_id == "source_line_formula") == "PASS"
        )
        row = db.execute(
            "SELECT fact_id,values_json FROM meta.facts WHERE table_role='physical_row'"
        ).fetchone()
        payload = json.loads(row[1])
        payload["derived_line_net_amount"]["value"] = "200"
        db.execute(
            "UPDATE meta.facts SET values_json=? WHERE fact_id=?", [json.dumps(payload), row[0]]
        )
        bad = audit_prepared(db, bindings)
        assert next(f.status for f in bad.findings if f.check_id == "source_line_formula") == "FAIL"
