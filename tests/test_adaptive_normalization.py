"""Bounded source-only recovery and transactional candidate acceptance."""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import duckdb
import httpx
import pytest

from datamate.inference.budget import RequestBudget
from datamate.inference.config import ModelProfile
from datamate.preparation.adaptive import (
    PlanFormatError,
    RecoveryPolicy,
    assess_capability,
    build_evidence_packet,
    candidate_transaction,
    cleaning_plan_proposer,
    recover_issue,
)
from datamate.preparation.models import (
    Binding,
    BusinessInputs,
    EvidenceRef,
    NormalizationIssue,
    ResolvedBinding,
    RuleClause,
    SemanticBindings,
    SourceDocument,
    SourceFile,
)
from datamate.preparation.normalization import CleaningPlan, CleaningStep
from datamate.preparation.operations import validate_plan


def source_packet(
    tmp_path,
    *,
    lines=("Source says keep the row.",),
    selected=None,
    bindings_digest="a" * 64,
):
    root = tmp_path / "train"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    rulebook = root / "rulebook.md"
    rulebook.write_text("# Active rules\n", encoding="utf-8")
    readme = business / "README.md"
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    source_files = tuple(
        SourceFile(path, path.relative_to(root).as_posix(), sha256(path.read_bytes()).hexdigest())
        for path in (rulebook, readme)
    )
    inputs = BusinessInputs("train", "shop", root, business, (), source_files)
    source_lines = readme.read_bytes().decode("utf-8").splitlines(keepends=True)
    refs = tuple(
        EvidenceRef(
            source="data/shop/README.md",
            sha256=source_files[1].sha256,
            locator=f"line:{index}",
            line_start=index,
            line_end=index,
            quote_sha256=sha256(source_lines[index - 1].encode()).hexdigest(),
        )
        for index, line in enumerate(lines, 1)
    )
    issue = NormalizationIssue(issue_id="issue-1", scope="table", kind="parse", detail="Unknown")
    packet = build_evidence_packet(
        issue,
        inputs,
        refs,
        Path_read_bytes,
        selected=selected,
        bindings_digest=bindings_digest,
        profile_digests=("b" * 64,),
    )
    return issue, packet, inputs, refs


def Path_read_bytes(path):
    return path.read_bytes()


def plan(bindings):
    return CleaningPlan(
        bindings_digest=bindings.digest,
        profile_digests=bindings.profile_digests,
        steps=(CleaningStep(table_id="table", operation={"name": "preserve_raw"}),),
    )


def policy(tmp_path, bindings):
    return RecoveryPolicy(
        budget=RequestBudget(tmp_path / "budget.sqlite", run_id="adaptive"),
        bindings=bindings,
    )


def test_oversized_evidence_requires_explicit_reselection(tmp_path):
    lines = ("a" * 23000, "b" * 2000)
    with pytest.raises(ValueError, match="reselect"):
        source_packet(tmp_path, lines=lines)
    issue, packet, _, refs = source_packet(tmp_path / "selected", lines=lines, selected=(0,))
    assert packet.issue_id == issue.issue_id
    assert packet.omitted == (refs[1],)
    assert len(packet.model_dump_json().encode()) <= 24000


def test_reference_artifact_cannot_be_solver_evidence(tmp_path):
    issue, packet, inputs, refs = source_packet(tmp_path)
    artifact = SourceFile(
        inputs.split_root / "answers.jsonl",
        "answers.jsonl",
        "0" * 64,
    )
    poisoned = replace(inputs, documents=inputs.documents + (artifact,))
    reference = refs[0].model_copy(update={"source": "answers.jsonl", "sha256": "0" * 64})
    with pytest.raises(ValueError, match="source"):
        build_evidence_packet(
            issue,
            poisoned,
            (reference,),
            Path_read_bytes,
            bindings_digest="a" * 64,
            profile_digests=("b" * 64,),
        )
    from datamate.preparation.models import EvidencePacket

    with pytest.raises(ValueError):
        EvidencePacket.model_validate(
            {
                "issue_id": issue.issue_id,
                "bindings_digest": "a" * 64,
                "profile_digests": ("b" * 64,),
                "excerpts": packet.excerpts,
                "omitted": (),
                "expected_label": "SENTINEL",
            }
        )
    with pytest.raises(ValueError, match="span"):
        EvidencePacket.model_validate(
            {
                "issue_id": issue.issue_id,
                "bindings_digest": "a" * 64,
                "profile_digests": ("b" * 64,),
                "excerpts": (
                    {
                        "evidence": refs[0].model_copy(
                            update={"line_start": None, "line_end": None}
                        ),
                        "text": inputs.documents[1].path.read_bytes().decode(),
                    },
                ),
            }
        )


