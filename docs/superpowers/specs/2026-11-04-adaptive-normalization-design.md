# New Milestone 1 — Evidence-Guided Adaptive Normalization

Status: DRAFT FOR USER APPROVAL. Date: 2026-11-04.
This is the consolidated design and execution roadmap. It is not the approved
file-by-file implementation plan. No product code has been changed by this work.

## 1. Intent and accepted decisions

The user selected incremental refactoring of preparation and evaluation using a
small verified reference set, synthetic truth and full CPU audit on five train
businesses. The goal is evidence-backed, query-ready data with explicit uncertainty
before further QA optimization. Preserve working components and the shared codebase.

Requirements: CSV/XLSX/SQLite; immutable raw identity/provenance; full deterministic
CPU profiling with bounded model inputs; active rulebook/README compilation;
Pandas-first flexible operations; bounded SLM fallback; independent validation;
scoped quarantine; versioned query-time measures; common inference profiles and
offline-capable execution; atomic Conventional Commits. No embedding, reranker,
vector database or multi-agent solving runtime. Development reviewers are separate.

Planner decisions proposed for approval: exact reference quotas/checkpoint assignment,
campaign budget, module boundaries and Linux-only initial generated-code qualification.
These were not separately promised by the user. Changes after freezing need a new
version and comparability statement.

Non-goals: rewrite QA; tune from QA answers; label every real record; create a full
teacher-generated golden DB; guarantee arbitrary future semantics without extensions;
download large models; start paid cloud jobs; clean/delete old state; merge, push or
rewrite history. Native development does not require Docker. Generated-code execution
has a separately qualified backend; absence makes that requirement PARTIAL.

## 2. Source authority and required specification corrections

Read together: docs/normalization/NORMALIZATION_SPEC.md, ERROR_CATALOG.md, the
earlier master request, active split rulebook/calendar and each business README.
Official constraints and active source semantics override speculative examples.
Questions, answers, reference aliases and evaluator artifacts never enter preparation.

Existing source-only audits under state/audits include the direction review, Luna
first-ten-business audit and Terra public-subset audit. The externally asserted
237/237 and business-specific totals are not verified results of this repository.

P0 must resolve these normative conflicts before production changes:

- Question months/quarters without years use as_of_date and the active book;
  incomplete SOURCE dates may use their sheet year. No fixed2026/TET window.
- Dedup requires evidenced same-order scope and relevant cell equality. Non-order
  ledgers retain repeated rows. Group inheritance respects source/group boundaries.
- Return, warranty and service-fee observations remain facts. Measures choose their
  predicates; do not materialize a universal is_valid or globally filtered dataset.
- Deaccented names, matching prices/date changes and staff-name tokens generate
  candidates, not sufficient identity proof. Check scope/cardinality/collisions.
- Parse/arithmetic scores do not establish scale. Keep equally valid alternatives
  ambiguous; an anchor decoded by the same wrong grammar is not independent proof.
- Reconcile raw dispositions separately from 1:N canonical fact counts. Every audit
  has applicability, units, rounding, temporal scope and explicit N/A evidence.
- Separate source-declared coverage, observed dates, event history and snapshot
  validity. Quarantine must not shrink declared coverage or manufacture zero totals.
- Unmapped clauses block dependency-relevant scopes rather than whole splits by
  default. Unknown raw records do not become clean entities merely through JSON.
- Rule/README changes may require reinterpreting facts; only demonstrably independent
  measure-only changes reuse clean data. Compiler/model/validator versions also matter.
- Keep precision lossless within supported bounds; no silent fixed-scale truncation.
  Manual development review is allowed; final inference cannot depend on a human.

Referenced schema.sql, validation.sql, A10 merged-cell details and the detailed
cleaning document were not found. Define/test these contracts; do not assume delivery.

## 3. Alternatives and chosen refactor boundary

A. Incremental refactor: retain discovery/loaders, verified source snapshots, raw
tagging, hashes, proven operations and atomic publication. Replace interleaved
load/normalize/profile with explicit stages. One production path; migration must
address current one-raw-to-one-normalized coupling.

B. Parallel normalizer: retain raw I/O but maintain another preparation/artifact
path before migration. Separation is easier, but lifecycle/verification/migration
are duplicated. The user selected A.

