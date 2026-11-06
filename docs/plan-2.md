# Plan 2: real runner, worktrees, commit and gate

Goal: run one real headless agent on one task in its own git worktree, commit its work, and gate it on real state (commits exist, tests exit 0). Everything except the final smoke test is exercised without the API, using a fake `claude` script and real local git.

## Decided (from plan 1 and the plan 2 spike; not up for review)
- uv, pytest, stdlib only. OAuth. Isolation flags stay on (`--setting-sources ""`, `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`).
- The orchestrator commits (unsigned), never the agent: the agent gets no git allow rules. Gates check git state and test exit codes, never the agent's claims.
- TDD, one red-green-commit cycle per bullet. Agent-run phase and verify phase are separate driver functions.
- Spike results (`docs/spike-findings.md`): isolation + tools work; repeated `--allowedTools` and `-p -- <prompt>` work; project CLAUDE.md is NOT loaded under isolation, so project guidance goes in via `--append-system-prompt` (flag existence to be checked in cycle 0).
- Task state gains `verified`; `failed` stays terminal.

## Cycles
0. **Check flags (no code):** `claude --help` confirms `--append-system-prompt`. If absent, project guidance goes into the prompt instead (adjust cycle B3).
A. **Budget required by `step`**
   - A1 `config.budget_usd is None` -> `MissingBudget`, runner not called, task stays queued
B. **Runner seam**
   - B1 `Runner.run(task, config, cwd)`: add `cwd`; update `FakeRunner` and the existing step tests (test-helper change only, flagged)
   - B2 `build_command(..., executable="claude")` so tests can point at a fake script
   - B3 `RunnerConfig.append_system_prompt` -> `--append-system-prompt <text>`
C. **WorktreeManager(repo, root)** (real git in `tmp_path`)
   - C1 `create(task_id)` -> directory under root on new branch `factory/<id>` from HEAD
   - C2 existing worktree for the id -> `WorktreeExists`
   - C3 `remove(task_id)` deletes the directory, keeps the branch (the branch is the work product)
   - C4 unsafe ids rejected (same rule as `TaskStore`; extract the shared check)
D. **SubprocessRunner(executable)** (fake `claude` shell script in `tmp_path`)
   - D1 runs with `cwd` set, returns stdout + exit code
   - D2 env overlay merged over the parent environment
   - D3 non-zero exit code preserved
   - D4 timeout kills the whole process group, returns `RunResult(timed_out=True)`
   - D5 `step` maps `timed_out` to `failed("timeout")`
E. **Commit and gate**
   - E1 `commit_all(worktree, message)` makes an unsigned commit, returns True
   - E2 nothing changed -> returns False, no commit
   - E3 `check_gate(worktree, base_ref, test_argv, test_cwd)`: fails with reason `no_commits` when HEAD == base
   - E4 fails with reason `tests_failed` (keeps exit code) when the test command exits non-zero
   - E5 passes when there is a commit and tests exit 0
   - E6 state machine: `agent_done` --`gate_passed`--> `verified`; --`gate_failed(reason)`--> `failed`
   - E7 `verify_task(task_id, store, worktrees, gate)`: commit, gate, transition, persist; non-`agent_done` -> `InvalidTransition`
F. **End to end (throwaway script, not in the suite)** `spike/e2e.py`: one real task against `target/` (add `subtract`), $0.50 budget, run -> verify -> print states, cost, branch. Then run the `verify` skill and update `docs/spike-findings.md`.

## Open decision
Cost on timeout or unparseable output is unknown, so plan 1 records 0.0 and the cap under-counts. Options:
1. Record the run's `budget_usd` as an assumed cost, flagged `assumed` in the ledger (recommended: the cap stays safe; changes the plan 1 test that expects 0.0 for unparseable, with your approval).
2. Keep 0.0 and accept under-counting.

## Out of scope
Plan/review/audit stages, retry, concurrency, daily cap, crash recovery (stale `running` or leftover worktrees), `TaskStore.list()`, CLI, worktree garbage collection, rate-limit handling.

## Assumptions
- Max-turns, auth and rate-limit result shapes are unobserved; they classify as `error` by default.
- Process-group kill (`start_new_session`) is assumed to catch the agent's tool subprocesses; D4 tests it with a fake script, the smoke test does not.
- Agent-claimed results (self-report of isolation) were judged from model self-report, not from a log of the context.
