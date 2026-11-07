"""Source-only schema gaps and evidence-checked mapping proposals."""

import csv
import json
from decimal import Decimal

import duckdb
import pytest

from datamate.preparation.catalog import compile_clauses, validate_bindings
from datamate.preparation.discovery import sha256_file
from datamate.preparation.loaders import load_tables
from datamate.preparation.models import Binding, EvidenceRef, SourceFile
from datamate.preparation.profiling import profile_table
from datamate.preparation.storage import create_storage, stage_table


def mapping_world(
    tmp_path, header="Montant", description="thành tiền sau giảm", section="Cột trong `sales.csv`"
):
    business = tmp_path / "data/shop"
    business.mkdir(parents=True)
    rules = tmp_path / "rulebook.md"
    rules.write_text("# Rules\nKeep unknown meanings unresolved.\n", encoding="utf-8")
    readme = business / "README.md"
    readme.write_text(f"# Sales\n## {section}\n- **{header}**: {description}\n", encoding="utf-8")
    path = business / "sales.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([[header], ["1.600đ"], ["69k"]])
    clauses = compile_clauses(
        tuple(
            SourceFile(p, n, sha256_file(p))
            for p, n in [(rules, "rulebook.md"), (readme, "data/shop/README.md")]
        ),
        split_id="train",
    )
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db, SourceFile(path, "data/shop/sales.csv", sha256_file(path)), next(load_tables(path))
        )
        profile = profile_table(db, table.table_id)
    return clauses, profile


