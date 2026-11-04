# Milestone 1 master execution plan

Reuse Stages 1/2. One single-agent runtime, configuration-driven OpenAI-compatible
providers, deterministic structured tools, evaluator-owned references and fixed suites.

1. Configuration/model profiles and compatibility research (Sol Max).
2. SQL AST allowlist, restricted worker, bounded lexical/fuzzy tools (main).
3. Persistent request accounting/retry transport (isolated Terra implementer).
4. PydanticAI runtime, progressive/resumable batch CLI, bounded context and evidence.
5. Deterministic evaluator-owned suites and comparable benchmark reports (isolated implementer).
6. Synthetic tests, NIM smoke then fixed-development baseline within budget; at most
   three major improvements, stop after two without meaningful improvement.
7. GPU/backend preflight, minimal shared bootstrap, real qualification only where
   hardware/access permits; independent risk review and final acceptance ledger.

Budgets: target25RPM (provider40), daily1200 (provider10000), experiment300 requests.
Every actual HTTP attempt counts including retries; no answer cache, no full3000
evaluation, no model downloads/cloud spend without provided permission/environment.
Conservative context/output/tool/time limits; no automatic mode/model/backend fallback.

Stop conditions: mandatory gate missing evidence is PARTIAL/BLOCKED, not PASS.
Do not invent official PA/matching clauses. Local/cloud GPU jobs require compatible
hardware and access; Windows RTX3060Ti8GB currently visible, Docker daemon unavailable.
No merges/push/history rewrite. Existing untracked .codex files are preserved.
