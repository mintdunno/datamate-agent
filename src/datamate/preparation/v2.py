"""Versioned source-only preparation, using the reviewed registry and scoped audits."""

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import Literal, Self

import duckdb
from pydantic import Field, model_validator

from datamate.contracts.base import ContractModel, Sha256
from datamate.preparation.catalog import (
    _GRAIN_ROLES,
    _README_ROLE_MEANINGS,
    _ROLE_HEADERS,
    _declared_role,
    _source_name,
    compile_clauses,
    rule_entries,
    validate_bindings,
)
from datamate.preparation.discovery import discover_business, sha256_file
from datamate.preparation.loaders import load_tables
from datamate.preparation.models import Binding, BusinessInputs, EvidenceRef, SemanticBindings
from datamate.preparation.normalization import (
    ClassifyStructureOperation,
    CleaningPlan,
    CleaningStep,
    DeduplicateOrderOperation,
    DeriveLineAmountOperation,
    FillMergedOperation,
    InheritGroupOperation,
    NormalizeQuantityOperation,
    ParseValuesOperation,
    PreserveRawOperation,
    ResolveEntitiesOperation,
    encoded,
)
from datamate.preparation.operations import (
    _DUPLICATE_RULE,
    _GROUP_RULE,
    OperationState,
    _rule_text,
    execute_batch,
    validate_plan,
)
from datamate.preparation.profiling import profile_table
from datamate.preparation.resources import PreparationLimits
from datamate.preparation.storage import (
    create_storage,
    iter_raw_batches,
    stage_table,
    store_clauses,
)
from datamate.preparation.text_integrity import TextIntegrityPolicy

VERSION = "adaptive-preparation-v2"


class SourceEncodingHint(ContractModel):
    encoding: str
    source_sha256: Sha256


class PreparationPolicy(ContractModel):
    readme_roles: bool = False
    mode: Literal["registry", "replay"] = "registry"
    binding_proposals: tuple[Binding, ...] = ()
    frozen_plan: CleaningPlan | None = None
    limits: PreparationLimits = Field(default_factory=PreparationLimits)
    text_integrity: TextIntegrityPolicy = Field(default_factory=TextIntegrityPolicy)
    csv_encodings: dict[str, SourceEncodingHint] = Field(default_factory=dict)

    @model_validator(mode="after")
    def replay_is_explicit(self) -> Self:
        if self.mode == "replay" and self.frozen_plan is None:
            raise ValueError("replay requires a frozen plan")
        return self


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _source_roles(
    profiles, clauses, supplied: tuple[Binding, ...], *, readme_roles: bool = False
) -> SemanticBindings:
    from datamate.preparation.text_state import effective_header

    # Dependency-bearing proposals need native roles before their final validation.
    proposals = []
    rejected = []
    for binding in supplied:
        if binding.kind == "role" and not binding.dependencies:
            entry = validate_bindings(clauses, profiles, (binding,)).entries[0]
            if entry.status != "ACCEPTED":
                rejected.append(entry)
                continue
        proposals.append(binding)
    occupied = {(b.kind, b.table_id, b.columns) for b in proposals}
    for profile in profiles:
        for column in profile.columns:
            name = _source_name(effective_header(profile, column))
            candidates = {}
            for role in sorted(_ROLE_HEADERS.keys() | _README_ROLE_MEANINGS.keys()):
                cited = [
                    c
                    for c in clauses
                    if (readme_roles or role in {"unit", "base_unit", "unit_factor"})
                    and c.status != "CONFLICT"
                    and _declared_role(name, role, profile, c)
                ]
                if name in _ROLE_HEADERS.get(role, set()) or cited:
                    candidates[role] = cited
            for role, cited in candidates.items():
                key = ("role", profile.table_id, (column.column_id,))
                if len(candidates) != 1 or key in occupied:
                    continue
                evidence = EvidenceRef(
                    source=profile.source,
                    sha256=profile.source_sha256,
                    locator=f"header:{profile.table_id}/{column.column_id}",
                )
                clause_refs = tuple(
                    EvidenceRef(
                        source=c.source_document.source,
                        sha256=c.source_document.sha256,
                        locator=f"clause:{c.clause_id}",
                        line_start=c.line_start,
                        line_end=c.line_end,
                        quote_sha256=c.quote_sha256,
                    )
                    for c in cited
                )
                proposals.append(
                    Binding(
                        kind="role",
                        table_id=profile.table_id,
                        columns=(column.column_id,),
                        value=role,
                        profile_digest=profile.digest,
                        evidence=(evidence, *clause_refs),
                    )
                )
    roles = validate_bindings(clauses, profiles, proposals)
    for profile in profiles:
        accepted = [
            b for b in roles.accepted if b.table_id == profile.table_id and b.kind == "role"
        ]
        for grain, required in _GRAIN_ROLES.items():
            if (
                grain == "inventory_snapshot"
                or ("grain", profile.table_id, tuple(c.column_id for c in profile.columns))
                in occupied
            ):
                continue
            fields = [b for b in accepted if b.value in required]
            if {b.value for b in fields} != required:
                continue
            proposals.append(
                Binding(
                    kind="grain",
                    table_id=profile.table_id,
                    columns=tuple(c.column_id for c in profile.columns),
                    value=grain,
                    profile_digest=profile.digest,
                    dependencies=tuple(b.binding_id for b in fields),
                    evidence=tuple(dict.fromkeys(e for b in fields for e in b.evidence)),
                )
            )
    result = validate_bindings(clauses, profiles, proposals)
    known = {e.binding.binding_id for e in result.entries}
    return replace(
        result,
        entries=(*result.entries, *(e for e in rejected if e.binding.binding_id not in known)),
    )


