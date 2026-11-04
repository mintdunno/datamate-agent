# Evidence-Guided Adaptive Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor preparation into an evidence-bound, independently audited normalization pipeline and qualify a fixed five-business train pilot without a teacher-generated global gold database.

**Architecture:** Reuse existing I/O, immutable source snapshots, raw tagging, operation utilities, atomic publication and common inference accounting. Separate staging, full CPU profiling, source compilation/binding, typed transformations/projections and independent qualification; expose only qualified factual scopes through the existing artifact interface. Reference/evaluation code stays outside preparation.

**Tech Stack:** Python 3.13, uv, Pydantic, DuckDB, openpyxl, current CSV/SQLite readers and Polars profiling; bounded Pandas batches, rank_bm25, RapidFuzz and psutil as justified locked additions; PydanticAI/common OpenAI-compatible inference; optional qualified Linux OCI micro-transform backend; pytest/Ruff.

**Spec:** docs/superpowers/specs/2026-11-04-adaptive-normalization-design.md, approved by the user on 2026-11-04. Read that document together with this plan. The source authority is the active split's rulebook/README/calendar and official competition rules; the uncorrected v3 examples do not override them.

**Status:** Main self-review complete; implementation has not started. Ready for the
user's plan review using the previously supplied execution method.

## Global Constraints

- Refactor current preparation; no QA rewrite, embeddings, reranker, vector database, new parallel normalizer or multi-agent solving runtime.
- All source inputs are read-only. Preserve raw identities, decoded values, native keys, formulas/formats, original hashes and complete dispositions/lineage; never use source-content equality as row identity.
- Never read answers/questions/reference aliases in production preparation, prompts or tools; do not tune on public/private answers or locked checkpoint diagnostics.
- Fixed pilot: Excel biz_124,biz_106,biz_120,biz_122 for development; SQLite biz_123 for one locked checkpoint. Full-data processing is limited to those five; no full QA evaluation is authorized.
- Initial reference target:20 cases/business=100;>=10 VERIFIED DETERMINATE/business. Synthetic minimum 88: positive and counterexample for each of 44 corrected cases, with N/M/Q ownership.
- References have separate evidence_status and expected_behavior axes. Model agreement/confidence is not verification; an all-abstain system cannot pass determinate-case gates.
- Batch1000; default one business at a time. Proposed safety ceilings from the approved design: DuckDB2GiB, process-tree RSS8GiB,45min/business, generated-output/spill10GiB. Record actual enforcement and measurements.
- NIM campaign 120 TOTAL actual HTTP attempts, smoke20/development60/final40, shared25RPM/1200UTC-day and300/run outer guard; no automatic increases or new-ledger bypass.
- Per issue<=2 proposal rounds,<=2 logical requests/round,<=1 transport retry/request,<=8HTTP attempts/issue, concurrency1. Context32768, request-input24000bytes, output2048tokens; preserve omission/token-completeness reporting.
- Generated Python only on a qualified Linux OCI backend. Defaults:1CPU,512MiB memory/swap ceiling,32PIDs,10s,64MiB tmpfs; explicitly qualified Pandas group/table mode may use1GiB. Native Windows arbitrary exec is disabled.
- No implicit model/backend/thinking/quantization fallback, large model downloads, paid jobs, merge/push/history rewrite or cleanup of old state.
- Every logical code unit: red test -> minimal implementation -> focused regressions -> Ruff -> relevant full/integration checks -> staged diff/secret review -> atomic Conventional Commit. A test file's existence alone is not coverage.

## Review Focus

1. A structural/blank/footer row between groups must not cause fill-forward or dedup across a boundary; Task 11 tests the same input over multiple batch sizes.
2. Parsing amounts and their totals with the same wrong scale can make arithmetic pass; Tasks 9/16/4 use independent declared-scale and ambiguity counterexamples.
3. Repeated invoice amounts,1:N extraction and overlapping files must conserve source dispositions while producing the correct fact grain; Tasks 2/14 test lineage and cardinality independently.
4. Quarantining a boundary-date or amount row must not shrink declared coverage or produce an apparently complete surviving-row total; Tasks 16/17/22 test dependency blocking and safe projections.
5. Restarted runs, changed references or missing OCI enforcement must not replenish budgets, unlock a checkpoint or count skipped isolation tests as PASS; Tasks 18/21/24 pin these behaviors.

## File structure and ownership

Keep current package directories. Proposed new files have separate responsibilities:

| Path | Responsibility |
| --- | --- |
| src/datamate/preparation/models.py | Existing source contracts plus shared typed evidence, plans, profiles, facts, lineage and qualification; no reference labels |
| src/datamate/preparation/storage.py | Versioned staging/meta/clean DDL, raw iteration, factual persistence and read-only snapshots; no rule inference |
| src/datamate/preparation/profiling.py | Full CPU statistics and bounded evidence, independent of candidate transformations |
| src/datamate/preparation/catalog.py | Source clauses, retrieval, declarations, binding validation and supported measure policies |
| src/datamate/preparation/normalization.py | Existing parsers/TablePlan compatibility and lossless parser extensions |
| src/datamate/preparation/operations.py | Typed registered Pandas-batch operations, group state and bounded/spilled dedup |
| src/datamate/preparation/projection.py | Canonical factual projections and trusted flat SQL views |
| src/datamate/preparation/audit.py | Independent applicability-aware checks and dependency qualification |
| src/datamate/preparation/adaptive.py | Proposal-only model integration, typed recovery and transactional acceptance |
| src/datamate/preparation/sandbox.py | Fail-closed isolation preflight/execution/qualification, no model credentials |
| src/datamate/preparation/pipeline.py | Orchestration, versions/fingerprints, atomic publication and PreparedBusiness compatibility |
| src/datamate/evaluation/normalization.py | Reference-only contracts/selection/proof checks/scoring, never imported by preparation |
| src/datamate/evaluation/normalization_campaign.py | Controlled baseline/freeze/checkpoint/report orchestration |
| src/datamate/inference/budget.py | Existing accounting plus atomic campaign/phase ceiling; no alternate ledger |
| tests/normalization_fixtures.py | Synthetic fixture helpers and latent truth, never production input |
| tests/fixtures/normalization/catalog_cases.json | Corrected44-case IDs/owner/task mapping, no business answer totals |
| config/normalization/pilot.json | Dataset selection, quotas, seed and safety policies, not production business-specific logic |

Tests belong to the component named by each task. Extend existing files when coherent;
do not create one test/module per catalog item. Packaging uses existing requirements/
and scripts/ directories. Real reference material and generated reports remain ignored
under existing state/suites, state/prepared and state/runs locations.

## Shared interface decisions

Task 1 defines evaluator-only PilotPolicy, SourceSnapshot, CaseManifest, ReferenceEvidencePacket, ReferenceProposal,
ProofCheckResult, AdjudicatedReference, ReferenceSuite, CandidateResultSet and NormalizationMetrics.
Task 2 defines production types used by later task signatures:

- EvidenceRef(source,sha256,locator,line_start,line_end,quote_sha256); optional spans do not weaken required source-hash validation. Keep normalization.Evidence as a backwards-compatible alias/adaptor.
- RawRecord(record_id,table_id,source:SourceRow,kind); StagedTable(table_id,source,source_sha256,name,header_row,columns,row_count,raw_record_count). row_count counts post-header input records; raw_record_count includes all persisted source row kinds.
- ColumnProfile, TableProfile(table_id,row_count,columns,samples,profile_version); SampleRef contains coordinates/pattern IDs, not hidden expected results.
- RuleClause, ClauseMatch, Binding, SemanticBindings(accepted,unresolved,conflicts,dependency_graph,digest), MeasurePolicy. Proposed and accepted bindings have distinct states.
- CleaningStep(step_id,operation,operation_version,parameters,input_scope,evidence_refs), CleaningPlan(plan_id,source_digest,profile_digest,binding_digest,steps,version).
- CandidateFact(fact_id,table_role,values), RawDisposition(record_id,kind,reason,evidence_refs), LineageEdge(fact_id,record_id,column_id,step_id), TransformEvent and ProjectionResult(fact_counts,disposition_count,lineage_count).
- AuditCheck, AuditSpec, AuditFinding, AuditReport(findings,counts,critical_failures), DependencyGraph, ScopeQualification(status,reasons,dependencies,declared_coverage,observed_range), Qualification(scopes,business_status).
- PreparationPolicy(mode,batch_size,limits,profile_paths,campaign_id); mode is registry/adaptive/replay. Default registry never opens an HTTP transport. Optional profiles are loaded only by explicitly allowed adaptive execution.
- PublishedProjection(view_id,columns,factual_scope,qualification_digest,provenance); no private relation names or blocked fields are allowed in columns. Task 14 creates these only after Task 16 supplies qualification.

