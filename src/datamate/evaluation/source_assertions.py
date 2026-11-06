"""Evaluator-owned, source-checked revisions with fixed non-executable targets."""

import re
from collections.abc import Callable, Sequence
from datetime import date
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256
from datamate.evaluation.normalization import (
    _CHECKER_SEAL,
    ARTIFACT_OBSERVATION_VERSION,
    ArtifactObservationTarget,
    CaseManifest,
    ProofCheckResult,
    ReferenceProposal,
    ReferenceSuite,
    _source_cell,
    adjudicate_reference,
)
from datamate.preparation.models import EvidenceRef


class SourceAssertion(ContractModel):
    case_id: Sha256
    kind: Literal[
        "header", "quantity", "phone", "ingredient", "expense", "integer", "monthly_date", "size"
    ]
    expected_value: dict[str, JsonValue]
    evidence: tuple[EvidenceRef, ...] = Field(min_length=2, max_length=50)
    reason: NonblankString
    anchor_index: int | None = Field(default=None, ge=1)
    quantity_index: int | None = Field(default=None, ge=1)
    links: tuple[tuple[int, int], ...] = ()

    @model_validator(mode="after")
    def fixed_value(self) -> Self:
        from datamate.evaluation.prepared_observations import _valid_scalar

        kind = (
            "date"
            if self.kind == "monthly_date"
            else "int"
            if self.kind in {"quantity", "integer"}
            else "str"
        )
        if self.kind == "header":
            valid = self.expected_value == {"kind": "structural"}
        else:
            valid = _valid_scalar(self.expected_value, kind)
        if not valid:
            raise ValueError("assertion expected value has an invalid fixed type")
        if (self.anchor_index is None) != (self.quantity_index is None):
            raise ValueError("native amount needs both anchor and quantity")
        return self


def _record(ref):
    return (
        (ref.source, ref.locator[4:].split("/", 1)[0]) if ref.locator.startswith("raw:") else None
    )


def _native_anchor(item, observed, amount, reader):
    if item.anchor_index is None or item.quantity_index is None or not item.links:
        return False
    cells = [_source_cell(value) for value in observed]
    anchor, quantity = cells[item.anchor_index], cells[item.quantity_index]
    if not anchor or not quantity or anchor["type"] != "int" or quantity["type"] != "int":
        return False
    if str(observed[item.anchor_index].get("header", "")).casefold() not in {
        "giá bán",
        "giá bán theo đơn vị quy đổi",
        "unit_price",
    } or str(observed[item.quantity_index].get("header", "")).casefold() not in {
        "số lượng",
        "slg",
        "quantity",
    }:
        return False
    if int(anchor["value"]) * int(quantity["value"]) != amount:
        return False
    anchor_unit = observed[item.anchor_index].get("unit")
    quantity_unit = observed[item.quantity_index].get("unit")
    base_quantity = observed[item.quantity_index].get("base_quantity")
    same_unit = (
        isinstance(anchor_unit, str)
        and bool(anchor_unit)
        and (
            anchor_unit == quantity_unit
            or (
                base_quantity == quantity["value"]
                and str(observed[item.anchor_index].get("header", "")).casefold() == "giá bán"
            )
        )
    )
    if not same_unit:
        return False
    nodes = {_record(item.evidence[0])}
    edges = []
    for left, right in item.links:
        a, b = cells[left], cells[right]
        if not a or not b or a["type"] != "str" or b["type"] != "str" or not a["value"] or a != b:
            return False
        if not all(
            re.search(r"id|mã|ma |code|sku", str(observed[i].get("header", "")), re.I)
            for i in (left, right)
        ):
            return False
        edges.append({_record(item.evidence[left]), _record(item.evidence[right])})
    for _ in edges:
        for edge in edges:
            if edge & nodes:
                nodes.update(edge)
    anchor_record = _record(item.evidence[item.anchor_index])
    anchor_keys = {
        index
        for edge in item.links
        for index in edge
        if _record(item.evidence[index]) == anchor_record
    }
    if (
        not anchor_keys
        or not hasattr(reader, "key_count")
        or not hasattr(reader, "key_column_declared")
    ):
        return False
    if any(
        not reader.key_column_declared(item.evidence[index])
        or reader.key_count(item.evidence[index], cells[index]["value"]) != 1
        for index in anchor_keys
    ):
        return False
    return anchor_record in nodes and _record(item.evidence[item.quantity_index]) in nodes


