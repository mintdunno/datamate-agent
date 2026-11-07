# 0005: Close adaptive-normalization Milestone 1 on accepted bounded evidence

Date: 2026-11-07 (Asia/Bangkok).
Status: ACCEPTED by the user. Milestone 1: CLOSED.

## Decision

After the reported small regression run and real Gemma SQL-to-answer success,
the user explicitly instructed: "ok vay chot milestone 1 nha".
Close the adaptive-normalization milestone on that disclosed scope. Stop this
fix/test campaign; no additional five-business run or API experiment is implied.

This is the user's project acceptance decision. It supersedes requiring complete
five-business qualification before closing this milestone. It does not change
scoring rules, frozen references, evaluator thresholds or measured gate outcomes.
The original design remains a historical record of the broader acceptance target.

## Accepted evidence

- The fixed small suite passed 44 tests in 42.30 seconds; Ruff lint and format checks
  passed. These regressions used small synthetic fixtures and mocked inference.
- Prior unchanged-code full regression: 1,061 tests passed in 336.84 seconds.
- Real Google Gemma metadata proposal passed source-bound validation/audit; full
  cold biz_122 preparation completed. Selected-source SQL retained 5,516 rows and
  all 11 recorded-unit totals matched independent raw-source checks.
- The real agent called restricted SQL, consumed its result and returned the typed
  numeric answer 5516. Both final requests returned HTTP 200; SDK/provider usage
  agreed. Raw inputs/provenance and source/code/profile identities were preserved.

See the [execution evidence](../engineering/preheader_recovery_report.md) for
actual commands, resources, request accounting, prior failures and local commits.

## Limitations retained

The latest pipeline has not passed a fresh complete five-business quality
qualification. Historical strict gate failures and reference-coverage limitations
are not relabeled as PASS. This closure does not establish general normalization
accuracy, official QA scores or correctness of every business field/measure.
Unknown semantics remain unresolved and access stays scoped to qualified data.

The provider constraints are 30 RPM and 16,000 TPM. The final diagnostic used
serial requests with at least 61 seconds after response completion; the core
still has no general concurrent TPM limiter. Seven experiment attempts and known
usage totals remain in the persistent ledger; no reset or hidden retry occurred.
No new model/platform qualification, full training evaluation, merge or push is
part of this closure. Future work requires its own scope and preserves these facts.
