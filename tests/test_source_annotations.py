"""Pre-header annotations travel through the real proposal, audit and SQL boundary."""

import asyncio
import json

import duckdb
import httpx
import pytest
from openpyxl import Workbook

from datamate.inference.budget import RequestBudget
from datamate.preparation.orchestration import prepare_with_mapping_recovery
from datamate.preparation.pipeline import prepare_business
from datamate.preparation.v2 import PreparationPolicy
from datamate.runtime.tools import StructuredTools


def annotation_split(folder):
    shop = folder / "data/shop"
    shop.mkdir(parents=True)
    (folder / "rulebook.md").write_text(
        "# Rules\nDo not invent order-status completeness.\n", encoding="utf-8"
    )
    (folder / "calendar.csv").write_text(
        "date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n"
        "2025-01-01,3,2,12,2024,false,\n",
        encoding="utf-8",
    )
    (shop / "README.md").write_text(
        "# Sales\nEach data row records one product.\n", encoding="utf-8"
    )
    book = Workbook()
    sheet = book.active
    sheet.title = "Monthly"
    sheet.append(["status", "approved", None, None, None])
    sheet.append([None] * 5)
    sheet.append(["order_id", "status", "product_name", "quantity", "unit"])
    sheet.append(["O1", "complete", "Alpha", 2, "piece"])
    sheet.append(["O2", "cancelled", "Beta", 3, "box"])
    book.save(shop / "sales.xlsx")
    return folder


def test_metadata_issue_is_marked_without_pretending_it_is_clean(tmp_path):
    split = annotation_split(tmp_path / "train")
    prepared = prepare_business(
        split, "shop", tmp_path / "cache", split_id="train", policy=PreparationPolicy()
    )
    assert any(i["kind"] == "unresolved_preheader_metadata" for i in prepared.catalog["issues"])
    assert not prepared.catalog["projections"]


@pytest.mark.parametrize("valid", [True, False])
def test_typed_metadata_proposal_is_applied_or_rolled_back(tmp_path, monkeypatch, valid):
    from test_adaptive_normalization import adaptive_profile, typed_response

    monkeypatch.setenv("LLM_API_KEY", "fixture-key")
    split = annotation_split(tmp_path / "train")
    requests = []

    def respond(request):
        body = json.loads(request.content)
        assert set(body["tools"][0]["function"]["parameters"]["properties"]) == {"source_metadata"}
        context = json.loads(body["messages"][-1]["content"].split("SOURCE_CONTEXT\n", 1)[1])
        table = context["source_samples"]["tables"][0]
        record = table["preamble_rows"][0]
        assert record["values"]["c0001"]["value"] == "status"
        requests.append(body)
        output = {
            "source_metadata": [
                {
                    "record_id": record["record_id"] if valid else table["rows"][0]["record_id"],
                    "label_column": "c0001",
                    "value_column": "c0002",
                }
            ],
        }
        return typed_response(body, output)

    budget = RequestBudget(tmp_path / "budget.sqlite", run_id="annotation", run_limit=1)
    result = asyncio.run(
        prepare_with_mapping_recovery(
            split,
            "shop",
            tmp_path / "cache",
            split_id="train",
            run_dir=tmp_path / "run",
            allow_model_calls=True,
            profile=adaptive_profile(),
            budget=budget,
            transport=httpx.MockTransport(respond),
        )
    )
    assert len(requests) == budget.snapshot()["run_attempts"] == 1
    if not valid:
        assert result.status == "PARTIAL"
        assert result.prepared.fingerprint == result.baseline.fingerprint
        return
    assert result.status == "MAPPING_ACCEPTED"
    projection = next(
        p for p in result.prepared.catalog["projections"] if p.get("kind") == "source_fields"
    )
    annotation = projection["source_field_policy"]["source_annotations"][0]
    assert annotation["key"] == "status" and annotation["value"] == "approved"
    assert annotation["scope_semantics"] == "UNRESOLVED"
    assert projection["source_field_policy"]["measure_qualification"] == "UNRESOLVED"
    assert result.prepared.catalog["qualification"]["order_lines"] == "UNRESOLVED"
    tools = StructuredTools(result.prepared.database, result.prepared.catalog)
    assert tools.execute_sql(f"SELECT count(*) FROM clean.{projection['scope']}")["rows"] == [[2]]
    with duckdb.connect(str(result.prepared.database), read_only=True) as db:
        assert db.execute(
            "SELECT d.kind FROM meta.dispositions d JOIN staging.raw_rows r "
            "USING(record_id) WHERE r.kind='preamble' ORDER BY ordinal LIMIT 1"
        ).fetchone() == ("structural",)
        assert db.execute("SELECT count(*) FROM meta.source_annotations").fetchone() == (1,)
    assert not any(
        i["kind"] == "unresolved_preheader_metadata" for i in result.prepared.catalog["issues"]
    )


@pytest.fixture(scope="module")
def annotation_baseline(tmp_path_factory):
    folder = tmp_path_factory.mktemp("annotation-guards")
    split = annotation_split(folder / "train")
    prepared = prepare_business(
        split, "shop", folder / "cache", split_id="train", policy=PreparationPolicy()
    )
    return split, prepared


