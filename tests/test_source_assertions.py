"""Fixed source assertions must become executable evaluator targets, never solver inputs."""

import importlib
import json
from decimal import Decimal

import duckdb
import pytest
from test_prepared_observations import accepted, world

from datamate.evaluation.normalization import evaluate_reference_proposals
from datamate.evaluation.normalization_campaign import score_normalization
from datamate.evaluation.prepared_observations import (
    evaluate_prepared_cases,
    migrate_reference_suite,
)
from datamate.preparation.models import RawDisposition
from datamate.preparation.storage import store_disposition


def api():
    return importlib.import_module("datamate.evaluation.source_assertions")


def assertion(case, kind, expected, **kwargs):
    evidence = [
        {
            "source": case.source,
            "sha256": case.source_sha256,
            "locator": f"raw:{case.record_id}/{case.column_id}",
        },
        {
            "source": "data/a/README.md",
            "sha256": "b" * 64,
            "locator": "md:1-1",
            "line_start": 1,
            "line_end": 1,
        },
    ]
    return api().SourceAssertion(
        case_id=case.case_id,
        kind=kind,
        expected_value=expected,
        evidence=tuple(evidence),
        reason="Independent source review",
        **kwargs,
    )


def revised(manifest, item, reader):
    old = evaluate_reference_proposals(manifest, [], {"a": reader}).suite
    old = migrate_reference_suite(old, manifest, [], {"a": reader})
    before = old.model_dump_json()
    new = api().revise_development_suite(old, manifest, [item], {"a": reader})
    assert old.model_dump_json() == before
    return new


@pytest.mark.parametrize(
    "raw,kind,header,quote,expected",
    [
        (
            "0123456789",
            "phone",
            "Điện thoại",
            "Điện thoại: liên lạc",
            {"type": "str", "value": "0123456789"},
        ),
        (
            30,
            "quantity",
            "Tồn cuối kỳ",
            "Tồn cuối kỳ theo đơn vị cơ bản",
            {"type": "int", "value": "30"},
        ),
        (
            "A + B",
            "ingredient",
            "Hoạt chất",
            "Hoạt chất gồm nhiều chất nối bằng +",
            {"type": "str", "value": "A + B"},
        ),
        (
            "sửa chữa",
            "expense",
            "Mục",
            "Sổ chi phí: các khoản chi vận hành",
            {"type": "str", "value": "sửa chữa"},
        ),
    ],
)
def test_declared_preservation_reaches_actual_artifact(
    tmp_path, raw, kind, header, quote, expected
):
    path, manifest = world(tmp_path, raw, header=header)
    case = manifest.cases[0]
    item = assertion(case, kind, expected)

    def reader(ref):
        return (
            {"quote": quote}
            if ref.line_start
            else {"cell": {"type": type(raw).__name__, "value": str(raw)}, "header": header}
        )

    suite = revised(manifest, item, reader)
    assert suite.references[0].determinate
    accepted(path, case, raw)
    result = evaluate_prepared_cases(path, [case], suite)
    assert result.case_results[0].value == expected
    assert score_normalization(result, suite).verified_determinate_successes == 1


def test_raw_copy_without_declared_preservation_cannot_promote(tmp_path):
    _, manifest = world(tmp_path, "arbitrary")
    case = manifest.cases[0]
    item = assertion(case, "phone", {"type": "str", "value": "arbitrary"})
    suite = revised(
        manifest,
        item,
        lambda ref: (
            {"quote": "General notes"}
            if ref.line_start
            else {"cell": {"type": "str", "value": "arbitrary"}, "header": "note"}
        ),
    )
    assert not suite.references[0].determinate


@pytest.mark.parametrize("kind", ["monthly_date", "size", "integer"])
def test_derived_assertions_are_checked_from_sources(tmp_path, kind):
    raw, header, quote, expected = {
        "monthly_date": (
            "01/02/2025",
            "date",
            "sales.csv: mỗi tháng một sheet",
            {"type": "date", "value": "2025-02-01"},
        ),
        "size": (
            "Drink (M)",
            "name",
            "Size M, size L: tên kết thúc bằng (M) hoặc (L)",
            {"type": "str", "value": "M"},
        ),
        "integer": ("13k", "Tổng tiền", "Tiền là số nguyên VND", {"type": "int", "value": "13000"}),
    }[kind]
    _, manifest = world(tmp_path, raw, header=header)
    case = manifest.cases[0]
    item = assertion(case, kind, expected)

    def reader(ref):
        return (
            {"quote": quote}
            if ref.line_start
            else {"cell": {"type": "str", "value": raw}, "header": header, "table_name": "T02-2025"}
        )

    suite = revised(manifest, item, reader)
    assert suite.references[0].determinate
    wrong_value = (
        "1900-01-01" if kind == "monthly_date" else "0" if kind == "integer" else "invented"
    )
    wrong = item.model_copy(update={"expected_value": expected | {"value": wrong_value}})
    assert not revised(manifest, wrong, reader).references[0].determinate


