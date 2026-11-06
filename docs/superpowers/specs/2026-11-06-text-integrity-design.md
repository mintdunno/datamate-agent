# Character encoding and mojibake integrity

Approved design: conservative default with strong-evidence automatic recovery on unseen inputs; always generate protected-key candidates and validate relations before acceptance. Local scoped qualification, consistent derived text, immutable raw bytes/values/provenance. No LLM, lossy decode, new framework, merges or pushes.

Use strict CSV decoders UTF-8/BOM, UTF-16 LE/BE, CP1258, CP1252. Trust source-bound configuration/BOM; detector proposes only. Validate the whole stream and CSV structure. Ambiguous distinct renderings remain unresolved. Pin one decision for headers and rows.

Use ftfy.fix_encoding_and_explain with consistent reversible codec steps only, bounded layers and text length. Preserve Unicode combining marks. Never rewrite formulas, executable metadata, paths, physical identifiers, raw header evidence or rulebook quotes. Keep accepted derived labels/values separately. Key acceptance requires more than heuristic: declared namespace, complete collision/uniqueness and relational validation. Unknown/missing proof remains scoped unresolved, not a business-wide failure.

Reuse storage, working cells, transform events, audit, qualification, cache and readonly SQL boundary. One receipt relation covers non-cell labels/decoder decisions; cell events retain lineage. Receipt changes invalidate audit/publication. UTF-8 clean fixtures remain logically identical; raw IDs unaffected by mojibake repair.

Tests cover valid/error encodings, late decode errors, BOM conflicts, ambiguous legacy, one/multilayer mojibake, clean Vietnamese/combining marks, mixed text, loss/false positives, identifiers, collision, relation mismatch, scopes, batch invariance, cache drift, SQL filtering and immutable documents.
