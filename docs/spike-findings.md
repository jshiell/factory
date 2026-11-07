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

## Plan 2 cycle 0 results (spike/probe3.py, throwaway; exit codes in spike/p3-*.exit.txt)
- **Correction:** `--max-turns` DOES exist. Earlier notes saying it doesn't came from `claude --help`, which does not list every flag. Check flags by running them.
- **`--append-system-prompt` works** under isolation: the agent quoted the appended `PROJECT-MARKER-9` line back (exit 0).
- **`--max-turns 1`:** exit 1, `subtype: error_max_turns`, `terminal_reason: max_turns`, `is_error: true`, `errors: ["Reached maximum number of turns (1)"]`, cost recorded ($0.023), `num_turns: 2` for a cap of 1. Pinned as fixture `max_turns.json`; classifies as `error`.
- **`--permission-prompts none`: inconclusive.** The flag was accepted (exit 0) and the unallowed command was denied, but headless runs deny unallowed tools anyway and there was no baseline run without the flag. Dropped from plan 2.
- **`ANTHROPIC_API_KEY` takes precedence over OAuth:** a bogus key gave exit 1, `api_error_status: 401`, `result: "Invalid API key"`, cost 0 (same `subtype: success` + `is_error: true` shape as the bad-model case), and a stderr warning. The runner must strip it from the child environment.
- **The Bash tool runs commands in its own process group.** claude's pgid was 45363; a Python child run through the Bash tool had pgid 45393 (parent 45393, the tool's shell). Killing claude's process group does NOT kill tool children (one run; not repeated). The timeout must kill the descendant tree, not just the group.
- Inside the agent's sandbox `ps` is blocked ("operation not permitted") and `$$` is rejected by the Bash tool's pre-run check; use `python3 -c` for process introspection.

## Plan 2 end-to-end result (spike/e2e.py, throwaway)
- One real task against `target/` (add `subtract`) ran through `run_task` then `verify_task`: states `queued` -> `running` -> `agent_done` -> `verified`, no denials, cost $0.056 (budget $0.50), recorded in the ledger as not assumed.
- The branch `factory/<id>` holds one commit authored by `factory`, touching only `target/src/calc/__init__.py` and `target/tests/test_calc.py`; the diff was inspected by hand, not taken from the task state.
- Used live for the first time: `--max-turns 30`, `--disallowedTools "Bash(git *)"`, `--append-system-prompt`, `Bash(uv run pytest*)`. All accepted (exit 0). Not shown: that the git block actually denied anything (the agent never tried).
- The gate's `uv run pytest` ran with the orchestrator's `VIRTUAL_ENV` inherited (the gate does not strip env, unlike the runner) and still passed.

## Known gaps after plan 2
- The runner discards stderr (`DEVNULL`), so error diagnostics like `[claude-code:unrecognized_model]` are lost.
- Timeout kill is SIGKILL with no grace period; the gate's test-timeout does not kill grandchildren.
- Test tampering (deleting or weakening a test) passes the gate.
- The timeout path (assumed cost, tree kill) is tested with fake scripts only, never against a real hung agent.
- Worktrees and branches from spikes (`p2-*`, `p3-*`, `e2e-*`) are never cleaned up; no crash recovery for stale `running` tasks.
- The signing agent was unavailable mid-session (1Password error), so later commits were made unsigned.

## Plan 3 cycle 0: audit probe (`spike/audit_probe.py`, outputs `spike/p4-*`)
Single runs each; exit codes saved to `.exit.txt`, all 0.
- **Confirmed:** with no permission mode, `--allowedTools "Read Grep Glob"` plus `--disallowedTools "Write Edit NotebookEdit Bash"` denies writes in `-p` mode. An explicit write request created no file (`pwned.txt` absent); the model said it had no write tool. `permission_denials` was empty, so denial is not visible there; rely on the clean-worktree check.
- **Confirmed:** enum `--json-schema` returns `structured_output` as `{"verdict": "pass"|"fail", "findings": [...]}`. Verdict `fail` in all four runs.
- **Detection:** the auditor flagged an added `pytestmark = pytest.mark.skip` and a new test that monkeypatches `calc.subtract`. One sample each, not a reliability measure. The "good" diff was failed because my hand-written diff lacked the import, which the auditor correctly noticed: no true-pass sample was obtained yet (cycle D will give one).
- **Observed:** the auditor inspects the worktree as well as the diff (said "the working tree does not contain the change" for a diff not applied). In the real flow the change is committed, so this is fine. Cost per audit $0.02-0.08.
- **Confirmed:** macOS `ARG_MAX` = 1048576. Diff limit for C5 set well below that (e.g. 200 KB; env also counts against it).
- **Confirmed:** `:(glob)proj/**/test_*.py` matches zero directories (`proj/test_top.py` matched) and nested depths.
- **Confirmed:** pytest exits 0 when every test is skipped (the gate cannot see added skips; left to the audit).