Keep prepare_business, PreparedBusiness and the shared CLI as caller boundaries.
Version the artifact and add qualification/capability contracts. QA changes are
limited to consuming qualified projections, exposing declarations and preserving
SQL restrictions; no new natural-language QA planner.

## 4. Pilot and immutable checkpoint

Initial full-data runs are limited to these TRAIN businesses:

| Business | Sources | Existing catalog input rows | Role |
| --- | --- | ---: | --- |
| biz_124 | 12 Excel files /12 tables | 305893 | development |
| biz_106 | 10 Excel files /10 tables | 289353 | development |
| biz_120 | 10 Excel files /27 tables | 240702 | development |
| biz_122 | 9 Excel files /26 tables | 214306 | development |
| biz_123 | 1 SQLite file /17 tables | 268911 | locked checkpoint |

Total1319165 catalogued input records, not certified clean facts: counts can include
duplicates/footer rows. Selection used aggregate Excel rows per business, not the
four largest individual workbooks. Keep all related files and documents. Exercise
CSV with synthetic fixtures. No implicit full30-business normalization/evaluation.

P0 freezes source/document hashes, inventory, row-location semantics, selection and
versions. IDs select evaluation inputs only, never production corrections. Freeze
case selectors before candidate results and keep hard cases rather than replacing them.

Four Excel businesses support development; biz_123 is this milestone's checkpoint,
not pristine unseen data (its existence/size metadata was already inspected).
Develop generic SQLite behavior using synthetic cases. Reference/evaluator workers
can read checkpoint sources to prepare hidden expectations. The learner sees sources
as at inference, never expected labels or checkpoint diagnostics during development.
Run the locked candidate once after freezing. Failure is reported, not tuned away.
Later repairs require a new version and the exposed checkpoint becomes regression
evidence; do not select another convenient business and call it the original holdout.

Previously inspected public data is compatibility evidence only; no public answers
or answer-derived totals become training/tuning targets. Expansion beyond the five
requires a justified next checkpoint, not automatic background work.

## 5. Pipeline and typed contracts

Flow: discover/read complete documents -> snapshot/stage raw -> full CPU profiles ->
compile clauses and bind roles/grain/relationships -> evidence-bound plan -> execute
candidate transformations -> independent audit/recovery -> qualified publication.
Read rules early; bind physical fields only after schemas/profiles are available.
Raw ingestion remains usable with no SLM. No row-per-model loop.

| Contract | Required content / invariant |
| --- | --- |
| SourceDocument/Table | hashes, split/business scope, original locations, version, headers/columns, native keys, workbook epoch/formats/merged ranges |
| RawRecord | source/table/ordinal/parser-bound identity, tagged decoded values, formulas/formats and native record key; original source remains authoritative |
| Profile | full counts/types/nulls/blanks, explicit exact/approximate cardinality, shape/format frequencies, bounded representative/rare samples, parse/relation candidates |
| RuleClause/Declaration | exact source quote/lines/hash, normative scope/precedence, MAPPED/UNMAPPED/CONFLICT/NOT_APPLICABLE and dependencies |
| Binding | role/grain/status/entity/unit/measure/capability kind, subjects, proposed interpretation, source references, distinguishing evidence, acceptance rule/version |
| CleaningPlan | typed registered steps/parameters, source/profile/rule/operator versions, preconditions, preservation constraints, scopes and rollback policy |
| RawDisposition | exactly one per source record: retained, structural, duplicate, quarantined or explicitly unsupported; evidence and zero/many output facts |
| Lineage | fact/field to source record/cell and transformation chain; 1:N and N:1 are explicit |
| AuditFinding | check/version/applicability, units/rounding/effective date/scope, PASS/FAIL/NOT_APPLICABLE/UNRESOLVED and bounded counterexamples |
| Qualification | READY/PARTIAL/BLOCKED per scope, declared/observed coverage, blocked fields/joins/measures and dependency reasons |
| TransformationAttempt | proposed/accepted/rejected operations, logical vs actual HTTP counts, identities/hashes, resources and rollback chain |

Acceptance is operation-specific. Trusted keys/declarations need scope and conflict
checks; graph merges revalidate the entire resulting component. Scores/confidence
are diagnostic. Source absence may support unanswerable under the active book;
unparsed/unmapped data means processing uncertainty/PARTIAL/abstain, not source absence.

