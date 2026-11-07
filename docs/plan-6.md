# Plan 6: confine the read-only agents with nono

Goal: stop the planner, reviewer and auditor reading outside their worktree (plan 4 spike: they read `../ROOT_NOTE.txt` and a file in an unrelated temp dir). Mechanism: run their `claude -p` under `nono` (Seatbelt, macOS) with the `nolabs-ai/claude` profile. The goal is "reads confined to the worktree **plus an enumerated residual set**"; cycle 0 records that set, and it is written into the known gaps. Not "worktree only": the profile grants `~/.claude` and Keychains (see Residual exposure).

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per bullet, failing test shown red first, never edit a passing test. Isolation flags stay on; OAuth stays (`ANTHROPIC_API_KEY` stays stripped).
- **nono is the mechanism; claude under nono always uses the `nolabs-ai/claude` profile.** Permission-rule mechanisms (`--settings` deny rules, dropping `Read` from the allow list) are not alternatives; they are at most an optional layer, probed in cycle 0h.
- Boundary = the **worktree root**, not `project_subdir` (the auditor reads files outside `project_subdir` to verify a diff). Two layers, both required: nono `--read <resolved worktree>` (OS) and claude's own default headless rules widened with `--add-dir <resolved worktree>` (tool level). **Chosen layering (probe round 2): confined stages run with an empty `--allowedTools`** (default rules confine Read and Grep to cwd plus added dirs) instead of an explicit `Read Grep Glob` allow list, because an explicit allow list lets Read and Grep reach `$TMPDIR` and `~/.claude`.
- **`Glob` is disallowed for the confined stages**: under nono it silently returns nothing, even for files in the cwd (round 2), so an agent would conclude files do not exist. Disallowing it gives a clear error instead.
- **Second allowed edit to passing tests** (the first was the plan 5 tripwire), listed here so it is approved with the plan: the four assertions that pin the old tool lists, `PLAN_CONFIG` / `REVIEW_CONFIG` / `AUDIT_CONFIG` in `test_plan_task.py`, `test_review_plan.py`, `test_audit_task.py` (allowed `()` and `Glob` added to the disallowed set), and the `Read Grep Glob` argv assertion in `test_cli_wiring.py` (the argv now has no `--allowedTools` and does have `--add-dir`). Each is changed in the same increment as the config, nowhere else.
- Only the plan, review and audit stages are confined. The implementer is not (it needs write tools and `Bash`; confining it is a future plan).
- Only the executable is a setting (`sandbox_executable`, default `nono`, so tests can inject a fake); the profile name is a constant. No fallback: if confinement is requested and cannot start, the stage fails as infrastructure; claude never runs unconfined.
- `RunnerConfig`s are frozen module constants and `build_command` never sees the worktree, so the path is added per task in `SubprocessRunner.run`. The runner and settings therefore change (the stage functions, state machine and CLI behaviour do not).
- No API calls in the unit suite. A nested `nono run` is denied inside the agent's own nono session (audit dir `~/.local/state/nono/audit` not granted; confirmed with `nono why --self`), so unit and wiring tests use a fake nono; the human runs the spike and the e2e from a real terminal.
- Consequence to document: `factory advance` run from inside an agent's nono session fails at the plan stage (infrastructure `plan_error`), because nested nono is denied. The agent cannot exercise the real pipeline any more; the human does.