Use Pydantic at serialized boundaries, immutable dataclasses internally where useful.
TypedValue is RawValue|Decimal; JSON encoding is tagged where necessary. Do not put
DataFrame/callables/free-form executable code into serialized CleaningPlan parameters.
All methods below use these shared types rather than independently redefining them.

### Corrected catalog ownership

Task 1 freezes the following mapping in catalog_cases.json. Each item has one primary
N/M/Q owner and may list secondary owners; this table names implementation/test
responsibility, not rules to apply without active-source evidence.

| Catalog IDs | Primary owner / task | Test modules and secondary responsibility |
| --- | --- | --- |
| 1–3, 5–6, 9 | N / 10 | tests/test_excel_regressions.py; summary applicability also Task 16 |
| 4 | N / 14 | tests/test_canonical_projection.py; Task 11 resets source boundaries |
| 7, 11–12 | N / 11 | tests/test_cleaning_operations.py |
| 8 | N / 2 | tests/test_loaders.py, tests/test_normalization_storage.py; CSV/SQLite synthetic negatives |
| 10 | M / 6 | tests/test_semantic_bindings.py |
| 13–16 | N / 9 | tests/test_normalization.py; negative quantity does not independently prove returned status |
| 17 | N / 14 | tests/test_canonical_projection.py; source-declared compound-value grammar only |
| 18–19, 21–22 | N / 12 | tests/test_semantic_bindings.py; explicit proof positive, similarity-only negative |
| 20, 23–25 | M / 6 | tests/test_semantic_bindings.py; Task 12 preserves observed identity variants |
| 26–29 | N / 13 | tests/test_cleaning_operations.py |
| 30–31 | Q / 6 | tests/test_semantic_bindings.py; Task 14 preserves separate factual money/fee components |
| 32 | N / 14 | tests/test_canonical_projection.py; Task 9 supplies declared text grammar, ambiguous notes stay raw |
| 33 | M / 6 | tests/test_semantic_bindings.py; receipts do not prove expenditures |
| 34 | Q / 6 | tests/test_semantic_bindings.py; active-source policy, never answer-derived target |
| 35 | N / 14 | tests/test_canonical_projection.py; Task 16 independent arithmetic/applicability |
| 36 | Q / 15 | tests/test_periods.py; source-year and question-year stay separate |
| 37–40 | M / 6 | tests/test_semantic_bindings.py; Task 16 distinguishes absence from processing uncertainty |
| 41 | N / 14 | tests/test_canonical_projection.py; Tasks 13/16 scoped unit/lot/return checks |
| 42 | N / 14 | tests/test_canonical_projection.py; Task 16 installment/IMEI constraints |
| 43 | Q / 6 | tests/test_semantic_bindings.py; factual zero/warranty observations are retained |
| 44 | Q / 6 | tests/test_semantic_bindings.py; Task 14 effective-price/delivery-event facts |

## Execution order

Tasks 1–4 establish contracts/raw/profiles/hidden references. Tasks 5–15 implement source
semantics and deterministic candidates;16–17 audit and publish. Task 18 accounting can
run independently after Task 1, but must land before any live model call. Tasks 19–21
add adaptive execution and sandbox. Tasks 22–24 integrate, benchmark and qualify.
Reference adjudication runs independently after Task 3/4, before learner baseline.
At execution start create feat/adaptive-normalization from the current
feat/milestone1-harness HEAD containing the approved design and this plan; do not
restart from main or discard the existing Stage1/Stage2/harness implementation.
Plan/contract changes are main-owned; parallel writers use separate worktrees, clear
file ownership and no overlapping changes. Preserve the user's existing untracked files.

## Per-task verification and Git convention

Commands below use uv for portability. Before each code commit also run:

    uv run --locked ruff check .
    uv run --locked ruff format --check .
    git -c core.commitGraph=false diff --check

After implementation, repeat that task's exact red-test command with PASS expected,
plus its named regressions. This is the green command whenever the task says
"re-run task tests"; no task may commit with its red assertion still failing.

The full-suite command is uv run --locked pytest -q. Dependency additions also need
an audit of the locked runtime environment: export with uv export --locked --no-dev
--no-hashes --format requirements-txt --output-file state/runs/normalization/requirements-audit.txt,
then run uvx pip-audit --disable-pip -r state/runs/normalization/requirements-audit.txt.
Save the audit/tool version and result; unavailable vulnerability data is UNVERIFIED.
Do not disable certificate verification or treat a failed audit as clean.

Run full pytest after dependency additions, model/shared-contract changes and at final
integration. If Windows sandbox prevents async/subprocess tests, use the already
authorized execution environment escalation; never call a hanging/unrun check PASS.
Stage only files listed by that task, inspect staged changes/known secrets and commit
with its proposed message. No git add .; do not stage real references, state or .env.

Assertion fragments below belong to the named test immediately above them. Arrange
the stated fixture and capture those local observations from the task's typed results;
the variable names are not additional public APIs. Use pytest.raises for the specified
rejections and literal synthetic expected values, not candidate-derived expectations.
Task 24 is a controlled experiment/report task, not a new production implementation.

---

### Task 1: Correct source contracts and freeze pilot policy (P0)

**Files:** Modify docs/normalization/NORMALIZATION_SPEC.md, docs/normalization/ERROR_CATALOG.md; create config/normalization/pilot.json, src/datamate/evaluation/normalization.py, tests/fixtures/normalization/catalog_cases.json, tests/test_normalization_references.py.
**Interfaces:** Produces the evaluator-only shared types above, snapshot_sources(split_root:Path,business_ids:Sequence[str],*,split_id:str)->SourceSnapshot, freeze_selection_policy(policy:PilotPolicy,sources:SourceSnapshot)->str and load_pilot_policy(path:Path)->PilotPolicy. Snapshot uses the existing discovery allowlist, never question/answer/reference files. Case/ref types have case_id,source hashes,scope,assertions and versions; evidence_status is separate from expected_behavior.

- [ ] Write test_pilot_policy_is_five_train_businesses_and_has_no_labels: assert four exact Excel IDs, biz_123 checkpoint, seed 42,20 cases/business,10 determinate minimum, campaign 120 and20/60/40 caps; reject unknown/duplicate business IDs in selection and label/prediction paths.
- [ ] Write test_catalog_has_44_unique_owned_cases and test_spec_temporal_warranty_rules_match_active_sources: IDs 1..44; every case has N/M/Q owner and owning task; no fixed 2026 rule, unconditional warranty exclusion or price-only merge. Count assertions are documentary contracts, not business data targets.

```python
assert dev_ids == {"biz_124", "biz_106", "biz_120", "biz_122"}
assert checkpoint_ids == {"biz_123"}
assert (seed, cases_per_business, minimum_determinate) == (42, 20, 10)
assert (campaign_limit, phase_limits) == (120, {"smoke": 20, "development": 60, "final": 40})
assert case_ids == set(range(1, 45))
```

- [ ] Run uv run --locked pytest tests/test_normalization_references.py -q; expect missing API/config failures, not unexpected network/data access.
- [ ] Implement types/policy freeze, quoted-source corrections and a versioned 44-case task mapping. Keep external237/237/count claims explicitly unverified; do not encode public targets/alias lists as expected outputs.
- [ ] Re-run the task tests; policy freezes before profiles, source drift changes digest and immutable-policy overwrite fails. Update docs hashes in the new execution report when corrections are committed.
- [ ] Commit: docs(normalization): reconcile source rules and freeze pilot contracts.

### Task 2: Raw storage, stable identity and 1:N production contracts (P1)

