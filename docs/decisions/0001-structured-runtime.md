# Structured runtime and evaluation boundaries

Use one PydanticAI agent and prepared DuckDB artifacts. No multi-agent solving,
embeddings, rerankers, vector database or arbitrary Python execution is needed.

One OpenAI-compatible factory accepts explicit model/backend profiles. Native
server parsers remain backend configuration. Small output functions bind IDs and
types in code and validate the existing strict submission contracts. Live NIM
probes showed nested JSON and redundant type fields caused avoidable validation
failures; the common function interface removes those model-generated fields.
No fallback changes model, backend, reasoning mode or quantization.

SQL is authorized by AST scopes/selected physical sources and a function allowlist,
then executed in a disposable read-only DuckDB worker. Deny external access and
extensions; cap memory, runtime, rows and serialized result bytes. Process boundaries
are not OS filesystem permissions. Constant/calendar queries cannot authorize
substantive business answers. Evidence checks constrain output values but do not
prove correct business interpretation or completeness of rule retrieval.

Question-only suites and separate label-free input snapshots bind solving to
allowed source hashes. Reference types may inform evaluator-owned sampling; labels,
aliases, type strata and reference-file hashes never enter solver snapshots.
Fixed suite artifacts refuse silent replacement. Resume binds source, preparation
plans, implementation, dependencies, profile and budget policy; it never reuses
predictions across configurations or models.

Persist actual HTTP attempts and provider usage centrally, including retries and
responses that fail agent validation. Keep generation/context/tool limits explicit.
Missing provider token usage remains unknown. Evaluation remains provisional where
official PA/matching specifications are missing; engineering uncertainty is not
resolved by inventing scoring rules.
