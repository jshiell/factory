# Plan 6: confine read-only agents to their worktree

Goal: close the known gap that `Read` (and `Grep`/`Glob`) in the planner, reviewer and auditor can reach files outside the worktree (plan 4 spike: it read `../ROOT_NOTE.txt` and a file in an unrelated temp dir). After this plan, those agents can read only the worktree, and a test proves it. No new agent behaviour.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per bullet, failing test shown red first, never edit a passing test. Isolation flags stay on.
- Stage functions, the state machine, the CLI and the `Runner` Protocol do not change. Only `command.py` / `RunnerConfig` / the three read-only configs change, plus the runner only if cycle 0 needs it.
- No API calls in the unit suite (argv assertions and a fake `claude`). Only the cycle 0 spike and the cycle G e2e are paid; the e2e is run by the human.
- The implementer (write tools) is out of scope: it needs `Bash`, which no path rule confines. Recorded as a known gap, not addressed here.

## Candidate mechanisms (cycle 0 picks one; nothing here is verified yet)
1. **Permission rules via `--settings` JSON:** `deny` rules for `Read`/`Grep`/`Glob` outside the worktree, or dropping `Read` from `--allowedTools` so headless default rules apply (reads inside cwd only, prompts auto-denied). Cheap, in argv.
2. **`--add-dir` / working-directory semantics:** rely on the default "cwd and added dirs only" rule.
3. **OS sandbox via nono (0.81.0, installed, Seatbelt on macOS).** Wrap the `claude -p` argv as `nono run --profile nolabs-ai/claude --allow-cwd --read <worktree> -- claude -p ...` (**decided: use the `nolabs-ai/claude` profile whenever claude is run under nono**).
   - **Confirmed (user's terminal, canary under `$HOME`, plain `--read <dir>`, no profile):** read inside the dir works; a read of a file outside it, a read through a symlink inside it that points outside, and a write inside the dir were all denied (`Operation not permitted`). A first attempt with the canary under `mktemp` (`/var/folders`) read `secret`: temp is evidently inside nono's default grants (hypothesis, not checked), so canaries must live under `$HOME`.
   - **Confirmed:** a confined `claude -p` with only `--read <dir>` returns "Not logged in": it needs `~/.claude` and the macOS Keychain (denied by default, 22 paths blocked). Hence the profile.
   - **From `nono profile show nolabs-ai/claude` (read, not yet exercised with claude):** extends `default`; grants r+w to `~/.claude`, `~/.cache/claude`, `~/Library/Keychains` (with `bypass_protection`), `~/.claude.json`; workdir access is ReadWrite; keeps `deny_credentials`, `deny_shell_history`, `deny_shell_configs`, `deny_browser_data_*`. **Residual exposure this accepts:** the confined agent can read (and write) `~/.claude`, including other sessions' transcripts under `~/.claude/projects`, and the Keychain files. It is far narrower than the whole home directory but is not "worktree only". Whether a derived profile can narrow this (for example read-only workdir, no `~/.claude/projects`) is a cycle 0 question.
   - **Not yet tested:** a real `claude -p` under the profile authenticating and returning structured output; the canary outside the worktree and a symlink out still denied under the profile (the profile's grants could widen it); whether workdir ReadWrite can be made read-only for the three read-only stages; `Bash` confinement for the implementer.
   - **Cannot be tried by the agent:** a nested `nono run` from inside the agent's own nono sandbox is denied (audit dir `~/.local/state/nono/audit` not granted; confirmed by `nono why --self`). The human runs every nono probe and the e2e; unit tests use a fake `claude` and assert argv only.
Nono's filesystem confinement is now the only mechanism with a confirmed result; 1 and 2 are untested. Cycle 0 still compares them, because they would avoid giving the agent Keychain and `~/.claude` access. Choose by residual exposure first, then simplicity; the user decides if the choice is close.

## Cycles
0. **Spike (throwaway, flagged; real API, ~$0.30 total; exit codes saved).** `spike/confine_probe.py`, results in `docs/spike-findings.md`. In a temp git repo with a worktree and a canary file outside it (and one at the worktree root above `project_subdir`), try each mechanism with a read-only agent told to read: the canary outside, `../`, an absolute path, a symlink inside the worktree pointing outside, `~/.ssh`-style home path, and (as a control) a file inside the worktree. Also check `Grep` and `Glob` with an outside path. Record for each mechanism: which reads were blocked, whether the in-worktree read still works, whether the denial is silent or reported (`permission_denials`), and whether `--setting-sources ""` still honours `--settings`. Unconfirmed items are labelled as such. A mechanism that blocks some but not all vectors is a finding, not a pass.
A. **Config surface (`command.py`)**
   - A1 `RunnerConfig` gains the field the chosen mechanism needs (for example `settings: dict | None`); `build_command` emits it; absent field leaves argv unchanged (existing command tests untouched)
   - A2 argv-injection check: the value is passed as a single argv element, not shell-split
B. **Apply to the read-only stages**
   - B1 `PLAN_CONFIG`, `REVIEW_CONFIG`, `AUDIT_CONFIG` carry the confinement; a test per config asserts it (the worktree path is per task, so if the rule needs it, it is built per run in the runner or `readonly_stage`, with a test of what the runner receives)
   - B2 `build_stages` test: the implementer config does not get the confinement and the read-only configs do
C. **Fake-claude wiring test ($0).** Extend `test_cli_wiring.py` so the logged argv for plan and review includes the confinement
D. **Throwaway e2e (human runs it).** `factory new` / `advance` / `approve` / `advance` as in plan 5 with a canary file outside the worktree whose path the goal tells the agent to read; expect it denied and the pipeline to still reach `audited` (or stop with a sensible reason). Then `verify`, update `docs/spike-findings.md`, sign.

## Known gaps (accepted)
- The implementer can still read and write anywhere its `Bash` reaches; confining it needs an OS sandbox (future plan).
- Whatever cycle 0 shows is not blocked (for example symlinks or `Grep` paths) stays a gap and is named.
- The confinement is only as good as the CLI's permission engine; a CLI change could silently loosen it, so the e2e canary is the regression check and is manual.
- Plan 3 to 5 gaps otherwise unchanged (TTY check is process control, SIGKILL leaves `running`, orphaned worktrees, auditor judges the plan not the goal).

## Out of scope
Implementer confinement, OS sandboxing as the default, network egress controls, retry/merge/GC work.

## Assumptions (unverified until cycle 0)
- `--settings` is honoured with `--setting-sources ""`.
- Deny/allow rules or default headless behaviour can express "this directory only" for `Read`, `Grep` and `Glob`.
- A symlink inside the worktree is resolved before the rule is applied.
