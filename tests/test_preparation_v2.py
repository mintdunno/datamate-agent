"""Versioned preparation integrates source-only stages before atomic publication."""

import json
import sqlite3

import duckdb
import pytest

from datamate.preparation import pipeline


def source_split(tmp_path):
    root = tmp_path / "split"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    (root / "rulebook.md").write_text("# Rules\nUnknown rules remain unresolved.\n")
    (business / "README.md").write_text("# Sales\nNative source amounts.\n")
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
            "INSERT INTO sales VALUES (?,?,?,?)", [("A", "P1", 1, 100), ("B", "P2", 2, 80)]
        )
    return root


def test_v2_builds_all_stages_and_publishes_only_qualified_facts(tmp_path):
    root = source_split(tmp_path)
    policy = pipeline.PreparationPolicy()
    result = pipeline.prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=policy
    )
    assert result.catalog["artifact_version"] == 2
    assert result.catalog["mode"] == "registry"
    assert result.catalog["api_attempts"] == 0
    projection = next(p for p in result.catalog["projections"] if p["scope"] == "order_lines")
    assert projection["provenance_columns"] == ["fact_id", "table_id"]
    assert {"fact_id", "table_id", "line_net_amount"} <= set(projection["columns"])
    assert projection["source_tables"]
    assert projection["column_metadata"]["line_net_amount"]["sql_type"].startswith("DECIMAL")
    assert result.catalog["rules"][0]["source"] in {"rulebook.md", "data/shop/README.md"}
    assert result.catalog["diagnostic"]["raw_records"] == 5
    assert result.catalog["diagnostic"]["resources"]["measurements_complete"]
    with duckdb.connect(str(result.database), read_only=True) as db:
        assert db.execute("SELECT count(*) FROM staging.raw_rows").fetchone()[0] == 5
        assert db.execute("SELECT count(*) FROM meta.dispositions").fetchone()[0] == 5
        assert db.execute("SELECT sum(line_net_amount) FROM clean.order_lines").fetchone()[0] == 180
        assert db.execute("SELECT count(*) FROM meta.profiles").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM clean.calendar_days").fetchone()[0] == 2
        unresolved = db.execute(
            "SELECT count(*) FROM meta.clauses WHERE status IN ('UNMAPPED','CONFLICT')"
        ).fetchone()[0]
        assert unresolved > 0
        assert result.catalog["rule_coverage"]["unresolved"] == unresolved
    assert result.catalog["status"] == "PARTIAL"  # Unsupported rules are not a business PASS.


def test_v2_cache_is_separate_from_legacy_and_replays_logical_facts(tmp_path):
    root = source_split(tmp_path)
    cache = tmp_path / "cache"
    legacy = pipeline.prepare_business(root, "shop", cache, split_id="demo")
    policy = pipeline.PreparationPolicy()
    first = pipeline.prepare_business(root, "shop", cache, split_id="demo", policy=policy)
    second = pipeline.prepare_business(root, "shop", cache, split_id="demo", policy=policy)
    assert legacy.fingerprint != first.fingerprint
    assert not first.cache_hit and second.cache_hit
    assert first.catalog == second.catalog
    assert json.loads(first.manifest_path.read_text())["artifact_version"] == 2


def test_v2_source_and_external_code_versions_invalidate_cache(tmp_path, monkeypatch):
    root = source_split(tmp_path)
    policy = pipeline.PreparationPolicy()
    first = pipeline.prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=policy
    )
    original = pipeline._v2_implementation
    monkeypatch.setattr(pipeline, "_v2_implementation", lambda: original() | {"test": "new-code"})
    changed = pipeline.prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=policy
    )
    assert first.fingerprint != changed.fingerprint
    (root / "data/shop/README.md").write_text("# Revised evidence\n")
    revised = pipeline.prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=policy
    )
    assert revised.fingerprint != changed.fingerprint


