"""Complete source-row projections with independently checked, scoped fields."""

import json
import re
from decimal import Decimal
from fractions import Fraction

from datamate.preparation.models import SemanticBindings, TableProfile
from datamate.preparation.storage import _decoded, iter_raw_batches

SOURCE_FIELDS_VERSION = "source-fields-v1"
NUMERIC_ROLES = frozenset(
    {
        "quantity",
        "inventory_quantity",
        "unit_price",
        "unit_cost",
        "line_net_amount",
        "order_total",
        "payment_amount",
        "line_discount_amount",
        "order_discount_amount",
        "line_discount_percent",
        "order_discount_percent",
        "unit_factor",
    }
)
TEXT_ROLES = frozenset(
    {
        "order_id",
        "product_id",
        "customer_id",
        "product_name",
        "customer_name",
        "unit",
        "base_unit",
    }
)


def source_scope(table_id: str) -> str:
    if not re.fullmatch(r"t_[0-9a-f]{64}", table_id):
        raise ValueError("invalid source table identity")
    return "source_" + table_id[2:]


def source_metadata(db, scope: str) -> dict | None:
    exists = db.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='meta' "
        "AND table_name='source_field_descriptors'"
    ).fetchone()
    if not exists:
        return None
    row = db.execute(
        "SELECT source_table_id,metadata_json FROM meta.source_field_descriptors WHERE scope=?",
        [scope],
    ).fetchone()
    if row is None:
        return None
    metadata = json.loads(row[1])
    if (
        source_scope(row[0]) != scope
        or source_scope(metadata.get("source_table_id", "")) != scope
        or metadata.get("version") != SOURCE_FIELDS_VERSION
    ):
        raise ValueError("invalid source field descriptor")
    return metadata


def source_view_sql(scope: str, columns) -> str:
    if not re.fullmatch(r"source_[0-9a-f]{64}", scope) or any(
        not re.fullmatch(r"c[0-9]{4,}", column) for column in columns
    ):
        raise ValueError("invalid source projection name")
    names = ",".join(("fact_id", "table_id", *columns))
    return f"SELECT {names} FROM meta.candidate_{scope}"


def _number(raw, role: str, style) -> Fraction:
    """Independent narrow lexical proof; never calls the normalization parser."""
    if type(raw) in (int, Decimal) or type(raw) is float:
        number = Fraction(str(raw))
        if role.endswith("_percent") and isinstance(style, str) and "%" in style:
            if not re.fullmatch(r"[0#]+(?:\.[0#]+)?\s*%", style):
                raise ValueError("unsupported native percentage format")
            number *= 100
    elif type(raw) is str:
        text = raw.strip()
        if role.endswith("_percent"):
            text = text.removesuffix("%").strip()
        if re.fullmatch(r"[+-]?[0-9]+", text):
            number = Fraction(text)
        elif role not in {"quantity", "inventory_quantity", "unit_factor"} and re.fullmatch(
            r"[+-]?[0-9]{1,3}(?:\.[0-9]{3})+\s*(?:đ|₫|VND)", text
        ):
            number = Fraction(re.sub(r"[.\sđ₫]|VND", "", text))
        else:
            raise ValueError("numeric source interpretation is not independently established")
    else:
        raise ValueError("missing or unsupported numeric source")
    if role.endswith("_percent") and not 0 <= number <= 100:
        raise ValueError("percentage outside source contract")
    return number


def _checked(raw, working, role: str, style):
    if role in NUMERIC_ROLES:
        if type(working) not in (int, Decimal) or not Decimal(working).is_finite():
            raise ValueError("normalization did not produce an exact numeric value")
        if Fraction(working) != _number(raw, role, style):
            raise ValueError("normalized numeric differs from independent source proof")
        value = Decimal(working)
        parts = value.as_tuple()
        scale = max(-parts.exponent, 0)
        digits = max(len(parts.digits) + parts.exponent, 0)
        if scale + digits > 38:
            raise ValueError("numeric exceeds DuckDB decimal precision")
        return value, scale, digits
    if role in TEXT_ROLES and type(raw) is str and raw.strip() and working == raw:
        return raw, 0, 0
    raise ValueError("text or semantic field lacks independent source proof")