## 6. Storage, profiling and operations

Keep the existing per-business DuckDB artifact with three namespaces:

- staging: immutable tagged raw_rows, source-table/column identity, original structural
  rows and source keys. Never destructively delete originals.
- meta: documents, schemas/profiles, clauses/declarations, typed bindings, operation
  history, dispositions, lineage, audit and qualification. Registry kinds can share
  controlled metadata tables with typed views; no module/table for every noun.
- clean: typed factual orders/lines, entities, payments/snapshots, imports/lines,
  inventory/lots, price history and calendar. Use typed extensions for devices,
  services/installments as necessary; source absence has explicit capability/N/A.
  JSON suits raw payload/metadata/uncommon attributes, not all query facts.

Repeated order fields are checked for consistency and represented once at order
grain. Snapshots retain snapshot_at and source semantics; payments keep event times.
Keep reported money/components separate. Never reconstruct unrecorded history or
overwrite observations merely because an equation fits.

Use lossless Decimal and evidence/profile-driven DuckDB precision/scale, up to38
digits. Unsupported precision remains raw with blocked affected values. Keep raw
quantity/unit, base quantity and conversion proof. Date-only is not midnight time.
Respect Excel epoch/fractional serials and keep unknown timezone explicitly unknown.

Pandas is a bounded-batch operation interface, not replacement I/O. Retain proven
CSV/openpyxl/sqlite3 and Polars profiling. Proposed new direct dependencies: Pandas,
rank_bm25, psutil (batch transforms, lexical index, resource telemetry). Validate and
lock/audit compatible versions during implementation, not this design stage.

Default batch1000. Test Decimal/null/ID/leading-zero/date/time/bytes roundtrips with
no implicit float/NaN coercion. Carry group state across chunks; reset at evidenced
boundaries. Spill large dedup state to DuckDB, not an unbounded Python dictionary.

Reuse correct operations, then add structural-row/merged-range classification,
mixed-pattern value parsers, collision-checked IDs, scoped group fill/exact dedup,
grain-aware projections, 1:N extraction and accepted status/entity/unit mappings.
An unknown pattern or equally valid scale stays unresolved. Transaction dates need
not form a gap-free calendar. Footers cannot bridge groups during fill-forward.

BM25/RapidFuzz retrieve clauses and candidates. Version Unicode/token preprocessing
and apply it consistently; retrieval scores are not proof. Track every source clause
and required dependencies, including unresolved ones. Top-k is not complete rule reading.

## 7. Rules, measures and invalidation

Official constraints and active rulebook govern; explicit question criteria override
defaults where the book permits; industry clauses override relevant general defaults.
README explains business schema, grain, units and snapshots under higher authority.
Measured evidence can resolve supported hypotheses; SLM cannot overrule declarations.

Compile every clause to a supported typed declaration or explicit unresolved status.
First-use unknown language may require bounded model assistance. Store exact source
spans, quote hashes and conflicts. MAPPED does not by itself mean semantically correct.

Measure declarations contain components, grain, status/payment predicates, time axis,
units, applicability and dependencies. Use a restricted typed predicate/aggregate
vocabulary and trusted renderer for contract queries, not a new QA planner. Do not
accept arbitrary model SQL as a trusted measure. Unknown expressions stay unmapped.

Calendar is typed and discoverable. Derive periods from active definitions/records.
Do not activate the current train-hash period helper for a different rulebook silently.

Fingerprints include schemas, operators, compiler/prompt/model policy and validators,
including semantic code outside preparation. Source changes that affect parsing,
identity/fill/dedup/status require rebuilding affected facts. Reuse for measure-only
changes only after dependency independence is demonstrated.

## 8. Independent audit, quarantine and compatibility

Audits read source evidence and candidate facts independently: disposition/lineage,
type/null/parse changes, group consistency, dedup scope, identity collisions, conversion
cycles/scope, keys/join coverage, arithmetic anchors, temporal/snapshot applicability
and rule coverage. Anchors also need independently resolved scale/scope/time.

Raw count equals exhaustive disjoint raw dispositions. Fact counts are separate and
supported by lineage/conservation. N/A requires source/capability evidence; it is not
a way to hide failed checks. Quarantining all failures does not make a complete total.

