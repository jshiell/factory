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

## Answers (spike 1, one run each; isolation judged from the model's self-report)
1. **Settings isolation.** `--setting-sources project` does NOT stop user `~/.claude/CLAUDE.md` leaking, and `--system-prompt` doesn't either.
   Two things did: `--setting-sources ""` and env `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` (agent answered NONE under both). OAuth still worked.
   Memory instructions still appear in context; not yet tested how to disable. Untested: whether `--setting-sources ""` also drops project-level CLAUDE.md in the worktree (we'd want project-level kept).
2. **Budget cap.** `--max-budget-usd 0.02` aborted with exit code 1, `subtype: error_max_budget_usd`, `terminal_reason: budget_exhausted`, `is_error: true`, no `result` field.
   The cap is checked after a turn: spend was $0.069 vs the $0.02 limit, so treat it as a soft cap and keep a separate orchestrator ledger/limit.
   No `--max-turns` flag exists in `claude --help`; `num_turns` is reported but can't be capped via CLI (unverified for SDK).
3. **Scoped git.** With `--permission-mode acceptEdits` and allow rules `Bash(git add *)`, `Bash(git commit *)`, `Bash(git status*)`, `Bash(git log *)`, the agent created a file and committed in the worktree: no denials.
   So the earlier denials were from compound `cd … &&` commands / missing rules, not a worktree restriction.
   Agent still appended its Co-Authored-By trailer and offered to sign (user CLAUDE.md effect) -> isolate, or let the orchestrator own commits.

## Implications for runner design
- Runner builds the command from a config: model, budget, tool allowlist, isolation (`--setting-sources ""` + env var).
- Runner classifies outcomes from `subtype`/`terminal_reason`/exit code: success, budget_exhausted, error, plus `permission_denials` non-empty.
- Orchestrator keeps its own cost ledger and stops launching work when the cap is hit.

## Gaps carried into plan 2 (recorded after plan 1)
- Exit codes were not saved by the spike; the budget-abort exit code 1 was seen on the console only. Record exit codes in future probes.
- Isolation flags (`--setting-sources ""` + `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`) and tool use have not been tested together. First spike of plan 2.
- Whether `--setting-sources ""` keeps a project-level CLAUDE.md in the worktree is untested.
- `--allowedTools` repeated-arg form and `--` before the prompt are unverified; `build_command` uses one space-joined argument.
- `step` assumes `RunnerConfig.budget_usd` is set; with `None` the cap check raises `TypeError`. Make it required or fail explicitly in plan 2.
- Other result subtypes (max turns, auth, rate limit) are unobserved; they classify as `error` by default.

## Plan 2 spike results (spike/probe2.py, throwaway; exit codes saved to spike/p2-*.exit.txt)
- **Isolation + tools work together.** Via `build_command` (isolation flags, `acceptEdits`, scoped git allow rules): exit 0, no denials, agent committed in the worktree. About $0.045 per run.
- **Project CLAUDE.md is NOT loaded under `--setting-sources ""`** (agent self-report: no `PROJECT-MARKER` content in context). Project guidance must be passed in the prompt or `--append-system-prompt`.
- **The user CLAUDE.md did not leak** (agent self-report: NONE).
- **The harness attribution reminder still applies**: the agent added a `Co-Authored-By` trailer despite isolation. Cosmetic; tell the agent not to if unwanted.
- **Repeated `--allowedTools` args work** (exit 0, no denials). The space-joined form from plan 1 is also fine.
- **`-p -- <prompt>` works** (exit 0, committed).
- **Bad model name:** exit 1, `subtype: "success"` with `is_error: true`, `terminal_reason: "api_error"`, `api_error_status: 404`, cost 0, message in `result` (not `errors`), stderr `[claude-code:unrecognized_model]`. `subtype == "success"` alone must never mean success; classifier checks `is_error` first (pinned by fixture `bad_model.json`).
- Budget-abort exit code 1 is still only a console observation; the bad-model probe confirms errors exit 1, success exits 0.
- Still unobserved: max-turns, auth failure, rate limit subtypes. No `--max-turns` flag exists, so a timeout in the runner is the backstop.
