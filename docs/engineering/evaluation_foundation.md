# Evaluation foundation (Stage 1)

## Authority and scope

docs/official/evaluation.md records gaps in the copied global Evaluation tab.
The actual datasets/train/rulebook.md, sections 7 and 9, supplies additional
**train-specific** formats, matching clauses, and +1/-1/0 outcome points.
These sources must be reconciled; the global PA formula remains unverified.
No real training evaluation has run in Stage 1.

EvaluationSpec defaults to global known outcomes only. Train matching requires
explicit profile="train_rulebook_v1", split_id="train", and the selected
rulebook's SHA-256. The digest must match the reviewed v1 rulebook; changed rules
cannot silently activate this profile. Callers must hash the actual selected file.
This is version binding, not automatic semantic parsing of arbitrary rulebooks.
There is no default train policy for another split.

## Contracts and isolation

- datamate.contracts owns runtime question/submission shapes. Unknown fields are
  rejected as an internal safety policy, not an assertion about official scoring.
- datamate.evaluation.references owns reference answers and aliases. The runtime
  package never imports evaluation; submission serialization rejects references.
- Scalar aliases and the observed nested table aliases retain their shapes.
  Nested/list/table alias mapping is not sufficiently specified for a full matcher.
- evaluation.io.load_reference_file is the explicit reference-file reader.
  No module discovers dataset roots, loads .env, or reads labels on import.
- These are import, API, and data-flow boundaries. OS-level isolation will be
  enforced when the SQL worker/runtime is implemented; it is not present in Stage 1.

Submission models use strict native JSON kinds, finite numbers, calendar-valid
day/month dates, ordered arrays, and rectangular tables. They do not impose
undocumented list-element or table-cell business types. Unanswerable references
require answer:null; submissions serialize without a fabricated answer.

## Outcome versus validation status

QuestionEvaluation.outcome is correct/wrong/abstain **or null when unresolved**.
Status separately records scored/invalid_prediction/invalid_reference/unresolved.
An invalid record is not automatically a fourth official competition outcome.

Confirmed cases:

- Missing prediction and explicit abstain: abstain.
- Valid prediction with the wrong reference type: wrong.
- Train unanswerable against unanswerable: correct; its global matching is unresolved.
- Substantive answer against an unanswerable reference: wrong.
- Train nonstring reason: unreadable record, scored abstain (section 7).
- Recognized train answer-format violations: wrong (section 9).
- A provided string reason is ignored by automated scoring.

Malformed JSON/envelopes, duplicate IDs, unknown fields/types, and invalid references
have explicit diagnostics without invented official outcomes. Duplicate IDs are
quarantined; neither first nor last record wins. Unattributed malformed lines prevent
missing records from silently receiving abstain. Unexpected IDs block a complete
aggregate. Diagnostics omit raw records, answers, aliases, and validation inputs.

## Deliberately partial train matcher

- Exact numeric equality is accepted. Unequal integers are wrong. Unequal comparisons
  involving floats remain unresolved: no epsilon or automatic rounding is invented.
- Strings ignore case, whitespace, Unicode punctuation; accents and symbols remain.
  Flat, supplied scalar aliases are accepted. Unmatched strings remain unresolved
  because the evaluator has not established all accepted variants in business files.
- Lists preserve order and multiplicity. A permutation of the same known values is
  wrong; unsupported alias/nested-value comparisons remain unresolved.
- Tables ignore row order. Unique matching column names can reorder columns;
  wholly unmatched names use position. Mixed matches, duplicate columns/entities,
  and alias-dependent row association remain unresolved.
- Boolean and date values compare exactly after strict format validation.

These conservative gaps limit scorer coverage; they are intentional and reported,
not evidence of poor solver accuracy. Future matcher additions require source
evidence and synthetic regression fixtures.

## Aggregation

The denominator is the expected question list, not an ID intersection.
Known points equal correct minus wrong and describe only resolved outcomes.
Provisional PA is (correct - wrong) / total_questions, emitted only when every
outcome is known, there are no file diagnostics, and the batch is nonempty.
Unresolved/invalid cases are never silently scored zero or removed from N.
The metric is explicitly provisional, not an official PA reproduction.

## Synthetic API example

~~~python
from datamate.contracts.inputs import Question
from datamate.evaluation.batch import evaluate_batch
from datamate.evaluation.io import read_records
from datamate.evaluation.scoring import EvaluationSpec, TRAIN_V1_RULEBOOK_SHA256

questions = [Question(id="demo", business_id="shop", question="Số đơn?", as_of_date="2025-01-01")]
references = read_records(['{"id":"demo","type":"number","answer":3,"alias":[]}'])
predictions = read_records(['{"id":"demo","type":"number","answer":3}'])
# Use the fingerprint of the actual selected rulebook in a real caller.
spec = EvaluationSpec(
    split_id="train", profile="train_rulebook_v1", rulebook_sha256=TRAIN_V1_RULEBOOK_SHA256
)
report = evaluate_batch(questions, references, predictions, spec)
assert report.correct == 1
assert report.provisional_pa == 1.0
~~~