Dependency closure includes joins, entity groups, fields, periods and measures. Initially
use conservative whole-field/table scope where finer exclusions cannot be proved.
Other qualified fields may remain usable. Never expose only surviving monetary rows
as a complete total. Processing uncertainty does not become zero or source absence.

Preserve SQL's denial of arbitrary schema-qualified names: publish trusted flat
projections with safe generated IDs and whitelist only qualified factual scopes.
Private staging/meta stays outside solving SQL. Old artifacts remain explicitly
legacy/unqualified; do not silently widen SQL allowlists. For a partially qualified
table expose only complete factual projections; deny the affected field/scope if
predicate-level exclusion cannot be proved.

PreparedBusiness/catalog exposes artifact version, allowed projections, capabilities,
measure prerequisites and evidence. Minimal runtime tools consume that contract.
This verifies data readiness, not that arbitrary QA SQL obeys all measure semantics.

Publish business.duckdb, catalog.json and manifest.json under a new fingerprint;
embed qualification in DB/catalog. Old caches remain untouched and fail version
checks; no in-place reinterpretation or deletion. Failed builds do not appear complete.

## 9. Adaptive proposals and strict resource budgets

Reuse ModelProfile, OpenAI-compatible client, PydanticAI outputs and persistent ledger.
Candidate gets bounded profiles, patterns/counterexamples and source clauses, not
reference labels. Escalate registered configuration -> structured plan -> isolated
micro-transformation only when necessary. The model proposes; trusted code executes
transactionally and independently audits affected/dependent scopes before acceptance.

Initial qualification campaign:120 actual NIM HTTP attempts TOTAL, divided into
smoke<=20, development baseline/recovery<=60, frozen verification/checkpoint<=40.
Unused quota is not a target. Global25RPM/1200UTC-day and existing300/experiment
remain outer guards. Existing per-run counters do not enforce a multi-phase total:
add a transactional campaign identity/ceiling to the same ledger; count failed calls,
format repairs, transport retries and NIM reference calls. Read today's ledger; the
historical776 does not establish current remaining budget. Never rotate/reset ledgers
or run IDs to bypass caps; no automatic increases.

Per issue: at most2 distinct proposal rounds,2 logical requests/round including one
format repair,1 transport retry/request (at most8 HTTP attempts/issue), always under
phase/campaign/day caps. Concurrency1 initially; bounded Retry-After/backoff/deadline.
No SDK hidden retries. Start candidate NIM profile explicitly at2048 output tokens,
24000 input bytes and32768 operational context, same declared mode/model. Reselect
or chunk oversized evidence with omission tracking; never silently truncate it.
Token thresholds can overshoot on the last exchange; report that separately from
hard request/output bounds and missing provider usage.

At most2 major development improvement rounds after baseline; stop earlier for
destructive errors, repeated unchanged hypotheses, exhausted caps or no meaningful
useful-coverage improvement without correctness loss. No checkpoint-driven tuning.
Critical scoped safety fixes need regressions and do not authorize extra API spend.

Reference reviewer is development-only, initially blind to candidate outputs/plans/
scores. Use a distinct strong configured profile or development-reviewer route;
record requested/observable identity, input/prompt/response hashes and usage visibility.
Development-route token telemetry unavailable means UNKNOWN, not zero. Cap initial
annotation at five batches plus one adjudication batch/business (ten jobs). If no
independent profile is available, do not silently use the candidate as its own judge.

Final normalization/QA reuse eligible offline profiles and count every model used
during inference toward the official aggregate parameter cap, even sequentially.
Evaluation-only teacher is outside the final pipeline. No model downloads/paid jobs
are authorized by this design.

## 10. Generated-code sandbox and portability

Proposed first backend: qualified Linux OCI/Docker with pinned image, non-root user,
network disabled, read-only root, no-new-privileges, dropped capabilities, restricted
seccomp, process/CPU/memory/swap limits, bounded tmpfs and no host dataset/reference/
secret/socket mounts or privileged mode. Supply only a bounded serialized batch and
approved declarations. Validate returned rows/bytes/types/lineage outside the sandbox;
no pickle, emitted shell or arbitrary SQL executes in the parent.

Typed micro-transform interface handles cell/group/table batches with source IDs.
No installs, downloads, network/filesystem effects or nested model calls. AST checks
are defense in depth, not isolation. Proposed defaults:1CPU,512MiB RAM/swap ceiling,
32PIDs,10s deadline,64MiB tmpfs; explicitly configured Pandas group/table work may use
1GiB only after qualification. Never silently relax confinement.

