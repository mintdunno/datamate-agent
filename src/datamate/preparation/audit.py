"""Independent source/fact checks and conservative scope qualification."""

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from duckdb import DuckDBPyConnection

from datamate.preparation.models import SemanticBindings
from datamate.preparation.projection import _NUMERIC, _SCOPES

AuditStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE", "UNRESOLVED"]
ScopeStatus = Literal["READY", "FAIL", "NOT_APPLICABLE", "UNRESOLVED"]
_STATE_TABLES = (
    ("staging.raw_rows", "record_id"),
    ("meta.source_tables", "table_id"),
    ("meta.dispositions", "record_id"),
    ("meta.facts", "fact_id"),
    ("meta.lineage", "fact_id,record_id,column_id,step_id"),
    ("meta.projection_descriptors", "scope"),
    ("meta.projection_lineage", "scope,fact_id,role,column_id,record_id"),
)


@dataclass(frozen=True)
class AuditFinding:
    check_id: str
    scope: str
    status: AuditStatus
    detail: str


@dataclass(frozen=True)
class AuditReport:
    findings: tuple[AuditFinding, ...]
    report_digest: str
    state_digest: str
    bindings_digest: str
    declared_data_rows: int


@dataclass(frozen=True)
class Qualification:
    report_digest: str
    dependencies: tuple[tuple[str, tuple[str, ...]], ...]
    statuses: tuple[tuple[str, ScopeStatus], ...]

    def status(self, scope: str) -> ScopeStatus:
        return dict(self.statuses).get(scope, "UNRESOLVED")


def _state_digest(db: DuckDBPyConnection) -> str:
    digest = hashlib.sha256()
    candidates = db.execute(
        "SELECT scope,table_name FROM meta.projection_descriptors "
        "WHERE table_name IS NOT NULL ORDER BY scope"
    ).fetchall()
    allowed = {item[0] for item in _SCOPES.values()}
    if any(scope not in allowed or name != "candidate_" + scope for scope, name in candidates):
        raise ValueError("unknown candidate table in audit state")
    tables = (*_STATE_TABLES, *((f'meta."{name}"', "fact_id") for _, name in candidates))
    for table, order in tables:
        digest.update(table.encode())
        cursor = db.execute(f"SELECT * FROM {table} ORDER BY {order}")
        while rows := cursor.fetchmany(1000):
            for row in rows:
                digest.update(json.dumps(row, default=str, ensure_ascii=False).encode("utf-8"))
                digest.update(b"\n")
    return digest.hexdigest()


def _report_digest(
    findings: tuple[AuditFinding, ...], state_digest: str, bindings_digest: str, count: int
) -> str:
    payload = [state_digest, bindings_digest, count, [asdict(finding) for finding in findings]]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _descriptors(db: DuckDBPyConnection) -> list[tuple]:
    return db.execute(
        "SELECT scope,table_name,columns_json,blocked_json,row_count,source_tables_json,"
        "bindings_digest FROM meta.projection_descriptors ORDER BY scope"
    ).fetchall()


def _raw_accounting(db: DuckDBPyConnection) -> tuple[AuditFinding, int]:
    raw_count, data_count = db.execute(
        "SELECT count(*),count(*) FILTER (WHERE kind='data') FROM staging.raw_rows"
    ).fetchone()
    missing = db.execute(
        "SELECT count(*) FROM staging.raw_rows r LEFT JOIN meta.dispositions d "
        "ON r.record_id=d.record_id WHERE d.record_id IS NULL"
    ).fetchone()[0]
    orphan = db.execute(
        "SELECT count(*) FROM meta.dispositions d LEFT JOIN staging.raw_rows r "
        "ON d.record_id=r.record_id WHERE r.record_id IS NULL"
    ).fetchone()[0]
    disposition_count = db.execute("SELECT count(*) FROM meta.dispositions").fetchone()[0]
    if raw_count == 0:
        status = "UNRESOLVED"
    elif missing or orphan or disposition_count != raw_count:
        status = "FAIL"
    else:
        status = "PASS"
    return AuditFinding(
        "raw_accounting",
        "*",
        status,
        f"raw={raw_count}; dispositions={disposition_count}; missing={missing}; orphan={orphan}",
    ), data_count