def _batches(db, table_id: str):
    for batch in iter_raw_batches(db, table_id, 1000):
        data = [record for record in batch if record.kind == "data"]
        if not data:
            continue
        ids = [record.record_id for record in data]
        rows = db.execute(
            "SELECT r.record_id,d.kind,f.values_json FROM staging.raw_rows r "
            "LEFT JOIN meta.dispositions d USING(record_id) "
            "LEFT JOIN meta.facts f ON f.fact_id='row:'||r.record_id "
            "AND f.table_role='physical_row' WHERE r.record_id=ANY(?::VARCHAR[])",
            [ids],
        ).fetchall()
        state = {
            r: (
                kind,
                None if values is None else {c: _decoded(v) for c, v in json.loads(values).items()},
            )
            for r, kind, values in rows
        }
        # Repairs have separately source-bound receipts checked by audit_text_state.
        exists = db.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='meta' "
            "AND table_name='text_integrity'"
        ).fetchone()
        repairs = (
            {}
            if not exists
            else {
                (record, column): _decoded(json.loads(value))
                for record, column, value in db.execute(
                    "SELECT record_id,column_id,candidate_json FROM meta.text_integrity "
                    "WHERE status='ACCEPTED' AND record_id=ANY(?::VARCHAR[])",
                    [ids],
                ).fetchall()
            }
        )
        yield [(record, *state[record.record_id], repairs) for record in data]


def _cell(record, working, repairs, column: str, role: str):
    index = int(column[1:]) - 1
    raw = repairs.get((record.record_id, column), record.source.values[index])
    style = record.source.formats[index] if index < len(record.source.formats) else None
    return _checked(raw, working.get(column), role, style)


def project_source_fields(db, bindings: SemanticBindings):
    """Withhold an uncertain field as a whole; retain the complete row denominator."""
    from datamate.preparation.projection import ProjectionDescriptor
    from datamate.preparation.text_state import audit_text_state, effective_header

    exists = db.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='meta' "
        "AND table_name='profiles'"
    ).fetchone()
    if not exists:
        return ()
    db.execute(
        "CREATE TABLE meta.source_field_descriptors("
        "scope VARCHAR PRIMARY KEY,source_table_id VARCHAR,metadata_json JSON)"
    )
    issues = audit_text_state(db)
    profiles = [
        TableProfile.model_validate_json(row[0])
        for row in db.execute("SELECT profile FROM meta.profiles ORDER BY table_id").fetchall()
    ]
    result = []
    for profile in profiles:
        if profile.source == "calendar.csv":
            continue
        fields = {}
        withheld = {}
        for binding in bindings.accepted:
            if binding.table_id != profile.table_id or binding.kind != "role":
                continue
            if len(binding.columns) != 1:
                continue
            column = binding.columns[0]
            if column in fields:
                withheld[column] = "conflicting field semantics"
            fields[column] = {"role": binding.value, "binding_id": binding.binding_id}
        if not fields:
            continue
        for column in issues.get(profile.table_id, ()):
            if column in fields:
                withheld[column] = "unresolved source text integrity"
        count = unknown = 0
        scales = {c: 0 for c in fields}
        digits = {c: 0 for c in fields}
        for batch in _batches(db, profile.table_id):
            for record, kind, working, repairs in batch:
                count += 1
                if kind != "retained" or working is None:
                    unknown += 1
                    continue
                for column, field in fields.items():
                    if column in withheld:
                        continue
                    try:
                        _, scale, width = _cell(record, working, repairs, column, field["role"])
                        scales[column] = max(scales[column], scale)
                        digits[column] = max(digits[column], width)
                        if scales[column] + digits[column] > 38:
                            raise ValueError("mixed numeric scale exceeds precision")
                    except (ValueError, TypeError, ArithmeticError, IndexError):
                        withheld[column] = "incomplete or independently unproven source field"
        # Preambles and headers must also have an explicit structural disposition.
        unknown += db.execute(
            "SELECT count(*) FROM staging.raw_rows r "
            "LEFT JOIN meta.dispositions d USING(record_id) "
            "WHERE r.table_id=? AND r.kind<>'data' AND (d.kind IS NULL OR d.kind<>'structural')",
            [profile.table_id],
        ).fetchone()[0]
        selected = {c: field for c, field in fields.items() if c not in withheld}
        for column, field in selected.items():
            field["sql_type"] = (
                f"DECIMAL(38,{scales[column]})" if field["role"] in NUMERIC_ROLES else "VARCHAR"
            )
            field["header"] = next(
                effective_header(profile, c) for c in profile.columns if c.column_id == column
            )
        scope = source_scope(profile.table_id)
        metadata = {
            "version": SOURCE_FIELDS_VERSION,
            "kind": "source_fields",
            "source_table_id": profile.table_id,
            "profile_digest": profile.digest,
            "declared_data_rows": count,
            "unknown_rows": unknown,
            "fields": selected,
            "withheld_fields": withheld,
            "coverage": "single_source_table_observations",
            "joins": "denied",
            "measure_qualification": "UNRESOLVED",
        }
        db.execute(
            "INSERT INTO meta.source_field_descriptors VALUES (?,?,?)",
            [scope, profile.table_id, json.dumps(metadata, default=str)],
        )
        blocked = ("incomplete_source_rows",) if unknown or not count else ()
        if not selected:
            blocked += ("no_qualified_fields",)
        candidate = None
        if not blocked:
            candidate = "candidate_" + scope
            ddl = ",".join(f'"{c}" {f["sql_type"]}' for c, f in selected.items())
            db.execute(
                f'CREATE TABLE meta."{candidate}"('
                f"fact_id VARCHAR PRIMARY KEY,table_id VARCHAR NOT NULL,{ddl})"
            )
            sql_types = ["VARCHAR", "VARCHAR", *(f["sql_type"] for f in selected.values())]
            parameters = ",".join(f"unnest(?::{kind}[])" for kind in sql_types)
            for batch in _batches(db, profile.table_id):
                rows, lineage = [], []
                for record, _, working, repairs in batch:
                    fact_id = "row:" + record.record_id
                    values = [
                        _cell(record, working, repairs, c, f["role"])[0]
                        for c, f in selected.items()
                    ]
                    rows.append((fact_id, profile.table_id, *values))
                    lineage.extend((scope, fact_id, c, c, record.record_id) for c in selected)
                db.execute(
                    f'INSERT INTO meta."{candidate}" SELECT {parameters}',
                    [list(column) for column in zip(*rows, strict=True)],
                )
                db.execute(
                    "INSERT INTO meta.projection_lineage SELECT "
                    "unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),unnest(?::VARCHAR[]),"
                    "unnest(?::VARCHAR[]),unnest(?::VARCHAR[])",
                    [list(column) for column in zip(*lineage, strict=True)],
                )
        descriptor = ProjectionDescriptor(
            scope,
            candidate,
            tuple(selected) if candidate else (),
            blocked,
            count if candidate else 0,
            (profile.table_id,),
        )
        db.execute(
            "INSERT INTO meta.projection_descriptors VALUES (?,?,?,?,?,?,?)",
            [
                scope,
                candidate,
                json.dumps(descriptor.columns),
                json.dumps(blocked),
                descriptor.row_count,
                json.dumps(descriptor.source_tables),
                bindings.digest,
            ],
        )
        result.append(descriptor)
    return tuple(result)


