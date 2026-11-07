"""Candidate storage preserves wire values and transaction constraints in batches."""

import json
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from datamate.preparation.models import CandidateFact, LineageEdge, RawDisposition
from datamate.preparation.normalization import encoded
from datamate.preparation.operations import BatchResult, TransformEvent
from datamate.preparation.storage import create_storage
from datamate.preparation.v2 import _store_result


def storage():
    db = duckdb.connect()
    create_storage(db)
    db.execute(
        "CREATE TABLE meta.transform_events(record_id VARCHAR,column_id VARCHAR,"
        "step_id VARCHAR,status VARCHAR,before_json JSON,after_json JSON,reason VARCHAR)"
    )
    return db


def result(*, duplicate_fact=False):
    values = {
        "text": 'Tiếng Việt 雪 " \\',
        "missing": None,
        "amount": Decimal("123456789012345678.001"),
        "day": date(2026, 11, 7),
        "bytes": b"\xff\x00",
    }
    fact = CandidateFact("f1", "physical_row", values)
    return BatchResult(
        facts=(fact, fact) if duplicate_fact else (fact,),
        dispositions=(
            RawDisposition("r1", "retained", "source"),
            RawDisposition("r2", "duplicate", "same", duplicate_of="r1"),
        ),
        lineage=(LineageEdge("f1", "r1", "c1", "s1"),),
        events=(TransformEvent("r1", "c1", "s1", "ACCEPTED", None, "viên", "source"),),
    )


def test_result_batch_preserves_exact_wire_values_and_nullable_fields():
    batch = result()
    with storage() as db:
        db.execute("BEGIN")
        _store_result(db, batch)
        db.execute("COMMIT")
        assert db.execute(
            "SELECT record_id,duplicate_of,evidence_json FROM meta.dispositions ORDER BY record_id"
        ).fetchall() == [("r1", None, "[]"), ("r2", "r1", "[]")]
        wire = json.loads(db.execute("SELECT values_json FROM meta.facts").fetchone()[0])
        assert wire == {key: encoded(value) for key, value in batch.facts[0].values.items()}
        assert db.execute("SELECT * FROM meta.lineage").fetchall() == [("f1", "r1", "c1", "s1")]
        before, after = db.execute(
            "SELECT before_json,after_json FROM meta.transform_events"
        ).fetchone()
        assert json.loads(before) == encoded(None)
        assert json.loads(after) == encoded("viên")


def test_result_constraint_failure_remains_inside_preparation_transaction():
    with storage() as db:
        db.execute("INSERT INTO meta.facts VALUES ('sentinel','physical_row','{}')")
        db.execute("BEGIN")
        with pytest.raises(duckdb.ConstraintException):
            _store_result(db, result(duplicate_fact=True))
        db.execute("ROLLBACK")
        assert db.execute("SELECT fact_id FROM meta.facts").fetchall() == [("sentinel",)]
        assert db.execute("SELECT count(*) FROM meta.dispositions").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM meta.lineage").fetchone() == (0,)


def test_empty_result_still_refuses_instead_of_publishing_success():
    with storage() as db, pytest.raises(duckdb.InvalidInputException):
        _store_result(db, BatchResult((), (), (), ()))