**Files:** Modify src/datamate/preparation/models.py, src/datamate/preparation/normalization.py, src/datamate/preparation/pipeline.py; create src/datamate/preparation/storage.py, tests/test_normalization_storage.py.
**Interfaces:** Produces all production shared types; create_storage(db:DuckDBPyConnection)->None, stage_table(db,source:SourceFile,table:SourceTable)->StagedTable, iter_raw_batches(db,table_id:str,batch_size:int)->Iterator[list[RawRecord]]. raw values are immutable; consume SourceTable.rows exactly once.

- [ ] Write test_staging_preserves_types_formulas_keys_preamble_and_distinct_row_identity and test_one_raw_row_can_have_two_facts_but_one_disposition. Assert two identical source rows have different IDs, exact tagged raw values survive, two lineage edges do not double raw accounting and duplicate dispositions fail.

```python
assert identical_rows[0].record_id != identical_rows[1].record_id
assert decoded_values == [Decimal("0.10"), None, "001", b"\x00"]
assert (raw_count, disposition_count, fact_count, lineage_count) == (1, 1, 2, 2)
assert original_formula == "=A1+B1"
```

- [ ] Run uv run --locked pytest tests/test_normalization_storage.py -q; expect missing storage/contracts.
- [ ] Implement versioned staging/meta/clean scaffolding, source/hash/ordinal/parser-bound IDs and separate persistence for facts, disposition and lineage. Move Evidence ownership without import cycles; keep ColumnPlan/TablePlan behavior compatible. Preserve existing source snapshot and single-use iterator semantics.
- [ ] Run task tests plus tests/test_preparation.py, tests/test_normalization.py. Do not reuse normalized_json as the only place for canonical children; leave legacy helpers versioned until Task 17 switches publication.
- [ ] Commit: refactor(preparation): separate immutable raw storage and fact lineage.

### Task 3: Full CPU profiles and deterministic evidence sampling (P1)

**Files:** Modify src/datamate/preparation/profiling.py, src/datamate/preparation/storage.py; create tests/test_normalization_profiles.py.
**Interfaces:** profile_table(db,table_id:str,*,batch_size:int=1000)->TableProfile; select_evidence(profile:TableProfile,*,seed:int,max_samples:int)->list[SampleRef]. No model/candidate/evaluator inputs.

- [ ] Write test_profiles_scan_every_record_with_bounded_samples, test_profile_is_chunk_size_independent and test_cardinality_exactness_is_explicit. Assert same counts/digests for batches 1/7/1000; rare formats represented;<=configured sample cap; exact vs lower-bound distinct markers honest.
- [ ] Add test_candidate_relations_are_not_accepted_keys: nonunique overlapping columns remain proposals; no row payload in source-only summary unless explicitly selected.

```python
assert full_row_counts == [1003, 1003, 1003]  # batch sizes 1, 7, 1000
assert len(set(profile_digests)) == 1
assert len(sample_refs) <= 12
assert rare_pattern_id in sampled_pattern_ids
assert accepted_key_count == 0  # overlapping, nonunique fixture
```

- [ ] Run uv run --locked pytest tests/test_normalization_profiles.py -q; expect missing APIs/format statistics.
- [ ] Decouple profiles from PreparedRow, scan staged raw instead of exhausted loader iterators, retain correct Polars counters and add shape/format distributions, parse candidates and candidate relationship coverage. Bound samples/distinct examples and keep rare-pattern counts.
- [ ] Run task tests and tests/test_preparation.py::test_all_rows_and_profile_cardinality_cap_are_explicit. Check all records are profiled without retaining every unique value in Python.
- [ ] Commit: feat(preparation): add bounded full-data profiles and pattern evidence.

### Task 4: Blind case selection, proof checks and reference freezing (P0/P1)

**Files:** Modify src/datamate/evaluation/normalization.py, tests/test_normalization_references.py; create tests/normalization_fixtures.py.
**Interfaces:** select_cases(policy:PilotPolicy,sources:SourceSnapshot,profiles:Sequence[TableProfile])->CaseManifest; check_reference_proof(proposal:ReferenceProposal,evidence_reader:Callable[[EvidenceRef],object])->ProofCheckResult; adjudicate_reference(proposal,proof)->AdjudicatedReference; freeze_reference_suite(manifest,references)->ReferenceSuite. Helpers in tests/normalization_fixtures.py create deterministic source snapshots and independently specified latent truth.

- [ ] Write test_selection_is_seeded_source_order_independent_and_frozen_before_results and test_unresolved_reference_is_not_replaced. Assert100IDs,20/business,80dev/20checkpoint; same inputs/seed yield same IDs; changed source/policy rejects reuse; unresolved stays in original manifest.
- [ ] Write test_reviewer_cannot_self_assert_verified, test_ambiguous_scale_total_is_not_proof, test_verified_refusal_does_not_count_as_determinate and test_reference_revision_changes_digest. Synthetic price1.000/qty2/total2.000 alone cannot select scale; confidence/consensus cannot promote a label.

```python
assert (len(case_ids), len(dev_case_ids), len(checkpoint_case_ids)) == (100, 80, 20)
assert selected_before_results == selected_after_source_reordering
assert unproved_numeric_scale_reference.evidence_status != "VERIFIED"
assert verified_refusal_determinate_count == 0
assert old_reference_digest != revised_reference_digest
```

- [ ] Run uv run --locked pytest tests/test_normalization_references.py -q; expect missing selection/proof behavior.
- [ ] Implement evaluator-owned proof dispatch for exact source observations, scoped row equality, explicit scale/unit/role plus independent arithmetic, trusted-key checks, declared absence and demonstrated ambiguity. Do not import candidate parsing/binding acceptance to derive expected values. Require versioned independent adjudication for unsupported natural-language proofs.
- [ ] Freeze references under state/suites/normalization-v1 only; commit synthetic helpers and policy, not real values. Add test_reference_sentinel_never_enters_preparation_catalog_or_request using a forbidden expected-value sentinel.
- [ ] Run reference tests plus tests/test_isolation.py and tests/test_runtime_inputs.py.
- [ ] Commit: feat(evaluation): add blinded evidence-backed normalization references.

### Task 5: Complete source-clause ledger and precedence (P2)

**Files:** Modify src/datamate/preparation/catalog.py, src/datamate/preparation/models.py, src/datamate/preparation/storage.py, tests/test_catalog.py; create tests/test_semantic_bindings.py.
**Interfaces:** compile_clauses(documents:tuple[SourceFile,...])->tuple[RuleClause,...]; returns an entry for every normative clause, including UNMAPPED/CONFLICT. source_documents retain full original text/hash; clause spans/quote digests bind to it.

- [ ] Write test_all_clauses_preserved_even_when_unmapped and test_source_quote_hash_and_split_are_bound. Assert every input clause appears once; bad quote/span/hash rejected; no train-rule fallback for another split.
- [ ] Add test_question_industry_general_readme_precedence: explicit permitted question override and industry special rule win defaults; README cannot override higher rules. Source declarations remain data, not executable instructions.

```python
assert persisted_clause_ids == input_clause_ids
assert unfamiliar_clause_status == "UNMAPPED"
assert accepted_precedence == ["permitted_question_override", "industry", "general", "readme"]
assert stale_quote_accepted is False
assert other_split_used_train_default is False
```

- [ ] Run uv run --locked pytest tests/test_catalog.py tests/test_semantic_bindings.py -q; expect missing clause status/precedence behavior.
- [ ] Extend current Markdown section logic with typed supported declarations and unresolved records, not a hardcoded universal industry list. Persist source text before binding, record conflicts and dependencies.
- [ ] Re-run tests with an unfamiliar synthetic clause; it must be visible/unmapped, not silently discarded.
- [ ] Commit: feat(preparation): compile source clauses with explicit authority and uncertainty.

### Task 6: Roles, grain, capabilities and restricted measure policies (P2)

**Files:** Modify src/datamate/preparation/catalog.py, src/datamate/preparation/models.py, tests/test_semantic_bindings.py.
**Interfaces:** validate_bindings(clauses:Sequence[RuleClause],profiles:Sequence[TableProfile],proposals:Sequence[Binding])->SemanticBindings; render_measure(policy:MeasurePolicy,bindings:SemanticBindings)->str for allowlisted typed contract queries only. No arbitrary SQL string supplied by a model is trusted.