def test_source_hash_and_span_are_read_back_before_serializing(tmp_path):
    issue, _, inputs, refs = source_packet(tmp_path)
    changed = inputs.documents[1].path
    changed.write_text("Replaced source.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        build_evidence_packet(
            issue,
            inputs,
            refs,
            Path_read_bytes,
            bindings_digest="a" * 64,
            profile_digests=("b" * 64,),
        )


def test_repeated_hypothesis_stops_before_second_execution(tmp_path):
    bindings = SemanticBindings((), (), ("b" * 64,), ("table",))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)
    calls = []
    attempts = []

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        calls.append("request")
        return plan(bindings)

    with duckdb.connect() as db:
        db.execute("CREATE TABLE candidate(value INTEGER)")
        db.execute("INSERT INTO candidate VALUES (1)")

        def apply(_plan, connection):
            attempts.append("execute")
            connection.execute("UPDATE candidate SET value=2")

        async def auditor(*_):
            return False

        result = asyncio.run(
            recover_issue(
                issue,
                packet,
                proposer,
                lambda candidate: candidate_transaction(db, candidate, apply),
                auditor,
                recovery_policy,
            )
        )
        assert db.execute("SELECT value FROM candidate").fetchone() == (1,)
    assert result.status == "PARTIAL"
    assert result.logical_requests == 2
    assert result.http_attempts == 2
    assert len(attempts) == 1


def test_audited_candidate_commits_once_and_records_ledger_attempt(tmp_path):
    bindings = SemanticBindings((), (), ("b" * 64,), ("table",))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        return plan(bindings)

    with duckdb.connect() as db:
        db.execute("CREATE TABLE candidate(value INTEGER)")
        result = asyncio.run(
            recover_issue(
                issue,
                packet,
                proposer,
                lambda candidate: candidate_transaction(
                    db,
                    candidate,
                    lambda _plan, connection: connection.execute(
                        "INSERT INTO candidate VALUES (1)"
                    ),
                ),
                lambda *_: True,
                recovery_policy,
            )
        )
        assert db.execute("SELECT value FROM candidate").fetchall() == [(1,)]
    assert result.status == "ACCEPTED"
    assert (result.proposal_rounds, result.logical_requests, result.http_attempts) == (1, 1, 1)
    assert result.accepted_hash is not None


def test_format_repair_consumes_second_logical_request(tmp_path):
    bindings = SemanticBindings((), (), ("b" * 64,), ("table",))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)
    repairs = []

    async def proposer(_issue, _packet, _round, repair_error):
        await recovery_policy.budget.acquire()
        repairs.append(repair_error)
        if len(repairs) % 2:
            raise PlanFormatError("invalid model output")
        return plan(bindings)

    with duckdb.connect() as db:
        db.execute("CREATE TABLE candidate(value INTEGER)")

        async def auditor(*_):
            return False

        result = asyncio.run(
            recover_issue(
                issue,
                packet,
                proposer,
                lambda candidate: candidate_transaction(db, candidate, lambda *_: None),
                auditor,
                recovery_policy,
            )
        )
    assert result.logical_requests == 4
    assert result.http_attempts == 4
    assert repairs[0] is None and repairs[1] == "invalid model output"