def _derive(item, observed, reader):
    raw = _source_cell(observed[0])
    if raw is None:
        return None
    text, kind = raw["value"], item.kind
    quotes = [
        v["quote"] for v in observed if isinstance(v, dict) and isinstance(v.get("quote"), str)
    ]
    authority = "\n".join(quotes).casefold()
    header = str(observed[0].get("header", "")).casefold()
    if not authority:
        return None
    if kind == "header":
        peers = [
            v
            for ref, v in zip(item.evidence, observed, strict=True)
            if isinstance(v, dict)
            and v.get("record_kind") == "header"
            and _record(ref) == _record(item.evidence[0])
        ]
        declared = any(
            c
            and c["type"] == "str"
            and any(
                f"**{c['value']}**".casefold() in quote.casefold()
                or re.search(
                    r"(?:column header|header|cột)\s+" + re.escape(c["value"]), quote, re.I
                )
                for quote in quotes
            )
            for v in peers
            if (c := _source_cell(v))
        )
        # A source-declared file plus a multi-column header vector and a distinct
        # following data witness supports reviewed structure, not canonical roles.
        filename = item.evidence[0].source.rsplit("/", 1)[-1].casefold()
        labels = {c["value"] for peer in peers if (c := _source_cell(peer)) and c["type"] == "str"}
        following = any(
            isinstance(v, dict)
            and v.get("record_kind") == "data"
            and v.get("source_row", 0) > observed[0].get("source_row", 0)
            and _record(ref)[0] == _record(item.evidence[0])[0]
            and _record(ref)[1].rsplit(":", 1)[0] == _record(item.evidence[0])[1].rsplit(":", 1)[0]
            and (cell := _source_cell(v))
            and cell["value"] is not None
            and cell["value"] not in labels
            for ref, v in zip(item.evidence, observed, strict=True)
            if _record(ref)
        )
        reviewed_vector = filename in authority and len(labels) >= 3 and following
        if (
            observed[0].get("record_kind") == "header"
            and observed[0].get("source_row") == observed[0].get("header_row")
            and (declared or reviewed_vector)
        ):
            return {"kind": "structural"}
        return None
    value_type, value = raw["type"], text
    if kind == "quantity":
        if (
            value_type != "int"
            or header not in {"quantity", "conversion_factor", "tồn cuối kỳ", "quy đổi"}
            or header not in authority
        ):
            return None
    elif kind == "phone":
        if (
            value_type != "str"
            or header not in {"phone", "điện thoại", "số điện thoại"}
            or not re.fullmatch(r"0[0-9]{9}", text)
            or header not in authority
        ):
            return None
    elif kind == "ingredient":
        if (
            value_type != "str"
            or header != "hoạt chất"
            or "hoạt chất" not in authority
            or " + " not in text
        ):
            return None
    elif kind == "expense":
        if (
            value_type != "str"
            or header not in {"mục", "khoản chi", "expense_category"}
            or "chi phí" not in authority
        ):
            return None
    elif kind == "size":
        if value_type != "str" or not all(
            token in authority for token in ("(m)", "(l)", "kết thúc")
        ):
            return None
        match = re.search(r"\(([ML])\)$", text, re.I)
        return {"type": "str", "value": match.group(1).upper()} if match else None
    elif kind == "monthly_date":
        if value_type != "str" or "mỗi tháng một sheet" not in authority:
            return None
        # The declaration must name this file, not an unrelated monthly workbook.
        filename = item.evidence[0].source.rsplit("/", 1)[-1].casefold()
        if not any(
            filename in line.casefold() and "mỗi tháng một sheet" in line.casefold()
            for quote in quotes
            for line in quote.splitlines()
        ):
            return None
        sheet = re.fullmatch(r"T([0-9]{2})-([0-9]{4})", str(observed[0].get("table_name", "")))
        parts = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})", text)
        if not sheet or not parts:
            return None
        month, year = map(int, sheet.groups())
        a, b, y = map(int, parts.groups())
        candidates = set()
        for day, m in ((a, b), (b, a)):
            try:
                candidate = date(y, m, day)
            except ValueError:
                continue
            if candidate.month == month and candidate.year == year:
                candidates.add(candidate.isoformat())
        return {"type": "date", "value": candidates.pop()} if len(candidates) == 1 else None
    elif kind == "integer":
        if (
            value_type != "str"
            or header not in {"amount", "tổng tiền hàng", "tổng tiền", "còn nợ", "số tiền thu"}
            or "vnd" not in authority
        ):
            return None
        value_type = "int"
        if re.fullmatch(r"[0-9]+[kK]", text):
            value = str(int(text[:-1]) * 1000)
        elif re.fullmatch(r"[0-9]{1,3}(,[0-9]{3}){2,}", text):
            value = str(int(text.replace(",", "")))
        elif re.fullmatch(r"[0-9]+|[0-9]{1,3},[0-9]{3}", text):
            value = str(int(text.replace(",", "")))
            if not _native_anchor(item, observed, int(value), reader):
                return None
        else:
            return None
    return {"type": value_type, "value": value}