- [ ] Write test_combined_table_binds_line_and_order_grain_separately, test_snapshot_is_not_event_history and test_stale_or_conflicting_binding_is_not_accepted. Assert repeated invoice total vs line amount roles differ, snapshot_at stays fixed and incompatible scopes remain unresolved.
- [ ] Write test_measure_policy_keeps_return_fee_and_warranty_predicates_separate: revenue/debt/visit/work-count policies differ while facts remain; missing time/product detail becomes capability metadata, not invented columns or zeros.

```python
assert bound_grains == {"order_header", "order_line"}
assert revenue_policy_digest != debt_policy_digest
assert snapshot_at_before == snapshot_at_after
assert unsupported_hour_capability is False
assert unresolved_conflict_count == 1
```

- [ ] Run uv run --locked pytest tests/test_semantic_bindings.py -q; expect missing binding validation.
- [ ] Implement typed role/grain/status/capability/measure binding validation, allowed predicate vocabulary, source-dependent applicability and dependency graph. New roles are declarations; unknown executable semantics remain UNMAPPED. A raw-value score cannot choose a role against README evidence.
- [ ] Verify forbidden expression/callback injection rejected and renderer references only validated flat projection IDs.
- [ ] Commit: feat(preparation): bind grain roles and versioned measure prerequisites.

### Task 7: Bounded BM25/fuzzy retrieval with explicit completeness (P2)

**Files:** Modify src/datamate/preparation/catalog.py, pyproject.toml, uv.lock; extend tests/test_catalog.py.
**Interfaces:** retrieve_clauses(query:str,clauses:Sequence[RuleClause],*,limit:int)->list[ClauseMatch]; required_clauses(bindings:SemanticBindings,scope:str)->set[str]. Versioned tokenizer is owned by src/datamate/preparation/catalog.py, shared for query/corpus.

- [ ] Write test_bm25_retrieval_is_deterministic_but_does_not_drop_unmatched_rules and test_required_rule_dependencies_ignore_top_k_truncation. Assert same ranking/tie-breaks, bounded matches and complete normative ledger/dependency set even when relevant generic clause ranks low.

```python
assert ranked_ids_first == ranked_ids_second
assert len(ranked_ids_first) <= 3
assert all_persisted_clause_ids == input_clause_ids
assert low_ranked_required_clause_id in required_dependency_ids
assert similarity_alone_accepted_binding_count == 0
```

- [ ] Run uv run --locked pytest tests/test_catalog.py -q; expect missing retrieval contract.
- [ ] Add rank-bm25 through uv, lock compatible dependencies, retain RapidFuzz only for candidates. Use explicit versioned Unicode/case/token processing; hashes include tokenizer/index settings. Retrieval never accepts identity/semantic mappings by score.
- [ ] Run focused tests, full pytest/Ruff and dependency audit after installation. A dependency/compatibility failure is surfaced, not silently replaced by another ranking engine.
- [ ] Commit: feat(preparation): add versioned bounded lexical rule retrieval.

### Task 8: Typed Pandas batch boundary and operation registry (P3)

**Files:** Create src/datamate/preparation/operations.py, tests/test_cleaning_operations.py; modify src/datamate/preparation/normalization.py, pyproject.toml, uv.lock.
**Interfaces:** OperationState(db:DuckDBPyConnection,boundary_id:str) with reset_boundary(boundary_id:str)->None and close()->None; execute_batch(batch:pd.DataFrame,step:CleaningStep,state:OperationState)->BatchResult; validate_plan(plan:CleaningPlan,bindings:SemanticBindings)->None. BatchResult is defined in src/datamate/preparation/operations.py with facts:tuple[CandidateFact,...], dispositions:tuple[RawDisposition,...], lineage:tuple[LineageEdge,...], events:tuple[TransformEvent,...]. State owns/resets spill tables, not the caller's DB connection; close cleans its temporary state on success/failure. Frame record_id is immutable.

- [ ] Write test_batch_roundtrip_preserves_decimal_none_leading_zero_ids_dates_and_bytes and test_unregistered_operation_or_callable_parameters_fail_closed. Assert Decimal('0.10'),None,'001',date/time/bytes survive with no NaN/float conversion; raw columns/IDs are unchanged.

```python
assert roundtrip_values == [Decimal("0.10"), None, "001", date(2025, 1, 2), b"\x00"]
assert all(type(a) is type(b) for a, b in zip(roundtrip_values, source_values, strict=True))
assert record_ids_after == record_ids_before
assert unregistered_operation_executed is False
```

- [ ] Run uv run --locked pytest tests/test_cleaning_operations.py -q; expect missing registry/batch API.
- [ ] Add Pandas through uv and implement explicit object/nullable batch adapters, typed parameter models and reviewed operation/version lookup. Wrap existing pure parsers rather than rewriting tested I/O; do not deserialize callables or executable strings as registered steps.
- [ ] Run task tests, existing normalization/loaders tests and full suite after dependency change. Record compatible locked versions and confirm no premature Polars removal.
- [ ] Commit: feat(preparation): introduce lossless typed batch operation registry.

### Task 9: Mixed-pattern numeric, datetime, quantity and ID parsers (P3)

**Files:** Modify src/datamate/preparation/normalization.py, src/datamate/preparation/operations.py; extend tests/test_normalization.py and tests/test_cleaning_operations.py.
**Interfaces:** parse_candidates(value:TypedValue,role:str,evidence:Sequence[EvidenceRef],context:dict)->tuple[ParseCandidate,...] in src/datamate/preparation/normalization.py; select_parse(candidates:Sequence[ParseCandidate],binding:Binding)->ParseDecision. Define ParseCandidate/Decision in src/datamate/preparation/models.py; accepted value vs alternatives/unresolved is explicit.

- [ ] Write parametrized tests for 532.000đ,4,544tr,294,7703tr,8tr399,16 076,k-suffix,negative/parenthesized values,units,overflow,1900/1904 serials/fractional day,Vietnamese dates and mixed native/text columns. Expected values come from literal synthetic truth, not this parser.
- [ ] Add test_equal_arithmetic_does_not_resolve_scale and test_incomplete_source_date_uses_sheet_year_not_question_year. Assert ambiguous1.000 is unresolved without distinguishing evidence; source year/context never hardcodes2026; IDs preserve leading zeros and normalization collisions are reported.

```python
assert accepted_money_values == [
    Decimal("532000"),
    Decimal("4544000"),
    Decimal("294770300"),
    Decimal("8399000"),
    Decimal("16076"),
]
assert ambiguous_1000_was_committed is False
assert source_date_with_sheet_2025 == date(2025, 6, 3)
assert trimmed_leading_zero_id == "001"
assert negative_quantity == Decimal("-2")
```

- [ ] Run uv run --locked pytest tests/test_normalization.py tests/test_cleaning_operations.py -q; expect missing formats/ambiguity behavior.
- [ ] Extend existing parser functions with disjoint shape candidates and evidence-backed selection. Preserve signs, scale, units and time; never choose solely by highest parse score or source-total agreement. Unsupported precision stays raw with a blocking event.
- [ ] Verify boundary dates, no-midnight fabrication, text serial epoch and trim-collision cases.
- [ ] Commit: feat(normalization): parse mixed formats with explicit ambiguity and precision.

### Task 10: Structural rows, headers, merged ranges and summary anchors (P1/P3)

**Files:** Modify src/datamate/preparation/loaders.py, src/datamate/preparation/models.py, src/datamate/preparation/operations.py; extend tests/test_loaders.py, tests/test_excel_regressions.py and tests/test_cleaning_operations.py.
**Interfaces:** classify_structure(records:Sequence[RawRecord],table:StagedTable,bindings:SemanticBindings)->tuple[StructuralDecision,...]; StructuralDecision in src/datamate/preparation/models.py includes record_id,role,evidence_refs,anchor_scope. Loader exposes merged spans/epoch without executing formulas.