## Plan 3 end-to-end result (spike/e2e3.py, throwaway)
- One real task: `queued` -> `running` -> `agent_done` -> `verified` -> `audited`. The agent added `subtract` and `test_subtract` to the existing test file (additive, so R2 and the tamper gate passed). Costs $0.060 (implementer) and $0.067 (auditor), neither assumed.
- First true `pass` sample from the auditor: it checked the diff only added lines, the test was a real assertion, and noted it could not run tests or confirm test-first ordering (it has no execution tool).
- The worktree stayed clean through the gate and the audit, so the dirty check did not trip on test-run artefacts here (`__pycache__` is gitignored).
- Not exercised live: `fail` verdict, auditor timeout, tampering. Unit tests cover them with fakes.

## Known gaps after plan 3
- A stale `audit_error` stays on a task that later fails with `audit_rejected`/`audit_modified_worktree`.
- Auditor reliability is unmeasured (a handful of single runs). Gitignored-state and import-time tampering are not caught by the gate.
- The ledger has no stage field; implementer and auditor costs share a task id.
- Plan said to parametrise the assumed-cost helper by event; unnecessary because auditor infrastructure failures leave the task `verified`. The audit driver appends the assumed cost directly.

## Plan 4 cycle 0: plan/review probe (`spike/plan_probe.py`, outputs `spike/p5-*`)
Single runs; all exit 0, `permission_denials` empty, worktree clean afterwards.
- **Confirmed:** a read-only planner (cwd `<tmp>/target`) returns `{"plan": str, "risks": [str]}` as `structured_output`. 6 turns, $0.084, plan 841 bytes, with real files named (`src/calc/__init__.py`, `tests/test_calc.py`) and correct details (existing `add`, no `__all__`, pytest in dev deps). `max_turns` 8 was enough for this tiny project only.
- **Confirmed:** a read-only reviewer returned `approve` for the sound plan ($0.043) and `revise` with relevant findings for a plan that deletes the test file and names a nonexistent path, and for an off-goal plan (divide instead of multiply). One sample each, three plans; not a reliability measure. It also made a useful extra catch on the sound plan (unnamed tests could shadow each other).
- **Finding (security-relevant):** `Read` is not confined to the worktree. The agent read `../ROOT_NOTE.txt` (worktree root) and a file in an unrelated temp directory outside the repo. Read-only agents can read anything the user can, so the planner, reviewer and auditor could exfiltrate or be steered by local files. The plan's read-only checks protect writes, not reads. Not mitigated in plan 4; recorded as a known gap.
- Plan size is small here, so `MAX_PLAN_BYTES` (to be set well under argv limits, e.g. 100 KB) is unexercised.

## Plan 4 end-to-end result (spike/e2e4.py, run by the user in a terminal)
- One goal ("add multiply") went `proposed` -> `planned` -> `reviewed` -> (human `y`) -> `queued` -> `agent_done` -> `verified` -> `audited`. Costs by stage: plan $0.086, review $0.085, implement $0.054, audit $0.028; none assumed, total about $0.25.
- The approval was given by the user at the prompt on the exact printed text (digest taken from it); the script did not auto-approve.
- The plan observed the additive-only rule on its own (a second import line instead of editing `from calc import add`), because `build_plan_prompt` states it. The implementer then satisfied the tamper gate; the audit passed.
- The reviewer found a real detail: a failing red phase would break collection of the whole test file, not just the new tests. Harmless, but shows it checks the plan against the repo.
- The audit again noted it could not run tests or confirm test-first ordering (no execution tool). Reused the pre-made planning worktree for implementation (pristine check passed).
- Not exercised live: `revise` verdict, `reject`, infrastructure failures at plan or review, digest mismatch. Unit tests cover them with fakes.

## Known gaps after plan 4
- Read-only agents can still read outside the worktree (see the plan 4 spike).
- Legacy `queued` tasks bypass the front half; the digest is not access control; approval is human-only by process only.
- The auditor judges against the plan text, not the goal; worktrees from rejected plans accumulate.
- Planner and reviewer reliability rests on a handful of single runs.