def test_unsafe_plan_cannot_mutate_db(tmp_path):
    bindings = SemanticBindings((), (), ("b" * 64,), ("table",))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)
    unsafe = plan(bindings).model_dump(mode="python")
    unsafe["steps"][0]["operation"] = {"name": "preserve_raw", "code": "exec(1)"}

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        return unsafe

    with duckdb.connect() as db:
        db.execute("CREATE TABLE candidate(value INTEGER)")
        starts = []
        result = asyncio.run(
            recover_issue(
                issue,
                packet,
                proposer,
                lambda candidate: starts.append(candidate),
                lambda *_: True,
                recovery_policy,
            )
        )
        assert db.execute("SELECT COUNT(*) FROM candidate").fetchone() == (0,)
    assert result.status == "PARTIAL"
    assert starts == []


def test_code_hidden_in_operator_column_is_rejected_before_executor(tmp_path):
    injected = "exec(1)"
    proof = EvidenceRef(source="data/shop/x.csv", sha256="c" * 64, locator="header:table/x")
    binding = Binding(
        kind="role",
        table_id="table",
        columns=(injected,),
        value="product_id",
        profile_digest="b" * 64,
        evidence=(proof,),
    )
    bindings = SemanticBindings(
        (ResolvedBinding(binding, "ACCEPTED", "synthetic"),), (), ("b" * 64,), ("table",)
    )
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)
    candidate = CleaningPlan(
        bindings_digest=bindings.digest,
        profile_digests=bindings.profile_digests,
        steps=(
            CleaningStep(
                table_id="table",
                operation={"name": "trim_text", "columns": (injected,)},
                binding_ids=(binding.binding_id,),
                evidence=(proof,),
            ),
        ),
    )
    started = []

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        return candidate

    result = asyncio.run(
        recover_issue(
            issue,
            packet,
            proposer,
            lambda proposal: started.append(proposal),
            lambda *_: True,
            recovery_policy,
        )
    )
    assert result.status == "PARTIAL"
    assert started == []


def test_sql_hidden_in_step_table_id_is_rejected_before_executor(tmp_path):
    table_id = "table;DROP TABLE raw"
    bindings = SemanticBindings((), (), ("b" * 64,), (table_id,))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)
    candidate = CleaningPlan(
        bindings_digest=bindings.digest,
        profile_digests=bindings.profile_digests,
        steps=(CleaningStep(table_id=table_id, operation={"name": "preserve_raw"}),),
    )
    started = []

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        return candidate

    result = asyncio.run(
        recover_issue(
            issue,
            packet,
            proposer,
            lambda proposal: started.append(proposal),
            lambda *_: True,
            recovery_policy,
        )
    )
    assert result.status == "PARTIAL"
    assert started == []


def test_disabled_level2_never_hides_required_capability_failure():
    assert assess_capability(required_capabilities_met=True).level2_status == "DISABLED"
    assert assess_capability(required_capabilities_met=True).status == "PARTIAL"
    assert (
        assess_capability(required_capabilities_met=True, mandatory_gates_pass=True).status
        == "PASS"
    )
    assert (
        assess_capability(required_capabilities_met=False, mandatory_gates_pass=True).status
        == "PARTIAL"
    )
    assert (
        assess_capability(
            required_capabilities_met=True, mandatory_gates_pass=True, level2_enabled=True
        ).status
        == "PARTIAL"
    )
    assert (
        assess_capability(
            required_capabilities_met=True, mandatory_gates_pass=True, level2_required=True
        ).status
        == "PARTIAL"
    )
    with pytest.raises(ValueError, match="mode"):
        RecoveryPolicy(mode="fallback")
    with pytest.raises(ValueError, match="generated Python"):
        RecoveryPolicy(generated_python="unknown")


def test_four_logical_requests_and_retries_count_eight_ledger_attempts(tmp_path):
    bindings = SemanticBindings((), (), ("b" * 64,), ("table",))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        await recovery_policy.budget.acquire()
        raise PlanFormatError("malformed")

    result = asyncio.run(
        recover_issue(
            issue,
            packet,
            proposer,
            lambda *_: None,
            lambda *_: True,
            recovery_policy,
        )
    )
    assert (result.proposal_rounds, result.logical_requests, result.http_attempts) == (2, 4, 8)
    assert recovery_policy.budget.snapshot()["run_attempts"] == 8