def candidate(clauses, profile, role="line_net_amount"):
    clause = next(c for c in clauses if "**" in c.quote)
    return Binding(
        kind="role",
        table_id=profile.table_id,
        columns=("c0001",),
        value=role,
        profile_digest=profile.digest,
        evidence=(
            EvidenceRef(
                source=profile.source,
                sha256=profile.source_sha256,
                locator=f"header:{profile.table_id}/c0001",
            ),
            EvidenceRef(
                source=clause.source_document.source,
                sha256=clause.source_document.sha256,
                locator=f"clause:{clause.clause_id}",
                line_start=clause.line_start,
                line_end=clause.line_end,
                quote_sha256=clause.quote_sha256,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("description", "role"),
    [
        ("thành tiền sau giảm", "line_net_amount"),
        ("mã đơn", "order_id"),
        ("mã hàng", "product_id"),
        ("số lượng", "quantity"),
        ("đơn vị tính", "unit"),
        ("ngày bán", "ordered_at"),
        ("tên hàng", "product_name"),
    ],
)
def test_colon_declaration_proves_a_role_for_an_unfamiliar_header(tmp_path, description, role):
    clauses, profile = mapping_world(tmp_path, description=description)
    proposal = candidate(clauses, profile, role)
    assert proposal in validate_bindings(clauses, [profile], [proposal]).accepted


@pytest.mark.parametrize(
    ("description", "section", "role"),
    [
        ("tổng tiền cả đơn", "Cột trong `sales.csv`", "line_net_amount"),
        ("thành tiền sau giảm", "Cột trong `other.csv`", "line_net_amount"),
        ("có lẽ là tiền", "Cột trong `sales.csv`", "line_net_amount"),
    ],
)
def test_uncertain_or_different_file_declarations_do_not_authorize_role(
    tmp_path, description, section, role
):
    clauses, profile = mapping_world(tmp_path, description=description, section=section)
    assert not validate_bindings(clauses, [profile], [candidate(clauses, profile, role)]).accepted


def test_rejected_supplied_mapping_does_not_hide_native_role(tmp_path):
    from datamate.preparation.v2 import _source_roles

    clauses, profile = mapping_world(tmp_path, header="order_id", description="mã đơn")
    invalid = candidate(clauses, profile, "unit_cost")
    result = _source_roles([profile], clauses, (invalid,))
    assert any(b.kind == "role" and b.value == "order_id" for b in result.accepted)


def test_unknown_column_is_a_source_issue_not_an_unmarked_clean_column(tmp_path):
    from datamate.preparation.mapping import discover_mapping_issues
    from datamate.preparation.v2 import _source_roles

    clauses, profile = mapping_world(tmp_path)
    bindings = _source_roles([profile], clauses, ())
    issues = discover_mapping_issues([profile], bindings)
    assert len(issues) == 1
    assert issues[0].kind == "unmapped_role"
    assert issues[0].scope == profile.table_id
    assert "c0001" in issues[0].detail
    assert issues == discover_mapping_issues([profile], bindings)
    accepted = _source_roles([profile], clauses, (candidate(clauses, profile),))
    assert discover_mapping_issues([profile], accepted) == ()


def test_parent_file_section_still_applies_under_nested_heading(tmp_path):
    clauses, profile = mapping_world(tmp_path, section="Cột trong `other.csv`\n### Columns")
    assert not validate_bindings(clauses, [profile], [candidate(clauses, profile)]).accepted


def test_generic_name_does_not_prove_product_name(tmp_path):
    clauses, profile = mapping_world(tmp_path, description="tên")
    assert not validate_bindings(
        clauses, [profile], [candidate(clauses, profile, "product_name")]
    ).accepted


def test_supplied_grain_can_depend_on_native_roles_without_duplicate_ids(tmp_path):
    from test_semantic_bindings import binding_world

    from datamate.preparation.v2 import _source_roles

    clauses, profile = binding_world(tmp_path)
    native = _source_roles([profile], clauses, ())
    grain = next(b for b in native.accepted if b.kind == "grain" and b.value == "order_line")
    result = _source_roles([profile], clauses, (grain,))
    assert grain in result.accepted
    assert len(result.entries) == len({e.binding.binding_id for e in result.entries})


def test_invalid_competing_role_does_not_duplicate_native_id(tmp_path):
    from datamate.preparation.v2 import _source_roles

    clauses, profile = mapping_world(tmp_path, header="order_id", description="mã đơn")
    native = next(b for b in _source_roles([profile], clauses, ()).accepted if b.kind == "role")
    result = _source_roles([profile], clauses, (native, candidate(clauses, profile, "unit_cost")))
    assert native in result.accepted
    assert len(result.entries) == len({e.binding.binding_id for e in result.entries})


def test_mapping_proposal_is_typed_and_does_not_accept_model_authored_proof(tmp_path):
    from pydantic import ValidationError

    from datamate.preparation.mapping import MappingProposal, mapping_bindings

    clauses, profile = mapping_world(tmp_path)
    clause = next(c for c in clauses if "**" in c.quote)
    proposal = MappingProposal.model_validate_json(
        json.dumps(
            {
                "mappings": [
                    {
                        "column_id": "c0001",
                        "role": "line_net_amount",
                        "clause_ids": [clause.clause_id],
                    }
                ]
            }
        )
    )
    bound = mapping_bindings(proposal, profile, clauses)
    assert bound[0] in validate_bindings(clauses, [profile], bound).accepted
    with pytest.raises(ValidationError):
        MappingProposal.model_validate({"mappings": [], "accepted": True})
    with pytest.raises(ValueError, match="clause"):
        mapping_bindings(
            proposal.model_copy(
                update={
                    "mappings": (
                        proposal.mappings[0].model_copy(update={"clause_ids": ("f" * 64,)}),
                    )
                }
            ),
            profile,
            clauses,
        )


def mapping_split(tmp_path):
    clauses, profile = mapping_world(tmp_path)
    (tmp_path / "calendar.csv").write_text(
        "date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n"
        "2025-01-01,3,2,12,2024,false,\n",
        encoding="utf8",
    )
    return tmp_path


def mapping_transport(requests, *, role="line_net_amount", abstain=False):
    import json

    import httpx
    from test_adaptive_normalization import typed_response

    def respond(request):
        body = json.loads(request.content)
        text = "".join(m["content"] for m in body["messages"] if isinstance(m.get("content"), str))
        context = json.loads(text.split("SOURCE_CONTEXT\n", 1)[1])
        clause = next(c for c in context["clauses"] if "**" in c["quote"])
        answer = (
            {"mappings": []}
            if abstain
            else {
                "mappings": [
                    {"column_id": "c0001", "role": role, "clause_ids": [clause["clause_id"]]}
                ]
            }
        )
        requests.append(body)
        return typed_response(body, answer)

    return httpx.MockTransport(respond)


def test_automatic_issue_to_model_mapping_drives_existing_parser(tmp_path, monkeypatch):
    import asyncio

    from test_adaptive_normalization import adaptive_profile

    from datamate.inference.budget import RequestBudget
    from datamate.preparation.orchestration import prepare_with_mapping_recovery

    monkeypatch.setenv("LLM_API_KEY", "local-fixture-key")
    root = mapping_split(tmp_path / "source")
    requests = []
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="mapping", run_limit=1)
    result = asyncio.run(
        prepare_with_mapping_recovery(
            root,
            "shop",
            tmp_path / "cache",
            split_id="train",
            run_dir=tmp_path / "run",
            allow_model_calls=True,
            profile=adaptive_profile(),
            budget=budget,
            transport=mapping_transport(requests),
        )
    )
    assert result.status == "MAPPING_ACCEPTED"
    assert result.prepared.fingerprint != result.baseline.fingerprint
    assert result.baseline.catalog["issues"] and not result.prepared.catalog["issues"]
    with duckdb.connect(str(result.prepared.database), read_only=True) as db:
        events = db.execute(
            "SELECT status,after_json FROM meta.transform_events ORDER BY record_id"
        ).fetchall()
        assert len(events) == 2 and all(e[0] == "APPLIED" for e in events)
        assert [Decimal(json.loads(e[1])["value"]) for e in events] == [
            Decimal(1600),
            Decimal(69000),
        ]
        assert db.execute("SELECT count(*) FROM meta.normalization_issues").fetchone()[0] == 0
    assert len(requests) == budget.snapshot()["run_attempts"] == 1
    assert (tmp_path / "run/report.json").is_file()


@pytest.mark.parametrize("case", ["unsupported", "abstain", "offline"])
def test_mapping_refusal_retains_baseline_and_bounded_requests(tmp_path, monkeypatch, case):
    import asyncio

    from test_adaptive_normalization import adaptive_profile

    from datamate.inference.budget import RequestBudget
    from datamate.preparation.orchestration import prepare_with_mapping_recovery

    monkeypatch.setenv("LLM_API_KEY", "local-fixture-key")
    root = mapping_split(tmp_path / "source")
    requests = []
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="mapping", run_limit=1)
    result = asyncio.run(
        prepare_with_mapping_recovery(
            root,
            "shop",
            tmp_path / "cache",
            split_id="train",
            run_dir=tmp_path / "run",
            allow_model_calls=case != "offline",
            profile=adaptive_profile(),
            budget=budget,
            transport=mapping_transport(requests, role="order_id", abstain=case == "abstain"),
        )
    )
    assert result.status == "PARTIAL" and result.prepared == result.baseline
    assert len(requests) == budget.snapshot()["run_attempts"] == (0 if case == "offline" else 1)
    assert len(list((tmp_path / "cache").glob("*/manifest.json"))) == 1


