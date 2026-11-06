"""Source-only schema gaps; absence of a role is uncertainty, not corrupt data."""

from collections.abc import Sequence
from hashlib import sha256

from duckdb import DuckDBPyConnection

from datamate.preparation.models import NormalizationIssue, SemanticBindings, TableProfile


def discover_mapping_issues(
    profiles: Sequence[TableProfile], bindings: SemanticBindings
) -> tuple[NormalizationIssue, ...]:
    assigned = {(b.table_id, c) for b in bindings.accepted if b.kind == "role" for c in b.columns}
    return tuple(
        NormalizationIssue(
            issue_id=sha256(f"unmapped:{p.digest}:{c.column_id}".encode()).hexdigest(),
            scope=p.table_id,
            kind="unmapped_role",
            detail=f"Physical column {c.column_id} has no accepted semantic role. "
            "This is unknown coverage, not proof of a corrupt value. "
            "Consult source field declarations; do not infer authority from value shape.",
        )
        for p in profiles
        if p.source != "calendar.csv" and p.row_count
        for c in p.columns
        if (p.table_id, c.column_id) not in assigned
    )


def store_mapping_issues(db: DuckDBPyConnection, profiles, bindings):
    issues = list(discover_mapping_issues(profiles, bindings))
    for table, column, count in db.execute(
        "SELECT r.table_id,e.column_id,count(*) FROM meta.transform_events e "
        "JOIN staging.raw_rows r USING(record_id) WHERE e.status IN ('UNRESOLVED','UNSUPPORTED') "
        "GROUP BY r.table_id,e.column_id ORDER BY r.table_id,e.column_id"
    ).fetchall():
        issues.append(
            NormalizationIssue(
                issue_id=sha256(f"parse:{bindings.digest}:{table}:{column}".encode()).hexdigest(),
                scope=table,
                kind="unresolved_transform",
                detail=f"Column {column} has {count} unresolved/unsupported transformations; "
                "preserve the raw values and inspect existing transform events for evidence.",
            )
        )
    db.execute(
        "CREATE TABLE meta.normalization_issues(issue_id VARCHAR PRIMARY KEY, "
        "scope VARCHAR,kind VARCHAR,issue_json JSON)"
    )
    if issues:
        db.executemany(
            "INSERT INTO meta.normalization_issues VALUES (?,?,?,?)",
            [(i.issue_id, i.scope, i.kind, i.model_dump_json()) for i in issues],
        )
    return tuple(issues)