def registry_plan(bindings: SemanticBindings) -> CleaningPlan:
    steps = []
    parsing = {
        "order_total": "money",
        "line_net_amount": "money",
        "payment_amount": "money",
        "unit_cost": "money",
        "unit_price": "money",
        "line_discount_amount": "money",
        "order_discount_amount": "money",
        "line_discount_percent": "percentage",
        "order_discount_percent": "percentage",
        "quantity": "quantity",
        "inventory_quantity": "quantity",
        "ordered_at": "datetime",
        "payment_at": "datetime",
    }
    for table in bindings.table_ids:
        accepted = [b for b in bindings.accepted if b.table_id == table]
        if not accepted:
            steps.append(CleaningStep(table_id=table, operation=PreserveRawOperation()))
            continue
        ids = tuple(b.binding_id for b in accepted)
        evidence = tuple(dict.fromkeys(e for b in accepted for e in b.evidence))
        steps.append(
            CleaningStep(
                table_id=table,
                operation=ClassifyStructureOperation(),
                binding_ids=ids,
                evidence=evidence,
            )
        )
        role_columns = {column for b in accepted if b.kind == "role" for column in b.columns}
        if role_columns:
            steps.append(
                CleaningStep(
                    table_id=table,
                    operation=FillMergedOperation(columns=tuple(sorted(role_columns))),
                    binding_ids=ids,
                    evidence=evidence,
                )
            )
        keys = [b for b in accepted if b.kind == "role" and b.value == "order_id"]
        conventions = {}
        for name, expected in (("group", _GROUP_RULE), ("duplicate", _DUPLICATE_RULE)):
            matches = [
                c
                for c in bindings.clauses
                if c.source_document.source == "rulebook.md"
                and c.status != "CONFLICT"
                and not c.conflicts_with
                and _rule_text(c.quote) == _rule_text(expected)
            ]
            if len(matches) == 1:
                clause = matches[0]
                conventions[name] = EvidenceRef(
                    source=clause.source_document.source,
                    sha256=clause.source_document.sha256,
                    locator=f"clause:{clause.clause_id}",
                    line_start=clause.line_start,
                    line_end=clause.line_end,
                    quote_sha256=clause.quote_sha256,
                )
        if len(keys) == 1:
            key = keys[0].columns[0]
            inheritable = {
                "order_id",
                "customer_id",
                "customer_name",
                "ordered_at",
                "order_total",
                "order_status",
                "payment_method",
                "note",
            }
            columns = tuple(
                sorted(
                    {
                        c
                        for b in accepted
                        if b.kind == "role" and b.value in inheritable
                        for c in b.columns
                    }
                )
            )
            if "group" in conventions:
                steps.append(
                    CleaningStep(
                        table_id=table,
                        operation=InheritGroupOperation(group_key=key, columns=columns),
                        binding_ids=ids,
                        evidence=(*evidence, conventions["group"]),
                    )
                )
            grains = {b.value for b in accepted if b.kind == "grain"}
            if (
                "duplicate" in conventions
                and "order_line" in grains
                and not grains & {"payment_event", "inventory_snapshot"}
            ):
                steps.append(
                    CleaningStep(
                        table_id=table,
                        operation=DeduplicateOrderOperation(group_key=key),
                        binding_ids=ids,
                        evidence=(*evidence, conventions["duplicate"]),
                    )
                )
        by_role = defaultdict(list)
        for binding in accepted:
            if binding.kind == "role" and binding.value in parsing:
                by_role[parsing[binding.value]].extend(binding.columns)
        for role, columns in sorted(by_role.items()):
            steps.append(
                CleaningStep(
                    table_id=table,
                    operation=ParseValuesOperation(
                        role=role, columns=tuple(dict.fromkeys(columns))
                    ),
                    binding_ids=ids,
                    evidence=evidence,
                )
            )
        aliases = [
            b
            for b in accepted
            if b.kind == "entity"
            and b.entity_relation == "alias"
            and b.entity_scope == "product"
            and b.value.startswith("product:")
        ]
        if aliases:
            steps.append(
                CleaningStep(
                    table_id=table,
                    operation=ResolveEntitiesOperation(
                        columns=aliases[0].columns,
                        scope="product",
                        namespace="product",
                        input_kind="alias",
                    ),
                    binding_ids=ids,
                    evidence=evidence,
                )
            )
        units = [b for b in accepted if b.kind == "unit"]
        roles = {b.value: b for b in accepted if b.kind == "role"}
        product = roles.get("product_id") or roles.get("product_name")
        quantity = roles.get("quantity") or roles.get("inventory_quantity")
        if units and product and quantity and "unit" in roles:
            steps.append(
                CleaningStep(
                    table_id=table,
                    operation=NormalizeQuantityOperation(
                        product_column=product.columns[0],
                        quantity_column=quantity.columns[0],
                        unit_column=roles["unit"].columns[0],
                    ),
                    binding_ids=ids,
                    evidence=evidence,
                )
            )
        formulas = [
            b for b in accepted if b.kind == "measure" and b.value.startswith("line_amount:")
        ]
        if len(formulas) == 1 and "line_net_amount" not in roles:
            formula = formulas[0].value.removeprefix("line_amount:")
            from datamate.preparation.financial import FORMULA_ROLES

            inputs = FORMULA_ROLES[formula]
            steps.append(
                CleaningStep(
                    table_id=table,
                    operation=DeriveLineAmountOperation(
                        quantity_column=roles["quantity"].columns[0],
                        unit_price_column=roles["unit_price"].columns[0],
                        formula=formula,
                        discount_column=roles[inputs[2]].columns[0] if len(inputs) == 3 else None,
                    ),
                    binding_ids=ids,
                    evidence=evidence,
                )
            )
    return CleaningPlan(
        bindings_digest=bindings.digest,
        profile_digests=bindings.profile_digests,
        steps=tuple(steps),
    )