def test_budget_exhaustion_reports_attempts_already_sent(tmp_path):
    bindings = SemanticBindings((), (), ("b" * 64,), ("table",))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = replace(
        policy(tmp_path, bindings),
        budget=RequestBudget(tmp_path / "small.sqlite", run_id="small", run_limit=1),
    )

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        await recovery_policy.budget.acquire()
        raise PlanFormatError("never reached")

    result = asyncio.run(
        recover_issue(
            issue,
            packet,
            proposer,
            lambda *_: None,
            lambda *_: True,
            recovery_policy,
        )
    )
    assert result.status == "PARTIAL"
    assert result.http_attempts == 1
    assert result.logical_requests == 1


def test_existing_transaction_is_not_treated_as_candidate_savepoint(tmp_path):
    bindings = SemanticBindings((), (), ("b" * 64,), ("table",))
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    recovery_policy = policy(tmp_path, bindings)

    async def proposer(*_):
        await recovery_policy.budget.acquire()
        return plan(bindings)

    with duckdb.connect() as db:
        db.execute("CREATE TABLE candidate(value INTEGER)")
        db.execute("BEGIN TRANSACTION")
        result = asyncio.run(
            recover_issue(
                issue,
                packet,
                proposer,
                lambda candidate: candidate_transaction(
                    db,
                    candidate,
                    lambda _plan, connection: connection.execute(
                        "INSERT INTO candidate VALUES (1)"
                    ),
                ),
                lambda *_: True,
                recovery_policy,
            )
        )
        db.execute("ROLLBACK")
        assert db.execute("SELECT COUNT(*) FROM candidate").fetchone() == (0,)
    assert result.status == "PARTIAL"


def test_raw_expected_label_json_cannot_enter_solver_packet(tmp_path):
    with pytest.raises(ValueError, match="reference"):
        source_packet(tmp_path, lines=("README source: expected_label_sentinel",))
    with pytest.raises(ValueError, match="reference"):
        source_packet(tmp_path / "json", lines=('{"expected_label":"secret"}',))
    with pytest.raises(ValueError, match="reference"):
        NormalizationIssue(
            issue_id="issue",
            scope="table",
            kind="parse",
            detail="expected_label_sentinel: secret",
        )


def adaptive_profile():
    return ModelProfile(
        name="candidate",
        model_id="selected-model",
        base_url="http://localhost:8000/v1",
        context_window=32768,
        input_byte_limit=24000,
        max_tokens=2048,
    )


def prompt_bindings(*, value="product_id"):
    proof = EvidenceRef(source="data/shop/x.csv", sha256="c" * 64, locator="header:table/x")
    binding = Binding(
        kind="role",
        table_id="table",
        columns=("sku",),
        value=value,
        profile_digest="b" * 64,
        evidence=(proof,),
    )
    unresolved = binding.model_copy(update={"value": "unresolved-private-value"})
    document = SourceDocument(
        "train",
        "rulebook.md",
        sha256(b"Do not serialize full source.\n").hexdigest(),
        "Do not serialize full source.\n",
    )
    clause = RuleClause(
        "clause-private",
        document,
        1,
        1,
        document.text,
        document.sha256,
        None,
        "general",
        None,
        "UNMAPPED",
    )
    return SemanticBindings(
        (
            ResolvedBinding(binding, "ACCEPTED", "reason-private"),
            ResolvedBinding(unresolved, "UNRESOLVED", "reason-private"),
        ),
        (clause,),
        ("b" * 64,),
        ("table",),
        profile_sources=(("table", "b" * 64, "data/shop/x.csv", "c" * 64),),
    )