def _fact_lineage(db: DuckDBPyConnection) -> AuditFinding:
    orphan = db.execute(
        "SELECT count(*) FROM meta.lineage l "
        "LEFT JOIN meta.facts f ON f.fact_id=l.fact_id "
        "LEFT JOIN staging.raw_rows r ON r.record_id=l.record_id "
        "WHERE f.fact_id IS NULL OR r.record_id IS NULL"
    ).fetchone()[0]
    missing = db.execute(
        "SELECT count(*) FROM meta.facts f LEFT JOIN meta.lineage l "
        "ON l.fact_id=f.fact_id WHERE l.fact_id IS NULL"
    ).fetchone()[0]
    fact_count = db.execute("SELECT count(*) FROM meta.facts").fetchone()[0]
    status: AuditStatus = (
        "UNRESOLVED" if fact_count == 0 else "FAIL" if orphan or missing else "PASS"
    )
    return AuditFinding(
        "source_fact_lineage",
        "*",
        status,
        f"facts={fact_count}; missing={missing}; orphan={orphan}",
    )


def _coverage(db: DuckDBPyConnection, tables: tuple[str, ...]) -> AuditFinding:
    if not tables:
        return AuditFinding("declared_coverage", "", "UNRESOLVED", "no source tables")
    placeholders = ",".join("?" for _ in tables)
    declared, retained, duplicate, quarantined = db.execute(
        "SELECT count(*),"
        "count(*) FILTER (WHERE d.kind='retained'),"
        "count(*) FILTER (WHERE d.kind='duplicate'),"
        "count(*) FILTER (WHERE d.kind='quarantined') "
        "FROM staging.raw_rows r LEFT JOIN meta.dispositions d ON r.record_id=d.record_id "
        f"WHERE r.kind='data' AND r.table_id IN ({placeholders})",
        list(tables),
    ).fetchone()
    if declared == 0:
        status = "UNRESOLVED"
    elif retained == declared:
        status = "PASS"
    else:
        # Duplicate-collapse proof is unavailable in this storage version.
        status = "UNRESOLVED"
    return AuditFinding(
        "declared_coverage",
        "",
        status,
        f"declared={declared}; retained={retained}; "
        f"duplicate={duplicate}; quarantined={quarantined}",
    )


def _field_lineage(
    db: DuckDBPyConnection, scope: str, table_name: str, columns: tuple[str, ...]
) -> AuditFinding:
    missing = 0
    for role in columns:
        missing += db.execute(
            f'SELECT count(*) FROM meta."{table_name}" c LEFT JOIN meta.projection_lineage p '
            "ON p.scope=? AND p.fact_id=c.fact_id AND p.role=? "
            "WHERE p.fact_id IS NULL",
            [scope, role],
        ).fetchone()[0]
    invalid = db.execute(
        "SELECT count(*) FROM meta.projection_lineage p "
        f'LEFT JOIN meta."{table_name}" c ON c.fact_id=p.fact_id '
        "LEFT JOIN staging.raw_rows r ON r.record_id=p.record_id "
        "LEFT JOIN meta.lineage l ON l.fact_id='row:'||p.record_id "
        "AND l.record_id=p.record_id AND l.column_id=p.column_id "
        "WHERE p.scope=? AND (r.record_id IS NULL OR c.fact_id IS NULL "
        "OR c.table_id<>r.table_id OR (p.column_id<>'' AND l.fact_id IS NULL))",
        [scope],
    ).fetchone()[0]
    status = "FAIL" if missing or invalid else "PASS"
    return AuditFinding("field_lineage", scope, status, f"missing={missing}; invalid={invalid}")


