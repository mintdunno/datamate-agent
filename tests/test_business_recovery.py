"""Contract tests for bounded business mapping recovery; no provider or dataset IO."""

import asyncio
import json
from types import SimpleNamespace

import duckdb
import pytest

from datamate.preparation import business_recovery as recovery
from datamate.preparation.models import Binding, EvidenceRef
from datamate.preparation.v2 import PreparationPolicy


class Budget:
    rpm = 40

    def __init__(self, remaining=10):
        self.attempts = 0
        self.remaining = remaining

    def snapshot(self):
        return {
            "run_attempts": self.attempts,
            "run_remaining": self.remaining,
            "day_remaining": self.remaining,
            "run_input_tokens": 11 * self.attempts,
            "run_output_tokens": 3 * self.attempts,
        }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    inputs = SimpleNamespace(split_root=tmp_path / "source", sources=(), documents=())
    monkeypatch.setattr(recovery, "discover_business", lambda *a, **k: inputs)
    artifacts = []

    def artifact(name, tables=("a", "b"), policy=None, roles=None):
        database = tmp_path / f"{name}.duckdb"
        with duckdb.connect(str(database)) as db:
            db.execute("CREATE SCHEMA meta")
            db.execute("CREATE TABLE meta.bindings(status VARCHAR, binding_json JSON)")
            for table, values in (roles or {}).items():
                for role in values:
                    db.execute(
                        "INSERT INTO meta.bindings VALUES ('ACCEPTED', ?)",
                        [json.dumps({"kind": "role", "table_id": table, "value": role})],
                    )
        result = SimpleNamespace(
            database=database,
            fingerprint=name,
            catalog={
                "status": "PARTIAL",
                "qualification": {},
                "issues": [{"kind": "unmapped_role", "scope": t} for t in tables],
                "inputs": {"policy": (policy or PreparationPolicy()).model_dump(mode="json")},
            },
        )
        artifacts.append(result)
        return result

    baseline = artifact("baseline")
    monkeypatch.setattr(recovery, "prepare_business", lambda *a, **k: baseline)
    kwargs = dict(
        split_root=inputs.split_root,
        business_id="business",
        cache_root=tmp_path / "cache",
        split_id="synthetic",
        run_dir=tmp_path / "run",
    )
    return artifact, baseline, kwargs


def invoke(kwargs, **extra):
    return asyncio.run(recovery.prepare_business_with_recovery(**(kwargs | extra)))


def binding(table):
    return Binding(
        kind="role",
        table_id=table,
        columns=("c1",),
        value="quantity",
        profile_digest="a" * 64,
        evidence=(
            EvidenceRef(source="data/business/source.csv", sha256="a" * 64, locator="header:c1"),
        ),
    )


def test_successive_tables_carry_prior_policy(setup, monkeypatch):
    artifact, baseline, kwargs = setup
    policy = PreparationPolicy(binding_proposals=(binding("a"),))
    first = artifact("first", ("b",), policy)
    second = artifact("second", (), policy)
    seen = []
    budget = Budget()

    async def mapping(*args, **options):
        seen.append(options)
        budget.attempts += 1
        options["run_dir"].mkdir()
        (options["run_dir"] / "report.json").write_text('{"http_attempts": 1}')
        return SimpleNamespace(prepared=[first, second][len(seen) - 1], status="MAPPING_ACCEPTED")

    monkeypatch.setattr(recovery, "prepare_with_mapping_recovery", mapping)
    result = invoke(kwargs, allow_model_calls=True, profile=object(), budget=budget)
    assert [s["table_id"] for s in seen] == ["a", "b"]
    assert seen[1]["policy"].binding_proposals == policy.binding_proposals
    assert all(s["budget"] is budget for s in seen)
    assert result.baseline is baseline and result.prepared is second
    assert result.status == "PARTIAL"
    report = json.loads((kwargs["run_dir"] / "report.json").read_text())
    assert report["http_attempts"] == 2
    assert report["input_tokens"] == 22 and report["output_tokens"] == 6


def test_unchanged_tables_attempted_only_once(setup, monkeypatch):
    _, baseline, kwargs = setup
    seen = []

    async def mapping(*args, **options):
        seen.append(options["table_id"])
        options["run_dir"].mkdir()
        (options["run_dir"] / "report.json").write_text("{}")
        return SimpleNamespace(prepared=baseline, status="PARTIAL")

    monkeypatch.setattr(recovery, "prepare_with_mapping_recovery", mapping)
    result = invoke(kwargs, allow_model_calls=True, profile=object(), budget=Budget(), max_tables=8)
    assert seen == ["a", "b"]
    assert result.prepared is baseline


