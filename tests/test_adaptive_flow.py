"""Synthetic source-only adaptive preparation through the real v2 pipeline."""

import asyncio
import json
import sqlite3
from hashlib import sha256

import duckdb
import httpx
import pytest
from test_adaptive_normalization import adaptive_profile, typed_response

from datamate.inference.budget import RequestBudget
from datamate.preparation import orchestration
from datamate.preparation.models import Binding, EvidenceRef, NormalizationIssue


@pytest.fixture
def split_root(tmp_path):
    root = tmp_path / "split"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    (root / "rulebook.md").write_text("# Rules\nUnknown rules remain unresolved.\n")
    (business / "README.md").write_text(
        "# Sales\nOrder identifiers ignore surrounding whitespace.\nNative source amounts.\n"
    )
    (root / "calendar.csv").write_text(
        "date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n"
        "2025-01-01,3,2,12,2024,false,\n2025-01-02,4,3,12,2024,false,\n"
    )
    with sqlite3.connect(business / "sales.sqlite") as db:
        db.execute(
            "CREATE TABLE sales(order_id TEXT,product_id TEXT,quantity INTEGER,"
            "line_net_amount INTEGER)"
        )
        db.executemany(
            "INSERT INTO sales VALUES (?,?,?,?)",
            [(" A ", " P1 ", 1, 100), (" B ", "P2", 2, 80)],
        )
    return root


def select_identifier_issue(root, baseline):
    """Detect the source-declared whitespace convention without evaluation labels."""
    with duckdb.connect(str(baseline.database), read_only=True) as db:
        bindings = [
            Binding.model_validate_json(row[0])
            for row in db.execute(
                "SELECT binding_json FROM meta.bindings WHERE status='ACCEPTED'"
            ).fetchall()
        ]
    binding = next(b for b in bindings if b.kind == "role" and b.value == "order_id")
    readme = root / "data/shop/README.md"
    rule = readme.read_bytes().splitlines(keepends=True)[1]
    assert rule.decode().strip() == "Order identifiers ignore surrounding whitespace."
    reference = EvidenceRef(
        source="data/shop/README.md",
        sha256=sha256(readme.read_bytes()).hexdigest(),
        locator="line:2",
        line_start=2,
        line_end=2,
        quote_sha256=sha256(rule).hexdigest(),
    )
    return orchestration.IssueSelection(
        issue=NormalizationIssue(
            issue_id="identifier-whitespace",
            scope=binding.table_id,
            kind="normalization",
            detail="Source-declared order identifier whitespace convention is unapplied.",
        ),
        references=(reference,),
    )


def order_rows(prepared):
    with duckdb.connect(str(prepared.database), read_only=True) as db:
        return db.execute(
            "SELECT order_id,product_id,quantity,line_net_amount "
            "FROM clean.order_lines ORDER BY order_id"
        ).fetchall()


def raw_rows(prepared):
    with duckdb.connect(str(prepared.database), read_only=True) as db:
        return db.execute("SELECT * FROM staging.raw_rows ORDER BY record_id").fetchall()


def trim_transport(requests):
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        prompt = body["messages"][0]["content"]
        context = json.loads(prompt.split("\n", 1)[1])
        binding = next(
            b
            for b in context["accepted_bindings"]
            if b["kind"] == "role" and b["value"] == "order_id"
        )
        assert context["table_ids"] == [binding["table_id"]]
        assert all(b["table_id"] == binding["table_id"] for b in context["accepted_bindings"])
        assert "expected_label" not in prompt
        return typed_response(
            body,
            {
                "bindings_digest": context["bindings_digest"],
                "profile_digests": context["profile_digests"],
                "steps": [
                    {
                        "table_id": binding["table_id"],
                        "operation": {"name": "trim_text", "columns": binding["columns"]},
                        "binding_ids": [binding["binding_id"]],
                        "evidence": binding["evidence"],
                    }
                ],
            },
        )

    return httpx.MockTransport(respond)


def run_flow(root, tmp_path, **overrides):
    options = {
        "split_id": "synthetic",
        "run_dir": tmp_path / "run",
        "issue_selector": lambda baseline: select_identifier_issue(root, baseline),
        "verifier": lambda *_: True,
        "allow_model_calls": True,
        "profile": adaptive_profile(),
        "budget": RequestBudget(tmp_path / "budget.sqlite", run_id="flow", run_limit=3),
        "max_rounds": 1,
        "logical_requests_per_round": 2,
        "transport_retries": 0,
    }
    options.update(overrides)
    result = asyncio.run(
        orchestration.prepare_with_recovery(root, "shop", tmp_path / "cache", **options)
    )
    return result, options["budget"]