- [ ] Write test_repeated_header_footer_and_summary_are_not_sale_facts and test_merged_data_fill_stays_inside_declared_span. Assert a real-shaped note footer stays raw/structural, repeated header excluded with reason, a legitimate item containing 'tổng' is retained, and unknown summary arithmetic is unresolved rather than dropped.

```python
assert sale_fact_source_ids == {legitimate_item_id}
assert repeated_header_id in structural_disposition_ids
assert filled_source_ordinals == {8, 9}  # declared merged span, not row 10
assert unknown_anchor_status == "UNRESOLVED"
assert raw_record_count_after == raw_record_count_before
```

- [ ] Run uv run --locked pytest tests/test_excel_regressions.py tests/test_cleaning_operations.py -q; expect missing structure/span classification.
- [ ] Preserve original rows and add scoped structural decisions/anchors. Require source-role and applicable arithmetic evidence for summary recognition. Sparse/late columns and preamble handling remain faithful; no broad keyword deletion.
- [ ] Test data merges, header candidate ambiguity, missing formula caches and footer amounts with unknown scale.
- [ ] Commit: feat(preparation): classify structural rows with source-backed anchors.

### Task 11: Streaming group inheritance and scoped deduplication (P3)

**Files:** Modify src/datamate/preparation/normalization.py, src/datamate/preparation/operations.py, src/datamate/preparation/storage.py; extend tests/test_normalization.py and tests/test_cleaning_operations.py.
**Interfaces:** OperationState carries group boundary/first values and a DuckDB-backed seen-key store. Existing normalize_rows remains a compatibility wrapper over reviewed behavior; no global same-name/order-ID dedup.

- [ ] Write test_fill_and_dedup_are_invariant_for_batches_1_7_1000 and test_footer_or_file_boundary_resets_group. Assert identical facts/dispositions, first-group-value semantics, no fill across structural/file boundaries or uncached keys, and raw values retained.
- [ ] Add test_same_order_exact_duplicate_only: near duplicates, different orders and non-order identical ledger rows stay; equality includes relevant tagged source values/formulas, with rule/evidence scope recorded.

```python
assert outputs_for_batch_1 == outputs_for_batch_7 == outputs_for_batch_1000
assert next_group_inherited_previous_value is False
assert duplicate_source_ids == {same_order_exact_copy_id}
assert retained_source_ids >= {near_duplicate_id, other_order_id, ledger_copy_id}
assert disposition_count == raw_record_count
```

- [ ] Run uv run --locked pytest tests/test_normalization.py tests/test_cleaning_operations.py -q; expect missing spill/boundary behavior.
- [ ] Reuse safe existing inheritance/signature logic, move large seen state into DuckDB and verify group-key conflict/cardinality. Monthly-source union does not imply cross-file fill; cross-source same-order dedup needs an explicit identity/scope declaration.
- [ ] Assert full raw disposition counts with duplicate_of links and deterministic conflict events.
- [ ] Commit: feat(normalization): enforce bounded group and duplicate scope across batches.

### Task 12: Evidence-accepted entity and alias links (P3)

**Files:** Modify src/datamate/preparation/catalog.py, src/datamate/preparation/operations.py, src/datamate/preparation/models.py; extend tests/test_semantic_bindings.py and tests/test_cleaning_operations.py.
**Interfaces:** accept_entity_links(proposals:Sequence[Binding],bindings:SemanticBindings)->EntityResolution in src/datamate/preparation/catalog.py; EntityResolution in src/datamate/preparation/models.py contains accepted links/components,aliases,conflicts and unresolved candidates.

- [ ] Write test_price_name_and_last_token_similarity_never_prove_identity and test_new_union_rechecks_entire_component. Assert two codes with the same display name remain distinct; deaccenting/price history alone cannot merge; a transitive component contradiction rejects the union.

```python
assert same_display_name_distinct_code_entity_count == 2
assert price_only_merge_count == name_only_merge_count == last_token_only_merge_count == 0
assert conflicting_transitive_union_accepted is False
assert trusted_explicit_alias_link_accepted is True
assert original_names_after == original_names_before
```

- [ ] Run uv run --locked pytest tests/test_semantic_bindings.py -q; expect missing identity acceptance.
- [ ] Implement scoped trusted-code/explicit-map/verified-key link acceptance, preserve observed names and source aliases and record accepted evidence. Keep fuzzy candidates unresolved until proof exists; distinguish shared relation keys from equivalence keys.
- [ ] Test walk-in declarations, lookup ambiguity, source-code trim collisions and domain links without business-specific branches.
- [ ] Commit: feat(normalization): resolve entity links with collision-safe evidence.

### Task 13: Product-scoped unit conversion and consistency (P3)

**Files:** Modify src/datamate/preparation/catalog.py, src/datamate/preparation/operations.py, src/datamate/preparation/models.py; extend tests/test_semantic_bindings.py and tests/test_cleaning_operations.py.
**Interfaces:** resolve_units(bindings:SemanticBindings,*,product_id:str,unit:str)->UnitResolution; UnitResolution defines factor/base_unit/evidence or explicit unresolved/conflict. Product IDs are data keys, not hardcoded exceptions.

- [ ] Write test_multihop_conversion_is_product_scoped_and_cycle_checked and test_preconverted_source_quantity_is_preserved_and_reconciled. Assert box->blister->tablet factors compose, another product does not inherit the factor, incompatible cycles block the scope and raw/base quantities remain separate.

```python
assert product_a_box_factor == Decimal("100")  # 10 blisters x 10 tablets
assert product_b_box_factor is None
assert conflicting_cycle_accepted is False
assert (raw_quantity, base_quantity) == (Decimal("2"), Decimal("200"))
assert preconverted_mismatch_finding_count == 1
```

- [ ] Run uv run --locked pytest tests/test_semantic_bindings.py tests/test_cleaning_operations.py -q; expect missing graph/resolution logic.
- [ ] Build accepted conversion edges from declarations/reference data, retain Decimal factors and base units, verify independent source-preconverted values where available. Unknown edges block dependent quantities rather than inventing a factor1.
- [ ] Add24-can vs20-bottle synthetic negative and incompatible-unit cases.
- [ ] Run uv run --locked pytest tests/test_semantic_bindings.py tests/test_cleaning_operations.py -q; expect PASS.
- [ ] Commit: feat(normalization): add scoped unit graphs and quantity reconciliation.

### Task 14: Canonical factual grain, snapshots and one-to-many projections (P3)

**Files:** Create src/datamate/preparation/projection.py, tests/test_canonical_projection.py; modify src/datamate/preparation/storage.py, src/datamate/preparation/models.py.
**Interfaces:** project_facts(db:DuckDBPyConnection,plan:CleaningPlan,bindings:SemanticBindings)->ProjectionResult; publish_projections(db:DuckDBPyConnection,qualification:Qualification)->tuple[PublishedProjection,...] in src/datamate/preparation/projection.py. Trusted DDL/templates are owned by src/datamate/preparation/storage.py; projections produce facts/dispositions/lineage, not business conclusions. Publication is a preparation-time DDL action after audit, never a runtime/read-only connection action.

- [ ] Write test_repeated_invoice_fields_project_once_per_order and test_single_raw_record_can_create_two_device_facts. Assert two legitimate line items share one order header/shipping component, two IMEIs have two facts/two lineage edges but one source disposition; no permanent is_valid/revenue column.
- [ ] Write test_snapshot_validity_does_not_create_historical_events and test_source_without_order_ids_does_not_invent_them. Date-only capability remains explicit; unsupported entity/role stays staging/unresolved.

```python
assert (order_headers, order_lines, shipping_components) == (1, 2, 1)
assert (imei_facts, imei_lineage_edges, source_dispositions) == (2, 2, 1)
assert invented_historical_event_count == invented_order_id_count == 0
assert "is_valid" not in published_columns
assert blocked_amount_column_published is False
```

- [ ] Run uv run --locked pytest tests/test_canonical_projection.py -q; expect missing projections.
- [ ] Materialize typed common factual archetypes from accepted bindings; preserve reported amounts/components, event times and snapshot_at. Add typed child/domain extension facts only for supported declarations. Common source-role union preserves overlap/provenance and raw IDs.
- [ ] Test no lossy Decimal/quantity schema and no blanket removal of returned/warranty/fee facts.
- [ ] Commit: feat(preparation): materialize typed canonical facts with grain and lineage.