def _insert_result_rows(db, statement: str, rows: list[tuple]) -> None:
    # Columnar parameters avoid one Python-to-DuckDB execution per lineage edge.
    # The caller owns the preparation transaction and supplies only static SQL.
    if not rows:
        raise duckdb.InvalidInputException("Result insertion requires a nonempty batch")
    db.execute(statement, [list(column) for column in zip(*rows, strict=True)])


def _store_result(db, result) -> None:
    _insert_result_rows(
        db,
        "INSERT INTO meta.dispositions SELECT "
        "unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),"
        "unnest(?::VARCHAR[]),unnest(?::VARCHAR[])",
        [
            (
                d.record_id,
                d.kind,
                d.reason,
                _json([e.model_dump(mode="json") for e in d.evidence_refs]),
                d.duplicate_of,
            )
            for d in result.dispositions
        ],
    )
    if result.facts:
        _insert_result_rows(
            db,
            "INSERT INTO meta.facts SELECT "
            "unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),unnest(?::VARCHAR[])",
            [
                (f.fact_id, f.table_role, _json({k: encoded(v) for k, v in f.values.items()}))
                for f in result.facts
            ],
        )
    if result.lineage:
        _insert_result_rows(
            db,
            "INSERT INTO meta.lineage SELECT "
            "unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),"
            "unnest(?::VARCHAR[])",
            [(e.fact_id, e.record_id, e.column_id, e.step_id) for e in result.lineage],
        )
    if result.events:
        _insert_result_rows(
            db,
            "INSERT INTO meta.transform_events SELECT "
            "unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),"
            "unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),"
            "unnest(?::VARCHAR[])",
            [
                (
                    e.record_id,
                    e.column_id,
                    e.step_id,
                    e.status,
                    _json(encoded(e.before)),
                    _json(encoded(e.after)),
                    e.reason,
                )
                for e in result.events
            ],
        )