def test_inline_readme_field_declaration_keeps_its_exact_boundary(tmp_path):
    clauses, profile = mapping_world(tmp_path)
    path = tmp_path / "data/shop/README.md"
    path.write_text(
        "- **Công nợ**: mô tả chung; **Montant** là số tiền trả lần đó.\n", encoding="utf8"
    )
    rules = tmp_path / "rulebook.md"
    clauses = compile_clauses(
        (
            SourceFile(rules, "rulebook.md", sha256_file(rules)),
            SourceFile(path, "data/shop/README.md", sha256_file(path)),
        ),
        split_id="train",
    )
    proposal = candidate(clauses, profile, "payment_amount")
    assert proposal in validate_bindings(clauses, [profile], [proposal]).accepted


def test_mapping_cli_requires_explicit_profile_accounting_and_run_location(monkeypatch, capsys):
    from datamate.preparation.__main__ import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare",
            "--split-root",
            "unused",
            "--split-id",
            "train",
            "--business-id",
            "shop",
            "--cache-root",
            "cache",
            "--adaptive-mapping",
        ],
    )
    with pytest.raises(SystemExit):
        main()
    assert "--profile, --ledger, --run-id and --run-dir" in capsys.readouterr().err


def test_oversized_mapping_context_sends_zero_requests(tmp_path, monkeypatch):
    import asyncio

    from test_adaptive_normalization import adaptive_profile

    from datamate.inference.budget import RequestBudget
    from datamate.preparation.adaptive import propose_role_mapping
    from datamate.preparation.v2 import _source_roles

    clauses, profile = mapping_world(tmp_path, description="x" * 26000)
    bindings = _source_roles([profile], clauses, ())
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="mapping", run_limit=1)
    with pytest.raises(ValueError, match="byte budget"):
        asyncio.run(propose_role_mapping(adaptive_profile(), budget, profile, clauses, bindings))
    assert budget.snapshot()["run_attempts"] == 0


