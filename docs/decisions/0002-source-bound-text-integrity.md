# Source-bound character decoding and derived Unicode repair

Status: implemented; conservative default approved by the user.

CSV decoding is selected once and bound to source SHA-256. BOM and explicit
source-bound hints outrank statistical detector proposals; all records must pass
strict validation. An undecodable file stops the build because its schema and
coverage cannot yet be established safely.

Mojibake repair precedes semantic matching and working-cell normalization. Keep
raw inputs, physical IDs and exact rule quotes immutable. Use separately audited
candidates/derived labels and the existing transform events, lineage and scoped
qualification. One receipt relation covers decoder, label, metadata and cell
proofs. No LLM, lossy decoding or new orchestration framework is introduced.

Ftfy explanations are proposals, not proof of intent. Automatically accept only
unique, bounded, reversible strict codec chains that remove corruption and are
idempotent. Key repair additionally requires complete, collision-free declared
SQLite relation evidence across all connected foreign keys. Composite and
implicit-target relations remain protected/unresolved until a separate complete
proof is supported. An unused damaged header does not block an unrelated scope.

This spends extra streaming I/O and temporary disk on strict CSV preflight and
complete key-domain checks. Mixed-fragment repairs, formula/path rewrites,
document-quote replacement and source editing are deliberately excluded. See the
[approved design](../superpowers/specs/2026-11-06-text-integrity-design.md) and
[implementation plan](../superpowers/plans/2026-11-06-text-integrity.md).