def typed_response(body, candidate):
    return httpx.Response(
        200,
        json={
            "id": "typed-result",
            "object": "chat.completion",
            "created": 0,
            "model": "selected-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": body["tools"][0]["function"]["name"],
                                    "arguments": json.dumps(candidate),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


def test_format_repair_receives_safe_field_paths_and_nested_step_contract(tmp_path):
    bindings = prompt_bindings()
    recovery_policy = policy(tmp_path, bindings)
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    received = []

    def respond(request):
        body = json.loads(request.content)
        context = json.loads(body["messages"][0]["content"].split("\n", 1)[1])
        received.append(context)
        assert "table_id" in context["output_contract"]
        assert "operation" in context["output_contract"]
        if len(received) == 1:
            candidate = {
                "bindings_digest": bindings.digest,
                "profile_digests": list(bindings.profile_digests),
                "steps": [
                    {
                        "action": "trim_text",
                        "scope": "table",
                        "columns": ["sku"],
                        "private_payload_key": "DO_NOT_ECHO_THIS_VALUE",
                        "evidence": {},
                    }
                ],
            }
        else:
            repair = context["repair_error"]
            assert "steps.0.table_id:missing" in repair
            assert "steps.0.operation:missing" in repair
            assert "steps.0.evidence:tuple_type" in repair
            assert "private_payload_key" not in repair and "DO_NOT_ECHO_THIS_VALUE" not in repair
            binding = context["accepted_bindings"][0]
            candidate = {
                "bindings_digest": context["bindings_digest"],
                "profile_digests": context["profile_digests"],
                "steps": [
                    {
                        "table_id": "table",
                        "operation": {"name": "trim_text", "columns": ["sku"]},
                        "binding_ids": [binding["binding_id"]],
                        "evidence": binding["evidence"],
                    }
                ],
            }
        return typed_response(body, candidate)

    async def execute():
        async with cleaning_plan_proposer(
            adaptive_profile(),
            recovery_policy.budget,
            bindings,
            transport=httpx.MockTransport(respond),
            transport_retries=0,
        ) as proposer:
            with pytest.raises(PlanFormatError) as error:
                await proposer(issue, packet, 0, None)
            repaired = await proposer(issue, packet, 0, str(error.value))
            assert repaired.steps[0].operation.name == "trim_text"

    asyncio.run(execute())
    assert len(received) == recovery_policy.budget.snapshot()["run_attempts"] == 2


def test_typed_plan_factory_supplies_accepted_context_for_a_valid_plan(tmp_path):
    bindings = prompt_bindings()
    recovery_policy = policy(tmp_path, bindings)
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        prompt = body["messages"][0]["content"]
        context = json.loads(prompt.split("\n", 1)[1])
        accepted = context["accepted_bindings"]
        assert accepted == [
            {
                "binding_id": bindings.accepted[0].binding_id,
                "table_id": "table",
                "columns": ["sku"],
                "kind": "role",
                "value": "product_id",
                "profile_digest": "b" * 64,
                "evidence": [bindings.accepted[0].evidence[0].model_dump(mode="json")],
            }
        ]
        assert context["table_ids"] == ["table"]
        assert context["profile_digests"] == ["b" * 64]
        assert context["profiles"] == [
            {
                "table_id": "table",
                "profile_digest": "b" * 64,
                "source": "data/shop/x.csv",
                "source_sha256": "c" * 64,
            }
        ]
        assert all(
            token not in prompt
            for token in (
                "reason-private",
                "unresolved-private-value",
                "Do not serialize full source.",
                "source_document",
                "clause-private",
                "expected_label",
            )
        )
        witness = accepted[0]
        candidate = {
            "bindings_digest": context["bindings_digest"],
            "profile_digests": context["profile_digests"],
            "steps": [
                {
                    "table_id": witness["table_id"],
                    "operation": {"name": "trim_text", "columns": witness["columns"]},
                    "binding_ids": [witness["binding_id"]],
                    "evidence": witness["evidence"],
                }
            ],
        }
        return typed_response(body, candidate)

    async def run():
        async with cleaning_plan_proposer(
            adaptive_profile(),
            recovery_policy.budget,
            recovery_policy.bindings,
            transport=httpx.MockTransport(respond),
        ) as proposer:
            return await proposer(issue, packet, 0, None)

    result = asyncio.run(run())
    validate_plan(result, bindings)
    assert result.steps[0].operation.name == "trim_text"
    assert result.steps[0].binding_ids == (bindings.accepted[0].binding_id,)
    assert len(requests) == recovery_policy.budget.snapshot()["run_attempts"] == 1
    assert requests[0]["model"] == "selected-model"
    assert requests[0]["max_tokens"] == 2048


@pytest.mark.parametrize("mode", ["registry", "replay"])
def test_offline_factory_rejects_before_client_creation(tmp_path, monkeypatch, mode):
    import datamate.inference.client as client

    def forbidden_client(*args, **kwargs):
        pytest.fail("offline factory opened a client")

    monkeypatch.setattr(client, "inference_model", forbidden_client)
    budget = RequestBudget(tmp_path / "offline.sqlite", run_id="offline")

    async def run():
        with pytest.raises(ValueError, match="offline"):
            async with cleaning_plan_proposer(None, budget, None, mode=mode):
                pass

    asyncio.run(run())
    assert budget.snapshot()["run_attempts"] == 0


@pytest.mark.parametrize(
    "invalid",
    [
        "bindings_digest",
        "profile_digests",
        "issue_id",
        "scope",
        "excerpt_hash",
        "accepted_profile",
        "accepted_source",
        "profile_source",
        "label_value",
    ],
)
def test_malformed_or_stale_prompt_context_is_refused_before_http(tmp_path, invalid):
    bindings = prompt_bindings()
    issue, packet, _, _ = source_packet(tmp_path, bindings_digest=bindings.digest)
    if invalid in {"bindings_digest", "profile_digests", "issue_id"}:
        value = {
            "bindings_digest": "d" * 64,
            "profile_digests": ("d" * 64,),
            "issue_id": "another-issue",
        }[invalid]
        packet = packet.model_copy(update={invalid: value})
    elif invalid == "scope":
        issue = issue.model_copy(update={"scope": "unknown-table"})
    elif invalid == "excerpt_hash":
        excerpt = packet.excerpts[0].model_copy(update={"text": "changed"})
        packet = packet.model_copy(update={"excerpts": (excerpt,)})
    elif invalid == "profile_source":
        bindings = replace(
            bindings, profile_sources=(("table", "d" * 64, "data/shop/x.csv", "c" * 64),)
        )
        packet = packet.model_copy(update={"bindings_digest": bindings.digest})
    else:
        binding = bindings.accepted[0]
        if invalid == "accepted_profile":
            binding = binding.model_copy(update={"profile_digest": "d" * 64})
        elif invalid == "accepted_source":
            proof = binding.evidence[0].model_copy(update={"source": "answers.jsonl"})
            binding = binding.model_copy(update={"evidence": (proof,)})
        else:
            binding = binding.model_copy(update={"value": "expected_label_sentinel"})
        bindings = replace(bindings, entries=(ResolvedBinding(binding, "ACCEPTED", "test"),))
        packet = packet.model_copy(update={"bindings_digest": bindings.digest})
    budget = RequestBudget(tmp_path / "invalid.sqlite", run_id="invalid")
    sent = []

    async def run():
        with pytest.raises(ValueError, match="stale|scope|hash|source|reference|profile"):
            async with cleaning_plan_proposer(
                adaptive_profile(),
                budget,
                bindings,
                transport=httpx.MockTransport(lambda request: sent.append(request)),
            ) as proposer:
                await proposer(issue, packet, 0, None)

    asyncio.run(run())
    assert budget.snapshot()["run_attempts"] == 0
    assert sent == []


@pytest.mark.parametrize("oversized", ["evidence", "bindings", "repair", "wire"])
def test_combined_prompt_over_24k_requires_reselection_before_http(tmp_path, oversized):
    bindings = prompt_bindings(value="v" * 23000 if oversized == "bindings" else "product_id")
    issue, packet, _, _ = source_packet(
        tmp_path,
        lines=(
            "x" * 23000
            if oversized == "evidence"
            else "x" * 18000
            if oversized == "wire"
            else "Source row.",
        ),
        bindings_digest=bindings.digest,
    )
    assert len(packet.model_dump_json().encode()) <= 24000
    budget = RequestBudget(tmp_path / "context.sqlite", run_id="context")
    sent = []

    async def run():
        async with cleaning_plan_proposer(
            adaptive_profile(),
            budget,
            bindings,
            transport=httpx.MockTransport(lambda request: sent.append(request)),
        ) as proposer:
            with pytest.raises(ValueError, match="reselect"):
                await proposer(issue, packet, 0, "repair" * 5000 if oversized == "repair" else None)

    asyncio.run(run())
    assert budget.snapshot()["run_attempts"] == 0
    assert sent == []
