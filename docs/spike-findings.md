# Spike 0 findings (throwaway; observed on 1 run, sonnet, OAuth)

- `claude -p --output-format json` returns one JSON object: `result`, `structured_output` (parsed, present when `--json-schema` given), `total_cost_usd`, `duration_ms`, `num_turns`, `session_id`, `is_error`, `terminal_reason`, `permission_denials`, `modelUsage`.
- Cost/time for a trivial task: $0.19, 36s, 19 turns. Cost is machine-readable, so a ledger is easy.
- `permission_denials` lists every blocked tool call -> orchestrator can detect "agent got stuck on permissions".
- Unattended permissions: `acceptEdits` + narrow `--allowedTools` worked for edits/pytest. Compound `cd x && ...` Bash commands and `git add/commit` were denied.
  The agent reported this honestly in its output rather than failing -> need a gate that checks real state (git status/log), not the agent's claim.
- Worktree was left dirty, no commit: the orchestrator (not the agent) should commit, unsigned, after gates pass.
- Config leaks in with OAuth: the agent loaded user CLAUDE.md (TDD, verify, commit rules), tried to rename the Rex session, and ran `git log --show-signature`. Reproducibility cost accepted; `--setting-sources` / `--settings` could narrow it (not yet tested).
- stderr warns "no stdin data received in 3s": pass `stdin=DEVNULL`.
- `--max-budget-usd` accepted; hitting the cap not yet tested.

## Open questions for Increment 1
- Does `--setting-sources project` stop user CLAUDE.md leaking while keeping OAuth?
- What does a budget-cap or max-turns abort look like in the JSON?
- Can a Bash allow-rule permit `git add`/`commit` inside the worktree only?