def test_v2_failed_audit_never_publishes_complete_manifest(tmp_path, monkeypatch):
    from datamate.preparation import audit

    root = source_split(tmp_path)
    cache = tmp_path / "cache"
    original = audit.audit_prepared

    def fail(db, bindings):
        db.execute("DELETE FROM meta.dispositions")
        return original(db, bindings)

    monkeypatch.setattr(audit, "audit_prepared", fail)
    # Execute this fault injection in-process; spawn starts a fresh module environment.
    monkeypatch.setattr(
        pipeline,
        "run_bounded_preparation",
        lambda target, workspace, **kwargs: target(workspace),
    )
    with pytest.raises(ValueError, match="audit"):
        pipeline.prepare_business(
            root, "shop", cache, split_id="demo", policy=pipeline.PreparationPolicy()
        )
    assert list(cache.rglob("manifest.json")) == []


def test_v2_replay_requires_a_frozen_plan_and_registry_never_opens_http(tmp_path, monkeypatch):
    from datamate.inference import client

    root = source_split(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("registry must not construct HTTP clients")

    monkeypatch.setattr(client, "inference_model", forbidden)
    result = pipeline.prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=pipeline.PreparationPolicy()
    )
    assert result.catalog["api_attempts"] == 0
    with pytest.raises(ValueError, match="frozen plan"):
        pipeline.PreparationPolicy(mode="replay")


def test_v2_rejects_legacy_table_plans_without_claiming_qualification(tmp_path):
    from datamate.preparation.normalization import TablePlan

    root = source_split(tmp_path)
    with pytest.raises(ValueError, match="legacy"):
        pipeline.prepare_business(
            root,
            "shop",
            tmp_path / "cache",
            split_id="demo",
            policy=pipeline.PreparationPolicy(),
            plans={"sales": TablePlan()},
        )


@pytest.mark.parametrize("declared,duplicates", [(True, 1), (False, 0)])
def test_v2_registry_applies_only_source_declared_order_deduplication(
    tmp_path, declared, duplicates
):
    from datamate.preparation.operations import _DUPLICATE_RULE, _GROUP_RULE

    root = source_split(tmp_path)
    if declared:
        (root / "rulebook.md").write_text(
            "# Source conventions\n" + _GROUP_RULE + "\n" + _DUPLICATE_RULE + "\n", encoding="utf-8"
        )
    with sqlite3.connect(root / "data/shop/sales.sqlite") as db:
        db.execute("INSERT INTO sales VALUES ('A','P1',1,100)")
    result = pipeline.prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=pipeline.PreparationPolicy()
    )
    with duckdb.connect(str(result.database), read_only=True) as db:
        assert (
            db.execute("SELECT count(*) FROM meta.dispositions WHERE kind='duplicate'").fetchone()[
                0
            ]
            == duplicates
        )
        assert (
            db.execute("SELECT count(*) FROM staging.raw_rows WHERE kind='data'").fetchone()[0] == 5
        )


def test_registry_plan_accounts_for_businesses_with_more_than_64_tables():
    from datamate.preparation.models import SemanticBindings
    from datamate.preparation.v2 import registry_plan

    tables = tuple(f"table_{i}" for i in range(65))
    bindings = SemanticBindings((), (), ("b" * 64,), tables)
    plan = registry_plan(bindings)
    assert {step.table_id for step in plan.steps} == set(tables)


def test_v2_artifact_is_queryable_through_the_qualified_runtime(tmp_path):
    from datamate.runtime.tools import StructuredTools

    root = source_split(tmp_path)
    prepared = pipeline.prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=pipeline.PreparationPolicy()
    )
    tools = StructuredTools(prepared.database, prepared.catalog)
    assert {"clean.order_lines", "clean.calendar_days"} <= tools.tables
    result = tools.execute_sql("SELECT sum(line_net_amount) FROM clean.order_lines")
    assert str(result["rows"][0][0]) == "180"
    with pytest.raises(ValueError):
        tools.execute_sql("SELECT * FROM meta.facts")


def test_registry_plan_keeps_an_explicit_upper_bound():
    from datamate.preparation.models import SemanticBindings
    from datamate.preparation.v2 import registry_plan

    bindings = SemanticBindings((), (), ("b" * 64,), tuple(f"table_{i}" for i in range(257)))
    with pytest.raises(ValueError):
        registry_plan(bindings)