def audit_source_fields(db, bindings: SemanticBindings, text_issues):
    """Recheck every published value and its complete raw row/field lineage."""
    from datamate.preparation.audit import AuditFinding, _coverage, _field_lineage

    findings = []
    exists = db.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='meta' "
        "AND table_name='source_field_descriptors'"
    ).fetchone()
    if not exists:
        return findings
    accepted = {b.binding_id: b for b in bindings.accepted}
    for scope, table, columns_json, blocked_json, count in db.execute(
        "SELECT scope,table_name,columns_json,blocked_json,row_count "
        "FROM meta.projection_descriptors ORDER BY scope"
    ).fetchall():
        metadata = source_metadata(db, scope)
        if metadata is None:
            continue
        source = metadata["source_table_id"]
        profile = TableProfile.model_validate_json(
            db.execute("SELECT profile FROM meta.profiles WHERE table_id=?", [source]).fetchone()[0]
        )
        fields = metadata["fields"]
        columns = json.loads(columns_json)
        valid = profile.digest == metadata["profile_digest"] and all(
            field["binding_id"] in accepted
            and accepted[field["binding_id"]].table_id == source
            and accepted[field["binding_id"]].columns == (column,)
            and accepted[field["binding_id"]].value == field["role"]
            and accepted[field["binding_id"]].profile_digest == profile.digest
            for column, field in fields.items()
        )
        findings.append(
            AuditFinding(
                "applicability", scope, "PASS" if valid else "FAIL", "accepted source field roles"
            )
        )
        coverage = _coverage(db, (source,), bindings)
        findings.append(AuditFinding("declared_coverage", scope, coverage.status, coverage.detail))
        if json.loads(blocked_json) or not table:
            findings.append(
                AuditFinding(
                    "projection_shape", scope, "UNRESOLVED", "source rows or fields unresolved"
                )
            )
            continue
        if table != "candidate_" + scope or columns != list(fields):
            raise ValueError("source candidate descriptor mismatch")
        mismatch = checked = 0
        nondata_unknown = db.execute(
            "SELECT count(*) FROM staging.raw_rows r "
            "LEFT JOIN meta.dispositions d USING(record_id) "
            "WHERE r.table_id=? AND r.kind<>'data' AND (d.kind IS NULL OR d.kind<>'structural')",
            [source],
        ).fetchone()[0]
        findings.append(
            AuditFinding(
                "source_structure",
                scope,
                "UNRESOLVED" if nondata_unknown else "PASS",
                f"unproven_nondata_rows={nondata_unknown}",
            )
        )
        lineage_bad = 0
        for batch in _batches(db, source):
            ids = ["row:" + record.record_id for record, *_ in batch]
            projected = {
                row[0]: row
                for row in db.execute(
                    f'SELECT * FROM meta."{table}" WHERE fact_id=ANY(?::VARCHAR[])',
                    [ids],
                ).fetchall()
            }
            edges = set(
                db.execute(
                    "SELECT fact_id,role,column_id,record_id FROM meta.projection_lineage "
                    "WHERE scope=? AND fact_id=ANY(?::VARCHAR[])",
                    [scope, ids],
                ).fetchall()
            )
            for record, kind, working, repairs in batch:
                checked += 1
                fact_id = "row:" + record.record_id
                row = projected.get(fact_id)
                if kind != "retained" or working is None or row is None or row[1] != source:
                    mismatch += 1
                    continue
                for index, (column, field) in enumerate(fields.items(), 2):
                    try:
                        value = _cell(record, working, repairs, column, field["role"])[0]
                        mismatch += value != row[index] or column in text_issues.get(source, ())
                    except (TypeError, ValueError, ArithmeticError, IndexError):
                        mismatch += 1
                    edge = (fact_id, column, column, record.record_id)
                    lineage_bad += edge not in edges
            lineage_bad += len(edges) != len(batch) * len(fields)
        actual = db.execute(f'SELECT count(*) FROM meta."{table}"').fetchone()[0]
        lineage_bad += db.execute(
            "SELECT count(*) FROM meta.projection_lineage WHERE scope=?", [scope]
        ).fetchone()[0] != checked * len(fields)
        mismatch += checked != metadata["declared_data_rows"] or metadata["unknown_rows"] != 0
        findings.append(
            AuditFinding(
                "projection_shape",
                scope,
                "PASS" if actual == count == checked else "FAIL",
                f"declared={count}; checked={checked}; actual={actual}",
            )
        )
        findings.append(_field_lineage(db, scope, table, tuple(fields)))
        findings.append(
            AuditFinding(
                "source_field_lineage",
                scope,
                "FAIL" if lineage_bad else "PASS",
                f"mismatches={lineage_bad}",
            )
        )
        findings.append(
            AuditFinding(
                "source_numeric",
                scope,
                "FAIL" if mismatch else "PASS",
                f"checked_fields={checked * len(fields)}; mismatches={mismatch}",
            )
        )
    return findings


