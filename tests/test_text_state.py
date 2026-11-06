"""Receipts preserve raw evidence and require complete declared key relations."""

import hashlib
import sqlite3
from types import SimpleNamespace

import duckdb
import pytest

from datamate.preparation.models import (
    BusinessInputs,
    SemanticBindings,
    SourceFile,
    SourceRow,
    SourceTable,
)
from datamate.preparation.storage import create_storage, iter_raw_batches, stage_table
from datamate.preparation.text_integrity import TextIntegrityPolicy
from datamate.preparation.text_state import (
    apply_text_to_batch,
    audit_text_state,
    finalize_text_state,
    prepare_text_state,
    text_summary,
)


def prepared(tmp_path, *, foreign_key=False, collision=False):
    path = tmp_path / "sales.sqlite"
    damaged = "Tiáº¿ng Viá»‡t"
    with sqlite3.connect(path) as native:
        native.execute("CREATE TABLE products(product_id TEXT PRIMARY KEY)")
        native.execute("INSERT INTO products VALUES ('Tiếng Việt')")
        reference = " REFERENCES products(product_id)" if foreign_key else ""
        native.execute(f"CREATE TABLE sales(product_id TEXT{reference}, note TEXT)")
        native.execute("INSERT INTO sales VALUES (?,?)", [damaged, "FranÃ§ois"])
        if collision:
            native.execute("INSERT INTO sales VALUES ('Tiếng Việt','clean')")
    source = SourceFile(
        path, "data/shop/sales.sqlite", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    inputs = BusinessInputs("demo", "shop", tmp_path, tmp_path, (source,), ())
    db = duckdb.connect()
    db.execute("BEGIN")
    create_storage(db)
    stage_table(
        db, source, SourceTable("products", ("product_id",), None, (SourceRow(1, ("Tiếng Việt",)),))
    )
    rows = [SourceRow(1, (damaged, "FranÃ§ois"))]
    if collision:
        rows.append(SourceRow(2, ("Tiếng Việt", "clean")))
    sales = stage_table(db, source, SourceTable("sales", ("product_id", "note"), None, rows))
    bindings = SemanticBindings((), (), (), (sales.table_id,))
    prepare_text_state(db, inputs, TextIntegrityPolicy())
    finalize_text_state(db, bindings)
    return db, sales


def test_only_nonkeys_overlay_without_declared_relation(tmp_path):
    db, table = prepared(tmp_path)
    try:
        batch = next(iter_raw_batches(db, table.table_id, 10))
        state = SimpleNamespace(
            db=db,
            cells={
                r.record_id: dict(zip(table.columns, r.source.values, strict=True)) for r in batch
            },
            events=[],
            origins={},
            extra_lineage=[],
        )
        apply_text_to_batch(state, batch)
        assert state.cells[batch[0].record_id] == {"c0001": "Tiáº¿ng Viá»‡t", "c0002": "François"}
        assert batch[0].source.values == ("Tiáº¿ng Viá»‡t", "FranÃ§ois")
        assert {event.status for event in state.events} == {"APPLIED", "UNRESOLVED"}
        assert text_summary(db)["unresolved"] == 1
        assert audit_text_state(db).get(table.table_id, False) is False
    finally:
        db.close()


def test_native_foreign_key_accepts_complete_injective_domain(tmp_path):
    db, table = prepared(tmp_path, foreign_key=True)
    try:
        assert text_summary(db)["accepted"] == 2
        assert not audit_text_state(db).get(table.table_id, False)
        proof = db.execute(
            "SELECT proof_json FROM meta.text_integrity WHERE column_id='c0001' "
            "AND status='ACCEPTED'"
        ).fetchone()[0]
        assert "foreign_key" in proof
    finally:
        db.close()


def test_foreign_key_domain_collision_refuses_repair(tmp_path):
    db, _ = prepared(tmp_path, foreign_key=True, collision=True)
    try:
        assert text_summary(db)["unresolved"] == 1
    finally:
        db.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM meta.text_integrity WHERE record_id IS NOT NULL",
        "UPDATE meta.text_integrity SET candidate_json='\"forged\"' WHERE record_id IS NOT NULL",
    ],
)
def test_audit_rejects_missing_or_tampered_receipts(tmp_path, mutation):
    db, _ = prepared(tmp_path)
    try:
        db.execute(mutation)
        with pytest.raises(ValueError):
            audit_text_state(db)
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["generated", "implicit", "composite"])
def test_native_opaque_keys_never_bypass_protection(tmp_path, kind):
    from datamate.preparation.loaders import load_tables

    path = tmp_path / "keys.sqlite"
    with sqlite3.connect(path) as native:
        native.execute(
            "CREATE TABLE products(sku TEXT, variant TEXT, PRIMARY KEY(sku,variant))"
            if kind == "composite"
            else "CREATE TABLE products(sku TEXT PRIMARY KEY)"
        )
        native.execute(
            "INSERT INTO products VALUES ('Tiếng Việt','v')"
            if kind == "composite"
            else "INSERT INTO products VALUES ('Tiếng Việt')"
        )
        ddl = {
            "generated": (
                "CREATE TABLE sales(g TEXT GENERATED ALWAYS AS ('constant') STORED,"
                "ref TEXT REFERENCES products(sku))"
            ),
            "implicit": "CREATE TABLE sales(ref TEXT REFERENCES products)",
            "composite": (
                "CREATE TABLE sales(ref TEXT, variant_ref TEXT, "
                "FOREIGN KEY(ref,variant_ref) REFERENCES products(sku,variant))"
            ),
        }[kind]
        native.execute(ddl)
        if kind == "composite":
            native.executemany(
                "INSERT INTO sales(ref,variant_ref) VALUES (?, 'v')",
                [("Tiáº¿ng Viá»‡t",), ("Tiếng Việt",)],
            )
        else:
            native.executemany(
                "INSERT INTO sales(ref) VALUES (?)", [("Tiáº¿ng Viá»‡t",), ("Tiếng Việt",)]
            )
    source = SourceFile(
        path, "data/shop/keys.sqlite", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    inputs = BusinessInputs("demo", "shop", tmp_path, tmp_path, (source,), ())
    with duckdb.connect() as db:
        db.execute("BEGIN")
        create_storage(db)
        for table in load_tables(path):
            stage_table(db, source, table)
        prepare_text_state(db, inputs, TextIntegrityPolicy())
        finalize_text_state(db, SemanticBindings((), (), (), ()))
        status = db.execute(
            "SELECT status FROM meta.text_integrity WHERE record_id IS NOT NULL"
        ).fetchone()[0]
        assert status == "UNRESOLVED"


def test_all_foreign_key_namespaces_must_agree(tmp_path):
    from datamate.preparation.loaders import load_tables

    path = tmp_path / "multi.sqlite"
    with sqlite3.connect(path) as native:
        native.execute("CREATE TABLE target_a(sku TEXT PRIMARY KEY)")
        native.execute("CREATE TABLE target_b(sku TEXT PRIMARY KEY)")
        native.execute(
            "CREATE TABLE sales(ref TEXT REFERENCES target_a(sku) REFERENCES target_b(sku))"
        )
        native.execute("INSERT INTO target_a VALUES ('Tiếng Việt')")
        native.executemany(
            "INSERT INTO target_b VALUES (?)", [("Tiếng Việt",), ("Tiáº¿ng Viá»‡t",)]
        )
        native.execute("INSERT INTO sales VALUES ('Tiáº¿ng Viá»‡t')")
    source = SourceFile(
        path, "data/shop/multi.sqlite", hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with duckdb.connect() as db:
        create_storage(db)
        for table in load_tables(path):
            stage_table(db, source, table)
        prepare_text_state(
            db,
            BusinessInputs("demo", "shop", tmp_path, tmp_path, (source,), ()),
            TextIntegrityPolicy(),
        )
        finalize_text_state(db, SemanticBindings((), (), (), ()))
        status = db.execute(
            "SELECT status FROM meta.text_integrity x JOIN meta.source_tables t USING(table_id) "
            "WHERE t.table_name='sales'"
        ).fetchone()[0]
        assert status == "UNRESOLVED"