### Task 15: Typed active-calendar and temporal declarations (P3)

**Files:** Modify src/datamate/preparation/projection.py, src/datamate/preparation/catalog.py, src/datamate/preparation/storage.py; extend tests/test_canonical_projection.py, tests/test_periods.py.
**Interfaces:** calendar_interval(db,rule:MeasurePolicy,*,year:int)->tuple[date,date] uses only accepted active-calendar definitions. Expose calendar_days as a qualified factual projection; ordinary period helper remains hash-guarded until a compatible declaration is proven.

- [ ] Write test_calendar_types_discovery_and_requested_year_interval and test_question_month_vs_source_sheet_year_are_distinct. Synthetic 2025/2026 calendars produce their own Tet bounds; missing rows/ambiguous leap conditions report unresolved. No fixed 2026 tag or outside calendar lookup.

```python
assert synthetic_2025_tet_bounds == (date(2025, 1, 29), date(2025, 2, 4))
assert synthetic_2026_tet_bounds == (date(2026, 2, 17), date(2026, 2, 23))
assert (question_year_from_as_of, incomplete_source_year_from_sheet) == (2026, 2025)
assert missing_calendar_row_was_invented is False
assert outside_calendar_request_count == 0
```

- [ ] Run uv run --locked pytest tests/test_periods.py tests/test_canonical_projection.py -q; expect missing typed interval API.
- [ ] Add calendar numeric/bool/date typing, uniqueness/range/continuity checks for calendar itself (not sales dates), date-only/timezone capabilities and source-year declarations. Preserve active source fingerprints.
- [ ] Re-run tests for previous-month clamping/inclusive bounds and a different split book.
- [ ] Commit: feat(preparation): qualify active calendar and temporal context.

### Task 16: Independent intrinsic audits and applicability (P4)

**Files:** Create src/datamate/preparation/audit.py, tests/test_intrinsic_audit.py; modify src/datamate/preparation/storage.py and src/datamate/preparation/models.py only for shared audit types.
**Interfaces:** audit_candidate(db,spec:AuditSpec)->AuditReport; qualify_scopes(report:AuditReport,dependencies:DependencyGraph)->Qualification. Audit spec uses supported check IDs/version/applicability/evidence, never model-provided pass scores.

- [ ] Write test_dispositions_are_exhaustive_disjoint_and_fact_counts_separate, test_self_consistent_wrong_scale_fails_independent_evidence and test_na_requires_applicability_proof. Missing/duplicate dispositions fail; valid1:N passes; own-parser totals cannot prove scale.
- [ ] Add test_quarantine_does_not_shrink_declared_coverage_or_qualify_partial_total, test_snapshot_comparison_requires_same_effective_date and test_processing_uncertainty_is_not_source_absence.

```python
assert raw_record_count == exhaustive_disposition_count
assert self_consistent_wrong_scale_status == "FAIL"
assert unsupported_na_status != "NOT_APPLICABLE"
assert declared_coverage_after_quarantine == declared_coverage_before_quarantine
assert revenue_scope_status != "READY"
assert processing_error_reported_as_source_absence is False
```

- [ ] Run uv run --locked pytest tests/test_intrinsic_audit.py -q; expect missing audit behavior.
- [ ] Implement independent source/fact checks, explicit PASS/FAIL/N/A/UNRESOLVED, bounded counterexamples, fatal destructive violations and dependency closure. Do not import candidate parser/identity acceptance as the oracle; checks with unknown unit/time/scope cannot be marked PASS/N/A automatically.
- [ ] Verify unresolved amount blocks revenue-dependent scope but leaves unrelated identifiers usable; near/non-order duplicate removal is fatal even if a total improves.
- [ ] Commit: feat(preparation): add independent intrinsic audit and scoped qualification.

### Task 17: Versioned orchestration, cache migration and publication (P4)

**Files:** Modify src/datamate/preparation/pipeline.py, src/datamate/preparation/__main__.py, src/datamate/preparation/storage.py; extend tests/test_preparation.py and tests/test_preparation_cli.py.
**Interfaces:** build_candidate(db:DuckDBPyConnection,plan:CleaningPlan,bindings:SemanticBindings)->ProjectionResult; prepare_business(split_root:Path,business_id:str,cache_root:Path,*,split_id:str,plans:dict[str,TablePlan]|None=None,policy:PreparationPolicy|None=None)->PreparedBusiness keeps the existing positional/keyword boundary and adds optional policy. Returned catalog includes artifact_version=2,qualified projections,capabilities and evidence. Task 14 publish_projections runs after successful scoped audit and before atomic artifact publication.

- [ ] Write test_v2_rejects_legacy_cache_and_binds_compiler_validator_versions, test_failed_audit_or_source_drift_never_publishes and test_replay_matches_logical_facts_not_db_bytes. Assert source ABA, corrupt cache, stale policy and code outside preparation invalidate; failed candidates never expose complete manifests.

```python
assert published_catalog_version == 2
assert legacy_cache_accepted_as_v2 is False
assert failed_build_has_complete_manifest is False
assert changed_validator_fingerprint != original_fingerprint
assert replay_facts_and_lineage == original_facts_and_lineage
assert registry_http_attempts == 0
```

- [ ] Run uv run --locked pytest tests/test_preparation.py tests/test_preparation_cli.py -q; expect new orchestration/version failures.
- [ ] Replace _ingest_table interleaving with stage->profile->bind->plan->project->audit->publish. Preserve source snapshot/checksum/exclusive-lock/atomic-directory behavior. Default registry mode never creates an HTTP client. Replay requires frozen accepted plans and matching dependencies.
- [ ] Publish qualification embedded in DB/catalog and complete manifests only. Keep old caches untouched; explicit legacy fixtures can still test I/O, but cannot claim v2 readiness. Preserve the --plans compatibility adapter with validated source evidence.
- [ ] Run focused and full pytest/Ruff; expect PASS.
- [ ] Commit: refactor(preparation): publish only versioned capability-qualified artifacts.

### Task 18: Campaign-wide atomic API budget (P5 prerequisite; can land earlier)

**Files:** Modify src/datamate/inference/budget.py; extend tests/test_budget.py, tests/test_inference_client.py.
**Interfaces:** CampaignPolicy(campaign_id,total_limit=120,phase_limits={smoke:20,development:60,final:40}); register_campaign(path:Path,policy:CampaignPolicy)->None. Extend RequestBudget(...,campaign_id:str|None=None,phase:str|None=None), preserving existing noncampaign callers.

- [ ] Write test_campaign_cap_is_atomic_across_run_ids_and_phases, test_reopening_cannot_enlarge_campaign and test_old_attempts_still_count_toward_utc_day. Concurrent acquisition cannot exceed120; per-phase limits also hold; historical accounting is not reset by migration.

```python
assert total_acquired_attempts <= 120
assert phase_acquired_attempts["smoke"] <= 20
assert phase_acquired_attempts["development"] <= 60
assert phase_acquired_attempts["final"] <= 40
assert utc_day_attempts_after_migration == historical_attempts + new_attempts
assert cap_change_rejected is True
```

- [ ] Run uv run --locked pytest tests/test_budget.py -q; expect missing campaign methods/schema.
- [ ] Add transactional campaign/phase policy and attempt linkage to the same SQLite ledger. acquire atomically checks RPM/day/run/campaign/phase and records one actual attempt; reopening same campaign with changed caps fails. Do not label pre-acquire context rejection as a sent attempt.
- [ ] Test429/transient retries, format repairs, reference NIM calls and restart/run-ID changes against shared limits using MockTransport; max_retries=1 for this campaign, SDK retries0. Snapshot reports actual usage/completeness and current UTC remaining allowance, not old776 arithmetic.
- [ ] Run budget/client tests and full suite; expect PASS.
- [ ] Commit: feat(inference): enforce shared normalization campaign limits.

### Task 19: Evaluator runner and baseline capture before improvements (P6 infrastructure)

