"""Source-bound pre-header key/value annotations with unresolved scope semantics."""

import json
from dataclasses import replace

from datamate.preparation.models import Binding, EvidenceRef, ResolvedBinding, SemanticBindings
from datamate.preparation.storage import _decoded, read_staged_table

ANNOTATION_VERSION = "preheader-key-value-v1"
ANNOTATION_KIND = "preheader_key_value"


def metadata_binding(proposal, profile) -> Binding:
    columns = (proposal.label_column, proposal.value_column)
    if len(set(columns)) != 2 or not set(columns) <= {c.column_id for c in profile.columns}:
        raise ValueError("annotation requires two distinct source columns")
    if not proposal.record_id.startswith(profile.table_id + ":"):
        raise ValueError("annotation record belongs to another source table")
    return Binding(
        kind="structure",
        table_id=profile.table_id,
        columns=columns,
        value=ANNOTATION_KIND,
        marker=proposal.record_id,
        profile_digest=profile.digest,
        evidence=tuple(
            EvidenceRef(
                source=profile.source,
                sha256=profile.source_sha256,
                locator=f"raw:{proposal.record_id}/{column}",
            )
            for column in columns
        ),
    )


def _annotation(db, profile, binding):
    """Reconstruct the complete distinguishing shape from immutable staged cells."""
    from datamate.preparation.text_state import effective_header, effective_values

    if (
        binding.kind != "structure"
        or binding.value != ANNOTATION_KIND
        or binding.table_id != profile.table_id
        or binding.profile_digest != profile.digest
        or len(binding.columns) != 2
        or binding.dependencies
    ):
        raise ValueError("annotation source scope or profile is stale")
    table = read_staged_table(db, profile.table_id)
    if (
        table.source.relative_path != profile.source
        or table.source_sha256 != profile.source_sha256
        or table.header_row is None
        or len(table.columns) < 3
        or not set(binding.columns) <= set(table.columns)
    ):
        raise ValueError("annotation needs an established physical header")
    row = db.execute(
        "SELECT kind,source_row,ordinal,values_json,formulas_json FROM staging.raw_rows "
        "WHERE table_id=? AND record_id=?",
        [profile.table_id, binding.marker],
    ).fetchone()
    header = db.execute(
        "SELECT source_row,ordinal,values_json FROM staging.raw_rows "
        "WHERE table_id=? AND kind='header'",
        [profile.table_id],
    ).fetchall()
    if (
        row is None
        or row[0] != "preamble"
        or len(header) != 1
        or not row[1] < header[0][0] == table.header_row
        or not row[2] < header[0][1]
        or any(json.loads(row[4]))
    ):
        raise ValueError("annotation must be a formula-free record strictly before the header")
    if tuple(_decoded(v) for v in json.loads(header[0][2])) != table.headers:
        raise ValueError("staged header disagrees with physical source metadata")
    raw = tuple(_decoded(v) for v in json.loads(row[3]))
    values = effective_values(db, binding.marker, raw)
    occupied = {
        c
        for c, value in zip(table.columns, values, strict=True)
        if value is not None and value != ""
    }
    if occupied != set(binding.columns):
        raise ValueError("annotation record contains additional or missing source fields")
    label, value = (values[table.columns.index(c)] for c in binding.columns)
    if (
        type(label) is not str
        or type(value) is not str
        or not label.strip()
        or not value.strip()
        or max(len(label.encode()), len(value.encode())) > 2048
    ):
        raise ValueError("annotation requires bounded, nonblank textual key/value cells")
    headers = [effective_header(profile, c) for c in profile.columns]
    matches = [
        c.column_id
        for c, h in zip(profile.columns, headers, strict=True)
        if type(h) is str and h.strip() == label.strip()
    ]
    if len(matches) != 1 or value.strip() in {h.strip() for h in headers if type(h) is str}:
        raise ValueError("annotation key must identify one header; header echoes are not metadata")
    expected = tuple(
        EvidenceRef(
            source=profile.source,
            sha256=profile.source_sha256,
            locator=f"raw:{binding.marker}/{column}",
        )
        for column in binding.columns
    )
    if binding.evidence != expected:
        raise ValueError("annotation witnesses differ from their exact raw source cells")
    return {
        "version": ANNOTATION_VERSION,
        "record_id": binding.marker,
        "table_id": profile.table_id,
        "label_column": binding.columns[0],
        "value_column": binding.columns[1],
        "header_column": matches[0],
        "key": label,
        "value": value,
        "scope_semantics": "UNRESOLVED",
    }