Actually test file/network denial, resource enforcement and process-tree termination;
flags alone do not prove security. Current Windows host has no qualified daemon.
Native generated Python stays disabled; deterministic/structured plans work natively.
WSL/Linux/Colab/Kaggle qualification is separate. If mandatory generated-code execution
cannot be qualified, preserve completed safe scopes and report whole milestone PARTIAL.

CPU pilot defaults proposed: sequential businesses, batch1000, DuckDB2GiB, process-tree
RSS watchdog8GiB, configurable45min/business timeout, generated-output/spill cap10GiB.
These are safety ceilings, not runtime promises. Record watchdog vs kernel enforcement.
Measure wall time/RSS/disk and fail clearly; do not raise limits merely to continue.

## 11. Reference data and independent evaluation

No official normalized gold exists. QA answers stay evaluator-only and never select
parsers, aliases, entity counts or target aggregates.

A. Synthetic: at least one positive and one counterexample per corrected catalog case
(minimum88). Mark N=normalization/M=metadata/Q=query-time policy ownership. Q cases
test representation/capability/measure contracts and fixed queries, not a new QA agent.
Choose latent clean truth first, then render dirty data independently. Truth is outside
learner context. Include scale/anchor ambiguity, identities, scoped units, quarantine/
totals, chunk boundaries and1:N interactions. Freeze held-out format families/seeds.

B. Real:20 microcases/business=100 (80development/20checkpoint). Deterministic seed42,
provenance ordering and risk strata select cases before learner outputs. Cover common/
rare formats, grain/group boundaries, structure, identities/units, time/capabilities
and ambiguity. Typical context<=50 rows/case; larger-context facts use bounded evidence
plus reproducible aggregate provenance. Keep failed adjudications; do not cherry-pick
replacement easy cases. Require>=10 VERIFIED DETERMINATE cases/business for PASS;
otherwise PARTIAL even if all synthetic tests pass.

Two separate reference axes:
- evidence_status: VERIFIED / PROVISIONAL / UNRESOLVED.
- expected_behavior: TRANSFORM / PRESERVE / SOURCE_ABSENCE / PROCESSING_AMBIGUITY.

VERIFIED requires source coordinates/hashes, applicable authority clauses, expected
atomic result/constraint, evidence distinguishing alternatives and an independently
reproducible check. Reviewer confidence or agreement is not proof. Verified refusal
cases do not count toward the ten determinate cases. Validators must not import the
candidate parser/selected grammar/identity map to generate their expected values.

Only an evaluator-owned proof checker can promote a reference to VERIFIED; a model
cannot set that state directly. Initial proof kinds are exact source observations,
rule-backed scoped row equality/disposition, explicitly declared scale/unit/role with
independent arithmetic, trusted-key relationships with collision negatives, declared
source absence plus inventory, and a demonstrated pair of admissible interpretations
for ambiguity cases. Store checker/version/result with the evidence packet. Natural-
language claims not supported by such a check or documented independent development
adjudication stay PROVISIONAL/UNRESOLVED. No mandatory human loop is introduced at
inference, and unmet reference minima remain PARTIAL rather than fabricated gold.

References/adjudication live under evaluator-only state/suites/normalization-v1,
not prepared artifacts/prompts. Commit synthetic fixtures/generators and selection
configuration only, not private real rows/expected labels. Freeze source/selector/
case/reference versions. Later label changes need evidence, a new version and reasons;
rescore both candidates on the same version, never silently correct a losing target.

C. Full CPU audit processes all required records in the five businesses with complete
denominators and applicability-aware checks. This is not population accuracy or
independent astronomical calendar verification.

Metrics/gates:
- Every raw record has one disposition and every fact has valid lineage; count1:N
  facts separately from raw accounting.
- Verified determinate success = correct committed/preserved cases / verified
  determinate cases. Abstention is unsuccessful, quarantine stays in denominator.
- Committed correctness = correct / all commitments on those cases; undefined at
  zero commitments. Provisional reviewer matching is agreement only.
- Unsupported commitments on verified ambiguity/absence negatives must be zero.
  False merge, unauthorized exclusion, sign/scale corruption or lost lineage is fatal.