**Files:** Create src/datamate/evaluation/normalization_campaign.py, tests/test_normalization_campaign.py; extend src/datamate/evaluation/normalization.py.
**Interfaces:** score_normalization(results:CandidateResultSet,suite:ReferenceSuite)->NormalizationMetrics; run_development_baseline(policy:PilotPolicy,suite:ReferenceSuite,prepare:Callable)->CampaignRunManifest; freeze_candidate(manifest:CampaignRunManifest)->CandidateFreeze; reserve_checkpoint(freeze:CandidateFreeze,ledger_path:Path)->CheckpointReservation; run_frozen_verification(freeze:CandidateFreeze,reservation:CheckpointReservation,suite:ReferenceSuite,prepare:Callable)->CampaignRunManifest. Define these evaluator-only records in src/datamate/evaluation/normalization_campaign.py. Baseline/final use distinct phase cache roots under state/prepared and must execute complete CPU audits; a ready-cache hit is not a measured preparation pass. Record cold/replay mode and accepted-plan reuse explicitly; immutable caches are never overwritten.

- [ ] Write test_metrics_keep_abstentions_and_quarantine_in_denominator, test_verified_refusal_is_not_positive_coverage and test_baseline_final_share_80_cases. Zero commitments gives undefined correctness; model agreement has a separate label; fewer than ten verified determinate/business prevents PASS.
- [ ] Write test_checkpoint_is_atomic_single_use_and_has_no_pretuning_diagnostics, test_changed_sources_or_refs_abort_before_execution and test_new_run_or_candidate_id_cannot_relabel_exposed_checkpoint. Reservation occurs immediately before execution; a started failed checkpoint stays consumed. Consumption binds the pilot/source/checkpoint identity, not only a new candidate hash. Later exposed-source runs are marked regression, never a fresh locked checkpoint. Report 20 checkpoint cases separately from 80 dev and do not disclose their labels to learner calls.

```python
assert verified_determinate_denominator == 12  # includes 2 abstained/quarantined cases
assert verified_determinate_successes == 10
assert zero_commitment_correctness is None
assert baseline_case_ids == final_case_ids and len(final_case_ids) == 80
assert successful_checkpoint_reservations == 1
assert failed_started_checkpoint_is_consumed is True
```

- [ ] Run uv run --locked pytest tests/test_normalization_campaign.py tests/test_normalization_references.py -q; expect missing runner/metrics.
- [ ] Implement reproducible manifests binding source/reference/code/profile/prompt/operation/audit versions, measured resource/API outputs, failed artifact persistence and no automatic repair of locked results. Add psutil/lockfile for process-tree telemetry and approved safety watchdogs; distinguish measured/watchdog from kernel limits.
- [ ] Mock prepare/client in unit tests; no real full-data/model run here.
- [ ] Commit: feat(evaluation): add normalization baseline and immutable checkpoint runner.

### Task 20: Bounded proposal-only SLM fallback and rollback (P5)

**Files:** Create src/datamate/preparation/adaptive.py, tests/test_adaptive_normalization.py; modify src/datamate/preparation/pipeline.py, src/datamate/inference/config.py and src/datamate/inference/client.py only for reused interfaces where required.
**Interfaces:** RecoveryPolicy(max_rounds=2,logical_requests_per_round=2,transport_retries=1); async recover_issue(issue:NormalizationIssue,evidence:EvidencePacket,proposer:Callable,executor:Callable,auditor:Callable,policy:RecoveryPolicy)->RecoveryResult. Define production NormalizationIssue/EvidencePacket/RecoveryResult in src/datamate/preparation/models.py (no evaluator labels; evaluator reference packets use a distinct ReferenceEvidencePacket name).

- [ ] Write test_two_round_two_request_one_retry_bound, test_same_hypothesis_stops_without_extra_call, test_unsafe_plan_cannot_mutate_db and test_destructive_failure_rolls_back. Worst case<=8HTTP attempts/issue and shared campaign stops earlier; format repair consumes the second logical request.
- [ ] Add test_oversized_evidence_requires_explicit_reselection and test_reference_sentinel_is_absent_from_serialized_request. Context refusal sends zero attempts; omission tracking must show removed evidence rather than silently truncating a document.

```python
assert proposal_rounds <= 2
assert logical_requests <= 4
assert http_attempts <= 8
assert context_rejected_http_attempts == 0
assert rejected_candidate_state_after == candidate_state_before
assert reference_sentinel not in serialized_candidate_request
assert fallback_model_changes == fallback_mode_changes == 0
```

- [ ] Run uv run --locked pytest tests/test_adaptive_normalization.py -q; expect missing fallback contract.
- [ ] Reuse inference_model/ModelProfile and PydanticAI typed output functions to propose CleaningPlan steps. Existing operators first; validate source/version/evidence before execution. Apply in a candidate transaction and independently audit affected dependencies before commit. Persist accepted/rejected plan hashes and errors.
- [ ] Test offline registry/replay opens no network, no profile/mode fallback, budget exhaustion preserves PARTIAL scopes, and unqualified microcode cannot reach exec.
- [ ] Commit: feat(preparation): add budgeted evidence-guided adaptive plans.

### Task 21: Qualified isolated micro-transform backend (P5)

**Files:** Create src/datamate/preparation/sandbox.py, requirements/Dockerfile.normalization-sandbox, tests/test_normalization_sandbox.py; modify src/datamate/preparation/adaptive.py, pyproject.toml pytest marker registration.
**Interfaces:** SandboxSpec(image_digest,confinement,limits), SandboxQualification(spec_digest,status,evidence_hashes), MicroTransformInput(code_hash,code,batch,schema,source_ids), MicroTransformOutput(records,events,lineage). qualify_backend(spec:SandboxSpec)->SandboxQualification; execute_micro_transform(request,qualification)->MicroTransformOutput. No model client/secrets in this module/container.

- [ ] Write unit tests test_native_exec_fails_closed, test_qualification_binds_image_and_limits and test_oversized_or_untyped_output_is_rejected. Mocked argv only proves command construction, never isolation status.

```python
assert native_generated_code_executions == 0
assert qualified_from_mocked_argv_only is False
assert qualification_after_limit_or_image_change != "QUALIFIED"
assert qualification_when_oci_tests_skipped != "QUALIFIED"
assert forbidden_host_mounts == set()
```

- [ ] Run uv run --locked pytest tests/test_normalization_sandbox.py -m 'not oci' -q; expect missing backend.
- [ ] Implement pinned Linux OCI runner with no network/read-write-root/privileges/host data/reference/secrets/socket access, nonroot/no-new-privileges/cap-drop/seccomp and approved CPU/RAM/swap/PID/time/tmpfs caps. Pass bounded serialized input/output; no pickle, shell or emitted SQL in host. AST screening supplements real isolation.
- [ ] Add marked oci tests that actually attempt file/network access, memory/process/time exhaustion, tmp/output overflow and child survival. Existing provided/pinned image is required; detect absent daemon/image/enforcement, report UNQUALIFIED/PARTIAL and continue safe native tasks. Do not automatically pull large images/start paid jobs. If a build is needed, require an explicitly selected digest-qualified base and preserve its provenance.
- [ ] Run uv run --locked pytest tests/test_normalization_sandbox.py -m oci -q only in provided supported environment. A skip is not PASS; save backend qualification under state/runs/normalization/security. Record environmental qualification separately with actual results.
- [ ] Commit: feat(preparation): isolate generated micro-transform execution.

### Task 22: Minimal qualified runtime adapter and shared CLI (P6)

**Files:** Modify src/datamate/runtime/tools.py, src/datamate/runtime/batch.py, src/datamate/preparation/__main__.py, src/datamate/__main__.py; extend tests/test_runtime_tools.py, tests/test_runtime_batch.py, tests/test_preparation_cli.py and tests/test_normalization_campaign.py.
**Interfaces:** StructuredTools(...,allow_legacy_fixture:bool=False) defaults to qualification enforcement; production batch never enables the fixture escape. Catalog v2 contains trusted flat view IDs, allowed columns, complete factual scope and provenance descriptors. CLI dispatch imports evaluator-only normalization runner lazily.