def enrich_source_annotations(db, profiles, bindings: SemanticBindings, *, record_receipts=True):
    """Accept only proposed, independently reconstructed pre-header annotations."""
    if record_receipts:
        db.execute(
            "CREATE TABLE IF NOT EXISTS meta.source_annotations("
            "record_id VARCHAR PRIMARY KEY,table_id VARCHAR,binding_id VARCHAR,"
            "annotation_json JSON)"
        )
    by_table = {p.table_id: p for p in profiles}
    entries = []
    for entry in bindings.entries:
        binding = entry.binding
        if binding.kind != "structure" or binding.value != ANNOTATION_KIND:
            entries.append(entry)
            continue
        try:
            if binding.table_id not in by_table:
                raise ValueError("annotation profile is missing")
            role_columns = {
                c
                for b in bindings.accepted
                if b.table_id == binding.table_id and b.kind == "role"
                for c in b.columns
            }
            if len(role_columns) < 2:
                raise ValueError(
                    "annotation header needs at least two validated source field roles"
                )
            annotation = _annotation(db, by_table[binding.table_id], binding)
        except (ValueError, TypeError, IndexError) as error:
            entries.append(ResolvedBinding(binding, "UNRESOLVED", str(error)))
            continue
        entries.append(
            ResolvedBinding(
                binding,
                "ACCEPTED",
                "source-bound pre-header key/value",
                acceptance_rule=ANNOTATION_VERSION,
            )
        )
        if record_receipts:
            db.execute(
                "INSERT INTO meta.source_annotations VALUES (?,?,?,?)",
                [
                    binding.marker,
                    binding.table_id,
                    binding.binding_id,
                    json.dumps(annotation, ensure_ascii=False, sort_keys=True),
                ],
            )
    return replace(bindings, entries=tuple(entries))


def has_annotation_proof(binding, bindings) -> bool:
    return any(
        e.status == "ACCEPTED"
        and e.acceptance_rule == ANNOTATION_VERSION
        and e.binding.binding_id == binding.binding_id
        for e in bindings.entries
    )


def annotations_for_table(db, table_id: str) -> list[dict]:
    exists = db.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='meta' "
        "AND table_name='source_annotations'"
    ).fetchone()
    if not exists:
        return []
    return [
        json.loads(row[0])
        for row in db.execute(
            "SELECT annotation_json FROM meta.source_annotations WHERE table_id=? "
            "ORDER BY record_id",
            [table_id],
        ).fetchall()
    ]


def audit_source_annotations(db, bindings):
    """Rebuild receipts and verify dispositions; annotations never prove business coverage."""
    from datamate.preparation.audit import AuditFinding
    from datamate.preparation.models import TableProfile

    accepted = [
        e.binding
        for e in bindings.entries
        if e.status == "ACCEPTED"
        and e.binding.kind == "structure"
        and e.binding.value == ANNOTATION_KIND
    ]
    exists = db.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='meta' "
        "AND table_name='source_annotations'"
    ).fetchone()
    stored = (
        {}
        if not exists
        else {
            row[0]: row[1:]
            for row in db.execute(
                "SELECT record_id,table_id,binding_id,annotation_json FROM meta.source_annotations"
            ).fetchall()
        }
    )
    if not accepted and not stored:
        return AuditFinding("source_annotations", "*", "NOT_APPLICABLE", "no annotation receipts")
    mismatch = len(stored) != len(accepted)
    profiles = {
        p.table_id: p
        for row in db.execute("SELECT profile FROM meta.profiles").fetchall()
        if (p := TableProfile.model_validate_json(row[0]))
    }
    for binding in accepted:
        try:
            roles = {
                c
                for b in bindings.accepted
                if b.kind == "role" and b.table_id == binding.table_id
                for c in b.columns
            }
            if len(roles) < 2:
                raise ValueError("annotation header lacks validated source field roles")
            expected = _annotation(db, profiles[binding.table_id], binding)
            row = stored.get(binding.marker)
            disposition = db.execute(
                "SELECT kind FROM meta.dispositions WHERE record_id=?", [binding.marker]
            ).fetchone()
            mismatch |= (
                not has_annotation_proof(binding, bindings)
                or row is None
                or row[:2] != (binding.table_id, binding.binding_id)
                or json.loads(row[2]) != expected
                or disposition != ("structural",)
            )
        except (ValueError, TypeError, KeyError, IndexError):
            mismatch = True
    return AuditFinding(
        "source_annotations",
        "*",
        "FAIL" if mismatch else "PASS",
        f"accepted={len(accepted)}; receipts={len(stored)}; mismatch={int(mismatch)}",
    )