def _source_numeric(
    db: DuckDBPyConnection, scope: str, table_name: str, columns: tuple[str, ...]
) -> AuditFinding:
    numeric = [role for role in columns if role in _NUMERIC]
    if not numeric:
        return AuditFinding(
            "source_numeric", scope, "NOT_APPLICABLE", "trusted schema has no numeric fields"
        )
    checked = unknown = mismatch = 0
    for role in numeric:
        cursor = db.execute(
            f'SELECT c."{role}",r.values_json,t.columns_json,p.column_id '
            f'FROM meta."{table_name}" c JOIN meta.projection_lineage p '
            "ON p.fact_id=c.fact_id AND p.scope=? AND p.role=? "
            "JOIN staging.raw_rows r ON r.record_id=p.record_id "
            "JOIN meta.source_tables t ON t.table_id=r.table_id",
            [scope, role],
        )
        while rows := cursor.fetchmany(1000):
            for candidate, values_json, columns_json, column_id in rows:
                columns_source = json.loads(columns_json)
                values_source = json.loads(values_json)
                if column_id not in columns_source:
                    mismatch += 1
                    continue
                raw = values_source[columns_source.index(column_id)]
                if raw["type"] not in ("int", "Decimal"):
                    unknown += 1
                    continue
                checked += 1
                original = Decimal(raw["value"])
                if (
                    candidate is None
                    or original != candidate
                    or (
                        raw["type"] == "Decimal"
                        and original.as_tuple().exponent != candidate.as_tuple().exponent
                    )
                ):
                    mismatch += 1
    status: AuditStatus = "FAIL" if mismatch else "UNRESOLVED" if unknown or not checked else "PASS"
    return AuditFinding(
        "source_numeric",
        scope,
        status,
        f"checked={checked}; unknown={unknown}; mismatch={mismatch}",
    )


def audit_prepared(db: DuckDBPyConnection, bindings: SemanticBindings) -> AuditReport:
    """Audit original rows and projected facts, never parser-reported pass scores."""
    descriptors = _descriptors(db)
    if any(row[6] != bindings.digest for row in descriptors):
        raise ValueError("projection binding fingerprint mismatch")
    accounting, data_count = _raw_accounting(db)
    findings = [accounting, _fact_lineage(db)]
    by_scope = {row[0]: row for row in descriptors}
    supported = {item[0] for item in _SCOPES.values()}
    capability_absence = {
        binding.value
        for binding in bindings.accepted
        if binding.kind == "capability" and binding.available is False
    }
    for scope in sorted(supported):
        if scope not in by_scope:
            status: AuditStatus = "NOT_APPLICABLE" if scope in capability_absence else "UNRESOLVED"
            detail = (
                "accepted source absence declaration"
                if status == "NOT_APPLICABLE"
                else "no applicability proof"
            )
            findings.append(AuditFinding("applicability", scope, status, detail))
            continue
        _, table_name, columns_json, blocked_json, row_count, tables_json, _ = by_scope[scope]
        blocked = tuple(json.loads(blocked_json))
        columns = tuple(json.loads(columns_json))
        tables = tuple(json.loads(tables_json))
        findings.append(AuditFinding("applicability", scope, "PASS", "accepted grain binding"))
        coverage = _coverage(db, tables)
        findings.append(AuditFinding(coverage.check_id, scope, coverage.status, coverage.detail))
        if blocked or not table_name or row_count == 0:
            findings.append(
                AuditFinding(
                    "projection_shape",
                    scope,
                    "UNRESOLVED",
                    f"blocked={','.join(blocked)}; rows={row_count}",
                )
            )
            findings.append(
                AuditFinding("field_lineage", scope, "UNRESOLVED", "no typed candidate")
            )
            findings.append(
                AuditFinding("source_numeric", scope, "UNRESOLVED", "no typed candidate")
            )
            continue
        actual_count = db.execute(f'SELECT count(*) FROM meta."{table_name}"').fetchone()[0]
        findings.append(
            AuditFinding(
                "projection_shape",
                scope,
                "PASS" if actual_count == row_count else "FAIL",
                f"declared={row_count}; actual={actual_count}",
            )
        )
        findings.append(_field_lineage(db, scope, table_name, columns))
        findings.append(_source_numeric(db, scope, table_name, columns))
    frozen = tuple(findings)
    state_digest = _state_digest(db)
    report_digest = _report_digest(frozen, state_digest, bindings.digest, data_count)
    db.execute("""CREATE TABLE IF NOT EXISTS meta.audit_receipt(
        report_digest VARCHAR PRIMARY KEY,state_digest VARCHAR NOT NULL,
        bindings_digest VARCHAR NOT NULL,declared_data_rows BIGINT NOT NULL,
        findings_json JSON NOT NULL)""")
    db.execute("DELETE FROM meta.audit_receipt")
    db.execute(
        "INSERT INTO meta.audit_receipt VALUES (?,?,?,?,?)",
        [
            report_digest,
            state_digest,
            bindings.digest,
            data_count,
            json.dumps([asdict(finding) for finding in frozen], sort_keys=True),
        ],
    )
    return AuditReport(frozen, report_digest, state_digest, bindings.digest, data_count)


