"""Bounded source-only summaries of persisted preparation evidence.

This module executes only application-owned SELECTs over existing base tables.
It never reruns preparation/audits, derives readiness, or opens source files.
Missing tables or required columns represent unknown evidence, not zero counts.
"""

import math

from duckdb import DuckDBPyConnection

# Query fragments are fixed application constants, never identifiers from metadata.
_RELATIONS = {
    "raw": "staging.raw_rows",
    "issues": "meta.normalization_issues",
    "dispositions": "meta.dispositions",
    "bindings": "meta.bindings",
    "transform_events": "meta.transform_events",
    "qualification": "meta.qualification",
    "profiles": "meta.profiles",
    "facts": "meta.facts",
    "lineage": "meta.lineage",
    "audits": "meta.audit_receipt",
}
_GROUPS = {
    "dispositions": "kind",
    "issues": "kind",
    "bindings": "status",
    "transform_events": "status",
    "qualification": "status",
}
_AUDIT_FINDINGS = (
    "SELECT json_extract_string(j.value,'$.status') AS status, "
    "json_extract_string(j.value,'$.check_id') AS check_id "
    "FROM meta.audit_receipt r, json_each("
    "CASE WHEN json_type(r.findings_json)='ARRAY' THEN r.findings_json ELSE '[]' END) j"
)


def _measurements(resources: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in ("elapsed_seconds", "peak_rss_bytes", "peak_disk_bytes"):
        value = resources.get(name)
        valid = type(value) in (int, float) if name == "elapsed_seconds" else type(value) is int
        result[name] = value if valid and value >= 0 and math.isfinite(value) else None
    complete = resources.get("measurements_complete")
    result["measurements_complete"] = complete if type(complete) is bool else None
    return result


def diagnose_preparation(
    db: DuckDBPyConnection, *, resources: dict[str, object]
) -> dict[str, object]:
    """Summarize evidence without mutating it or interpreting source payloads.

    Exact totals retain all records; group/scope/failing-check details include at
    most 100 entries, with identifiers capped at 128 characters and truncation
    flags. Declared coverage counts all raw/data records before quarantine, not
    surviving facts. Resources are supplied measurements, never sampled here.
    Unsupported families and parse ambiguity units remain unknown until their
    own persisted contracts exist; event failures cannot establish either.
    """
    rows = db.execute(
        "SELECT c.table_schema,c.table_name,c.column_name "
        "FROM information_schema.columns c JOIN information_schema.tables t "
        "ON c.table_catalog=t.table_catalog AND c.table_schema=t.table_schema "
        "AND c.table_name=t.table_name "
        "WHERE t.table_type='BASE TABLE' AND c.table_catalog=current_database() "
        "AND c.table_schema IN ('meta','staging')"
    ).fetchall()
    columns: dict[str, set[str]] = {}
    for schema, table, column in rows:
        relation = f"{schema}.{table}"
        if relation in _RELATIONS.values():
            columns.setdefault(relation, set()).add(column)

    def exists(key: str, *required: str) -> bool:
        return _RELATIONS[key] in columns and set(required) <= columns[_RELATIONS[key]]

    def count(key: str) -> int | None:
        if not exists(key):
            return None
        return db.execute(f"SELECT count(*) FROM {_RELATIONS[key]}").fetchone()[0]

    def grouped(relation: str, column: str) -> dict[str, object]:
        # Both arguments originate exclusively from the fixed maps/CTE above.
        total = db.execute(f"SELECT count(*) FROM {relation}").fetchone()[0]
        groups = db.execute(
            f"SELECT {column},count(*) FROM {relation} GROUP BY {column} "
            f"ORDER BY {column} NULLS FIRST LIMIT 101"
        ).fetchall()
        # Do not merge truncated labels: an overlong group is omitted and marked.
        bounded = [(key, value) for key, value in groups[:100] if key is None or len(key) <= 128]
        return {
            "total": total,
            "counts": dict(bounded),
            "truncated": len(groups) > 100 or len(bounded) != len(groups[:100]),
        }

    raw = count("raw")
    data = (
        db.execute("SELECT count(*) FROM staging.raw_rows WHERE kind='data'").fetchone()[0]
        if exists("raw", "kind")
        else None
    )
    result: dict[str, object] = {
        "raw_records": raw,
        "data_records": data,
        "declared_coverage": {"raw_records": raw, "data_records": data},
        "profiles": count("profiles"),
        "facts": count("facts"),
        "lineage": count("lineage"),
        "unsupported_families": None,
        "parse_ambiguity_units": None,
        "resources": _measurements(resources),
        "checkpoint_claims": None,
        "http_attempts": None,
    }
    for key, column in _GROUPS.items():
        result[key] = grouped(_RELATIONS[key], column) if exists(key, column) else None
    if exists("qualification", "scope", "status"):
        qualification = result["qualification"]
        scopes = db.execute(
            "SELECT scope,status FROM meta.qualification ORDER BY scope,status LIMIT 101"
        ).fetchall()
        qualification["scopes"] = [
            {
                "scope": scope[:128] if scope is not None else None,
                "status": status[:128] if status is not None else None,
            }
            for scope, status in scopes[:100]
        ]
        qualification["truncated"] = (
            qualification["truncated"]
            or len(scopes) > 100
            or any(len(value) > 128 for row in scopes[:100] for value in row if value is not None)
        )
    else:
        result["qualification"] = None
    result["audits"] = None
    if exists("audits", "findings_json"):
        relation = f"({_AUDIT_FINDINGS}) findings"
        audits = grouped(relation, "status")
        checks = db.execute(
            f"SELECT DISTINCT check_id FROM {relation} "
            "WHERE status='FAIL' AND check_id IS NOT NULL ORDER BY check_id LIMIT 101"
        ).fetchall()
        audits["failing_check_ids"] = [row[0][:128] for row in checks[:100]]
        audits["failing_checks_truncated"] = len(checks) > 100 or any(
            len(row[0]) > 128 for row in checks[:100]
        )
        result["audits"] = audits
    return result