- [ ] Write test_runtime_denies_legacy_private_and_blocked_scopes, test_ready_flat_projection_is_discoverable and test_star_cannot_expose_blocked_amount. Add test_partial_total_does_not_become_complete_after_view_filter. Existing toy fixtures must explicitly opt into legacy-fixture mode or construct a valid synthetic v2 catalog; no production CLI bypass flag.

```python
assert discovered_view_ids == qualified_flat_view_ids
assert {"staging.raw_rows", "meta.bindings"}.isdisjoint(queryable_relation_ids)
assert "blocked_amount" not in star_result_columns
assert surviving_row_sum_accepted_as_complete is False
assert runtime_ddl_attempts == 0
```

- [ ] Run uv run --locked pytest tests/test_runtime_tools.py tests/test_runtime_batch.py -q; expect new catalog/qualification assertions failing.
- [ ] Consume Task 14/17's already-published trusted flat projections, enforce v2 catalog scope and declared provenance, retain AST physical-source/function restrictions. Runtime opens artifacts read-only and creates no views. Change batch only to pass the new qualified artifact; do not redesign answering prompts/tools/planning. Registry/replay CLI uses existing prepare_business entry point and explicit policy; model calls require --allow-model-calls with a profile/campaign.
- [ ] Add shared evaluator commands normalization-eval with actions select/references/baseline/freeze/verify/report; no action reads QA questions/answers and no preparation import reaches evaluator. Resource/API flags bind frozen manifests; --offline denies all HTTP.
- [ ] Run runtime inputs/isolation/security regressions and full suite.
- [ ] Commit: feat(runtime): consume qualified normalization artifacts without widening SQL access.

### Task 23: Complete 44-case synthetic matrix and frozen references (P6 prerequisite)

**Files:** Modify tests/normalization_fixtures.py, tests/fixtures/normalization/catalog_cases.json and owning test modules; extend tests/test_normalization_references.py. Update config/normalization/pilot.json only by explicit versioning, never after outcome-driven case selection.
**Interfaces:** Each catalog item maps to executed positive/counterexample tests and N/M/Q owner. Evaluator exports a ReferenceSuite digest and verified/provisional/unresolved counts, no labels in candidate manifests.

- [ ] Write test_all_44_cases_have_executed_positive_and_counterexample_coverage: assert IDs 1..44 map to at least 88 executable synthetic cases, not files merely named for them. Add interaction tests from Review Focus; Q ownership tests metadata/policy/fixed contract queries, not another QA agent.

```python
assert covered_catalog_ids == set(range(1, 45))
assert all({"positive", "counterexample"} <= executed_variants[i] for i in range(1, 45))
assert len(frozen_real_case_ids) == 100
assert set(verified_determinate_counts_by_business) == {
    "biz_124",
    "biz_106",
    "biz_120",
    "biz_122",
    "biz_123",
}
assert all(count >= 10 for count in verified_determinate_counts_by_business.values())
assert checkpoint_labels_in_candidate_context == 0
```

- [ ] Run uv run --locked pytest tests/test_normalization_references.py tests/test_excel_regressions.py tests/test_normalization.py tests/test_cleaning_operations.py tests/test_semantic_bindings.py tests/test_canonical_projection.py tests/test_periods.py tests/test_intrinsic_audit.py -q; expect missing coverage assertions until completed.
- [ ] Add minimal missing production support in its owning component using the corresponding task's red/green process, not business-specific corrections.
- [ ] Instantiate case IDs after profiles and before learner outputs; use a distinct reviewer route/profile to propose references blind to candidate outputs, <=5 initial +5 adjudication jobs. Start reviewers with no inherited conversation/candidate history and supply only the frozen reference evidence packet. Store source/prompt/response/proof provenance and usage UNKNOWN when unavailable. Do not let reviewer set VERIFIED directly.
- [ ] Run evaluator proof checks; retain every original unresolved case. Freeze 100 cases and reference versions; require>=10 verified determinate/business or mark real qualification PARTIAL. Never replace hard cases, consume public/QA answers or share checkpoint expectations with learners.
- [ ] Run full pytest/Ruff and dependency security audit. Commit only synthetic fixtures/code/coverage matrix; store private source/reference artifacts under ignored state.
- [ ] Commit: test(normalization): complete independent catalog and reference coverage.

### Task 24: Controlled pilot baseline, improvement budget and final evidence (P6)

**Files:** Modify README.md and docs/engineering/milestone1_report.md for final evidence; update the 44-case status matrix. Generated runs stay in state/runs/normalization and state/prepared; no real artifacts committed.
**Interfaces:** Consume Tasks 17–23 only. One campaign ledger identity enforces120 total HTTP attempts,20/60/40 phase ceilings, current UTC/day/RPM. Runner produces immutable baseline/freeze/final/checkpoint manifests and reports.

- [ ] Preflight source/reference/policy hashes, model profiles/credentials without printing secrets, actual ledger remaining allowance, CPU/disk ceilings and required sandbox qualification. Confirm implementation branch, clean relevant tracked diff and explicit real/reference independence. No model download or unsupported-mode fallback.
- [ ] Run small deterministic fixtures, then explicit smoke <=20 HTTP attempts demonstrating accepted/rejected proposals across >=3 operation families; if unavailable/failed, persist the gap and do not expand blind. Capture actual requests/provider usage completeness rather than question counts.
- [ ] Run one full four-business development baseline of the first working v2 integrated normalizer with frozen 80 cases/full CPU audits; the old QA run and legacy caches are not this baseline. Development phase <=60 HTTP attempts covers baseline and at most 2 major evidence-driven improvement rounds combined. Diagnose categories before changing code. Target fixtures/affected slices after edits; stop unchanged hypotheses/no meaningful improvement/destructive violations/caps. Never inspect locked labels or error breakdown for tuning.
- [ ] Freeze code/prompts/profiles/operators/validators/source/reference versions. Reserve checkpoint immediately before execution and run one final/checkpoint verification within 40 HTTP attempts: same 80dev cases plus separate 20 SQLite checkpoint, full required CPU audit across five. Started checkpoint failure consumes its reservation; no automatic repair/rerun.
- [ ] Score per-business verified determinate/negative cases, preserve quarantined/unresolved denominators, assess false merge/drop/scale/lineage failures and READY dependency scopes, report baseline/final on comparable 80 dev cases and checkpoint separately. No population100% or globally unseen claim.
- [ ] Run final deterministic/full tests, Ruff, dependency audit and staged secret/diff review. Record actual sandbox/platform status, RAM/time/disk/API/token completeness, retained/reverted improvements and commits. PASS only if every mandatory gate has real evidence; otherwise PARTIAL/BLOCKED component reasons, never extend budget automatically.
- [ ] Commit only final docs/status evidence: docs(normalization): record pilot qualification and remaining gates. Stop; no main merge/push/history rewrite or full QA evaluation.

## Self-review and execution handoff

- [x] Main checked every design section: authority/corrections and pilot in Tasks 1/4; storage/profiles/semantics/operations in Tasks 2–15; audit/qualification/cache in Tasks 16–17; budgets/adaptive/sandbox in Tasks 18/20/21; references/qualification/report in Tasks 19/22–24. Missing SQL/A10 deliverables become tested storage/audit/layout contracts, not assumed external files.
- [x] Main checked type ownership and signatures, including ReferenceEvidencePacket separation, BatchResult ownership, the complete prepare_business signature and preparation-time publish_projections; fixture helpers stay in tests and references in evaluation.
- [x] Main assigned all five Review Focus conditions to named tests in Tasks 2/4/9/11/14/16/18/19/21/22; Task24 reports real outcomes. Corrected checkpoint identity reuse, lexicographic budget assertions and cache-hit measurement traps during this review.
- [x] Main checked proportion and dependency order: 24 independently testable units, 23 short assertion fragments, exact red commands and a shared green/full-suite convention; no program bodies, invented cleanup or additional architecture review loop.

Preserved execution preference: main owns architecture/security/integration/Git;
selective configured development subagents handle independent bounded work, using
isolated worktrees for parallel edits. Do not ask to choose this method again.
After the user reviews this plan, use subagent-driven-development for delegated units
and executing-plans for main-owned inline work as appropriate to the preserved method.
No code implementation, model calls or full-data pilot was performed while writing it.
