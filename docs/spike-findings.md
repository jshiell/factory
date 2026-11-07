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

## Plan 6 cycle 0, round 2 (`spike/nono_probe2.py`, run by the user, about $0.30)
- **Confirmed (chosen layer):** under nono with an empty `--allowedTools` and `--add-dir <worktree>`, Read and Grep found exactly the control file and the worktree-root file; repo root, `.factory`, main `.git/config`, `$HOME`, the symlink out, `$TMPDIR` and `~/.claude` were all absent. This closes the temp and `~/.claude` exposure left by nono alone.
- **Confirmed (fallback):** nono with the allow list plus `--settings` deny rules for `~/.claude` and `$TMPDIR` gave the same result for Read and Grep, so Read deny rules also cover Grep.
- **Confirmed (Glob):** unsandboxed, Glob works and reaches every canary (so it is not confined by claude's own rules when allowed); under nono it returned nothing in every configuration, including the control file in the cwd. Cause not diagnosed. Treated as unavailable.
- **Confirmed (derived profile):** the nono profile guide says inherited filesystem paths cannot be removed (only groups can be excluded), so the temp grant cannot be dropped with a derived profile without excluding a whole system group (not tried).
- **Not tested:** the default-rule layer without nono (so nono's marginal value on top of it is unmeasured); Glob's cause; behaviour of the real plan, review and audit prompts with no allow list.

## Plan 6 cycle D: e2e under nono (run by the user in a real terminal, task 20261107-112109, $0.3162)
- **Confirmed:** the goal asked the planner to quote canaries at the repo root and under `$HOME`. Both reads were refused ("could not resolve where the path leads on disk"). The reviewer reported the same. `factory show | grep -c -E "ROOT-|HOME-"` gave 0.
- **Confirmed:** the real plan, review and audit prompts work with an empty allow list and no Glob. The pipeline went plan, review, approve (user `y`), implement, verify, audit and ended `audited`. Confined stages cost: plan $0.1259, review $0.1309, audit $0.0962.
- **Note:** the refusal text is the symlink-resolution error, not a plain permission denial. The planner could not read the files. It did not say what blocked the read, so the boundary is shown by the token being absent, not by the message.
- Unit suite: 391 passed; target: 1 passed (sniffed toolchain, no declared gate).

## Known gaps after plan 6
- Read-only agents (plan, review, audit) read the worktree only at the tool level (e2e canaries absent). nono still grants `$TMPDIR`, `~/.claude` (r+w), `~/.claude.json` and Keychain files at the OS level. This is defence in depth, not a clean boundary.
- Tool-level confinement depends on claude's default headless rules. A claude upgrade could change them. Fallback is `--settings` deny rules. The e2e canary run is the regression check.
- The workdir is OS-writable; write protection of read-only stages rests on the tool disallow list and the post-run dirty/HEAD check.
- `Glob` is unavailable to confined agents (empty results under nono, cause undiagnosed).
- nono stderr is discarded; a profile or sandbox error appears only as an empty-output infrastructure failure.
- `factory advance` from inside an agent's own nono session fails at plan (nested nono denied). Only the human runs the real pipeline.
- The implementer is not confined.
- Carried over: approve TTY check is process control; SIGKILL leaves `running`; orphaned worktrees; the auditor judges the plan, not the goal; the audit cannot confirm test-first or a passing run.

## Plan 7: `factory merge` e2e (`spike/merge_e2e.py`, run by the user, $0, synthetic repo)
- **Confirmed:** all seven checks passed. First merge (user `y`) fast-forwarded main to the audited sha, the task became `merged`, the worktree directory was removed and `factory/t1` was kept. The second merge was refused. The real repo's worktrees were untouched.
- **Note:** the second-merge refusal reads "merged not allowed from merged" (the raw `InvalidTransition` text); correct but terse.
- Unit suite: 424 passed; target: 1 passed (sniffed toolchain).

## Known gaps after plan 7
- Main moving between the plan stage (where `base_sha` is captured) and the merge makes the merge refuse; there is no rebase or re-verify, so the task must be re-run.
- "Human-only" is process (TTY check), as for approve. Merged commits are unsigned until the human signs them.
- The auditor reads only the `project_subdir` diff while `commit_all` stages the whole worktree; the merge diffstat covers the whole tree as the check.
- The implementer could make its own commits (only `Bash(git *)` is denied); the tip and `audited_sha` guards are the check.
- A worktree-removal failure after a merge leaves the path on the task; no GC command. Branches are never deleted. No rollback command.
- `merge.verifySignatures=true` makes git refuse the unsigned commits (error surfaced, not overridden).
- Carried over from plan 6 and earlier: confinement gaps, the implementer unconfined, SIGKILL leaves `running`, orphaned worktrees from rejected plans.

## Plan 8: retry, recover and clean e2e (`spike/housekeeping_e2e.py`, run by the user, $0, synthetic repo)
- **Confirmed:** all eleven checks passed. `recover` failed the stale running task as `interrupted` and appended an assumed ledger entry. `retry` of a failed and of an audited task each made a proposed task with `retry_of` set (ids `...-123248` and `...-123248-2`). `clean` listed the merged task (branch left), the dirty failed task and the recovered task, and after `y` removed the two worktrees and deleted the merged branch. The failed task's branch and the audited task's worktree and branch were untouched, as was the real repo.
- **Nits seen:** "1 commits ahead of base" (plural), and "branch left behind" is also printed for failed tasks whose branch is deliberately kept. `recover` printed "last driver pid unknown" because no lock file existed yet (expected in the synthetic repo).
- Unit suite: 487 passed; target: 1 passed (sniffed toolchain).

## Known gaps after plan 8
- `recover` cannot prove the agent is dead: a SIGKILLed driver frees the lock while the agent may live. The printed warning and pid are the only control. Ctrl-C, TERM and HUP kill the agent but also leave the task `running`.
- Work of an interrupted task is uncommitted (commits happen at verify), so `clean` discards the only copy; the preview flags dirty worktrees.
- Failed-task branches accumulate (manual `git branch -D`). A retried `audited` task keeps its worktree and branch forever. `queued` and `agent_done` tasks stopped by a not-pristine worktree have no command.
- Worktrees with no task record are not found. Legacy tasks with no goal cannot be retried.
- The assumed cost on `recover` may overstate what an interrupted run spent.
- Carried over: merge refuses when main moved (retry is the only way), commits are unsigned until the human signs, confinement gaps, the implementer is unconfined.

## Plan 9: git hardening, confined no-shell implementer, sandboxed gate
- **Cycle 0 spike (human-run probes):** agent-run code under the `nolabs-ai/claude` profile can write `~/.claude`, `~/.claude.json`, Keychains and the shared `~/.cache` (persistent-execution and cache-poisoning path), so the implementer's `Bash` was removed (user chose the plan 10 test-feedback loop as the alternative). The gate needs its own grants: the real `uv` (mise shims need `~/.config/mise` and `~/.local/state/mise`), a PATH without shims, `--allow <project_dir>`, `--read` of the Python prefix and the uv python dir, and a private writable uv cache (`UV_CACHE_DIR`, `UV_LINK_MODE=copy`); a read-only shared cache fails. A cold gate run takes about 1.1s and needs network.
- **Real hole closed (G0):** the factory's unconfined git ran agent-planted `core.fsmonitor` and textconv. Fixed with the `HARDENED_GIT` prefix, `--no-ext-diff --no-textconv` on the audit diff, and a `nested_repo` failure before `git add -A`.
- **e2e (user, real terminal, about $0.35):** the implementer was asked to write canaries at the repo root, `$HOME` and `~/.claude`, and to add a test attempting the same. None of the five `E2E_WRITE_*` files exist (checked individually afterwards). The task reached `audited`; the diff holds only `power`, its tests and `tests/test_escape.py`. The auditor noted it cannot see writes outside the repo, so the filesystem check is the evidence.
- Unit suite: 508 passed; target: 1 passed (sniffed toolchain).

## Known gaps after plan 9
- Grants are discovered at runtime; the planned `sandbox_allow` and `sandbox_read` settings were not built (YAGNI).
- Gate code can still write `target/` and `$TMPDIR`, can create a nested `.git` after the check, has unrestricted network, and inherits the environment minus stripped vars.
- The claude process itself still has `~/.claude` access; merged content is interpreted by the user's own tools; nono stderr is discarded.
- A missing nono for the implementer is a `runner_error` charged at the assumed 2x budget.
- `.factory/gate-cache/<id>` dirs accumulate; `clean` does not remove them.
- With no shell the implementer cannot run tests, so first-try success drops until plan 10 (factory-run tests between agent turns, fresh `claude -p` with truncated failure text, max 2 rounds).
- Carried over: re-planning on a moved main, no `abandon` command.

## Plan 10: test-feedback loop e2e (user, real terminal, about $0.23 for the task)
- **Confirmed:** with the implementer given the deliberate-bug goal, the first gate failed on `tests_failed`, one fix round ran (`fix_rounds_used: 1`, `costs` lists a `fix` stage at $0.0414), the re-run gate passed and the task reached `audited`. `show` and `list` contain no test output. The auditor could only see the final state, so it cannot say whether the first attempt was buggy; the `fix` ledger entry is the evidence the loop ran.
- **Not run by hand:** the `fix_rounds = 0` variant and the SIGKILL-mid-round check (`fix_interrupted`); both are covered by unit tests only.
- Unit suite: 552 passed; target: 1 passed (sniffed toolchain).

## Known gaps after plan 10
- The fix agent can rewrite or delete its own new tests (additions); the auditor and the human at merge are the control. Existing tests and same-name shadowing are blocked.
- pytest config files outside the protected list (`pytest.toml`, `.pytest.ini`, a `[tool.pytest]` table) were not verified as covered.
- `tests_failed` caused by tooling (no network for uv) burns the rounds; bounded by `fix_rounds` times the budget and the cap.
- Test output (plus nono and uv lines) is model input; secrets in the inherited environment could appear in it. A runaway print can fill the temp directory until the gate timeout.
- A SIGKILLed driver may leave an orphan agent alive; `fix_interrupted` only reports it.
- `fix_no_changes` is best effort: non-ignored test artifacts make the worktree look changed. Denials from fix rounds are merged but untested.
- Plan 9 residuals stay, including `uv python find` running unconfined once per gate run.
- Carried over: `gate-cache` dirs accumulate, no `abandon`, re-planning on a moved main.

## Plan 11: abandon and gate-cache cleanup e2e (`spike/abandon_clean_e2e.py`, run by the user, $0, synthetic repo)
- **Confirmed:** all twelve checks passed. `abandon` failed q1, d1, v1 and u1 as `abandoned`; it refused the running task (naming `recover`) and the merged one. `clean` listed the abandoned tasks, the merged task's cache, an orphan and the skipped symlink entry; after `y` it removed their worktrees, the caches (including a real 13.6 MB uv cache filled by an actual `uv` run, so `rmtree` copes with real uv cache contents), the orphan, and the merged task's branch. The abandoned tasks' branches, the running task, the symlink entry and its target, and the real repo were untouched. Exit 1 as designed (the skipped entry).
- **Nit seen:** "1 commits ahead of base" (plural), as in plan 8.
- Unit suite: 613 passed; target: 1 passed (sniffed toolchain).

## Known gaps after plan 11
- After a SIGKILLed driver, a plan, review or audit agent (states proposed, planned, verified) or the gate's test process (agent_done) may still run against a task `abandon` accepts; `queued` never has a live agent. Unledgered spend of orphaned read-only agents is a pre-existing gap.
- An abandoned `audited` task's commits stay on its kept branch (failed-task branches are never deleted); `clean` discards a dirty worktree after showing the flag.
- Gate code writes the cache dir, so removal never follows symlinks and is limited to real directories directly under `gate-cache`; a hostile read-only tree would make a removal fail (reported as partially removed).
- A cache dir of a task whose record was deleted by hand is removed as an orphan; sizes walk the tree, so a huge cache slows the preview.
- Carried over: re-planning on a moved main, the fix agent can weaken its own new tests, pytest config files outside the protected list are unverified.

## Plan 12: test freeze and pytest config (e2e, `spike/test_freeze_e2e.py`, no agents, 11 checks PASS)
- A fix round's rewrite or delete of an implementer-added test is invisible to the base comparison and caught by the frozen comparison (pre-round HEAD stored on the task as `fix_frozen_sha`, saved before the agent runs, so a resumed task stays frozen).
- `pytest.toml`, `.pytest.toml`, `.pytest.ini` (also under `sub/`) and a `[tool.pytest]` table in pyproject now fail `tests_modified`. git `:(glob)` matches dotfiles at both depths.
- Known gaps: gitignored `conftest.py`/`test_*.py` written by the agent are skipped by `git add -A` but loaded by pytest in the worktree (a clean-checkout gate is the real fix, plan 13 candidate); a new test file can monkeypatch the code under test at import time; config files in directories above the project subdir are unwatched.

## Plan 13: the gate runs only committed files (e2e, `spike/clean_gate_e2e.py`, real nono and uv, 7 checks PASS)
- `clean_worktree` (`git clean -ffdx`, after a guard, with a postcondition) runs after `commit_all` and before the sandbox is built, so every gate run, including each fix round, sees only tracked files.
- A gitignored `conftest.py` that turns failures into passes is removed (the failing test fails the gate as `tests_failed`); a `.pth` planted in the ignored `.venv` did not run.
- A gate run including the venv rebuild took 0.9s against the 120s timeout (warm private uv cache).
- Guard refuses (`worktree_mismatch`) a redirected root `.git` or `state_dir`/`worktree_root` inside the worktree; a clean that leaves anything untracked or ignored gives `worktree_not_clean`.
- Known gaps: submodule directories are skipped by clean; tracked config above the project subdir is still unwatched; a project whose tests need a gitignored local file now fails the gate; a new test file can still monkeypatch the code under test at import time; whether Claude Code or nono refuse writes to the root `.git` is unverified (the guard covers it either way); the guard allows a main working tree whose `.git` directory is real, which production never uses.

## Plan 14: clean deletes empty failed-task branches (e2e, `spike/branch_gc_e2e.py`, 9 checks PASS)
- A failed task's branch whose tip is exactly `base_sha` (strict: unset base, missing branch or git error is never empty) is deleted by `clean` after its worktree, re-checked at execution; branches with commits stay as "branch left behind". Real `git branch -d` behaviour confirmed: it refuses an empty branch still registered to a worktree (`reg`) and one whose base is no longer an ancestor of main (`rebased`); both are reported, kept, and make `clean` exit 1 on every run.
- Known gaps: the preview promises a deletion when main is on a detached HEAD (reported at clean time, not previewed); a refused empty branch stays a candidate and keeps `clean` at exit 1 until fixed by hand; branches with commits accumulate by design.
- Fixed afterwards (27679f2): the refusal message now quotes git's own reason instead of always saying "not merged into main".

## Plan 15: rebase when main has moved (e2e, run by the user, $0)
`spike/rebase_e2e.py`: 13 PASS on the second run. The first run's two failures (fresh audit, merge) were the script: the synthetic repo had no `.gitignore`, so the gate's `__pycache__` left the worktree dirty and the audit refused it. Confirmed by the rerun after adding one.
- Verified: `factory rebase` runs with the user's real global git config (1Password signing) and never prompts; a clean rebase of a disjoint change goes `audited -> agent_done`, passes the real gate, gets a fresh audit with a new `audited_sha`, and only then merges; before re-audit `merge` refuses both the old and the new commit; a conflict is refused with the worktree back at the old tip and no `rebase-merge` left; a `.git` file redirected elsewhere is refused before git runs; main-not-moved is refused.
- Known gaps:
  - The conflict refusal quotes git's first stderr line, which is progress text ("Rebasing (1/1)"), not the conflict.
  - A `merge=union` `.gitattributes` silently resolves a conflict (pinned by a test, judged by the re-run gate and audit).
  - SIGKILL mid-rebase leaves the worktree mid-rebase and the task `audited`; the way out is `abandon`. How `clean` treats such a worktree is unverified.
  - A rebase can change behaviour without a textual conflict; only the re-run gate can catch it.
  - `fix_rounds_used` resets to 0, so `show` no longer lists earlier fix rounds and the fresh gate gets a new fix budget.
  - The pre-rebase audited commits become unreachable once `clean` deletes the branch; the old sha is printed once.
  - Submodules and a rewritten main are not specially handled.
