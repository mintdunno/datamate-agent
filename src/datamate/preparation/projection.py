"""Fixed-schema factual candidates; publication requires an audited scope receipt."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from duckdb import DuckDBPyConnection

from datamate.preparation.models import SemanticBindings
from datamate.preparation.normalization import CleaningPlan
from datamate.preparation.operations import validate_plan
from datamate.preparation.storage import _decoded

if TYPE_CHECKING:
    from datamate.preparation.audit import Qualification

# Names and columns are code-owned templates. Bindings select among them; they never supply SQL.
_SCOPES = {
    "order_header": ("order_headers", ("order_id", "order_total")),
    "order_line": ("order_lines", ("order_id", "product_id", "quantity", "line_net_amount")),
    "shipping_component": ("shipping_components", ("order_id", "shipping_fee")),
    "payment_event": ("payment_events", ("order_id", "payment_at", "payment_amount")),
    "inventory_snapshot": (
        "inventory_snapshots",
        ("product_id", "inventory_quantity", "snapshot_at"),
    ),
    "order_line_device": ("order_line_devices", ("order_id", "imei")),
    "return_event": ("return_events", ("order_id", "return_amount")),
    "warranty_event": ("warranty_events", ("order_id", "warranty_status")),
    "fee_component": ("fee_components", ("order_id", "fee_amount")),
}
_MONEY = frozenset(
    {
        "order_total",
        "line_net_amount",
        "shipping_fee",
        "payment_amount",
        "return_amount",
        "fee_amount",
    }
)
_NUMERIC = _MONEY | {"quantity", "inventory_quantity"}
_IDS = frozenset({"order_id", "product_id", "imei", "warranty_status"})
_TIMES = frozenset({"payment_at", "snapshot_at"})
_GROUPED = frozenset({"order_headers", "shipping_components", "fee_components"})


@dataclass(frozen=True)
class ProjectionDescriptor:
    scope: str
    table_name: str | None
    columns: tuple[str, ...]
    blocked_fields: tuple[str, ...]
    row_count: int
    source_tables: tuple[str, ...]


@dataclass(frozen=True)
class ProjectionResult:
    descriptors: tuple[ProjectionDescriptor, ...]
    bindings_digest: str

    def descriptor(self, scope: str) -> ProjectionDescriptor:
        matches = [item for item in self.descriptors if item.scope == scope]
        if len(matches) != 1:
            raise KeyError(scope)
        return matches[0]

    def count(self, scope: str) -> int:
        return self.descriptor(scope).row_count


@dataclass(frozen=True)
class PublishedProjection:
    scope: str
    table_name: str
    columns: tuple[str, ...]
    row_count: int


@dataclass
class _Row:
    fact_id: str
    table_id: str
    record_id: str
    values: dict[str, object]
    source_columns: dict[str, str]
    record_ids: tuple[str, ...]


def _candidate_rows(db: DuckDBPyConnection) -> dict[str, list[tuple[str, str, dict]]]:
    result = defaultdict(list)
    rows = db.execute(
        "SELECT r.table_id,r.record_id,f.fact_id,f.values_json "
        "FROM meta.facts f JOIN staging.raw_rows r ON f.fact_id='row:'||r.record_id "
        "JOIN meta.dispositions d ON d.record_id=r.record_id "
        "WHERE f.table_role='physical_row' AND d.kind='retained' AND r.kind='data' "
        "ORDER BY r.table_id,r.ordinal"
    ).fetchall()
    for table_id, record_id, fact_id, values_json in rows:
        values = json.loads(values_json)
        result[table_id].append((fact_id, record_id, {k: _decoded(v) for k, v in values.items()}))
    return result


def _compatible(role: str, value: object) -> bool:
    if role in _NUMERIC:
        return type(value) in (int, Decimal) and Decimal(value).is_finite()
    if role in _IDS:
        return type(value) is str and bool(value.strip())
    if role == "snapshot_at":
        return type(value) is date
    if role in _TIMES:
        return type(value) in (date, datetime)
    return False


def _row_id(scope: str, table_id: str, key: str) -> str:
    return hashlib.sha256(f"{scope}\0{table_id}\0{key}".encode()).hexdigest()


def _sql_type(role: str, values: list[object]) -> str | None:
    if role in _IDS:
        return "VARCHAR" if all(type(value) is str for value in values) else None
    if role in _NUMERIC:
        numbers = [Decimal(value) for value in values]
        scale = max(max(-number.as_tuple().exponent, 0) for number in numbers)
        digits = max(
            max(len(number.as_tuple().digits) + number.as_tuple().exponent, 0) for number in numbers
        )
        return f"DECIMAL(38,{scale})" if scale + digits <= 38 else None
    if role == "snapshot_at":
        return "DATE" if all(type(value) is date for value in values) else None
    if role in _TIMES:
        kinds = {type(value) for value in values}
        if kinds == {date}:
            return "DATE"
        if kinds == {datetime}:
            awareness = {value.utcoffset() is not None for value in values}
            return (
                "TIMESTAMPTZ"
                if awareness == {True}
                else "TIMESTAMP"
                if awareness == {False}
                else None
            )
    return None


def _materialize(
    db: DuckDBPyConnection,
    scope: str,
    required: tuple[str, ...],
    rows: list[_Row],
    blocked: set[str],
    source_tables: set[str],
    bindings_digest: str,
) -> ProjectionDescriptor:
    table_name = None
    if not rows:
        blocked.add("missing_facts")
    if not blocked:
        column_types = {
            role: _sql_type(role, [row.values[role] for row in rows]) for role in required
        }
        blocked.update(role for role, sql_type in column_types.items() if sql_type is None)
    if not blocked:
        table_name = "candidate_" + scope
        column_ddl = ",".join(f'"{role}" {column_types[role]}' for role in required)
        db.execute(
            f'CREATE TABLE meta."{table_name}"('
            f"fact_id VARCHAR PRIMARY KEY,table_id VARCHAR NOT NULL,{column_ddl})"
        )
        placeholders = ",".join("?" for _ in range(len(required) + 2))
        db.executemany(
            f'INSERT INTO meta."{table_name}" VALUES ({placeholders})',
            [(row.fact_id, row.table_id, *(row.values[role] for role in required)) for row in rows],
        )
        lineage = [
            (scope, row.fact_id, role, row.source_columns[role], record_id)
            for row in rows
            for role in required
            for record_id in row.record_ids
        ]
        db.executemany("INSERT INTO meta.projection_lineage VALUES (?,?,?,?,?)", lineage)
    descriptor = ProjectionDescriptor(
        scope,
        table_name,
        required if table_name else (),
        tuple(sorted(blocked)),
        len(rows) if table_name else 0,
        tuple(sorted(source_tables)),
    )
    db.execute(
        "INSERT INTO meta.projection_descriptors VALUES (?,?,?,?,?,?,?)",
        [
            descriptor.scope,
            descriptor.table_name,
            json.dumps(descriptor.columns),
            json.dumps(descriptor.blocked_fields),
            descriptor.row_count,
            json.dumps(descriptor.source_tables),
            bindings_digest,
        ],
    )
    return descriptor


def project_facts(
    db: DuckDBPyConnection, plan: CleaningPlan, bindings: SemanticBindings
) -> ProjectionResult:
    """Project accepted physical rows without altering raw dispositions or QA state."""
    validate_plan(plan, bindings)
    db.execute("""CREATE TABLE meta.projection_descriptors(
        scope VARCHAR PRIMARY KEY,table_name VARCHAR,columns_json JSON NOT NULL,
        blocked_json JSON NOT NULL,row_count BIGINT NOT NULL,
        source_tables_json JSON NOT NULL,bindings_digest VARCHAR NOT NULL)""")
    db.execute("""CREATE TABLE meta.projection_lineage(
        scope VARCHAR,fact_id VARCHAR,role VARCHAR,column_id VARCHAR,record_id VARCHAR,
        PRIMARY KEY(scope,fact_id,role,column_id,record_id))""")
    physical = _candidate_rows(db)
    by_table: dict[str, dict[str, str]] = defaultdict(dict)
    collisions: dict[str, set[str]] = defaultdict(set)
    grain_bindings = []
    for binding in bindings.accepted:
        if binding.kind == "role" and binding.value in _IDS | _NUMERIC | _TIMES | {
            "device_imeis_slash"
        }:
            if len(binding.columns) != 1:
                collisions[binding.table_id].add(binding.value)
            elif binding.value in by_table[binding.table_id]:
                collisions[binding.table_id].add(binding.value)
            else:
                by_table[binding.table_id][binding.value] = binding.columns[0]
        elif binding.kind == "grain" and binding.value in _SCOPES:
            grain_bindings.append(binding)

    descriptors = []
    by_scope: dict[str, list] = defaultdict(list)
    for grain in grain_bindings:
        by_scope[_SCOPES[grain.value][0]].append(grain)
    for grain_name, (scope, required) in _SCOPES.items():
        grains = by_scope.get(scope, [])
        if not grains:
            continue
        rows: list[_Row] = []
        blocked: set[str] = set()
        source_tables = set()
        grouped: dict[tuple[str, str], _Row] = {}
        for grain in grains:
            table_id = grain.table_id
            source_tables.add(table_id)
            role_columns = by_table[table_id]
            for role in required:
                source_role = "device_imeis_slash" if role == "imei" else role
                if role != "snapshot_at" and source_role not in role_columns:
                    blocked.add(role)
                if source_role in collisions[table_id]:
                    blocked.add(role)
            if grain_name == "inventory_snapshot" and grain.snapshot_at is None:
                blocked.add("snapshot_at")
            if blocked:
                continue
            for source_fact_id, record_id, values in physical.get(table_id, []):
                mapped = {}
                source_columns = {}
                for role in required:
                    if role == "snapshot_at":
                        mapped[role] = date.fromisoformat(grain.snapshot_at)
                        source_columns[role] = ""  # declaration, not a source cell
                        continue
                    source_role = "device_imeis_slash" if role == "imei" else role
                    column = role_columns[source_role]
                    mapped[role] = values.get(column)
                    source_columns[role] = column
                    if not _compatible(role, mapped[role]) and role != "imei":
                        blocked.add(role)
                if scope == "order_line_devices":
                    raw_imeis = mapped["imei"]
                    pieces = raw_imeis.split(" / ") if type(raw_imeis) is str else []
                    if len(pieces) != 2 or any(
                        not piece.strip() or "/" in piece for piece in pieces
                    ):
                        blocked.add("imei")
                        continue
                    for index, imei in enumerate(pieces):
                        fact_id = _row_id(scope, table_id, f"{source_fact_id}:{index}")
                        rows.append(
                            _Row(
                                fact_id,
                                table_id,
                                record_id,
                                mapped | {"imei": imei},
                                source_columns,
                                (record_id,),
                            )
                        )
                    continue
                key = mapped.get("order_id")
                if scope in _GROUPED:
                    if type(key) is not str or not key.strip():
                        blocked.add("order_id")
                        continue
                    group_key = (table_id, key)
                    if group_key in grouped:
                        original = grouped[group_key]
                        for role in required:
                            if original.values[role] != mapped[role]:
                                blocked.add(role)
                        original.record_ids += (record_id,)
                        continue
                    fact_id = _row_id(scope, table_id, key)
                    grouped[group_key] = _Row(
                        fact_id, table_id, record_id, mapped, source_columns, (record_id,)
                    )
                else:
                    fact_id = _row_id(scope, table_id, source_fact_id)
                    rows.append(
                        _Row(fact_id, table_id, record_id, mapped, source_columns, (record_id,))
                    )
        rows.extend(grouped.values())
        descriptors.append(
            _materialize(db, scope, required, rows, blocked, source_tables, bindings.digest)
        )
    return ProjectionResult(tuple(descriptors), bindings.digest)


def publish_projections(
    db: DuckDBPyConnection, qualification: "Qualification"
) -> tuple[PublishedProjection, ...]:
    """Expose only scopes requalified against the persisted, unchanged audit."""
    from datamate.preparation.audit import qualify, read_report

    report = read_report(db)
    if qualification.report_digest != report.report_digest:
        raise ValueError("qualification report fingerprint mismatch")
    expected = qualify(report, dict(qualification.dependencies))
    if expected != qualification:
        raise ValueError("qualification status disagrees with persisted audit")
    descriptors = db.execute(
        "SELECT scope,table_name,columns_json,blocked_json,row_count,bindings_digest "
        "FROM meta.projection_descriptors ORDER BY scope"
    ).fetchall()
    published = []
    allowed = {scope for scope, _ in _SCOPES.values()}
    for scope, table_name, columns_json, blocked_json, count, bindings_digest in descriptors:
        if qualification.status(scope) != "READY":
            continue
        if (
            scope not in allowed
            or table_name != "candidate_" + scope
            or json.loads(blocked_json)
            or bindings_digest != report.bindings_digest
            or count < 1
        ):
            raise ValueError("qualification descriptor is incomplete")
        columns = tuple(json.loads(columns_json))
        db.execute(f'CREATE TABLE clean."{scope}" AS SELECT * FROM meta."{table_name}"')
        published.append(PublishedProjection(scope, scope, columns, count))
    return tuple(published)