def test_ambiguous_date_and_currency_are_not_guessed(tmp_path):
    _, manifest = world(tmp_path)
    case = manifest.cases[0]
    for kind, raw, expected in [
        ("monthly_date", "01/02/2025", {"type": "date", "value": "2025-02-01"}),
        ("integer", "201,600", {"type": "int", "value": "201600"}),
    ]:
        item = assertion(case, kind, expected)

        def reader(ref, raw=raw):
            return (
                {"quote": "Generic output format"}
                if ref.line_start
                else {
                    "cell": {"type": "str", "value": raw},
                    "header": "Số tiền thu",
                    "table_name": "Sheet1",
                }
            )

        assert not revised(manifest, item, reader).references[0].determinate


def test_header_disposition_requires_candidate_disposition(tmp_path):
    path, manifest = world(tmp_path, "data", header="invoice_id")
    case = manifest.cases[0].model_copy(update={"record_id": manifest.cases[0].table_id + ":1"})
    manifest = manifest.model_copy(update={"cases": (case, *manifest.cases[1:])})
    item = assertion(case, "header", {"kind": "structural"})

    def reader(ref):
        return (
            {"quote": "Column header invoice_id"}
            if ref.line_start
            else {
                "cell": {"type": "str", "value": "invoice_id"},
                "record_kind": "header",
                "source_row": 1,
                "header_row": 1,
                "header": "invoice_id",
            }
        )

    suite = revised(manifest, item, reader)
    assert suite.references[0].determinate
    assert evaluate_prepared_cases(path, [case], suite).case_results[0].status == "ABSTAINED"
    with duckdb.connect(str(path)) as db:
        store_disposition(db, RawDisposition(case.record_id, "structural", "source header", ()))
    result = evaluate_prepared_cases(path, [case], suite)
    assert result.case_results[0].value == {"kind": "structural"}
    assert score_normalization(result, suite).verified_determinate_successes == 1


def test_size_requires_derived_attribute_and_preserved_identity(tmp_path):
    path, manifest = world(tmp_path, "Drink (M)", header="name")
    case = manifest.cases[0]
    item = assertion(case, "size", {"type": "str", "value": "M"})

    def reader(ref):
        return (
            {"quote": "Size M, size L: tên kết thúc bằng (M) hoặc (L)"}
            if ref.line_start
            else {"cell": {"type": "str", "value": "Drink (M)"}, "header": "name"}
        )

    suite = revised(manifest, item, reader)
    accepted(path, case, "Drink (M)")
    assert evaluate_prepared_cases(path, [case], suite).case_results[0].status == "ABSTAINED"
    with duckdb.connect(str(path)) as db:
        payload = {
            "c0001": {"type": "str", "value": "Drink (M)"},
            "size": {"type": "str", "value": "M"},
        }
        db.execute("UPDATE meta.facts SET values_json=?", [json.dumps(payload)])
    assert evaluate_prepared_cases(path, [case], suite).case_results[0].value == {
        "type": "str",
        "value": "M",
    }
    with duckdb.connect(str(path)) as db:
        payload["c0001"] = {"type": "str", "value": "M"}
        db.execute("UPDATE meta.facts SET values_json=?", [json.dumps(payload)])
    assert evaluate_prepared_cases(path, [case], suite).case_results[0].status == "ABSTAINED"


@pytest.mark.parametrize(
    "value,want", [(Decimal("13000.00"), "13000"), (Decimal("13000.01"), None)]
)
def test_integral_decimal_is_numeric_without_rounding(tmp_path, value, want):
    path, manifest = world(tmp_path, "13k", header="amount")
    case = manifest.cases[0]
    item = assertion(case, "integer", {"type": "int", "value": "13000"})

    def reader(ref):
        return (
            {"quote": "Tiền là số nguyên VND"}
            if ref.line_start
            else {"cell": {"type": "str", "value": "13k"}, "header": "amount"}
        )

    suite = revised(manifest, item, reader)
    accepted(path, case, value)
    result = evaluate_prepared_cases(path, [case], suite).case_results[0]
    assert result.value == (None if want is None else {"type": "int", "value": want})