## Facts so far
- **Confirmed (user's terminal, plain `nono run --allow-cwd --read <dir>`, canary under `$HOME`):** read inside works; a read outside, a read through a symlink inside pointing outside, and a write inside were all denied (`Operation not permitted`).
- **Confirmed:** the same run with a canary under `mktemp` (`/var/folders`) read it. Temp is therefore inside nono's default grants (inferred), which is exactly the plan 4 escape. Canaries must be placed deliberately.
- **Confirmed:** a confined `claude -p` with only `--read <dir>` says "Not logged in": it needs `~/.claude` and the Keychain, hence the profile.
- **Read from `nono profile show nolabs-ai/claude` (not yet exercised):** extends `default`; r+w on `~/.claude`, `~/.cache/claude`, `~/Library/Keychains` (`bypass_protection`), `~/.claude.json`; workdir ReadWrite; keeps `deny_credentials`, `deny_shell_history`, `deny_shell_configs`, `deny_browser_data_*`.
- Default `worktree_root` is `repo/.worktrees`, so a worktree sits inside the main repo: the repo root, `repo/.factory/` (every goal, plan, ledger) and `repo/.git` (via the worktree's gitdir file) are its nearest neighbours.
- Unverified: that `--allow-cwd` is needed given the profile's workdir grant; fork vs exec; stdout/exit-code passthrough; Claude's git detection under the sandbox; nono's path canonicalisation (macOS `/var` is `/private/var`, so pass `Path(worktree).resolve()`).

## Cycle 0 results (done; details in `docs/spike-findings.md`)
- nono alone: Read and Grep reach the worktree, `$TMPDIR` and `~/.claude`; repo root, `.factory`, main `.git`, `$HOME` and symlinks out are denied. nono cannot drop the temp grant (the profile guide says inherited filesystem paths cannot be removed; only whole groups can be excluded).
- nono plus empty allow list plus `--add-dir <worktree>`: Read and Grep find exactly the control file and the worktree-root file and nothing else (round 2). This is the chosen layer.
- nono plus allow list plus `--settings` deny rules (`~/.claude`, `$TMPDIR`) also worked for Read and Grep, so it is the fallback if a later claude version breaks default-rule confinement.
- Glob: works unsandboxed (and reaches everywhere), returns nothing under nono; unresolved cause, handled by disallowing it.
- Not tested: the default-rule layer without nono; Read deny rules on Glob; why Glob fails under nono.

## Cycles (cycle 0 below is complete; kept for the record)
0. **Spike (human runs it; throwaway, flagged; ~$0.30 real API; exit codes saved).** `spike/nono_probe.py` or documented commands; results in `docs/spike-findings.md`. Fixture: a temp git repo under `$HOME` with a worktree at `repo/.worktrees/x`, `project_subdir=target`. Random-token canaries at: worktree root, repo root, `repo/.factory/`, `$HOME/canary`, `$TMPDIR/canary`; a symlink in `target/` to the `$HOME` canary. Record, each with its exit code:
   - a. effective grant list: `nono profile show default`, `nolabs-ai/claude`, `nono why` per canary path (and `--dry-run` if 0.81 has it)
   - b. the real `build_command` argv under `nono run --profile nolabs-ai/claude --read <resolved worktree> [--allow-cwd?] -- ...`: exit 0, stdout parses as JSON, structured output present
   - c. same with a bad model: non-zero exit passes through; stderr text noted
   - d. fork vs exec (`pgrep -P <nono pid>` while running)
   - e. one run per tool family (Read, Grep, Glob), each asked to quote every canary; **scored by canary tokens in `result`/`structured_output`, never by self-report** (denials appear as tool errors, not `permission_denials`, and a confined run still classifies `SUCCESS`)
   - f. worktree-root canary readable (expected: yes); repo root, `.factory`, `$HOME`, symlink out expected denied; `$TMPDIR` as found
   - g. Claude's git-repo detection with the main `.git` not granted (does it still work? decision: do **not** grant the main `.git`)
   - h. optional layering: with `Read Grep Glob` dropped from the allow list, and with a `--settings` deny on `~/.claude/**` (also whether `--settings` is honoured under `--setting-sources ""`)
   - i. whether a derived profile (`extends: nolabs-ai/claude`) can drop the `$TMPDIR` grant, drop `~/.claude/projects`, or make the workdir read-only
   - **Decision rule (agreed):** if a derived profile can remove the temp grant, narrow it and ship that; if not, accept temp as residual exposure and say so. Layering (h) and `~/.claude/projects` narrowing (i) are decided with the user after seeing the results, before cycle A.
A. **Config and command (`command.py`, `settings.py`)**
   - A1 pure `sandbox_prefix(executable, worktree) -> list[str]`: `[executable, "run", "--profile", PROFILE, ... "--read", <resolved worktree>, "--", ...]` (exact flags from cycle 0); test the exact argv, that the worktree is resolved, and that it ends with `--`
   - A2 `RunnerConfig.confine: bool = False`; `build_command(..., add_dirs=())` emits `--add-dir <dir>` per entry; the default leaves argv unchanged (existing command tests untouched)
   - A3 `Settings.sandbox_executable` (default `"nono"`); add to `_STRING_KEYS` / `_KNOWN`; tests: loads from TOML, wrong type rejected naming the key
R. **Runner (`runner.py`)**
   - R1 `SubprocessRunner(sandbox_executable=...)`; when `config.confine`, the claude argv is built with `add_dirs=(resolved worktree,)` and prefixed by `sandbox_prefix(..., task.worktree)`; test with a fake wrapper that logs its argv and execs everything after `--`: logged worktree path, `--add-dir` and the claude argv after `--`
   - R2 fail closed: confinement requested and the wrapper executable missing, or exiting non-zero with empty stdout (a missing profile), gives an infrastructure result (`runner_error` / error kind); claude is never started unconfined; test both
   - R3 timeout and interruption still kill the whole tree through a wrapper that forks the child and sleeps (assert the grandchild is gone)
   - R4 stdout passthrough: a wrapper banner on stderr only does not corrupt stdout JSON (shape taken from cycle 0b, c, d)
B. **Apply to the read-only stages**
   - B1 `PLAN_CONFIG`, `REVIEW_CONFIG`, `AUDIT_CONFIG` have `confine=True`, `allowed_tools=()` and `Glob` in `disallowed_tools` (the four pinned-assertion edits above happen here)
   - B2 `build_stages` / wiring: the implementer config has `confine=False`; `cli.py` passes `settings.sandbox_executable` into the runner
C. **Wiring test ($0).** `test_cli_wiring.py` sets `sandbox_executable` to a fake nono (logs argv, execs the rest); plan and review argv start with the nono prefix and the fake claude still receives its original argv; implementer argv has no prefix (existing assertions untouched)
D. **Throwaway e2e (human, real terminal, not under an agent session).** The plan 5 flow with a goal that asks the agent to quote the repo-root and `$HOME` canary tokens. Check the tokens are absent from `factory show` and the pipeline outcome is sensible. Then run `verify`, update `docs/spike-findings.md` and the known gaps, sign.

## Residual exposure (accepted)
- With the chosen layering the tool-level read set is the worktree only (round 2: temp and `~/.claude` absent for Read and Grep). nono still grants `$TMPDIR`, `~/.claude` (r+w), `~/.claude.json` and the Keychain files at the OS level, so they are exposed only if claude's own permission engine fails, or to anything else the agent's process can do. Defence in depth, not a clean boundary.
- Confinement of the tool layer depends on claude's default headless rules; a claude upgrade could change them. Fallback: `--settings` deny rules (worked in round 2). The e2e canary run is the manual regression check.
- The workdir is OS-writable (profile ReadWrite plus `--allow-cwd`); write-protection of the read-only stages rests on the tool disallow-list and the post-run dirty/HEAD check, as before.
- `Glob` is unavailable to the confined agents (silent empty results under nono); planning and review rely on Read and Grep.
- nono stderr is discarded by the runner (plan 2 gap), so a profile or sandbox error shows up only as an empty-output infrastructure failure; bounded stderr capture is out of scope.
- The implementer remains unconfined.
- Plans 3 to 5 gaps otherwise unchanged (TTY check is process control, SIGKILL leaves `running`, orphaned worktrees, the auditor judges the plan not the goal).

## Out of scope
Confining the implementer, network egress rules (`--block-net` and domain filtering), Linux (Landlock) support, capturing stderr, retry, merge and GC work.