def read_report(db: DuckDBPyConnection) -> AuditReport:
    """Reload the persisted audit and reject any post-audit candidate mutation."""
    rows = db.execute("SELECT * FROM meta.audit_receipt").fetchall()
    if len(rows) != 1:
        raise ValueError("qualification requires one persisted audit receipt")
    report_digest, state_digest, bindings_digest, data_count, findings_json = rows[0]
    findings = tuple(AuditFinding(**item) for item in json.loads(findings_json))
    if state_digest != _state_digest(db) or report_digest != _report_digest(
        findings, state_digest, bindings_digest, data_count
    ):
        raise ValueError("qualification audit receipt is stale")
    return AuditReport(findings, report_digest, state_digest, bindings_digest, data_count)


def qualify(report: AuditReport, dependencies: dict[str, tuple[str, ...]]) -> Qualification:
    """Require positive local evidence and recursively satisfied dependencies."""
    if any(
        not isinstance(scope, str) or not isinstance(deps, tuple)
        for scope, deps in dependencies.items()
    ):
        raise ValueError("invalid qualification dependencies")
    ordered_dependencies = tuple(
        sorted((scope, tuple(sorted(deps))) for scope, deps in dependencies.items())
    )
    scopes = sorted({finding.scope for finding in report.findings if finding.scope != "*"})
    global_statuses = [finding.status for finding in report.findings if finding.scope == "*"]
    statuses: dict[str, ScopeStatus] = {}
    for scope in scopes:
        local = [finding for finding in report.findings if finding.scope == scope]
        if any(
            finding.status == "FAIL"
            for finding in local + [f for f in report.findings if f.scope == "*"]
        ):
            statuses[scope] = "FAIL"
        elif (
            len(local) == 1
            and local[0].check_id == "applicability"
            and local[0].status == "NOT_APPLICABLE"
        ):
            statuses[scope] = "NOT_APPLICABLE"
        elif (
            global_statuses
            and all(status == "PASS" for status in global_statuses)
            and {finding.check_id for finding in local}
            >= {
                "applicability",
                "declared_coverage",
                "projection_shape",
                "field_lineage",
                "source_numeric",
            }
            and all(finding.status in ("PASS", "NOT_APPLICABLE") for finding in local)
            and any(
                finding.check_id == "projection_shape" and finding.status == "PASS"
                for finding in local
            )
        ):
            statuses[scope] = "READY"
        else:
            statuses[scope] = "UNRESOLVED"
    changed = True
    while changed:
        changed = False
        for scope, deps in ordered_dependencies:
            if statuses.get(scope) == "READY" and any(statuses.get(dep) != "READY" for dep in deps):
                statuses[scope] = "UNRESOLVED"
                changed = True
    return Qualification(
        report.report_digest, ordered_dependencies, tuple(sorted(statuses.items()))
    )