- Each VERIFIED determinate microcase must satisfy its assertion, with>=10/business;
  each verified negative meets its explicit refusal/preservation constraint. This is
  a small fixed regression gate, NOT a100% population-accuracy claim. No arbitrary80%
  coverage target is adopted.
- No unresolved critical invariant in any READY scope; N/A has evidence and blocked
  dependencies are visible. Passing one scope does not qualify every business measure.
- Report per-business integer counts, macro/pooled results, pattern/rule coverage,
  ambiguity/absence, quarantine impact, fallback outcomes and actual resources.

Small fixtures/affected slices run first. Default expensive passes: one development
baseline and one frozen final/checkpoint pass, not full scans after every small edit.
Compare baseline/final on the same80development cases and source versions; report
the20checkpoint cases separately because there is no pre-tuning checkpoint baseline.
P0 freezes source hashes, selector algorithm/seed/risk quotas; P1 profiles instantiate
the case IDs before learner transformations/results. This removes a dependency on
unavailable P1 profiles while still preventing result-driven selection.

## 12. Roadmap, deliverables and commit boundaries

This is acceptance/dependency design. A detailed file-by-file TDD implementation
plan follows only after user approval of this document.

| Phase | Deliverable | Acceptance gate | Logical commits |
| --- | --- | --- | --- |
| P0 | Resolve spec conflicts, freeze pilot/case selectors, define evidence/disposition/lineage/qualification and reference contracts | No known source-authority contradiction; no label access; explicit missing SQL/A10 deliverables | docs: align specification; feat: normalization contracts and pilot selection |
| P1 | Separate raw staging and full CPU profiles; layout/merged metadata and bounded representative evidence | No raw loss; stable identity/source hashes; chunk/layout/format fidelity; zero model calls | refactor: stage/profile separation; feat: source metadata/profiles |
| P2 | Versioned clauses, roles/grain/keys/status/units/capabilities and bounded BM25/fuzzy retrieval | Every clause mapped/unresolved; ambiguity/collision rejection; explicit precedence/dependencies and cache identity | feat: semantic registry; feat: binding validation |
| P3 | Existing/new deterministic ops, Pandas boundary, grain-aware canonical facts and1:N lineage | Synthetic positive/negative gates; dtype losslessness; repeated order amounts not multiplied; full source accounting | feat: operations/batch contracts; feat: projections/lineage |
| P4 | Independent audits, dependency quarantine, versioned atomic publication | No false READY, hidden partial totals or unsupported N/A; absent source differs from processing uncertainty; rollback/invalidation tests | feat: intrinsic audit; refactor: qualified publication/cache |
| P5 | Budgeted model plans, campaign guard, bounded recovery and isolated micro-code | Live accept/reject evidence on>=3 operation families; resource/offline/budget tests; actual required sandbox qualification | feat: adaptive plans; feat: isolated transform backend |
| P6 | Blinded references, baseline/final qualification, one checkpoint and minimal QA artifact adapter | Reference minima and fixed regression cases pass; full five-business audit; no label leakage; budgets and environment statuses honest | test: normalization evaluation; feat: artifact adapter; docs: evidence |

Start reference schema/selection in P0 and adjudicate independently alongside P1–P4,
before learner baseline results. P6 does not create truth after seeing predictions.
Unavailable P5 environment does not stop safe deterministic work, but required
generated-code qualification remains incomplete.

Touch map: current preparation/models.py, loaders.py, profiling.py, catalog.py,
normalization.py, pipeline.py; add focused operations.py/audit.py/adaptive.py only
when responsibilities warrant them, plus at most a projection/storage module.
Reuse inference/config.py/client.py/budget.py; isolate the sandbox backend. Keep
normalization references in evaluation/tests, not production preparation. Existing
__main__.py/runtime tools get minimal explicit version/qualification adapters.

Tests: source/ABA/cache corruption; body-vs-header/footer; merged/late cells; batch
equivalence; order/non-order/near duplicates; Decimal/ID/null/timezone preservation;
parser alternatives;1:N lineage; rule precedence and stale evidence; unit cycles/
entity contradictions; snapshot alignment; audit applicability/dependency closure;
malformed/unsafe SLM proposals; aggregate campaign caps across IDs; isolation denial/
cleanup; old/new artifact adapter and existing SQL safety regressions.

