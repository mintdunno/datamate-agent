# Codex Subagents — DataMate

## Purpose

Use a **hybrid delegation** approach. The main Sol agent owns architecture, complex business logic, integration, verification, and Git history. Delegate bounded independent tasks when doing so reduces work or context; do not require delegation for every task.

## Agent routing

| Role                 | Configured model                                      | Typical tasks                                                  | Can edit files?                 |
| -------------------- | ----------------------------------------------------- | -------------------------------------------------------------- | ------------------------------- |
| Main agent           | Select Sol High in Codex; Ultra for difficult reviews | architecture, core implementation, integration, tests, commits | Yes, within session permissions |
| `repo_explorer`      | GPT-6.1 Terra Low                                     | repository discovery, code paths, bounded samples              | No (by instruction)             |
| `rule_auditor`       | GPT-6.1 Terra Medium                                  | official rules, temporal semantics, evaluation contracts       | No (by instruction)             |
| `implementer`        | GPT-6.1 Terra Medium                                  | scoped helpers, synthetic fixtures, test utilities             | Yes, only assigned files        |
| `code_reviewer`      | GPT-6.1 Sol High                                      | independent correctness/security/regression review             | No (by instruction)             |
| Unspecified subagent | GPT-6.1 Sol Medium                                    | general-purpose fallback                                       | Depends on task                 |

## Delegation policy (append to existing `AGENTS.md`)

```markdown
## Subagent Delegation

- Delegate only bounded, independent tasks with explicit acceptance criteria.
- Prefer `repo_explorer` for repository discovery and `rule_auditor` for competition-rule audits.
- Use `implementer` for clearly scoped utilities, synthetic fixtures, or tests; keep high-risk business logic, scoring, SQL isolation, and architecture under the main Sol agent.
- Use `code_reviewer` for high-risk changes and before milestone completion, not for every trivial edit.
- Do not require subagents when direct work is cheaper or clearer.
- Never allow simultaneous edits to overlapping files. Use separate Git worktrees for parallel writers when necessary.
- Limit delegation depth and avoid review/fix loops without a concrete new finding.
- The main agent owns final integration, quality gates, and atomic Conventional Commits.
- Do not invoke external Gemini/OpenCode CLIs unless separately authorized and configured.
```

## Installation

Copy the `.codex/` directory into the repository root. This package deliberately **does not replace `AGENTS.md`**. Add the delegation section above to your existing file. Include this documentation under `docs/engineering/` if that directory is Git-tracked.

Avoid copying an existing `.codex/config.toml` blindly: merge the `[agents]` section if you have local or repository-specific Codex settings. Keep your chosen parent model separate from the subagent defaults.

## Verification (PowerShell, at project root)

```powershell
python -c "import pathlib,tomllib; fs=[pathlib.Path('.codex/config.toml'),*pathlib.Path('.codex/agents').glob('*.toml')]; [(tomllib.loads(p.read_text(encoding='utf-8')),print('OK',p)) for p in fs]"
```

Restart/reload the Codex session if necessary, and ask it to enumerate discovered agent roles and perform one read-only delegate test. Parsing TOML is not the same as verifying the actual client loads each model or respects routing.

## Safety and cost

- `max_concurrent_threads_per_session = 3` is a conservative starting cap, not a measured optimum.
- Custom-agent read-only instructions are **not** independent OS-level access controls; spawned agents inherit the parent permissions.
- Keep `.env`, full datasets, reference values, and logs out of prompts, commits, and external CLI calls.
- Do not use multiple writers in the same working tree at once.
- Compare task time, usage, review findings, and rework before deciding if delegation saves quota.

## References

- https://developers.openai.com/codex/subagents
- https://developers.openai.com/codex/config-reference
