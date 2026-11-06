# Plan 2: real runner, worktrees, commit and gate

Goal: run one real headless agent on one task in its own git worktree, commit its work, and gate it on real state (real files changed, tests exit 0). Everything except the probe and the final smoke test is exercised without the API, using a fake `claude` script and real local git.

## Decided (not up for review)
- uv, pytest, stdlib only. OAuth. Isolation flags stay on.
- The orchestrator commits (unsigned, fixed identity, `--no-verify`), never the agent. Gates check git state and test exit codes, never agent claims.
- TDD, one red-green-commit cycle per bullet. Never edit a passing test; this plan needs none (checked against `tests/test_step.py`).
- **Seam:** the `Runner` Protocol and `step` signature do not change. `SubprocessRunner` is given the worktree manager and reads the path from `Task.worktree`. `RunResult` gains a defaulted last field `timed_out: bool = False`.
- **Worktree scope:** the worktree is the whole factory repo; the project lives at `project_subdir` (e.g. `target`), used for the agent's `cwd` and the gate. The agent can still see `orchestrator/` and `spike/`; accepted.
- **Cost policy:** on timeout or runner exception the ledger records `2 * budget_usd`, flagged `assumed`. Unparseable-but-exited keeps 0.0 (plan 1 test unchanged). `classify` stays pure; the policy lives in the run driver. The CLI cap is soft ($0.069 spent against $0.02), so the assumed cost is an estimate, not a bound.
- `Task` gains `worktree` and `base_sha` (default `None`); old JSON still loads. State gains `verified`; `failed` stays terminal.
- A non-queued task fails with `InvalidTransition` before `MissingBudget` is considered.

## Cycles
0. **Probe (throwaway `spike/probe3.py`, saves exit codes):** check by running, not by `--help` (it omits flags): `--append-system-prompt`, `--max-turns`, `--permission-prompts none`, and whether a Bash-tool child shares claude's process group (`ps -o pgid`) and whether `ANTHROPIC_API_KEY` set in env changes auth. Correct `docs/spike-findings.md` (it wrongly says `--max-turns` does not exist). Unconfirmed flags are dropped from later cycles.
A. **Budget**
   - A1 `budget_usd is None` -> `MissingBudget`, runner not called, task stays queued
   - A2 non-queued task with no budget -> `InvalidTransition` wins
B. **Command**
   - B1 `build_command(..., executable="claude")`
   - B2 `append_system_prompt` -> flag (or prompt fallback per cycle 0)
   - B3 prompt passed as `-p -- <prompt>` (a prompt starting with `-` is not a flag)
   - B4 `max_turns` -> `--max-turns N` (if confirmed by cycle 0)
   - B5 `disallowed_tools` -> `--disallowedTools` (default `Bash(git *)`; defence in depth, the gate does not rely on it)
C. **Task fields**
   - C1 `Task.worktree`, `Task.base_sha` round-trip through the store; JSON without them loads
D. **WorktreeManager(repo, root)** (test repos built with `GIT_CONFIG_GLOBAL=/dev/null` and a fixed identity)
   - D0 refactor: extract the safe-id check shared with `TaskStore`; store tests untouched
   - D1 `create(id)` -> `(path, base_sha)`: new branch `factory/<id>` at that exact SHA
   - D2 existing directory -> `WorktreeExists`
   - D3 existing branch, no directory -> `BranchExists`
   - D4 `remove(id)` deletes even a dirty worktree (`--force`), keeps the branch
   - D5 unsafe id rejected
E. **SubprocessRunner(executable, worktrees, project_subdir, timeout_s)**
   - E1 runs in `<worktree>/<project_subdir>` with `stdin=DEVNULL`; returns stdout, exit code; stderr captured to a file (never an unread pipe)
   - E2 env overlay merged over the parent environment
   - E3 `VIRTUAL_ENV`, `GIT_DIR`, `GIT_WORK_TREE`, `ANTHROPIC_API_KEY` stripped from the child
   - E4 non-zero exit code kept
   - E5 `RunResult.timed_out` defaults to False
   - E6 timeout: `Popen(start_new_session=True)`, output to temp files, SIGTERM the group, grace, SIGKILL, bounded wait; a backgrounded grandchild holding stdout is killed and the call returns within the bound; `timed_out=True`
F. **Run driver `run_task`** (wraps `step`; budget check -> `create` -> persist `worktree`/`base_sha` -> `START` persisted -> run)
   - F1 `create` failure leaves the task queued, runner not called
   - F2 `timed_out` -> `failed("timeout")`, ledger records `2 * budget_usd` with `assumed: true` (`Ledger.append(..., assumed=False)` default)
   - F3 runner exception -> `failed("runner_error")`, same assumed cost; never stuck in `running`
G. **Commit and gate**
   - G1 `commit_all(worktree, message)`: unsigned (`-c commit.gpgsign=false`), fixed identity, `--no-verify`; returns True
   - G2 nothing changed -> False, no commit
   - G3 `check_gate(...)` fails `no_commits` when HEAD == `base_sha`; HEAD must descend from `base_sha` (`merge-base --is-ancestor`)
   - G4 fails `no_changes` when the diff `base_sha..HEAD` under `project_subdir` is empty (a commit of only ignored/junk files does not pass)
   - G5 fails `tests_failed` keeping the exit code; tests run in `<worktree>/<project_subdir>` with a timeout and `stdin=DEVNULL`; a `project_subdir` that escapes the worktree is rejected
   - G6 passes when a real change exists and tests exit 0
   - G7 state machine: `agent_done` --`gate_passed`--> `verified`; --`gate_failed(reason)`--> `failed`
   - G8 `verify_task(task_id, ...)`: reads `worktree`/`base_sha` from the Task, commits, gates, transitions, persists; non-`agent_done` -> `InvalidTransition`
H. **Throwaway end to end (`spike/e2e.py`, not in the suite):** one real task against `target/` (add `subtract`), $0.50 budget, tools include `Bash(uv run pytest*)`, explicit cap and ledger, worktree root `.worktrees/`. Print states, cost, branch. Then the `verify` skill and update `docs/spike-findings.md`.

## Known gaps (not fixed in plan 2)
- Test tampering: an agent that deletes or weakens a test still passes `tests_failed`. Needs the review/audit stage.
- Assumed-cost multiplier is a guess against a soft cap.
- A grandchild that calls `setsid` escapes the process-group kill (cycle 0 checks the Bash tool's behaviour only).

## Out of scope
Plan/review/audit stages, retry, concurrency, daily cap, crash recovery (stale `running` tasks, leftover worktrees), `TaskStore.list()`, CLI, worktree garbage collection, rate-limit handling.

## Assumptions
- Max-turns, auth and rate-limit result shapes are unobserved; they classify as `error`.
- Isolation checks relied on model self-report, not a context log.
- Reviewer's claims about the CLI docs (`--max-turns`, `--permission-prompts`, API-key precedence) are unverified until cycle 0.
