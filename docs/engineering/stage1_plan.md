# Stage 1 execution plan

Approved baseline: the onboarding assessment and the user's Stage 1 implementation request.
No model API, full ingestion, training evaluation, merge, or push belongs to this stage.

## Logical units and interfaces

1. Tooling, ignored artifacts, and strict `Question` input contract. Pin Python 3.13
   (available locally), lock dependencies with uv, and establish pytest/Ruff gates.
2. Strict discriminated submission contracts and evaluator-only reference contracts.
   Runtime modules must never import evaluation or accept reference aliases.
3. Evaluation foundation: JSONL diagnostics, explicit split-bound matching policy,
   per-question outcomes and provisional aggregation. Missing predictions abstain;
   unsupported semantics remain unresolved rather than receiving invented scores.
4. Structured run metadata and trace contracts, documentation, independent review.

Pre-flight: evaluation consumes submission contracts; traces consume submissions only.
References and their readers are owned exclusively by evaluation.

## Verification

Use synthetic records only. For each unit, add failing behavior tests, implement,
run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`;
review unstaged and staged diffs before an atomic Conventional Commit.

## Review focus

- Bool/number coercion and non-finite numbers, including nested answer values.
- Monthly dates and invalid calendar dates.
- Malformed predictions, nonstring reasons, missing IDs, and duplicate records.
- Ambiguous aliases, floating tolerance, table columns, and incomplete aggregates.
- Ground-truth import/file boundaries and traces that cannot contain references.

## Execution record

- Foundation/input contract: f8f8adc; 8 synthetic tests passed before commit.
- Submission/reference contracts: 2db060b; 43 synthetic tests passed before commit.
- Evaluation foundation: de3dfe9; 103 synthetic tests passed before commit.
- Scalar table normalization regression: f6550e3; regression observed RED then GREEN.
- Trace/run metadata and isolation: final unit; 132 synthetic tests passed during
  implementation; final checks and local commit are reported in the completion reply.
- Independent review: no remaining actionable findings after four fixes and
  regression tests. Reviewer independently ran the 126-test suite and Ruff; six
  additional metadata/serialization boundary cases subsequently passed locally.

Decisions:

- Ruling: keep unresolved cases unscored and block aggregate PA, rather than invent
  tolerance/alias/invalid-envelope rules. Cost: deliberately incomplete scorer coverage.
- Ruling: pin train-v1 matcher to the reviewed rulebook digest. Cost: changed source
  bytes require explicit review/versioning before that profile can run.
- Ruling: record tool argument digests until typed runtime tools exist. Cost: Stage 1
  cannot replay full tool arguments; Stage 3 must add controlled details.
- Ruling: provide import/API ground-truth isolation now; enforce OS filesystem
  isolation with the future worker. Cost: Stage 1 alone is not a security sandbox.

No training evaluation, model inference, merge, push, or source-data modification ran.