def build_candidate(db, plan: CleaningPlan, bindings: SemanticBindings, *, batch_size: int = 1000):
    from datamate.preparation.projection import project_facts

    validate_plan(plan, bindings)
    by_table = defaultdict(list)
    for step in plan.steps:
        by_table[step.table_id].append(step)
    if set(by_table) != set(bindings.table_ids):
        raise ValueError("candidate plan must account for every staged table")
    for table in bindings.table_ids:
        with OperationState(db, table) as state:
            state.authorize(plan, bindings)
            for batch in iter_raw_batches(db, table, batch_size):
                for step in by_table[table]:
                    result = execute_batch(batch, step, state)
                _store_result(db, result)
    return project_facts(db, plan, bindings)


def _calendar(db, staged, clauses, split_id):
    from datamate.preparation.calendar import REQUIRED_COLUMNS, build_calendar_candidate
    from datamate.preparation.calendar_binding import (
        compile_calendar_declarations,
        qualify_calendar_days,
    )

    columns = {
        header: column
        for header, column in zip(staged.headers, staged.columns, strict=True)
        if isinstance(header, str) and header in REQUIRED_COLUMNS | {"holiday"}
    }
    if not REQUIRED_COLUMNS <= columns.keys():
        return {"status": "UNRESOLVED", "reason": "missing_source_columns"}
    candidate = build_calendar_candidate(db, staged.table_id, columns, staged.source_sha256)
    receipt = qualify_calendar_days(
        candidate, source_sha256=staged.source_sha256, calendar_sha256=candidate.calendar_sha256
    )
    book = next(c.source_document for c in clauses if c.source_document.source == "rulebook.md")
    declarations = compile_calendar_declarations(
        clauses, split_id=split_id, rulebook_sha256=book.sha256
    )
    if receipt.status == "QUALIFIED":
        db.execute(
            "CREATE TABLE clean.calendar_days(date DATE PRIMARY KEY,weekday INTEGER,"
            "lunar_day INTEGER,lunar_month INTEGER,lunar_year INTEGER,lunar_leap BOOLEAN,"
            "holiday VARCHAR,record_id VARCHAR)"
        )
        db.executemany(
            "INSERT INTO clean.calendar_days VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    r.solar_date,
                    r.weekday,
                    r.lunar_day,
                    r.lunar_month,
                    r.lunar_year,
                    r.lunar_leap,
                    r.holiday,
                    r.record_id,
                )
                for r in receipt.rows
            ],
        )
    metadata = {
        "status": receipt.status,
        "source_sha256": receipt.source_sha256,
        "calendar_sha256": receipt.calendar_sha256,
        "rows": len(receipt.rows),
        "findings": list(receipt.findings),
        "declarations": {
            name: getattr(declarations, name).status
            for name in ("tet_interval", "question_year", "source_sheet_year")
        },
    }
    if receipt.status == "QUALIFIED":
        metadata.update(
            table_name="calendar_days",
            columns=[
                "date",
                "weekday",
                "lunar_day",
                "lunar_month",
                "lunar_year",
                "lunar_leap",
                "holiday",
                "record_id",
            ],
            provenance_columns=["record_id"],
            source_tables=[staged.table_id],
        )
    return metadata


def _published_metadata(db, projection) -> dict:
    source_tables = db.execute(
        "SELECT source_tables_json FROM meta.projection_descriptors WHERE scope=?",
        [projection.scope],
    ).fetchone()
    columns = db.execute(
        "SELECT column_name,data_type FROM information_schema.columns "
        "WHERE table_schema='clean' AND table_name=? ORDER BY ordinal_position",
        [projection.table_name],
    ).fetchall()
    return asdict(projection) | {
        "columns": [name for name, _ in columns],
        "column_metadata": {name: {"sql_type": kind, "header": name} for name, kind in columns},
        "source_tables": json.loads(source_tables[0]),
        "provenance_columns": ["fact_id", "table_id"],
    }


