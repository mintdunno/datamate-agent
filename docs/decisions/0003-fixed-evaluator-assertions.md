# Fixed source assertions and evaluator targets

Source adjudication and artifact observation must address the same explicit
assertion. Use one evaluator-only SourceAssertion contract and a small closed
set of source checks, reusing source hashes, EvidenceRef, ReferenceSuite and the
existing checker seal. Model verification/confidence fields are rejected.

Revisions affect only development cases, bind the first evidence coordinate to
the frozen case, and preserve checkpoint references. Raw-copy checks alone do
not establish normalization truth: preservation proofs require declared field
semantics; header metadata alone is insufficient. Ambiguous integer formats need
native amount/quantity anchors connected by exact identifier witnesses.

Add fixed disposition and size-attribute addresses and typed string observations
to the existing artifact contract. The evaluator reads committed facts/lineage
and dispositions; it does not fill a missing attribute from raw text. Derived
size additionally requires that the original product identity is preserved.
Integral Decimal-to-integer serialization is lossless, never rounded.

Existing reference serialization/digests remain unchanged. New assertions require
new reference content/version and the corresponding implementation revision;
older software rejects unsupported target fields. Baseline and final must both
be evaluated on the same new suite. No expected labels or new source checks
are made available to preparation/solving. This change neither qualifies a
normalization artifact nor claims that unsupported business semantics are solved.
