"""Transactional, content-addressed preparation of one explicitly selected business."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from functools import partial
from importlib.metadata import version
from itertools import islice
from pathlib import Path

import duckdb

from datamate.preparation.catalog import compile_clauses, rule_entries
from datamate.preparation.discovery import discover_business, sha256_file
from datamate.preparation.loaders import load_tables
from datamate.preparation.models import (
    BusinessInputs,
    SourceFile,
    SourceRow,
    SourceTable,
    TableProfile,
)
from datamate.preparation.normalization import TablePlan, encoded, normalize_rows
from datamate.preparation.profiling import TableProfiler, profile_table
from datamate.preparation.resources import (
    PreparationLimits,
    PreparationResourceError,
    PreparationResources,
    run_bounded_preparation,
)
from datamate.preparation.storage import create_storage, stage_table, store_clauses

PIPELINE_VERSION = "stage2-v1"


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


@dataclass(frozen=True)
class PreparedBusiness:
    database: Path
    catalog_path: Path
    manifest_path: Path
    catalog: dict
    fingerprint: str
    cache_hit: bool


def _inputs_manifest(inputs: BusinessInputs, plans: dict[str, TablePlan]) -> dict:
    return {
        "version": PIPELINE_VERSION,
        "split_id": inputs.split_id,
        "business_id": inputs.business_id,
        "files": [
            {"path": item.relative_path, "sha256": item.sha256}
            for item in (*inputs.sources, *inputs.documents)
        ],
        "plans": {key: value.model_dump(mode="json") for key, value in sorted(plans.items())},
        "versions": {
            name: version(name) for name in ("duckdb", "openpyxl", "polars", "pydantic", "pytz")
        },
        "python": list(sys.version_info[:3]),
        "implementation": {
            p.name: sha256_file(p) for p in sorted(Path(__file__).parent.glob("*.py"))
        },
    }


def _validate_evidence(inputs: BusinessInputs, plans: dict[str, TablePlan]) -> None:
    sources = {item.relative_path: item.sha256 for item in (*inputs.sources, *inputs.documents)}
    for plan in plans.values():
        for evidence in [plan.evidence, *(column.evidence for column in plan.columns.values())]:
            if evidence and sources.get(evidence.source) != evidence.sha256:
                raise ValueError("normalization evidence missing or source fingerprint changed")


def _snapshot_inputs(inputs: BusinessInputs, root: Path) -> BusinessInputs:
    """Bind parsing to private bytes verified against the original manifest, including ABA edits."""

    def copy(source: SourceFile) -> SourceFile:
        path = root / source.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.path, path)
        if sha256_file(path) != source.sha256:
            raise ValueError("source inputs changed while snapshotting")
        return SourceFile(path, source.relative_path, source.sha256)

    return BusinessInputs(
        inputs.split_id,
        inputs.business_id,
        root,
        root / "data" / inputs.business_id,
        tuple(copy(source) for source in inputs.sources),
        tuple(copy(source) for source in inputs.documents),
    )


def _source_profile_job(
    workspace: Path, *, inputs: BusinessInputs, limits: PreparationLimits
) -> dict:
    inputs = _snapshot_inputs(inputs, workspace / "inputs")
    profiles = []
    with duckdb.connect(
        str(workspace / "raw.duckdb"),
        config={
            "memory_limit": f"{limits.duckdb_memory_bytes}B",
            "max_temp_directory_size": f"{limits.max_disk_bytes}B",
            "temp_directory": str(workspace / "spill"),
            "threads": 2,
        },
    ) as db:
        db.execute("BEGIN")
        create_storage(db)
        store_clauses(db, compile_clauses(inputs.documents, split_id=inputs.split_id))
        calendar = next(
            source for source in inputs.documents if source.relative_path == "calendar.csv"
        )
        for source in (*inputs.sources, calendar):
            with closing(load_tables(source.path)) as tables:
                for table in tables:
                    staged = stage_table(db, source, table)
                    profiles.append(
                        profile_table(db, staged.table_id, batch_size=limits.batch_size)
                    )
        db.execute("COMMIT")
    (workspace / "profiles.json").write_text(
        _json([profile.model_dump(mode="json") for profile in profiles]), encoding="utf-8"
    )
    return {"tables": len(profiles), "data_rows": sum(profile.row_count for profile in profiles)}


def profile_business_sources(
    split_root: Path,
    business_id: str,
    workspace: Path,
    *,
    split_id: str,
    limits: PreparationLimits | None = None,
    expected_sources: dict[str, str] | None = None,
) -> tuple[tuple[TableProfile, ...], PreparationResources]:
    """Bounded source-only P1 diagnostic; it cannot publish qualified clean facts."""
    inputs = discover_business(split_root, business_id, split_id=split_id)
    sources = {
        source.relative_path: source.sha256 for source in (*inputs.sources, *inputs.documents)
    }
    if expected_sources is not None and sources != expected_sources:
        raise ValueError("sources differ from frozen source selection")
    if workspace.resolve().is_relative_to(inputs.split_root):
        raise ValueError("profile workspace must be outside the source split")
    workspace.mkdir(parents=True, exist_ok=False)
    limits = limits or PreparationLimits()
    try:
        result, report = run_bounded_preparation(
            partial(_source_profile_job, inputs=inputs, limits=limits), workspace, limits=limits
        )
    except PreparationResourceError as exc:
        (workspace / "resources.json").write_text(
            _json({"status": "FAILED", "reason": exc.reason, **asdict(exc.resources)}),
            encoding="utf-8",
        )
        raise
    (workspace / "resources.json").write_text(_json(asdict(report)), encoding="utf-8")
    current = discover_business(split_root, business_id, split_id=split_id)
    if sources != {s.relative_path: s.sha256 for s in (*current.sources, *current.documents)}:
        raise ValueError("sources changed during profiling; diagnostic is not publishable")
    profiles = tuple(
        TableProfile.model_validate_json(_json(value))
        for value in json.loads((workspace / "profiles.json").read_text(encoding="utf-8"))
    )
    (workspace / "profile-manifest.json").write_text(
        _json(
            {
                "status": "RAW_PROFILE_ONLY_UNQUALIFIED",
                "split_id": split_id,
                "business_id": business_id,
                "sources": sources,
                "profile_digests": [p.digest for p in profiles],
                "limits": limits.model_dump(mode="json"),
                **result,
            }
        ),
        encoding="utf-8",
    )
    return profiles, report


def _store_row(db, table_id: str, row: SourceRow, *, kind: str, prepared=None) -> tuple:
    return (
        f"{table_id}:{kind}:{row.number}",
        table_id,
        row.number,
        kind,
        _json([encoded(value) for value in row.values]),
        _json(list(row.formulas)),
        _json(list(row.formats)),
        _json({key: encoded(value) for key, value in (row.record_key or {}).items()}),
        _json([encoded(value) for value in prepared.values]) if prepared else None,
        prepared.excluded if prepared else False,
        prepared.duplicate_of if prepared else None,
        _json(list(prepared.events)) if prepared else "[]",
    )


INSERT_RAW = "INSERT INTO raw_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"


def _ingest_table(
    db, source: SourceFile, table: SourceTable, plan: TablePlan, *, calendar=False
) -> dict:
    key = f"{source.relative_path}::{table.name}"
    table_id = "calendar_data" if calendar else "t_" + hashlib.sha256(key.encode()).hexdigest()[:24]
    width = len(table.headers)
    if width == 0:
        raise ValueError("source table has no discoverable columns")
    profiler = TableProfiler(width)
    for row in table.preamble:
        db.execute(INSERT_RAW, _store_row(db, table_id, row, kind="preamble"))
    if table.header_row is not None:
        header = SourceRow(table.header_row, table.headers)
        db.execute(INSERT_RAW, _store_row(db, table_id, header, kind="header"))
    iterator = iter(normalize_rows(table.rows, width, plan))
    while batch := list(islice(iterator, 1000)):
        profiler.consume(batch)
        db.executemany(
            INSERT_RAW,
            [
                _store_row(
                    db, table_id, row.source, kind="calendar" if calendar else "data", prepared=row
                )
                for row in batch
            ],
        )
    columns = profiler.finish()
    expressions = []
    for index, column in enumerate(columns):
        value = f"json_extract_string(normalized_json, '$[{index}].value')"
        sql_type = column["sql_type"]
        expression = f"from_hex({value})" if sql_type == "BLOB" else f"CAST({value} AS {sql_type})"
        expressions.append(f'{expression} AS "{column["column_id"]}"')
        column["source_header"] = encoded(table.headers[index])
        column["source_column"] = index + 1
        column["declared_type"] = table.declared_types[index] if table.declared_types else None
    db.execute(
        f'CREATE TABLE "{table_id}" AS SELECT row_id AS _source_row_id, '
        f"source_row AS _source_row, {', '.join(expressions)} "
        "FROM raw_records WHERE table_id = ? AND normalized_json IS NOT NULL AND NOT excluded",
        [table_id],
    )
    output_rows = db.execute(f'SELECT count(*) FROM "{table_id}"').fetchone()[0]
    if output_rows + profiler.excluded_rows != profiler.input_rows:
        raise ValueError("source row-count reconciliation failed")
    entry = {
        "table_id": table_id,
        "table_key": key,
        "source": source.relative_path,
        "source_sha256": source.sha256,
        "source_table": table.name,
        "header_row": table.header_row,
        "header_inferred": source.path.suffix.lower() == ".xlsx",
        "preamble_rows": len(table.preamble),
        "columns": columns,
        "input_rows": profiler.input_rows,
        "output_rows": output_rows,
        "excluded_rows": profiler.excluded_rows,
        "policy": plan.model_dump(mode="json"),
    }
    db.execute("INSERT INTO schema_catalog VALUES (?, ?)", [table_id, _json(entry)])
    return entry


def _build_database(path: Path, inputs: BusinessInputs, plans: dict[str, TablePlan]) -> dict:
    db = duckdb.connect(str(path), config={"threads": 2, "memory_limit": "512MB"})
    try:
        db.execute("SET enable_external_access = false")
        db.execute("BEGIN")
        db.execute(
            "CREATE TABLE raw_records(row_id VARCHAR PRIMARY KEY, table_id VARCHAR, "
            "source_row BIGINT, record_kind VARCHAR, raw_json JSON, formulas_json JSON, "
            "formats_json JSON, record_key_json JSON, normalized_json JSON, excluded BOOLEAN, "
            "duplicate_of BIGINT, events_json JSON)"
        )
        db.execute("CREATE TABLE schema_catalog(table_id VARCHAR PRIMARY KEY, metadata JSON)")
        db.execute(
            "CREATE TABLE rule_catalog(rule_id VARCHAR PRIMARY KEY, source VARCHAR, "
            "source_sha256 VARCHAR, line_start BIGINT, line_end BIGINT, title VARCHAR, "
            "scope VARCHAR, text VARCHAR)"
        )
        db.execute("CREATE TABLE source_manifest(path VARCHAR PRIMARY KEY, sha256 VARCHAR)")
        db.executemany(
            "INSERT INTO source_manifest VALUES (?, ?)",
            [(item.relative_path, item.sha256) for item in (*inputs.sources, *inputs.documents)],
        )
        rules = rule_entries(inputs.documents)
        if rules:
            db.executemany(
                "INSERT INTO rule_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    tuple(
                        rule[name]
                        for name in (
                            "rule_id",
                            "source",
                            "source_sha256",
                            "line_start",
                            "line_end",
                            "title",
                            "scope",
                            "text",
                        )
                    )
                    for rule in rules
                ],
            )
        tables = []
        used_plans = set()
        for source in inputs.sources:
            with closing(load_tables(source.path)) as source_tables:
                for table in source_tables:
                    key = f"{source.relative_path}::{table.name}"
                    if key in plans:
                        used_plans.add(key)
                    with closing(table.rows):
                        tables.append(_ingest_table(db, source, table, plans.get(key, TablePlan())))
        if used_plans != set(plans):
            raise ValueError("normalization plan references an unknown source table")
        calendar_source = next(
            item for item in inputs.documents if item.relative_path == "calendar.csv"
        )
        with closing(load_tables(calendar_source.path)) as calendars:
            table = next(calendars)
            with closing(table.rows):
                calendar = _ingest_table(db, calendar_source, table, TablePlan(), calendar=True)
        db.execute("COMMIT")
        return {
            "split_id": inputs.split_id,
            "business_id": inputs.business_id,
            "tables": tables,
            "calendar": calendar,
            "rules": rules,
        }
    finally:
        db.close()


def _load_cache(target: Path, fingerprint: str) -> PreparedBusiness:
    paths = [target / name for name in ("business.duckdb", "catalog.json", "manifest.json")]
    if (
        target.is_symlink()
        or target.is_junction()
        or any(p.is_symlink() or p.is_junction() or not p.is_file() for p in paths)
    ):
        raise ValueError("corrupt or linked preparation cache")
    database, catalog, manifest = paths
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            metadata["fingerprint"] != fingerprint
            or metadata["database_sha256"] != sha256_file(database)
            or metadata["catalog_sha256"] != sha256_file(catalog)
        ):
            raise ValueError("corrupt preparation cache")
        return PreparedBusiness(
            database,
            catalog,
            manifest,
            json.loads(catalog.read_text(encoding="utf-8")),
            fingerprint,
            True,
        )
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("corrupt preparation cache metadata") from error


def prepare_business(
    split_root: Path,
    business_id: str,
    cache_root: Path,
    *,
    split_id: str,
    plans: dict[str, TablePlan] | None = None,
) -> PreparedBusiness:
    """Consume all allowed records; atomically publish only a complete, source-stable cache."""
    inputs = discover_business(split_root, business_id, split_id=split_id)
    output = cache_root.resolve()
    if output.is_relative_to(inputs.split_root):
        raise ValueError("output must be outside the source split")
    plans = {key: TablePlan.model_validate(value) for key, value in (plans or {}).items()}
    _validate_evidence(inputs, plans)
    manifest = _inputs_manifest(inputs, plans)
    fingerprint = hashlib.sha256(_json(manifest).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    target = output / fingerprint
    if target.exists():
        return _load_cache(target, fingerprint)
    lock = output / f"{fingerprint}.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("preparation cache is already being built") from error
    try:
        os.close(descriptor)
        if target.exists():
            return _load_cache(target, fingerprint)
        with tempfile.TemporaryDirectory(prefix=f".{fingerprint}.", dir=output) as temporary:
            directory = Path(temporary)
            database = directory / "business.duckdb"
            with tempfile.TemporaryDirectory(prefix=".source-snapshot.", dir=output) as snapshot:
                bound_inputs = _snapshot_inputs(inputs, Path(snapshot))
                catalog = _build_database(database, bound_inputs, plans)
            current = discover_business(split_root, business_id, split_id=split_id)
            if _inputs_manifest(current, plans) != manifest:
                raise ValueError("source inputs changed during preparation")
            catalog["fingerprint"] = fingerprint
            catalog["inputs"] = manifest
            catalog_path = directory / "catalog.json"
            catalog_path.write_text(_json(catalog), encoding="utf-8")
            (directory / "manifest.json").write_text(
                _json(
                    {
                        "fingerprint": fingerprint,
                        "database_sha256": sha256_file(database),
                        "catalog_sha256": sha256_file(catalog_path),
                    }
                ),
                encoding="utf-8",
            )
            # Rename a complete directory; failed builds are never visible as cache hits.
            directory.rename(target)
        result = _load_cache(target, fingerprint)
        return PreparedBusiness(
            result.database,
            result.catalog_path,
            result.manifest_path,
            result.catalog,
            fingerprint,
            False,
        )
    finally:
        lock.unlink()