def _build(workspace: Path, *, inputs: BusinessInputs, policy: PreparationPolicy) -> dict:
    from datamate.preparation import audit
    from datamate.preparation.pipeline import _snapshot_inputs
    from datamate.preparation.projection import publish_projections

    inputs = _snapshot_inputs(inputs, workspace / "inputs")
    limits = policy.limits
    with duckdb.connect(
        str(workspace / "business.duckdb"),
        config={
            "threads": 2,
            "memory_limit": f"{limits.duckdb_memory_bytes}B",
            "max_temp_directory_size": f"{limits.max_disk_bytes}B",
            "temp_directory": str(workspace / "spill"),
        },
    ) as db:
        db.execute("SET enable_external_access=false")
        db.execute("BEGIN")
        create_storage(db)
        clauses = compile_clauses(inputs.documents, split_id=inputs.split_id)
        store_clauses(db, clauses)
        db.execute(
            "CREATE TABLE meta.bindings(binding_id VARCHAR PRIMARY KEY,status VARCHAR,"
            "reason VARCHAR,binding_json JSON)"
        )
        db.execute(
            "CREATE TABLE meta.transform_events(record_id VARCHAR,column_id VARCHAR,"
            "step_id VARCHAR,status VARCHAR,before_json JSON,after_json JSON,reason VARCHAR)"
        )
        profiles = []
        calendar = next(s for s in inputs.documents if s.relative_path == "calendar.csv")
        decodings = {}
        available_csv = {
            source.relative_path
            for source in (*inputs.sources, calendar)
            if source.path.suffix.lower() == ".csv"
        }
        if set(policy.csv_encodings) - available_csv:
            raise ValueError("encoding hint is outside active CSV inventory")
        for source in (*inputs.sources, calendar):
            hint = policy.csv_encodings.get(source.relative_path)
            if hint is not None and hint.source_sha256 != source.sha256:
                raise ValueError("encoding hint source hash is stale")
            with closing(
                load_tables(
                    source.path,
                    encoding=hint.encoding if hint else None,
                    expected_sha256=source.sha256,
                )
            ) as tables:
                for table in tables:
                    staged = stage_table(db, source, table)
                    if table.decoding is not None:
                        decodings[staged.table_id] = table.decoding
                    if source == calendar:
                        calendar_staged = staged
                    profile = profile_table(db, staged.table_id, batch_size=limits.batch_size)
                    profiles.append(profile)
        from datamate.preparation.text_state import (
            annotate_profiles,
            finalize_text_state,
            prepare_text_state,
            store_decoding_receipt,
            text_summary,
        )

        prepare_text_state(db, inputs, policy.text_integrity)
        for table_id, decoding in decodings.items():
            store_decoding_receipt(db, table_id, decoding)
        profiles = list(annotate_profiles(db, profiles, policy.text_integrity))
        for profile in profiles:
            db.execute(
                "INSERT OR REPLACE INTO meta.profiles VALUES (?,?)",
                [profile.table_id, profile.model_dump_json()],
            )
        bindings = _source_roles(
            profiles, clauses, policy.binding_proposals, readme_roles=policy.readme_roles
        )
        from datamate.preparation.native_relations import enrich_native_relations

        finalize_text_state(db, bindings)
        bindings = enrich_native_relations(db, profiles, bindings)
        if bindings.entries:
            db.executemany(
                "INSERT INTO meta.bindings VALUES (?,?,?,?)",
                [
                    (e.binding.binding_id, e.status, e.reason, e.binding.model_dump_json())
                    for e in bindings.entries
                ],
            )
        plan = policy.frozen_plan or registry_plan(bindings)
        build_candidate(db, plan, bindings, batch_size=limits.batch_size)
        report = audit.audit_prepared(db, bindings)
        if any(f.status == "FAIL" for f in report.findings):
            raise ValueError("intrinsic audit failed")
        qualification = audit.qualify(report, {})
        published = publish_projections(db, qualification)
        db.execute(
            "CREATE TABLE meta.qualification(scope VARCHAR PRIMARY KEY,"
            "status VARCHAR,report_digest VARCHAR)"
        )
        db.executemany(
            "INSERT INTO meta.qualification VALUES (?,?,?)",
            [(scope, status, report.report_digest) for scope, status in qualification.statuses],
        )
        from datamate.preparation.mapping import store_mapping_issues

        issues = store_mapping_issues(db, profiles, bindings)
        calendar_metadata = _calendar(db, calendar_staged, clauses, inputs.split_id)
        db.execute("COMMIT")
        catalog = {
            "artifact_version": 2,
            "pipeline_version": VERSION,
            "split_id": inputs.split_id,
            "business_id": inputs.business_id,
            "mode": policy.mode,
            "status": "PARTIAL",
            "api_attempts": 0,
            "text_integrity": text_summary(db),
            "decoding": decodings,
            "bindings_digest": bindings.digest,
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "plan": plan.model_dump(mode="json"),
            "projections": [_published_metadata(db, p) for p in published],
            "qualification": dict(qualification.statuses),
            "calendar": calendar_metadata,
            "tables": [p.model_dump(mode="json") for p in profiles],
            "rule_coverage": {
                "total": len(clauses),
                "unresolved": sum(c.status in {"UNMAPPED", "CONFLICT"} for c in clauses),
            },
            "audit_digest": report.report_digest,
            "rules": rule_entries(inputs.documents),
            "physical_row_counts": {
                profile.table_id: dict(
                    db.execute(
                        "SELECT d.kind,count(*) FROM staging.raw_rows r JOIN meta.dispositions d "
                        "ON r.record_id=d.record_id WHERE r.table_id=? AND r.kind='data' "
                        "GROUP BY d.kind",
                        [profile.table_id],
                    ).fetchall()
                )
                for profile in profiles
            },
        }
    (workspace / "catalog.json").write_text(_json(catalog), encoding="utf-8")
    return {"artifact_version": 2, "status": "PARTIAL", "tables": len(profiles)}