## Plan 5 cycle 0: CLI probe (`spike/cli_probe/`, no API)
- **Confirmed:** `[project.scripts] cli-probe = "cli_probe:run"` installs and runs under `uv run --project <dir> cli-probe` with the `uv_build` backend.
- **Confirmed:** `uv run --project` preserves the caller's cwd (printed `.../target` when run from there), so a `repo` default of "git toplevel of the cwd" works.
- **Confirmed (Claude Code Bash tool):** `stdin.isatty()` and `stdout.isatty()` are False, piped or `</dev/null`; `/dev/tty` fails to open (`Device not configured`). An agent's Bash call cannot satisfy the TTY check or reach the user's terminal via `/dev/tty`.
- **Confirmed (user's real terminal):** `stdin.isatty=True`, `stdout.isatty=True`, `/dev/tty` opens.
- Not tested: a pty or `script` wrapper, which can fake a TTY (known gap: the check is a process control, not a security one).

## Plan 5 end-to-end result (`factory` CLI, run by the user in a terminal)
- `new` -> `advance` (plan, review; stopped at `reviewed`) -> `approve` (interactive `y` on the printed text) -> `advance` (implement, verify, audit) -> `audited`. `show` and `costs` worked. Costs: plan $0.044, review $0.046, implement $0.062, audit $0.028; total $0.180, none assumed.
- The approval was the user's `y` at the prompt; the agent never ran `approve`. The goal was displayed above the plan.
- The audit again said it could not confirm test-first ordering or a passing run (no execution tool).
- Not exercised live: `reject`, `list`, a stage stopping on infrastructure failure, `AlreadyRunning`, SIGTERM cleanup. Covered by unit and $0 subprocess tests.

## Known gaps after plan 5
- `Read` in the read-only agents is not confined to the worktree.
- The approve TTY check is a process control; a pty can fake it.
- An implement-stage infrastructure failure is terminal; a task stuck `queued` on a dirty worktree needs manual repair; legacy `queued` tasks bypass the front half.
- SIGKILL leaves a stale `running` task and possibly a live agent. Worktrees from rejected plans are orphaned.
- The auditor judges against the plan, not the goal.

## Plan 6 cycle 0: nono probe (`spike/nono_probe.py`, run by the user, ~7 runs, about $0.25)
Command shape: `nono run -s --profile nolabs-ai/claude --allow-cwd --read <resolved worktree> -- claude -p ...`, cwd `worktree/target`. Raw output in `spike/nono-probe-out/` (gitignored).
- **Confirmed (Read, scored by canary tokens):** readable: control file in `target/`, worktree-root file, `$TMPDIR` dir, `~/.claude` dir. Denied: repo root, `repo/.factory`, `repo/.git/config`, a `$HOME` dir, and a symlink in `target/` pointing at the `$HOME` canary. Authentication works under the profile. So the worktree plus `$TMPDIR` plus `~/.claude` is the effective read set (the last two are the profile's and nono's default grants).
- **Confirmed (Grep):** same split as Read (control, worktree root, `$TMPDIR`, `~/.claude` found; the rest absent).
- **Inconclusive (Glob):** returned nothing anywhere, including the control file, so the probe's Glob pattern or usage was wrong; no claim about Glob confinement.
- **Confirmed:** `permission_denials` is incomplete: only 2 of the 5 denied Reads in the Read run appear in it (the rest were nono errors). Score by tokens, never by denials.
- **Confirmed:** a bad model gives exit 1 with empty stdout and `[claude-code:unrecognized_model]` on stderr, so exit codes and failure shape pass through nono.
- **Confirmed (fork):** nono stays as a parent process: `nono` (parent) -> `claude` plus a `/usr/bin/log stream ... sandboxd` helper. The runner's descendant-tree kill must cover both (not yet tested under a wrapper).
- **Confirmed:** Claude still reports "git-repo: yes" in the worktree with the main `.git` not granted (likely via the worktree's `.git` file; mechanism not checked).
- **Confirmed (h1, `Read Grep Glob` dropped from the allow list, under nono):** only `target/` (the cwd) is readable; even the worktree-root file, `$TMPDIR` and `~/.claude` are denied. Headless default rules are stricter than nono's grants. Not tested: whether Grep and Glob work in cwd without being allowed, and whether `--add-dir <worktree>` restores the worktree root.
- **Confirmed (h2, `--settings` deny rule on `~/.claude/**`):** `--settings` is honoured under `--setting-sources ""`; `~/.claude` became unreadable for Read, `$TMPDIR` still readable. Not tested: a deny rule for `$TMPDIR`, or whether Read deny rules also stop Grep and Glob.
- **Limitation:** `nono why --profile ... --path` reported `DENIED` for the worktree files because it was not given `--read <worktree>`; only the `$TMPDIR` and `~/.claude` lines (`ALLOWED`) are informative. The default profile text has no explicit temp line (the grant likely comes from a group such as `system_read_macos`); cycle 0i (can a derived profile drop temp, `~/.claude/projects`, workdir write) is not yet tested. `nono run` has a `--dry-run` flag that was not used.
