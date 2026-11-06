# Isolated replay for bounded adaptive recovery

Reuse registry preparation, typed plan proposer, recover_issue and replay instead
of nesting a candidate transaction in the existing build transaction. A trusted
source-only selector chooses one issue; a source verifier must prove its repair.
Neither callback receives evaluator records. Automatic broad issue discovery is
not claimed by this adapter.

Use authoritative bindings scoped to the issue for bounded model context, and
rebase accepted typed deltas onto the complete registry plan in the same
prerequisite order enforced by validate_plan. Never drop other tables or steps.

Build trials below the destination cache on the same volume. Intrinsic audit,
source verification, no READY-scope regression and unchanged trial manifest/content
are mandatory before atomic promotion. Roll a newly created target back into the
owned temporary workspace if final validation fails. Baseline and rejected trials
remain separate. Model accounting belongs to the fresh run, while replay artifact
identity remains deterministic. Requested model/mode/profile are never changed.

The small live qualification uses synthetic source fixtures and a three-attempt
run ceiling within the existing adaptive campaign. It establishes integration and
failure containment, not private-data accuracy, full pilot readiness or completion
of Milestone 1. Invalid structured output is a model/API compatibility failure,
not proof that the model correctly abstained.