def audit_derived_amounts(db, bindings: SemanticBindings):
    """Independently recompute private formula candidates using rational arithmetic."""
    from datamate.preparation.audit import AuditFinding
    from datamate.preparation.financial import FORMULA_ROLES

    result = []
    for formula in bindings.accepted:
        if formula.kind != "measure" or not formula.value.startswith("line_amount:"):
            continue
        name = formula.value.removeprefix("line_amount:")
        roles = {
            binding.value: binding.columns[0]
            for binding in bindings.accepted
            if binding.binding_id in formula.dependencies
            and binding.kind == "role"
            and binding.table_id == formula.table_id
            and len(binding.columns) == 1
        }
        if name not in FORMULA_ROLES or set(roles) != set(FORMULA_ROLES[name]):
            raise ValueError("derived amount lacks exact accepted formula dependencies")
        checked = mismatch = unknown = 0
        for batch in _batches(db, formula.table_id):
            for record, kind, working, repairs in batch:
                if kind != "retained" or working is None:
                    continue
                actual = working.get("derived_line_net_amount")
                if actual is None:
                    unknown += 1
                    continue
                try:
                    values = {}
                    for role, column in roles.items():
                        index = int(column[1:]) - 1
                        raw = repairs.get((record.record_id, column), record.source.values[index])
                        style = (
                            record.source.formats[index]
                            if index < len(record.source.formats)
                            else None
                        )
                        values[role] = _number(raw, role, style)
                    expected = values["quantity"] * values["unit_price"]
                    if name == "minus_line_discount":
                        expected -= values["line_discount_amount"]
                    elif name == "after_line_discount_percent":
                        expected *= (100 - values["line_discount_percent"]) / 100
                    checked += 1
                    mismatch += type(actual) not in (Decimal, int) or Fraction(actual) != expected
                except (TypeError, ValueError, ArithmeticError):
                    unknown += 1
        result.append(
            AuditFinding(
                "source_line_formula",
                f"field:{formula.table_id}/derived_line_net_amount",
                "FAIL" if mismatch else "UNRESOLVED" if unknown or not checked else "PASS",
                f"checked={checked}; unknown={unknown}; mismatch={mismatch}",
            )
        )
    return result