def test_accepted_flow_replays_delta_and_preserves_raw_and_other_fields(split_root, tmp_path):
    requests = []
    verified = []

    def verifier(issue, baseline, candidate):
        assert issue.scope in {table["table_id"] for table in baseline.catalog["tables"]}
        assert "ignore surrounding whitespace" in (split_root / "data/shop/README.md").read_text()
        before, after = order_rows(baseline), order_rows(candidate)
        verified.append(candidate.database)
        return after == [(row[0].strip(), *row[1:]) for row in before]

    result, budget = run_flow(
        split_root, tmp_path, transport=trim_transport(requests), verifier=verifier
    )
    assert result.recovery.status == "ACCEPTED"
    assert len(verified) == 1
    assert len(requests) == budget.snapshot()["run_attempts"] == 1
    assert requests[0]["max_tokens"] == 2048
    assert result.prepared.database != result.baseline.database
    assert result.prepared.manifest_path.is_file()
    assert result.prepared.catalog["artifact_version"] == 2
    assert json.loads(result.prepared.manifest_path.read_text())["artifact_version"] == 2
    assert [row[0] for row in order_rows(result.baseline)] == [" A ", " B "]
    assert [row[0] for row in order_rows(result.prepared)] == ["A", "B"]
    assert [row[1:] for row in order_rows(result.prepared)] == [
        row[1:] for row in order_rows(result.baseline)
    ]
    assert order_rows(result.prepared)[0][1] == " P1 "
    assert raw_rows(result.prepared) == raw_rows(result.baseline)
    original_steps = result.baseline.catalog["plan"]["steps"]
    candidate_steps = result.prepared.catalog["plan"]["steps"]
    assert [
        step for step in candidate_steps if step["operation"]["name"] != "trim_text"
    ] == original_steps
    scoped = [
        step["operation"]["name"]
        for step in candidate_steps
        if step["table_id"] == result.recovery.accepted_plan.steps[0].table_id
    ]
    assert (
        scoped.index("classify_structure")
        < scoped.index("trim_text")
        < scoped.index("parse_values")
    )
    for scope, status in result.baseline.catalog["qualification"].items():
        if status == "READY":
            assert result.prepared.catalog["qualification"][scope] == "READY"


def test_independent_rejection_keeps_baseline_and_removes_candidate(split_root, tmp_path):
    requests, candidates = [], []

    def reject(_issue, baseline, candidate):
        assert order_rows(candidate) != order_rows(baseline)
        candidates.append(candidate.database)
        return False

    result, budget = run_flow(
        split_root, tmp_path, transport=trim_transport(requests), verifier=reject
    )
    assert result.recovery.status != "ACCEPTED"
    assert result.prepared == result.baseline
    assert result.baseline.manifest_path.is_file()
    assert [row[0] for row in order_rows(result.prepared)] == [" A ", " B "]
    assert candidates and all(not path.exists() for path in candidates)
    assert len(list((tmp_path / "cache").rglob("manifest.json"))) == 1
    assert len(requests) == budget.snapshot()["run_attempts"] <= 2


@pytest.mark.parametrize("disabled", ["model_calls", "no_issue"])
def test_offline_or_no_issue_never_constructs_http_client(
    split_root, tmp_path, monkeypatch, disabled
):
    from datamate.inference import client

    def forbidden(*_args, **_kwargs):
        pytest.fail("No-issue/offline flow constructed an inference client")

    monkeypatch.setattr(client, "inference_model", forbidden)
    options = (
        {"allow_model_calls": False}
        if disabled == "model_calls"
        else {"issue_selector": lambda _: None}
    )
    result, budget = run_flow(
        split_root, tmp_path, transport=httpx.MockTransport(forbidden), **options
    )
    assert result.prepared == result.baseline
    assert budget.snapshot()["run_attempts"] == 0


def test_budget_above_40_rpm_is_rejected_before_http(split_root, tmp_path):
    requests = []
    budget = RequestBudget(tmp_path / "fast.sqlite", run_id="fast", rpm=41, run_limit=3)
    with pytest.raises(ValueError, match="40|rpm|RPM"):
        run_flow(split_root, tmp_path, budget=budget, transport=trim_transport(requests))
    assert requests == []
    assert budget.snapshot()["run_attempts"] == 0


def test_modified_trial_is_rejected_before_cache_publication(split_root, tmp_path):
    def corrupt(_issue, _baseline, candidate):
        with candidate.catalog_path.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        return True

    result, _ = run_flow(split_root, tmp_path, transport=trim_transport([]), verifier=corrupt)
    assert result.status == "PARTIAL"
    assert result.prepared == result.baseline
    assert len(list((tmp_path / "cache").rglob("manifest.json"))) == 1


def test_trials_stage_on_destination_volume_and_new_cache_is_not_hit(split_root, tmp_path):
    locations = []

    def verify(_issue, _baseline, candidate):
        locations.append(candidate.database)
        return candidate.database.is_relative_to(tmp_path / "cache")

    result, _ = run_flow(split_root, tmp_path, transport=trim_transport([]), verifier=verify)
    assert result.status == "ACCEPTED"
    assert locations and all(path.is_relative_to(tmp_path / "cache") for path in locations)
    assert result.prepared.cache_hit is False