@pytest.mark.parametrize(
    "case",
    [
        "data_row",
        "extra_cell",
        "formula",
        "unknown_key",
        "header_echo",
        "numeric_value",
        "stale_witness",
    ],
)
def test_source_validator_rejects_invalid_metadata_shapes(annotation_baseline, tmp_path, case):
    import shutil

    from datamate.preparation.annotations import enrich_source_annotations
    from datamate.preparation.catalog import compile_clauses
    from datamate.preparation.discovery import discover_business
    from datamate.preparation.mapping import (
        MappingProposal,
        SourceMetadataMapping,
        mapping_bindings,
    )
    from datamate.preparation.models import TableProfile
    from datamate.preparation.v2 import _source_roles

    split, prepared = annotation_baseline
    profile = next(
        TableProfile.model_validate_json(json.dumps(p))
        for p in prepared.catalog["tables"]
        if p["source"].endswith("sales.xlsx")
    )
    inputs = discover_business(split, "shop", split_id="train")
    clauses = compile_clauses(inputs.documents, split_id="train")
    database = tmp_path / "candidate.duckdb"
    shutil.copy2(prepared.database, database)
    marker = profile.table_id + (":4" if case == "data_row" else ":1")
    proposal = MappingProposal(
        source_metadata=(
            SourceMetadataMapping(
                record_id=marker,
                label_column="c0001",
                value_column="c0002",
            ),
        )
    )
    (binding,) = mapping_bindings(proposal, profile, clauses)
    with duckdb.connect(str(database)) as db:
        if case in {"extra_cell", "unknown_key", "header_echo", "numeric_value"}:
            values = json.loads(
                db.execute(
                    "SELECT values_json FROM staging.raw_rows WHERE record_id=?", [marker]
                ).fetchone()[0]
            )
            if case == "extra_cell":
                values[3] = {"type": "int", "value": "7"}
            elif case == "unknown_key":
                values[0] = {"type": "str", "value": "unknown key"}
            elif case == "header_echo":
                values[1] = {"type": "str", "value": "quantity"}
            else:
                values[1] = {"type": "int", "value": "7"}
            db.execute(
                "UPDATE staging.raw_rows SET values_json=? WHERE record_id=?",
                [json.dumps(values), marker],
            )
        if case == "formula":
            db.execute(
                "UPDATE staging.raw_rows SET formulas_json=? WHERE record_id=?",
                [json.dumps(["=A9", None, None, None, None]), marker],
            )
        if case == "stale_witness":
            evidence = binding.evidence[0].model_copy(update={"sha256": "0" * 64})
            binding = binding.model_copy(update={"evidence": (evidence, *binding.evidence[1:])})
        bindings = _source_roles([profile], clauses, (binding,))
        checked = enrich_source_annotations(db, [profile], bindings, record_receipts=False)
        assert binding not in checked.accepted


def test_copied_annotation_context_must_match_independently_audited_receipt(
    annotation_baseline, tmp_path
):
    from datamate.preparation.annotations import enrich_source_annotations
    from datamate.preparation.audit import audit_prepared
    from datamate.preparation.catalog import compile_clauses
    from datamate.preparation.discovery import discover_business
    from datamate.preparation.mapping import (
        MappingProposal,
        SourceMetadataMapping,
        mapping_bindings,
    )
    from datamate.preparation.models import TableProfile
    from datamate.preparation.v2 import _source_roles

    split, baseline = annotation_baseline
    profile = next(
        TableProfile.model_validate_json(json.dumps(p))
        for p in baseline.catalog["tables"]
        if p["source"].endswith("sales.xlsx")
    )
    clauses = compile_clauses(
        discover_business(split, "shop", split_id="train").documents, split_id="train"
    )
    proposal = MappingProposal(
        source_metadata=(
            SourceMetadataMapping(
                record_id=profile.table_id + ":1",
                label_column="c0001",
                value_column="c0002",
            ),
        )
    )
    supplied = mapping_bindings(proposal, profile, clauses)
    prepared = prepare_business(
        split,
        "shop",
        tmp_path / "accepted",
        split_id="train",
        policy=PreparationPolicy(binding_proposals=supplied),
    )
    with duckdb.connect(str(prepared.database)) as db:
        profiles = [
            TableProfile.model_validate_json(json.dumps(p)) for p in prepared.catalog["tables"]
        ]
        bindings = enrich_source_annotations(
            db, profiles, _source_roles(profiles, clauses, supplied), record_receipts=False
        )
        scope, payload = db.execute(
            "SELECT scope,metadata_json FROM meta.source_field_descriptors"
        ).fetchone()
        metadata = json.loads(payload)
        metadata["source_annotations"][0]["value"] = "INVENTED"
        db.execute(
            "UPDATE meta.source_field_descriptors SET metadata_json=? WHERE scope=?",
            [json.dumps(metadata), scope],
        )
        report = audit_prepared(db, bindings)
        assert any(f.scope == scope and f.status == "FAIL" for f in report.findings)
