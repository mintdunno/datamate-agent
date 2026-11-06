"""Source-bound Unicode receipts and separately validated working-value overlays."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import closing
from typing import TYPE_CHECKING

from duckdb import DuckDBPyConnection

from datamate.contracts.base import ContractModel
from datamate.preparation.normalization import encoded
from datamate.preparation.text_integrity import TextDecision, TextIntegrityPolicy, inspect_text

if TYPE_CHECKING:
    from datamate.preparation.models import (
        BusinessInputs,
        RawRecord,
        TableProfile,
    )
    from datamate.preparation.operations import OperationState


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("ascii")).hexdigest()


def _tag(value):
    return value if isinstance(value, dict) else encoded(value)


def _inspect(value, policy, kind):
    if not policy.enabled:
        raw = value["value"] if isinstance(value, dict) else value
        return TextDecision(
            before=raw,
            candidate=None,
            status="UNCHANGED",
            steps=(),
            reason="Text integrity disabled by policy.",
            policy_digest=policy.digest,
        )
    if isinstance(value, dict):
        return TextDecision(
            before=value["value"],
            candidate=None,
            status="UNRESOLVED",
            steps=(),
            reason="Undecoded SQLite TEXT bytes require source repair.",
            protected=True,
            policy_digest=policy.digest,
        )
    return inspect_text(value, policy, protected=kind in {"document", "metadata", "formula_value"})


class _HeaderProof(ContractModel):
    table_id: str
    source_sha256: str
    column_id: str
    source_header: dict[str, object]
    decision: TextDecision
    policy: TextIntegrityPolicy


def _exists(db) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='meta' AND "
            "table_name='text_integrity'"
        ).fetchone()
        is not None
    )


def _rows(db, sql: str, parameters=()) -> Iterator[tuple]:
    # A duplicate DuckDB cursor cannot see the build's uncommitted raw tables.
    # Materialize one keyset page before nested queries on the same connection.
    first = db.execute(f"SELECT * FROM ({sql}) text_schema LIMIT 0", parameters).description[0][0]
    key = '"' + first.replace('"', '""') + '"'
    last = None
    while True:
        where = "" if last is None else f" WHERE CAST({key} AS VARCHAR)>?"
        args = list(parameters) + ([] if last is None else [last])
        rows = db.execute(
            f"SELECT * FROM ({sql}) text_rows{where} ORDER BY CAST({key} AS VARCHAR) LIMIT 500",
            args,
        ).fetchall()
        if not rows:
            return
        last = str(rows[-1][0])
        yield from rows


def _texts(db):
    for table, digest, name, columns, headers in _rows(
        db,
        "SELECT table_id,source_sha256,table_name,columns_json,headers_json "
        "FROM meta.source_tables ORDER BY table_id",
    ):
        yield f"table:{table}", table, None, None, digest, name, "table"
        for column, header in zip(json.loads(columns), json.loads(headers), strict=True):
            if header["type"] == "str":
                yield (
                    f"header:{table}:{column}",
                    table,
                    None,
                    column,
                    digest,
                    header["value"],
                    "header",
                )
    for record, table, values, formulas, digest in _rows(
        db,
        "SELECT r.record_id,r.table_id,r.values_json,r.formulas_json,t.source_sha256 "
        "FROM staging.raw_rows r JOIN meta.source_tables t USING(table_id) "
        "WHERE r.kind<>'header' ORDER BY r.record_id",
    ):
        formulas = json.loads(formulas)
        for index, value in enumerate(json.loads(values)):
            if value["type"] in {"str", "UndecodedText"}:
                column = f"c{index + 1:04d}"
                raw = value["value"] if value["type"] == "str" else value
                yield (
                    f"cell:{record}:{column}",
                    table,
                    record,
                    column,
                    digest,
                    raw,
                    ("formula_value" if index < len(formulas) and formulas[index] else "cell"),
                )
    for table, digest, source, types in _rows(
        db,
        "SELECT table_id,source_sha256,source,declared_types_json FROM meta.source_tables "
        "ORDER BY table_id",
    ):
        yield f"path:{table}", table, None, None, digest, source, "metadata"
        for index, value in enumerate(json.loads(types)):
            if isinstance(value, str):
                column = f"c{index + 1:04d}"
                yield f"type:{table}:{column}", table, None, column, digest, value, "metadata"
    for record, table, formulas, formats, digest in _rows(
        db,
        "SELECT r.record_id,r.table_id,r.formulas_json,r.formats_json,t.source_sha256 FROM "
        "staging.raw_rows r JOIN meta.source_tables t USING(table_id) ORDER BY r.record_id",
    ):
        for family, values in (("formula", formulas), ("format", formats)):
            for index, value in enumerate(json.loads(values)):
                if isinstance(value, str):
                    column = f"c{index + 1:04d}"
                    yield (
                        f"{family}:{record}:{column}",
                        table,
                        record,
                        column,
                        digest,
                        value,
                        "metadata",
                    )
    if db.execute(
        "SELECT 1 FROM duckdb_tables() WHERE schema_name='meta' AND table_name='source_documents'"
    ).fetchone():
        for source, digest, text in _rows(
            db, "SELECT source,source_sha256,text FROM meta.source_documents ORDER BY source"
        ):
            for line, quote in enumerate(text.splitlines(keepends=True), 1):
                yield f"document:{source}:{line}", "", None, None, digest, quote, "document"


def _write(db, locator, table, record, column, digest, before, decision, policy, proof):
    values = [
        locator,
        table,
        record,
        column,
        digest,
        _json(_tag(before)),
        None if decision.candidate is None else _json(encoded(decision.candidate)),
        decision.status,
        decision.model_dump_json(),
        policy.model_dump_json(),
    ]
    proof = dict(proof)
    proof["receipt_sha256"] = _hash([values, proof])
    db.execute(
        "INSERT OR REPLACE INTO meta.text_integrity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [*values, _json(proof)],
    )


def _receipts(db):
    return _rows(
        db,
        "SELECT locator,table_id,record_id,column_id,source_sha256,before_json,"
        "candidate_json,status,decision_json,policy_json,proof_json "
        "FROM meta.text_integrity ORDER BY locator",
    )


def prepare_text_state(
    db: DuckDBPyConnection, inputs: BusinessInputs, policy: TextIntegrityPolicy
) -> None:
    """Scan immutable staging; source files are opened only for native schema evidence."""
    db.execute(
        "CREATE TABLE meta.text_integrity(locator VARCHAR PRIMARY KEY,table_id VARCHAR,"
        "record_id VARCHAR,column_id VARCHAR,source_sha256 VARCHAR,before_json JSON,"
        "candidate_json JSON,status VARCHAR,decision_json JSON,policy_json JSON,proof_json JSON)"
    )
    _write(
        db,
        "@policy",
        "",
        None,
        None,
        "",
        policy.digest,
        inspect_text(policy.digest, policy),
        policy,
        {"kind": "policy"},
    )
    for locator, table, record, column, digest, text, kind in _texts(db):
        decision = _inspect(text, policy, kind)
        if decision.status != "UNCHANGED":
            _write(
                db, locator, table, record, column, digest, text, decision, policy, {"kind": kind}
            )
    for source in inputs.sources:
        if source.path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            continue
        if not source.path.resolve().is_relative_to(inputs.split_root.resolve()):
            raise ValueError("native schema source is outside the bound snapshot")
        with source.path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != source.sha256:
            raise ValueError("native schema source changed")
        tables = dict(
            db.execute(
                "SELECT table_name,table_id FROM meta.source_tables WHERE source=?",
                [source.relative_path],
            ).fetchall()
        )
        schema = {
            "kind": "schema",
            "primary_keys": [],
            "foreign_keys": [],
            "protected_columns": [],
            "unsupported_keys": [],
        }
        with closing(
            sqlite3.connect(source.path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as native:
            for name, table in sorted(tables.items()):
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [
                    row
                    for row in native.execute(f"PRAGMA table_xinfo({quoted})").fetchall()
                    if row[6] != 1
                ]
                names = {row[1]: f"c{index + 1:04d}" for index, row in enumerate(columns)}
                schema["primary_keys"].extend([table, names[row[1]]] for row in columns if row[5])
                keys = native.execute(f"PRAGMA foreign_key_list({quoted})").fetchall()
                for key in keys:
                    child_column = names.get(key[3])
                    if child_column is not None:
                        schema["protected_columns"].append([table, child_column])
                    if (
                        sum(row[0] == key[0] for row in keys) != 1
                        or key[2] not in tables
                        or key[4] is None
                    ):
                        if child_column is not None:
                            schema["unsupported_keys"].append([table, child_column])
                        continue  # Composite or external namespaces require a separate proof.
                    target = tables[key[2]]
                    target_columns = json.loads(
                        db.execute(
                            "SELECT headers_json FROM meta.source_tables WHERE table_id=?", [target]
                        ).fetchone()[0]
                    )
                    matches = [
                        f"c{i + 1:04d}"
                        for i, value in enumerate(target_columns)
                        if value["value"] == key[4]
                    ]
                    if key[3] in names and len(matches) == 1:
                        schema["foreign_keys"].append([table, names[key[3]], target, matches[0]])
        _write(
            db,
            "schema:" + source.relative_path,
            "",
            None,
            None,
            source.sha256,
            source.relative_path,
            inspect_text(source.relative_path, policy),
            policy,
            schema,
        )


def annotate_profiles(
    db: DuckDBPyConnection, profiles: Sequence[TableProfile], policy: TextIntegrityPolicy
) -> tuple[TableProfile, ...]:
    result = []
    for profile in profiles:
        columns, labels = [], []
        for column in profile.columns:
            row = db.execute(
                "SELECT decision_json FROM meta.text_integrity WHERE locator=?",
                [f"header:{profile.table_id}:{column.column_id}"],
            ).fetchone()
            proof = None
            if row:
                decision = TextDecision.model_validate_json(row[0])
                proof = _HeaderProof(
                    table_id=profile.table_id,
                    source_sha256=profile.source_sha256,
                    column_id=column.column_id,
                    source_header=column.source_header,
                    decision=decision,
                    policy=policy,
                ).model_dump(mode="json")
            updated = column.model_copy(update={"header_repair": proof})
            columns.append(updated)
            labels.append(
                unicodedata.normalize("NFKC", effective_header(profile, updated)).casefold()
            )
        collisions = {
            column.column_id
            for column, label in zip(columns, labels, strict=True)
            if label and labels.count(label) > 1
        }
        if collisions:
            for row in _receipts(db):
                if (
                    row[1] == profile.table_id
                    and row[3] in collisions
                    and json.loads(row[10])["kind"] == "header"
                ):
                    _rewrite(db, row, "UNRESOLVED", {"collision": True})
            columns = [
                column.model_copy(update={"header_repair": None})
                if column.column_id in collisions
                else column
                for column in columns
            ]
        name = db.execute(
            "SELECT candidate_json,status FROM meta.text_integrity WHERE locator=?",
            ["table:" + profile.table_id],
        ).fetchone()
        display = json.loads(name[0])["value"] if name and name[1] == "ACCEPTED" else None
        result.append(
            profile.model_copy(update={"columns": tuple(columns), "source_table_display": display})
        )
    return tuple(result)


def store_decoding_receipt(db: DuckDBPyConnection, table_id: str, decision: dict) -> None:
    source_hash = db.execute(
        "SELECT source_sha256 FROM meta.source_tables WHERE table_id=?", [table_id]
    ).fetchone()
    if source_hash is None or source_hash[0] != decision.get("source_sha256"):
        raise ValueError("decoder receipt source mismatch")
    policy = TextIntegrityPolicy.model_validate_json(
        db.execute(
            "SELECT policy_json FROM meta.text_integrity WHERE locator='@policy'"
        ).fetchone()[0]
    )
    encoding = decision["encoding"]
    _write(
        db,
        "decoder:" + table_id,
        table_id,
        None,
        None,
        source_hash[0],
        encoding,
        inspect_text(encoding, policy),
        policy,
        {"kind": "decoding", "decoder": decision},
    )


def effective_header(profile: TableProfile, column) -> str:
    raw = column.source_header
    if raw.get("type") != "str":
        return str(raw.get("value", ""))
    receipt = getattr(column, "header_repair", None)
    if receipt is None:
        return raw["value"]
    proof = _HeaderProof.model_validate_json(_json(receipt))
    if (
        proof.table_id != profile.table_id
        or proof.source_sha256 != profile.source_sha256
        or proof.column_id != column.column_id
        or proof.source_header != raw
        or proof.decision.before != raw["value"]
        or inspect_text(raw["value"], proof.policy) != proof.decision
    ):
        raise ValueError("header repair differs from immutable source or policy")
    return proof.decision.candidate if proof.decision.status == "ACCEPTED" else raw["value"]


def _rewrite(db, row, status, addition):
    proof = json.loads(row[10])
    proof.pop("receipt_sha256", None)
    proof.update(addition)
    decision = TextDecision.model_validate_json(row[8])
    _write(
        db,
        *row[:5],
        json.loads(row[5]),
        decision.model_copy(update={"status": status}),
        TextIntegrityPolicy.model_validate_json(row[9]),
        proof,
    )


def _declarations(db):
    primary, relations = set(), []
    for row in _receipts(db):
        proof = json.loads(row[10])
        if proof["kind"] == "schema":
            primary.update(tuple(value) for value in proof["primary_keys"])
            primary.update(tuple(value) for value in proof.get("protected_columns", ()))
            relations.extend(tuple(value) for value in proof["foreign_keys"])
    return primary, relations


def _domains(db, protected):
    db.execute(
        "CREATE OR REPLACE TEMP TABLE text_key_domains(table_id VARCHAR,column_id VARCHAR,"
        "raw_json VARCHAR,effective_json VARCHAR)"
    )
    for table, column in sorted(protected):
        index = int(column[1:]) - 1
        for record, values in _rows(
            db,
            "SELECT record_id,values_json FROM staging.raw_rows "
            "WHERE table_id=? AND kind='data' ORDER BY ordinal",
            [table],
        ):
            value = json.loads(values)[index]
            if value["type"] == "NoneType":
                continue
            row = db.execute(
                "SELECT candidate_json,decision_json FROM meta.text_integrity WHERE locator=?",
                [f"cell:{record}:{column}"],
            ).fetchone()
            effective = value
            if row and row[0]:
                decision = TextDecision.model_validate_json(row[1])
                # Candidate legitimacy must be independent of relationship acceptance.
                policy = db.execute(
                    "SELECT policy_json FROM meta.text_integrity WHERE locator=?",
                    [f"cell:{record}:{column}"],
                ).fetchone()[0]
                if (
                    inspect_text(
                        decision.before, TextIntegrityPolicy.model_validate_json(policy)
                    ).status
                    == "ACCEPTED"
                ):
                    effective = json.loads(row[0])
            db.execute(
                "INSERT INTO text_key_domains VALUES (?,?,?,?)",
                [table, column, _json(value), _json(effective)],
            )


def _relation_valid(db, relation):
    child, child_col, target, target_col = relation
    for table, column in ((child, child_col), (target, target_col)):
        counts = db.execute(
            "SELECT count(*),count(DISTINCT raw_json),count(DISTINCT effective_json) "
            "FROM text_key_domains WHERE table_id=? AND column_id=?",
            [table, column],
        ).fetchone()
        if counts[1] != counts[2] or (
            table == target and column == target_col and counts[0] != counts[2]
        ):
            return False
    return not db.execute(
        "SELECT 1 FROM text_key_domains c WHERE c.table_id=? AND c.column_id=? "
        "AND NOT EXISTS(SELECT 1 FROM text_key_domains t WHERE t.table_id=? "
        "AND t.column_id=? AND t.effective_json=c.effective_json) LIMIT 1",
        [child, child_col, target, target_col],
    ).fetchone()


def _protected_columns(db, bindings=None):
    protected, relations = _declarations(db)
    for table, columns, headers in _rows(
        db, "SELECT table_id,columns_json,headers_json FROM meta.source_tables"
    ):
        for column, header in zip(json.loads(columns), json.loads(headers), strict=True):
            raw_name = str(header["value"])
            repaired = inspect_text(raw_name)
            name = (repaired.candidate if repaired.status == "ACCEPTED" else raw_name).casefold()
            if name.endswith("_id") or name in {"id", "imei"}:
                protected.add((table, column))
        for (keys,) in _rows(
            db,
            "SELECT DISTINCT record_key_json FROM staging.raw_rows "
            "WHERE table_id=? AND record_key_json IS NOT NULL",
            [table],
        ):
            key_names = json.loads(keys)
            protected.update(
                (table, column)
                for column, header in zip(json.loads(columns), json.loads(headers), strict=True)
                if header["value"] in key_names
            )
    if bindings is not None:
        protected.update(
            (binding.table_id, column)
            for binding in bindings.accepted
            if binding.kind == "role" and (binding.value.endswith("_id") or binding.value == "imei")
            for column in binding.columns
        )
    for child, child_col, target, target_col in relations:
        protected.update(((child, child_col), (target, target_col)))
    return protected, relations


def _component_witnesses(db, relations, node):
    nodes = {node}
    changed = True
    while changed:
        changed = False
        for relation in relations:
            endpoints = {(relation[0], relation[1]), (relation[2], relation[3])}
            if endpoints & nodes and not endpoints <= nodes:
                nodes.update(endpoints)
                changed = True
    component = tuple(sorted(set(r for r in relations if (r[0], r[1]) in nodes)))
    unsupported = set()
    for row in _receipts(db):
        proof = json.loads(row[10])
        if proof["kind"] == "schema":
            unsupported.update(tuple(item) for item in proof.get("unsupported_keys", ()))
    valid = (
        bool(component)
        and not nodes & unsupported
        and all(_relation_valid(db, relation) for relation in component)
    )
    return component if valid else ()


def finalize_text_state(db, bindings) -> None:
    protected, relations = _protected_columns(db, bindings)
    _domains(db, protected)
    witnesses = {}
    for row in _receipts(db):
        node = (row[1], row[3])
        if row[2] is None or node not in protected or json.loads(row[10])["kind"] == "metadata":
            continue
        decision = TextDecision.model_validate_json(row[8])
        policy = TextIntegrityPolicy.model_validate_json(row[9])
        replay = inspect_text(decision.before, policy)
        if node not in witnesses:
            witnesses[node] = _component_witnesses(db, relations, node)
        component = witnesses[node]
        accepted = bool(component) and replay.status == "ACCEPTED"
        status = "ACCEPTED" if accepted else "LOSSY" if replay.status == "LOSSY" else "UNRESOLVED"
        _rewrite(
            db,
            row,
            status,
            {"protected": True, "foreign_keys": [list(r) for r in component] if accepted else []},
        )
    db.execute("DROP TABLE text_key_domains")


def apply_text_to_batch(state: OperationState, batch: Sequence[RawRecord]) -> None:
    if not _exists(state.db):
        return
    from datamate.preparation.models import LineageEdge, TableProfile
    from datamate.preparation.operations import TransformEvent
    from datamate.preparation.storage import _decoded

    if not hasattr(state, "text_profiles"):
        exists = state.db.execute(
            "SELECT 1 FROM duckdb_tables() WHERE schema_name='meta' AND table_name='profiles'"
        ).fetchone()
        state.text_profiles = (
            tuple(
                TableProfile.model_validate_json(row[0])
                for row in _rows(state.db, "SELECT profile FROM meta.profiles ORDER BY table_id")
            )
            if exists
            else ()
        )

    for record in batch:
        for row in _rows(
            state.db,
            "SELECT column_id,candidate_json,status,proof_json,before_json "
            "FROM meta.text_integrity WHERE record_id=? ORDER BY column_id",
            [record.record_id],
        ):
            column, candidate, status, proof, before = row
            if json.loads(proof)["kind"] == "metadata":
                continue
            before = _decoded(json.loads(before))
            after = json.loads(candidate)["value"] if status == "ACCEPTED" else before
            if state.cells[record.record_id][column] != before:
                raise ValueError("text overlay no longer begins at raw evidence")
            step = "text:" + json.loads(proof)["receipt_sha256"]
            state.cells[record.record_id][column] = after
            state.origins.setdefault((record.record_id, column), []).append(step)
            state.events.append(
                TransformEvent(
                    record.record_id,
                    column,
                    step,
                    "APPLIED" if status == "ACCEPTED" else "UNRESOLVED",
                    before,
                    after,
                    "source-bound text integrity receipt",
                )
            )
            state.extra_lineage.append(
                LineageEdge("row:" + record.record_id, record.record_id, column, step)
            )


def text_summary(db: DuckDBPyConnection) -> dict[str, int]:
    counts = dict(
        db.execute(
            "SELECT status,count(*) FROM meta.text_integrity "
            "WHERE locator<>'@policy' AND locator NOT LIKE 'schema:%' GROUP BY status"
        ).fetchall()
    )
    return {
        "accepted": counts.get("ACCEPTED", 0),
        "unresolved": counts.get("UNRESOLVED", 0),
        "lossy": counts.get("LOSSY", 0),
    }


def audit_text_state(db: DuckDBPyConnection) -> dict[str, set[str]]:
    if not _exists(db):
        return {}
    policy_row = db.execute(
        "SELECT policy_json FROM meta.text_integrity WHERE locator='@policy'"
    ).fetchone()
    if policy_row is None:
        raise ValueError("missing text integrity policy")
    policy = TextIntegrityPolicy.model_validate_json(policy_row[0])
    db.execute(
        "CREATE OR REPLACE TEMP TABLE text_expected(locator VARCHAR PRIMARY KEY,"
        "table_id VARCHAR,record_id VARCHAR,column_id VARCHAR,source_sha256 VARCHAR,"
        "before_json VARCHAR,kind VARCHAR)"
    )
    for locator, table, record, column, digest, text, kind in _texts(db):
        if _inspect(text, policy, kind).status != "UNCHANGED":
            db.execute(
                "INSERT INTO text_expected VALUES (?,?,?,?,?,?,?)",
                [locator, table, record, column, digest, _json(_tag(text)), kind],
            )
    issues = {}
    protected, relations = _protected_columns(db)
    _domains(db, protected)
    try:
        for row in _receipts(db):
            proof = json.loads(row[10])
            digest = proof.pop("receipt_sha256", None)
            if digest != _hash([list(row[:10]), proof]):
                raise ValueError("text integrity receipt digest mismatch")
            decision = TextDecision.model_validate_json(row[8])
            if row[9] != policy.model_dump_json():
                raise ValueError("text integrity policy drift")
            if proof["kind"] in {"schema", "decoding"}:
                sources = db.execute(
                    "SELECT table_id FROM meta.source_tables WHERE source_sha256=?", [row[4]]
                ).fetchall()
                tables = {item[0] for item in sources}
                declared = {item[0] for item in proof.get("primary_keys", ())}
                declared.update(
                    item[index] for item in proof.get("foreign_keys", ()) for index in (0, 2)
                )
                if not tables or not declared <= tables:
                    raise ValueError("schema or decoder receipt source mismatch")
                if proof["kind"] == "decoding" and (
                    row[1] not in tables or proof["decoder"].get("source_sha256") != row[4]
                ):
                    raise ValueError("decoder receipt source mismatch")
                continue
            if proof["kind"] == "policy":
                if row[0] != "@policy" or decision.before != policy.digest:
                    raise ValueError("text policy receipt mismatch")
                continue
            original = db.execute(
                "SELECT table_id,record_id,column_id,source_sha256,before_json,kind "
                "FROM text_expected WHERE locator=?",
                [row[0]],
            ).fetchone()
            if original is None or tuple(row[1:5]) != original[:4] or row[5] != original[4]:
                raise ValueError("text receipt locator or raw evidence mismatch")
            value = json.loads(original[4])
            replay = _inspect(
                value if value["type"] == "UndecodedText" else value["value"], policy, original[5]
            )
            db.execute("DELETE FROM text_expected WHERE locator=?", [row[0]])
            if decision.model_copy(update={"status": replay.status}) != replay:
                raise ValueError("text candidate replay mismatch")
            candidate = None if replay.candidate is None else encoded(replay.candidate)
            if (None if row[6] is None else json.loads(row[6])) != candidate or row[
                7
            ] != decision.status:
                raise ValueError("text candidate or status mismatch")
            if row[7] == "ACCEPTED":
                if replay.status != "ACCEPTED":
                    raise ValueError("text acceptance lacks reversible evidence")
                if (row[1], row[3]) in protected or proof.get("protected"):
                    component = _component_witnesses(db, relations, (row[1], row[3]))
                    actual = tuple(tuple(item) for item in proof.get("foreign_keys", ()))
                    if not component or actual != component:
                        raise ValueError("text key acceptance lacks complete native relation proof")
            if row[1] and proof["kind"] == "header" and row[7] in {"UNRESOLVED", "LOSSY"}:
                issues.setdefault(row[1], set()).add(row[3])
        if db.execute("SELECT 1 FROM text_expected LIMIT 1").fetchone():
            raise ValueError("missing text integrity receipts")
    finally:
        db.execute("DROP TABLE text_key_domains")
        db.execute("DROP TABLE text_expected")
    return issues


def effective_values(db, record_id, values):
    """Read accepted cell interpretations without changing physical source identity."""
    if not _exists(db):
        return values
    result = list(values)
    for column, candidate, proof in db.execute(
        "SELECT column_id,candidate_json,proof_json FROM meta.text_integrity WHERE "
        "record_id=? AND status='ACCEPTED' AND candidate_json IS NOT NULL",
        [record_id],
    ).fetchall():
        if json.loads(proof)["kind"] == "cell":
            result[int(column[1:]) - 1] = json.loads(candidate)["value"]
    return tuple(result)