def prepare_v2(
    split_root: Path,
    business_id: str,
    cache_root: Path,
    *,
    split_id: str,
    policy: PreparationPolicy,
):
    from datamate.preparation import pipeline

    policy = PreparationPolicy.model_validate(policy)
    inputs = discover_business(split_root, business_id, split_id=split_id)
    output = cache_root.resolve()
    if output.is_relative_to(inputs.split_root):
        raise ValueError("output must be outside the source split")
    manifest = pipeline._inputs_manifest(inputs, {}) | {
        "version": VERSION,
        "artifact_version": 2,
        "policy": policy.model_dump(mode="json"),
        "implementation": pipeline._v2_implementation(),
    }
    fingerprint = hashlib.sha256(_json(manifest).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    target = output / fingerprint

    def cached():
        result = pipeline._load_cache(target, fingerprint)
        if result.catalog.get("artifact_version") != 2:
            raise ValueError("legacy cache cannot qualify as version 2")
        return result

    if target.exists():
        return cached()
    lock = output / f"{fingerprint}.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("preparation cache is already being built") from error
    try:
        os.close(descriptor)
        if target.exists():
            return cached()
        with tempfile.TemporaryDirectory(prefix=f".{fingerprint}.", dir=output) as temporary:
            directory = Path(temporary)
            _, resources = pipeline.run_bounded_preparation(
                partial(_build, inputs=inputs, policy=policy), directory, limits=policy.limits
            )
            current = discover_business(split_root, business_id, split_id=split_id)
            check = pipeline._inputs_manifest(current, {}) | {
                "version": VERSION,
                "artifact_version": 2,
                "policy": policy.model_dump(mode="json"),
                "implementation": pipeline._v2_implementation(),
            }
            if check != manifest:
                raise ValueError("source inputs or implementation changed during preparation")
            catalog_path = directory / "catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog.update(fingerprint=fingerprint, inputs=manifest, resources=asdict(resources))
            from datamate.preparation.diagnostics import diagnose_preparation

            with duckdb.connect(str(directory / "business.duckdb"), read_only=True) as db:
                catalog["diagnostic"] = diagnose_preparation(db, resources=asdict(resources))
            catalog_path.write_text(_json(catalog), encoding="utf-8")
            (directory / "manifest.json").write_text(
                _json(
                    {
                        "artifact_version": 2,
                        "fingerprint": fingerprint,
                        "database_sha256": sha256_file(directory / "business.duckdb"),
                        "catalog_sha256": sha256_file(catalog_path),
                    }
                ),
                encoding="utf-8",
            )
            directory.rename(target)
        result = cached()
        return pipeline.PreparedBusiness(
            result.database,
            result.catalog_path,
            result.manifest_path,
            result.catalog,
            fingerprint,
            False,
        )
    finally:
        lock.unlink()