def test_offline_never_calls_mapping(setup, monkeypatch):
    _, baseline, kwargs = setup
    monkeypatch.setattr(recovery, "prepare_with_mapping_recovery", lambda *a, **k: pytest.fail())
    assert invoke(kwargs).prepared is baseline


@pytest.mark.parametrize(
    "extra",
    [
        {"allow_model_calls": True},
        {"allow_model_calls": True, "profile": object()},
        {"max_tables": 0},
        {"max_tables": 9},
        {"max_tables": True},
    ],
)
def test_invalid_config_rejected_before_io(setup, monkeypatch, extra):
    _, _, kwargs = setup
    monkeypatch.setattr(recovery, "discover_business", lambda *a, **k: pytest.fail("IO"))
    with pytest.raises(ValueError):
        invoke(kwargs, **extra)
    assert not kwargs["run_dir"].exists()


def test_rpm_rejected_before_io(setup, monkeypatch):
    _, _, kwargs = setup
    budget = Budget()
    budget.rpm = 41
    monkeypatch.setattr(recovery, "discover_business", lambda *a, **k: pytest.fail("IO"))
    with pytest.raises(ValueError):
        invoke(kwargs, budget=budget)


def test_stage_exception_retains_last_good_candidate(setup, monkeypatch):
    artifact, _, kwargs = setup
    good = artifact("good", ("b",))
    count = 0

    async def mapping(*args, **options):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("secret provider payload")
        options["run_dir"].mkdir()
        (options["run_dir"] / "report.json").write_text("{}")
        return SimpleNamespace(prepared=good, status="MAPPING_ACCEPTED")

    monkeypatch.setattr(recovery, "prepare_with_mapping_recovery", mapping)
    result = invoke(kwargs, allow_model_calls=True, profile=object(), budget=Budget())
    assert result.prepared is good and result.status == "PARTIAL"
    assert "secret" not in (kwargs["run_dir"] / "report.json").read_text()


def test_provider_failure_stops_after_one_stage(setup, monkeypatch):
    _, baseline, kwargs = setup
    seen = []

    async def mapping(*args, **options):
        seen.append(options["table_id"])
        options["run_dir"].mkdir()
        (options["run_dir"] / "report.json").write_text(
            json.dumps({"proposal_failure": {"code": "PROVIDER_HTTP_ERROR"}, "http_attempts": 1})
        )
        return SimpleNamespace(prepared=baseline, status="PARTIAL")

    monkeypatch.setattr(recovery, "prepare_with_mapping_recovery", mapping)
    invoke(kwargs, allow_model_calls=True, profile=object(), budget=Budget())
    assert seen == ["a"]


def test_exhausted_budget_skips_mapping(setup, monkeypatch):
    _, baseline, kwargs = setup
    monkeypatch.setattr(recovery, "prepare_with_mapping_recovery", lambda *a, **k: pytest.fail())
    result = invoke(kwargs, allow_model_calls=True, profile=object(), budget=Budget(0))
    assert result.prepared is baseline


def test_priority_uses_only_accepted_role_metadata(setup, monkeypatch):
    artifact, _, kwargs = setup
    selected = artifact(
        "ranked", ("a", "b", "c"), roles={"b": ("quantity", "order_id"), "c": ("unit_factor",)}
    )
    monkeypatch.setattr(recovery, "prepare_business", lambda *a, **k: selected)
    seen = []

    async def mapping(*args, **options):
        seen.append(options["table_id"])
        options["run_dir"].mkdir()
        (options["run_dir"] / "report.json").write_text("{}")
        return SimpleNamespace(prepared=selected, status="PARTIAL")

    monkeypatch.setattr(recovery, "prepare_with_mapping_recovery", mapping)
    invoke(kwargs, allow_model_calls=True, profile=object(), budget=Budget())
    assert seen == ["b", "c", "a"]


def test_write_once_run_directory(setup):
    _, _, kwargs = setup
    kwargs["run_dir"].mkdir()
    with pytest.raises(FileExistsError):
        invoke(kwargs)


def test_changed_source_snapshot_fails(setup, monkeypatch):
    _, _, kwargs = setup
    calls = 0

    def discover(*a, **k):
        nonlocal calls
        calls += 1
        source = SimpleNamespace(relative_path="x.csv", sha256=str(calls))
        return SimpleNamespace(split_root=kwargs["split_root"], sources=(source,), documents=())

    monkeypatch.setattr(recovery, "discover_business", discover)
    with pytest.raises(ValueError, match="source snapshot changed"):
        invoke(kwargs)