def revise_development_suite(
    suite: ReferenceSuite,
    manifest: CaseManifest,
    assertions: Sequence[SourceAssertion],
    readers: dict[str, Callable[[EvidenceRef], object]],
) -> ReferenceSuite:
    """Recheck source assertions before revising only selected development references."""
    if (
        suite.contract_version != ARTIFACT_OBSERVATION_VERSION
        or suite.manifest_digest != manifest.digest
    ):
        raise ValueError("versioned reference suite and manifest must agree")
    cases = {case.case_id: case for case in manifest.cases}
    references = {ref.case_id: ref for ref in suite.references}
    if set(cases) != set(references):
        raise ValueError("reference inventory differs from frozen cases")
    seen = set()
    for untrusted in assertions:
        item = SourceAssertion.model_validate_json(untrusted.model_dump_json())
        case = cases.get(item.case_id)
        if case is None or case.partition != "development" or item.case_id in seen:
            raise ValueError("revisions require distinct development cases")
        seen.add(item.case_id)
        primary = item.evidence[0]
        if (primary.source, primary.sha256, primary.locator) != (
            case.source,
            case.source_sha256,
            f"raw:{case.record_id}/{case.column_id}",
        ):
            raise ValueError("assertion differs from frozen source coordinates")
        allowed_docs = {"rulebook.md", f"data/{case.business_id}/README.md"}
        if any(
            ref.source not in allowed_docs
            if not ref.locator.startswith("raw:")
            else not ref.source.startswith(f"data/{case.business_id}/")
            for ref in item.evidence
        ):
            raise ValueError("assertion evidence escapes active business")
        if (
            any(index >= len(item.evidence) for pair in item.links for index in pair)
            or any(
                index is not None and index >= len(item.evidence)
                for index in (item.anchor_index, item.quantity_index)
            )
            or any(index < 0 for pair in item.links for index in pair)
        ):
            raise ValueError("assertion witness index is outside evidence")
        stage = (
            "disposition"
            if item.kind == "header"
            else "canonical_fact"
            if item.kind == "size"
            else "parsed_cell"
        )
        target = ArtifactObservationTarget(
            stage=stage,
            scope="disposition"
            if item.kind == "header"
            else "attribute"
            if item.kind == "size"
            else "source_cell",
            fields=("kind",)
            if item.kind == "header"
            else ("size",)
            if item.kind == "size"
            else ("value",),
            value_type=None
            if item.kind == "header"
            else "str"
            if item.kind == "size"
            else item.expected_value.get("type"),
        )
        proposal = ReferenceProposal(
            case_id=item.case_id,
            assertion_stage=stage,
            revision=references[item.case_id].revision + 1,
            revision_reason=item.reason,
            expected_behavior="PRESERVE"
            if item.kind in {"quantity", "phone", "ingredient", "expense"}
            else "TRANSFORM",
            expected_value=item.expected_value,
            proof_kind="source-assertion:" + item.kind,
            evidence=item.evidence,
        )
        try:
            observed = [readers[case.business_id](ref) for ref in item.evidence]
            derived = _derive(item, observed, readers[case.business_id])
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            derived = None
        proof = ProofCheckResult(
            proposal.digest,
            derived is not None and derived == item.expected_value,
            "source assertion independently checked"
            if derived is not None and derived == item.expected_value
            else "source assertion lacks distinguishing evidence",
            version="fixed-source-assertions-v1",
            _seal=_CHECKER_SEAL,
        )
        ref = adjudicate_reference(proposal, proof)
        # A proven target remains fixed even when a candidate lacks the capability.
        ref = ref.model_copy(update={"observation_target": target})
        references[item.case_id] = ref
    return ReferenceSuite(
        contract_version=ARTIFACT_OBSERVATION_VERSION,
        manifest_digest=suite.manifest_digest,
        references=tuple(references[ref.case_id] for ref in suite.references),
    )
