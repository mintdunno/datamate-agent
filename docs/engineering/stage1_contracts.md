# Stage 1 contracts and Stage 2 handoff

## Public boundaries

| Module | Responsibility |
|---|---|
| datamate.contracts.inputs | Strict Question: ID, business ID, question, explicit as_of_date |
| datamate.contracts.answers | Nine submission variants, strict parser and JSON serializer |
| datamate.evaluation.references | Reference-only values and accepted alias encodings |
| datamate.evaluation.io | Streaming JSONL parsing and explicit reference-file loading |
| datamate.evaluation.scoring | Split/version binding and per-question outcome diagnostics |
| datamate.evaluation.batch | Expected-question denominator and provisional aggregation |
| datamate.observability | Run identity, source/config fingerprints, typed events and JSONL writes |

See evaluation_foundation.md for source-backed scoring behavior and unresolved cases.
Unknown fields are rejected by internal contracts as an engineering choice; that
does not define official malformed-submission scoring behavior.

## Run metadata

RunMetadata records a schema version, run ID, aware start time, split, runtime-input
fingerprint, rulebook/calendar/configuration digests, full code revision and dirty
flag, Python/platform/dependency versions, and optional seed/model/prompt identity.
Model identity contains provider/model/revision only, never API credentials.

The caller must hash actual selected inputs and sanitized configuration. The runtime
input manifest must exclude answers.jsonl, reference aliases, and evaluator outputs.
Metadata construction does not load environment files, discover datasets, invoke
Git, access providers, or claim deterministic model behavior.

Train evaluator profile v1 is separately pinned to the exact reviewed rulebook
SHA-256. A changed rulebook requires source review and a new supported profile;
another split never inherits train matching by default.

## Trace events

Every event has a schema version, run/question IDs, and timezone-aware timestamp.

- QuestionStarted: business ID and question-specific as_of_date.
- ToolFinished: tool name, elapsed milliseconds, success/error code, optional SQL
  and argument digest, result/returned row counts, explicit truncation.
- QuestionFinished: elapsed time, final submission answer, evidence IDs, optional
  model usage, and final status/error. Failed questions require an abstain answer.

Question IDs must agree with final-answer IDs. Counts cannot be negative or conceal
known truncation; failures require error codes. Nested answer values are revalidated
before any bytes are written. Newlines inside strings remain escaped within one
JSONL record, and each write flushes the caller-supplied stream.

Stage 1 records argument digests instead of arbitrary argument/result payloads.
Stage 3 can add typed, sanitized execution details with explicit data boundaries.
These models are not a general secret-redaction system: callers must not embed
credentials or unnecessary business records into free-text SQL/identity fields.

## Isolation and limits

Runtime contracts and observability never import evaluation, accept reference
objects, or serialize reference aliases. A fresh-interpreter test blocks all
evaluation imports while exercising runtime validation and serialization.
The only explicit label-file reader lives in the evaluation package.

This is structural/API isolation. There is no solver, SQL worker, OS sandbox,
full ingestion pipeline, model provider, or training-evaluation execution in Stage 1.
Future runtime processes must receive only allowed artifacts and filesystem
capabilities; sharing a dataset root with a worker would not enforce isolation.

## Stage 2 dependencies

Stage 2 can consume these contracts without changing evaluator ownership:

1. Discover allowed business sources, split rulebook, calendar, and questions;
   never auto-discover or load reference JSONL into preparation artifacts.
2. Preserve source values and source coordinates; use deterministic parsing,
   recorded warnings, and row-count reconciliation.
3. Produce a runtime-input manifest and fingerprints excluding labels.
4. Keep business metrics, coverage, units, and temporal rules explicit; no universal
   valid-order filter or global train-rule fallback.
5. Add DuckDB/Polars/openpyxl dependencies with synthetic loader integration tests.

Official PA normalization, floating-point tolerance, complete source alias coverage,
ambiguous table/list alias mapping, and the separate Docker guideline remain open.
They do not block deterministic ingestion development.
