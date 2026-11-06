"""Private, versioned evaluation of already-prepared artifacts; no learner callbacks."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
from duckdb import DuckDBPyConnection

from datamate.evaluation.normalization import (
    ARTIFACT_OBSERVATION_VERSION,
    AdjudicatedReference,
    ArtifactObservationTarget,
    CaseManifest,
    NormalizationCase,
    ReferenceProposal,
    ReferenceSuite,
    _expected_date,
    _source_cell,
    check_reference_proof,
)
from datamate.preparation.models import EvidenceRef

if TYPE_CHECKING:
    from datamate.evaluation.normalization_campaign import CandidateResultSet

_MIGRATION_CHECKER = "artifact-observation-migration-v1"
_DATE_PROOFS = frozenset(
    {
        "iso_calendar_date",
        "calendar_iso_date",
        "iso_date_native_cell",
        "day_first_date_format",
        "sheet_scoped_day_first_date",
        "native_complete_dmy_date",
    }
)
_INTEGER_PROOFS = frozenset({"typed_calendar_integer", "calendar_integer_field"})
_CALENDAR_FIELDS = {
    "date": "date",
    "weekday": "int",
    "lunar_day": "int",
    "lunar_month": "int",
    "lunar_year": "int",
    "lunar_leap": "bool",
    "holiday": "str",
}


def _render_proven_target(
    proposal: ReferenceProposal, reader: Callable[[EvidenceRef], object]
) -> tuple[ArtifactObservationTarget | None, object]:
    """Render only a successfully rechecked source proof into the artifact wire format."""
    stage, kind = proposal.assertion_stage, proposal.proof_kind
    if stage == "raw" and kind == "exact_observation":
        return ArtifactObservationTarget(
            stage=stage, scope="source_cell", fields=("value",)
        ), proposal.expected_value
    value_type = None
    value = None
    if kind in _DATE_PROOFS:
        value_type = "date"
        value = date.fromisoformat(_expected_date(proposal.expected_value)).isoformat()
    elif kind in _INTEGER_PROOFS:
        value_type = "int"
        expected = proposal.expected_value
        value = str(expected["value"] if isinstance(expected, dict) else expected)
    elif kind == "native_order_timestamp":
        value_type = "datetime"
        cell = _source_cell(reader(proposal.evidence[0]))
        value = datetime.fromisoformat(cell["value"]).isoformat()
    if value_type is not None:
        return ArtifactObservationTarget(
            stage=stage,
            scope="source_cell",
            fields=("value",),
            value_type=value_type,
        ), {"type": value_type, "value": value}
    if kind in {"source_calendar_event", "scoped_calendar_row"}:
        return ArtifactObservationTarget(
            stage=stage,
            scope="calendar_days",
            fields=("date", "holiday"),
        ), {
            "date": {"type": "date", "value": proposal.expected_value["date"]},
            "holiday": {"type": "str", "value": proposal.expected_value["holiday"]},
        }
    if kind == "trusted_key_relationship":
        return ArtifactObservationTarget(
            stage=stage,
            scope="relationship",
            fields=("product_id", "matched_record_id"),
        ), {
            key: {"type": "str", "value": proposal.expected_value[key]}
            for key in ("product_id", "matched_record_id")
        }
    return None, proposal.expected_value


def migrate_reference_suite(
    suite: ReferenceSuite,
    manifest: CaseManifest,
    proposals: Sequence[ReferenceProposal],
    readers: dict[str, Callable[[EvidenceRef], object]],
) -> ReferenceSuite:
    """Explicitly revise legacy labels after rechecking their original independent proofs.

    Inputs remain immutable. Candidate capability never decides whether proven truth is
    determinate; fixed relationship targets retain their denominator even without an extractor.
    """
    suite = ReferenceSuite.model_validate_json(suite.model_dump_json())
    manifest = CaseManifest.model_validate_json(manifest.model_dump_json())
    if suite.contract_version is not None:
        raise ValueError("migration requires a legacy reference contract")
    cases = {case.case_id: case for case in manifest.cases}
    if suite.manifest_digest != manifest.digest or set(cases) != {
        ref.case_id for ref in suite.references
    }:
        raise ValueError("reference suite does not match frozen manifest")
    originals = {}
    for proposal in proposals:
        proposal = ReferenceProposal.model_validate_json(proposal.model_dump_json())
        if proposal.case_id not in cases or proposal.case_id in originals:
            raise ValueError("original proposal is duplicate or outside manifest")
        originals[proposal.case_id] = proposal
    revised = []
    for ref in suite.references:
        case = cases[ref.case_id]
        proposal = originals.get(ref.case_id)
        updates = {
            "revision": ref.revision + 1,
            "revision_reason": "artifact-observation-v1 migration; original source proof rechecked",
            "checker_version": _MIGRATION_CHECKER,
            "evidence_status": "UNRESOLVED",
            "observation_target": None,
            "reason": "original source proposal unavailable; no artifact target established",
        }
        if proposal is not None:
            if (
                proposal.digest != ref.proposal_digest
                or proposal.assertion_stage != ref.assertion_stage
                or proposal.revision != ref.revision
                or proposal.expected_behavior != ref.expected_behavior
                or proposal.expected_value != ref.expected_value
                or proposal.evidence != ref.evidence
                or proposal.evidence[0].source != case.source
                or proposal.evidence[0].sha256 != case.source_sha256
                or proposal.evidence[0].locator != f"raw:{case.record_id}/{case.column_id}"
            ):
                raise ValueError("migration requires the exact original source proposal")
            if case.business_id not in readers:
                raise ValueError("missing original source-only reader")
            reader = readers[case.business_id]
            proof = check_reference_proof(proposal, reader)
            updates["checker_version"] = f"{proof.version}/{_MIGRATION_CHECKER}"
            updates["reason"] = proof.reason
            if proof.verified:
                target, value = _render_proven_target(proposal, reader)
                if target is not None or proposal.expected_behavior in {
                    "SOURCE_ABSENCE",
                    "PROCESSING_AMBIGUITY",
                }:
                    updates.update(
                        evidence_status="VERIFIED",
                        observation_target=target,
                        expected_value=value,
                        reason="source proof rechecked; artifact contract rendered",
                    )
                else:
                    updates["reason"] = (
                        "independent source proof rechecked; artifact target undefined"
                    )
        revised.append(
            AdjudicatedReference.model_validate_json(
                ref.model_copy(update=updates).model_dump_json()
            )
        )
    return ReferenceSuite(
        contract_version=ARTIFACT_OBSERVATION_VERSION,
        manifest_digest=suite.manifest_digest,
        references=tuple(revised),
    )


def _relations(db: DuckDBPyConnection) -> dict[tuple[str, str], set[str]]:
    rows = db.execute(
        "SELECT c.table_schema,c.table_name,c.column_name FROM information_schema.columns c "
        "JOIN information_schema.tables t ON c.table_schema=t.table_schema "
        "AND c.table_name=t.table_name AND c.table_catalog=t.table_catalog "
        "WHERE t.table_type='BASE TABLE' AND c.table_schema IN ('staging','meta','clean')"
    ).fetchall()
    result = {}
    for schema, table, column in rows:
        result.setdefault((schema, table), set()).add(column)
    return result


def _has(relations: dict, schema: str, table: str, *columns: str) -> bool:
    return set(columns) <= relations.get((schema, table), set())


def _wire(value: object) -> dict[str, object]:
    """Native serialization only; this does not infer or parse candidate values."""
    payload = value.isoformat() if type(value) in (date, datetime) else value
    if type(value) is int:
        payload = str(value)
    return {"type": type(value).__name__, "value": payload}


def _valid_raw_cell(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        return False
    kind = value["type"]
    if not isinstance(kind, str):
        return False
    payload_types = {
        "NoneType": type(None),
        "str": str,
        "bool": bool,
        "int": str,
        "float": str,
        "Decimal": str,
        "bytes": str,
        "date": str,
        "datetime": str,
        "time": str,
    }
    return type(value["value"]) is payload_types.get(kind)


def _valid_scalar(value: object, kind: str) -> bool:
    if not isinstance(value, dict) or set(value) != {"type", "value"} or value["type"] != kind:
        return False
    payload = value["value"]
    if not isinstance(payload, str):
        return False
    try:
        if kind == "str":
            return True
        if kind == "date":
            return date.fromisoformat(payload).isoformat() == payload
        if kind == "datetime":
            return datetime.fromisoformat(payload).isoformat() == payload
        if kind == "int":
            return re.fullmatch(r"-?(0|[1-9][0-9]*)", payload, re.ASCII) is not None
    except ValueError:
        return False
    return False


def _calendar_value(db, relations, case, target, headers, columns):
    # Calendar publication is the qualification boundary in v2; candidate/meta rows
    # are never substituted for this fixed clean relation.
    if case.source != "calendar.csv" or not _has(relations, "clean", "calendar_days", "record_id"):
        return None
    if not isinstance(headers, list):
        return None
    names = [
        header.get("value")
        if isinstance(header, dict)
        and header.get("type") == "str"
        and isinstance(header.get("value"), str)
        else None
        for header in headers
    ]
    if len(names) != len(columns) or len(set(name for name in names if name is not None)) != len(
        [name for name in names if name is not None]
    ):
        return None
    source_header = names[columns.index(case.column_id)]
    if target.scope == "calendar_days":
        if source_header != "holiday" or not {"date", "holiday"} <= set(names):
            return None
        fields = ("date", "holiday")
    else:
        if _CALENDAR_FIELDS.get(source_header) != target.value_type:
            return None
        fields = (source_header,)
    if not _has(relations, "clean", "calendar_days", *fields):
        return None
    # Every identifier comes from the fixed field allowlist above.
    projection = ",".join(f'"{field}"' for field in fields)
    rows = db.execute(
        f"SELECT {projection} FROM clean.calendar_days WHERE record_id=? LIMIT 2", [case.record_id]
    ).fetchall()
    if len(rows) != 1:
        return None
    values = {field: _wire(value) for field, value in zip(fields, rows[0], strict=True)}
    if target.scope == "calendar_days":
        if not _valid_scalar(values["date"], "date") or values["holiday"]["type"] != "str":
            return None
        return values
    value = values[fields[0]]
    return value if _valid_scalar(value, target.value_type) else None


def _observe(db, relations, case, target):
    required = _has(
        relations, "staging", "raw_rows", "record_id", "table_id", "kind", "values_json"
    ) and _has(
        relations,
        "meta",
        "source_tables",
        "table_id",
        "source",
        "source_sha256",
        "columns_json",
        "headers_json",
    )
    if not required:
        return "ABSTAINED", None
    rows = db.execute(
        "SELECT r.kind,r.values_json,t.columns_json,t.headers_json FROM staging.raw_rows r "
        "JOIN meta.source_tables t ON t.table_id=r.table_id WHERE r.record_id=? AND r.table_id=? "
        "AND t.source=? AND t.source_sha256=? LIMIT 2",
        [case.record_id, case.table_id, case.source, case.source_sha256],
    ).fetchall()
    if len(rows) != 1:
        return "ABSTAINED", None
    row_kind, values_json, columns_json, headers_json = rows[0]
    values, columns, headers = (
        json.loads(values_json),
        json.loads(columns_json),
        json.loads(headers_json),
    )
    if (
        not isinstance(columns, list)
        or columns.count(case.column_id) != 1
        or not isinstance(values, list)
        or len(values) != len(columns)
    ):
        return "ABSTAINED", None
    if target is not None and target.stage == "raw":
        value = values[columns.index(case.column_id)]
        return ("COMMITTED", value) if _valid_raw_cell(value) else ("ABSTAINED", None)
    dispositions = []
    if _has(relations, "meta", "dispositions", "record_id", "kind"):
        dispositions = db.execute(
            "SELECT kind FROM meta.dispositions WHERE record_id=? LIMIT 2", [case.record_id]
        ).fetchall()
    if len(dispositions) > 1:
        return "ABSTAINED", None
    disposition = dispositions[0][0] if dispositions else None
    if target is not None and target.scope == "disposition":
        return (
            ("COMMITTED", {"kind": disposition}) if disposition is not None else ("ABSTAINED", None)
        )
    if disposition == "quarantined":
        return "QUARANTINED", None
    if target is None or target.scope == "relationship" or row_kind != "data":
        return "ABSTAINED", None
    if disposition is not None and disposition != "retained":
        return "ABSTAINED", None
    if _has(relations, "meta", "transform_events", "record_id", "column_id", "status"):
        unresolved = db.execute(
            "SELECT 1 FROM meta.transform_events WHERE record_id=? AND column_id=? "
            "AND status NOT IN ('APPLIED','ACCEPTED') LIMIT 1",
            [case.record_id, case.column_id],
        ).fetchone()
        if unresolved:
            return "ABSTAINED", None
    if target.scope == "calendar_days" or case.source == "calendar.csv":
        calendar = _calendar_value(db, relations, case, target, headers, columns)
        return ("COMMITTED", calendar) if calendar is not None else ("ABSTAINED", None)
    if disposition != "retained" or not (
        _has(relations, "meta", "facts", "fact_id", "table_role", "values_json")
        and _has(relations, "meta", "lineage", "fact_id", "record_id", "column_id")
    ):
        return "ABSTAINED", None
    facts = db.execute(
        "SELECT DISTINCT f.fact_id,f.values_json FROM meta.facts f "
        "JOIN meta.lineage l ON l.fact_id=f.fact_id "
        "WHERE l.record_id=? AND l.column_id=? AND f.table_role='physical_row' LIMIT 2",
        [case.record_id, case.column_id],
    ).fetchall()
    if len(facts) != 1 or facts[0][0] != "row:" + case.record_id:
        return "ABSTAINED", None
    payload = json.loads(facts[0][1])
    if not isinstance(payload, dict):
        return "ABSTAINED", None
    if target.scope == "attribute":
        if payload.get(case.column_id) != values[columns.index(case.column_id)]:
            return "ABSTAINED", None
        value = payload.get("size")
    else:
        value = payload.get(case.column_id)
    if target.value_type == "int" and isinstance(value, dict) and value.get("type") == "Decimal":
        try:
            numeric = Decimal(value["value"])
            value = (
                {"type": "int", "value": str(int(numeric))}
                if numeric.is_finite()
                and numeric == numeric.to_integral_value()
                and numeric.adjusted() < 38
                else None
            )
        except (InvalidOperation, ValueError, TypeError):
            value = None
    return ("COMMITTED", value) if _valid_scalar(value, target.value_type) else ("ABSTAINED", None)


def evaluate_prepared_cases(
    database: Path, cases: Sequence[NormalizationCase], suite: ReferenceSuite
) -> CandidateResultSet:
    """Read fixed private targets after preparation; legacy contracts fail before DB access."""
    suite = ReferenceSuite.model_validate_json(suite.model_dump_json())
    if suite.contract_version != ARTIFACT_OBSERVATION_VERSION:
        raise ValueError("prepared evaluation requires the artifact-observation-v1 contract")
    cases = tuple(NormalizationCase.model_validate_json(case.model_dump_json()) for case in cases)
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("prepared evaluation requires distinct cases")
    references = {ref.case_id: ref for ref in suite.references}
    for case in cases:
        ref = references.get(case.case_id)
        if ref is None:
            raise ValueError("case is outside reference suite")
        if ref.observation_target is not None and (
            not ref.evidence
            or ref.evidence[0].source != case.source
            or ref.evidence[0].sha256 != case.source_sha256
            or ref.evidence[0].locator != f"raw:{case.record_id}/{case.column_id}"
            or not case.record_id.startswith(case.table_id + ":")
        ):
            raise ValueError("artifact target differs from frozen case coordinates")
    from datamate.evaluation.normalization_campaign import CandidateCaseResult, CandidateResultSet

    results = []
    with duckdb.connect(
        str(database), read_only=True, config={"enable_external_access": "false"}
    ) as db:
        relations = _relations(db)
        for case in cases:
            status, value = _observe(
                db, relations, case, references[case.case_id].observation_target
            )
            results.append(
                CandidateCaseResult(
                    case_id=case.case_id, business_id=case.business_id, status=status, value=value
                )
            )
    return CandidateResultSet(case_results=tuple(results))