def test_assertions_reject_model_verification_fields_and_checkpoint(tmp_path):
    _, manifest = world(tmp_path)
    case = manifest.cases[0]
    with pytest.raises(ValueError):
        assertion(case, "header", {"kind": "structural"}, verified=True)
    item = assertion(case, "header", {"kind": "structural"})
    changed = manifest.model_copy(
        update={"cases": (case.model_copy(update={"partition": "checkpoint"}), *manifest.cases[1:])}
    )
    with pytest.raises(ValueError, match="development"):
        revised(changed, item, lambda ref: {})


@pytest.mark.parametrize("broken", [False, "key", "unit", "collision"])
def test_single_comma_amount_needs_native_price_and_connected_keys(tmp_path, broken):
    _, manifest = world(tmp_path, "201,600", header="amount")
    case = manifest.cases[0]
    primary = {"cell": {"type": "str", "value": "201,600"}, "header": "amount"}
    refs = [
        {
            "source": case.source,
            "sha256": case.source_sha256,
            "locator": f"raw:{case.record_id}/c0002",
        },
        {"source": case.source, "sha256": case.source_sha256, "locator": "raw:line:7/c0001"},
        {"source": case.source, "sha256": case.source_sha256, "locator": "raw:line:7/c0002"},
        {"source": case.source, "sha256": case.source_sha256, "locator": "raw:line:7/c0003"},
        {"source": case.source, "sha256": case.source_sha256, "locator": "raw:product:5/c0001"},
        {"source": case.source, "sha256": case.source_sha256, "locator": "raw:product:5/c0002"},
    ]
    item = assertion(case, "integer", {"type": "int", "value": "201600"})
    item = item.model_copy(
        update={
            "evidence": (*item.evidence, *(type(item.evidence[0]).model_validate(r) for r in refs)),
            "anchor_index": 7,
            "quantity_index": 5,
            "links": ((2, 3), (4, 6)),
        }
    )
    observations = [
        primary,
        {"quote": "Tiền là số nguyên VND"},
        {"cell": {"type": "str", "value": "O1"}, "header": "order_id"},
        {"cell": {"type": "str", "value": "O1"}, "header": "order_id"},
        {"cell": {"type": "str", "value": "P1"}, "header": "product_id"},
        {"cell": {"type": "int", "value": "1"}, "header": "quantity", "unit": "piece"},
        {
            "cell": {"type": "str", "value": "P2" if broken == "key" else "P1"},
            "header": "product_id",
        },
        {
            "cell": {"type": "int", "value": "201600"},
            "header": "unit_price",
            "unit": "box" if broken == "unit" else "piece",
        },
    ]
    lookup = dict(zip((r.locator for r in item.evidence), observations, strict=True))

    def reader(ref):
        return lookup[ref.locator]

    reader.key_count = lambda ref, key: 2 if broken == "collision" else 1
    reader.key_column_declared = lambda ref: True
    suite = revised(manifest, item, reader)
    assert suite.references[0].determinate is (not broken)


def test_unrelated_quote_cannot_promote_header_metadata(tmp_path):
    _, manifest = world(tmp_path)
    case = manifest.cases[0]
    item = assertion(case, "header", {"kind": "structural"})

    def reader(ref):
        return (
            {"quote": "unrelated notes"}
            if ref.line_start
            else {
                "cell": {"type": "str", "value": "id"},
                "record_kind": "header",
                "source_row": 1,
                "header_row": 1,
                "header": "id",
            }
        )

    assert not revised(manifest, item, reader).references[0].determinate


def test_monthly_scope_must_declare_the_actual_file(tmp_path):
    _, manifest = world(tmp_path, "01/02/2025")
    case = manifest.cases[0]
    item = assertion(case, "monthly_date", {"type": "date", "value": "2025-02-01"})

    def reader(ref):
        return (
            {"quote": "other.csv: mỗi tháng một sheet\nsales.csv exists"}
            if ref.line_start
            else {
                "cell": {"type": "str", "value": "01/02/2025"},
                "table_name": "T02-2025",
                "header": "date",
            }
        )

    assert not revised(manifest, item, reader).references[0].determinate