Every logical code unit: targeted regressions, Ruff lint/format, relevant integrations
and full pytest when dependencies change, Git/staged/secret diff review, then atomic
Conventional Commit. Do not rerun model/full-data benchmarks for each minor edit.
Main owns architecture/security/integration/Git. Independent writers use isolated
worktrees and non-overlapping ownership. Reviews target risky identity/dedup,
budget/publication and generated-code units; no recursive architecture-review loops.

## 13. Stops, final status and reproducibility

Stop model work on exhausted caps, unsafe context size, source/reference drift,
repeated unchanged hypotheses, two non-improving rounds or destructive validation
failure. Roll back candidate writes, preserve diagnostics, never invent fallback facts
or automatically enlarge API/resource/architecture budgets.

PASS requires mandatory N/M/Q-contract coverage, deterministic gates, verified pilot
references/full audit with useful successful transformations, live bounded SLM evidence,
actual required isolation qualification, reproducibility and minimum query compatibility.
No arbitrary QA score or population normalization percentage is required.
PARTIAL means missing required reference/semantic/local/offline/sandbox evidence.
A component can be BLOCKED by an unavailable external runtime; finish other meaningful
work and report its exact gap. Unknown cleaning is not data absence/unanswerable.

Final report: corrected44-case matrix; immutable versions; VERIFIED/PROVISIONAL/
UNRESOLVED counts; baseline/final per-business success/coverage and audit findings;
false merges/exclusions/scale errors; actual requests/token completeness/RAM/disk/time;
fallback/sandbox/platform status; retained/reverted work; commits and unresolved scope.
No new3000-question run, QA optimization or public-label tuning is implicit.

Keep generated artifacts in existing state/prepared, state/suites/normalization-v1
and state/runs/normalization/<run-id>. No new root output trees or automatic cleanup.
Fingerprint sources/calendar/rules, operations, schemas, compiler/validator/prompt/
profile/dependencies and all interpretation-affecting code. Publish only complete
versioned artifacts and test replay/idempotence/rollback.

## 14. Builder discretion and approval boundary

Builder chooses tested dependency versions, indexes, temporary paths and batching/test
internals. Builder cannot relax source authority, evidence, destructive-transform gates,
campaign limits, checkpoint isolation, model identity or confinement without a reviewed
change. Native-safe core plus separately qualified Linux micro-code, pilot quotas and
120attempt cap are explicit planner proposals for user approval.

After design approval, invoke superpowers:writing-plans to prepare the executable
TDD plan. This draft is intended for a local Conventional documentation commit;
committing a draft does not imply the user approved it. No product implementation,
model benchmark or dependency installation occurred during this planning task.

## 15. Sources and evidence

Project authority: AGENTS.md, docs/official/rule.md, active rulebooks/README, current
preparation/inference/runtime code, new normalization docs and source-only audit reports.
Previous targeted preparation check:65passed in7.91s; no new full-suite claim here.

Primary documentation consulted2026-11-04 (configuration guidance, not qualification):
- [Docker run controls](https://docs.docker.com/reference/cli/docker/container/run/)
- [Docker Engine security](https://docs.docker.com/engine/security/)
- [rank_bm25 preprocessing contract](https://github.com/dorianbrown/rank_bm25/blob/master/README.md)

Package compatibility, actual cgroup/isolation enforcement and runtime image pinning
remain implementation/qualification tasks; documentation alone does not prove them.

## 16. Review record and input snapshot

Read-only implementation-boundary review: Terra Medium. Evaluation/budget review:
Sol High. Fresh independent builder check: Sol XHigh; result: nothing important left.
These are requested routes; actual backend identity is only claimed if exposed.
No reviewer changed product code or invoked inference/evaluation. Main incorporated
campaign-wide accounting, reference-evidence axes and1:N migration constraints.

User-supplied documents at design time (preserved without modification):
- NORMALIZATION_SPEC.md SHA256 c5b6a1712ab5ad693fcfad7e65f33148d189d4cfc48f54a476f25665ec7c0aac
- ERROR_CATALOG.md SHA256 afc81cfee4925f0c663ea48ec2f4e1c3cb4e166b817c486bf3438244dce7fea4

The user has selected the approach/evaluation method, not yet approved this written
design. P0 will reconcile normative changes explicitly instead of silently editing
these input sources during planning.