def test_executable_model_fields_are_rejected_without_format_retry(tmp_path, monkeypatch):
    import asyncio

    import httpx
    from test_adaptive_normalization import adaptive_profile, typed_response

    from datamate.inference.budget import RequestBudget
    from datamate.preparation.adaptive import PlanFormatError, propose_role_mapping
    from datamate.preparation.v2 import _source_roles

    monkeypatch.setenv("LLM_API_KEY", "fixture-only")
    clauses, profile = mapping_world(tmp_path)
    bindings = _source_roles([profile], clauses, ())
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="mapping", run_limit=1)
    requests = []

    def bad(request):
        body = json.loads(request.content)
        requests.append(body)
        return typed_response(body, {"mappings": [], "python": "raise RuntimeError()"})

    with pytest.raises(PlanFormatError):
        asyncio.run(
            propose_role_mapping(
                adaptive_profile(),
                budget,
                profile,
                clauses,
                bindings,
                transport=httpx.MockTransport(bad),
            )
        )
    assert len(requests) == budget.snapshot()["run_attempts"] == 1


def test_negated_field_statement_never_proves_mapping(tmp_path):
    clauses, profile = mapping_world(tmp_path)
    path = tmp_path / "data/shop/README.md"
    path.write_text("Không được hiểu **Montant** là số tiền trả lần đó.\n", encoding="utf8")
    rules = tmp_path / "rulebook.md"
    clauses = compile_clauses(
        (
            SourceFile(rules, "rulebook.md", sha256_file(rules)),
            SourceFile(path, "data/shop/README.md", sha256_file(path)),
        ),
        split_id="train",
    )
    assert not validate_bindings(
        clauses, [profile], [candidate(clauses, profile, "payment_amount")]
    ).accepted


def test_cli_rejects_source_ledger_before_any_registration(tmp_path, monkeypatch, capsys):
    from datamate.preparation.__main__ import main

    root = mapping_split(tmp_path / "source")
    ledger = root / "data/shop/raw.sqlite"
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare",
            "--split-root",
            str(root),
            "--split-id",
            "train",
            "--business-id",
            "shop",
            "--cache-root",
            str(tmp_path / "cache"),
            "--adaptive-mapping",
            "--profile",
            "unused.json",
            "--ledger",
            str(ledger),
            "--run-id",
            "test",
            "--run-dir",
            str(tmp_path / "run"),
        ],
    )
    try:
        main()
    except (SystemExit, FileNotFoundError):
        pass
    assert "ledger must remain outside" in capsys.readouterr().err
    assert not ledger.exists()


def test_paid_candidate_failure_leaves_a_structured_failure_report(tmp_path, monkeypatch):
    import asyncio

    from test_adaptive_normalization import adaptive_profile

    from datamate.inference.budget import RequestBudget
    from datamate.preparation import orchestration

    monkeypatch.setenv("LLM_API_KEY", "fixture-only")
    root = mapping_split(tmp_path / "source")
    requests = []
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="mapping", run_limit=1)
    original = orchestration.prepare_business
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("simulated candidate failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(orchestration, "prepare_business", fail_second)
    with pytest.raises(ValueError, match="simulated candidate failure"):
        asyncio.run(
            orchestration.prepare_with_mapping_recovery(
                root,
                "shop",
                tmp_path / "cache",
                split_id="train",
                run_dir=tmp_path / "run",
                allow_model_calls=True,
                profile=adaptive_profile(),
                budget=budget,
                transport=mapping_transport(requests),
            )
        )
    assert len(requests) == 1
    failure = json.loads((tmp_path / "run/failure.json").read_text())
    assert failure["http_attempts"] == 1 and failure["error_type"] == "ValueError"
    assert failure["baseline_fingerprint"]


def test_mapping_request_can_exceed_24kb_when_profile_allows_it(tmp_path, monkeypatch):
    import asyncio

    import httpx
    from test_adaptive_normalization import adaptive_profile, typed_response

    from datamate.inference.budget import RequestBudget
    from datamate.preparation.adaptive import propose_role_mapping
    from datamate.preparation.v2 import _source_roles

    monkeypatch.setenv("LLM_API_KEY", "fixture-only")
    clauses, table = mapping_world(tmp_path, description="x" * 26000)
    bindings = _source_roles([table], clauses, ())
    profile = adaptive_profile().model_copy(
        update={"input_byte_limit": 48000, "context_window": 65536, "max_tokens": 1024}
    )
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="mapping", run_limit=1)
    sizes = []

    def respond(request):
        sizes.append(len(request.content))
        return typed_response(json.loads(request.content), {"mappings": []})

    result = asyncio.run(
        propose_role_mapping(
            profile, budget, table, clauses, bindings, transport=httpx.MockTransport(respond)
        )
    )
    assert not result.mappings
    assert 24000 < sizes[0] <= 48000
    assert budget.snapshot()["run_attempts"] == 1
