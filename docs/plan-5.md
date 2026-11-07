# Plan 5: CLI and pipeline driver

Goal: replace the hand-written `spike/e2e*.py` scripts with a `factory` command that drives a task through the whole pipeline, stopping where a human must act. Pipeline unchanged: goal -> plan -> review -> **human approval** -> implement -> verify -> audit. No new agent behaviour.

## Decided (not up for review)
- uv, pytest, stdlib only (`argparse`, `tomllib`, `fcntl`). Python 3.14. TDD, one red-green-commit per bullet, failing test shown red first, never edit a passing test.
- No API calls in the test suite: the CLI and driver are tested with fakes, a fake `claude` executable and real local git. Only the cycle G run is paid, and the human runs it.
- Stage functions (`plan_task`, `review_plan`, `run_task`, `verify_task`, `audit_task`, `approve_plan`, `reject_plan`) and `step` do not change. `runner.py` changes only in cycle R (process cleanup).
- Approval stays human-only by process: only the `approve` command calls `approve_plan`, and only after an interactive confirmation of the plan it displayed. `approve_plan` is imported directly in `cli.py`, never injected through deps, so the tripwire regex can see it.
- **Per-stage failure behaviour (what `advance` can retry):**
  - plan, review, audit: infrastructure failure leaves the state unchanged and sets `plan_error` / `review_error` / `audit_error`; re-running `advance` retries.
  - implement (`step`): a timeout, runner error or any non-success outcome moves the task to `failed`, which is terminal. A flaky network during implement means a new task and a new approval (known gap).
  - verify: never leaves the state unchanged; only exceptions escape. A task left `agent_done` by a crash is fine to re-verify (`commit_all` is a no-op on a clean tree).
  - `queued` with a dirty or moved pre-made worktree raises `WorktreeNotPristine` on every run; there is no `queued -> failed` edge. Reported as needing human action.
- Anything that merges or releases must require `audited` (no merge step here).

## Open decisions (settled by review: a, a, a)
1. Approval: `factory approve <id>` prints goal, plan, risks and review findings, then requires `y` on an interactive terminal; no `--digest` flag.
2. Configuration: optional `factory.toml`; discovery is `--config PATH` or `./factory.toml` only; absent file means defaults.
3. Commands: one `advance <id>` plus `new`, `list`, `show`, `approve`, `reject`, `costs`.

## Cycles
0. **Spike (throwaway, flagged; no API).** `spike/cli_probe.py`, results in `docs/spike-findings.md`. Check by running: a `[project.scripts] factory = factory.cli:run` entry installs and runs under `uv run --project orchestrator factory --help` with the `uv_build` backend, and what the cwd is there; `sys.stdin.isatty()` is False when piped and True in a real terminal under `uv run`. The user also confirms by hand that `isatty()` is False for a command run from a Claude Code Bash tool call, and whether `</dev/tty` reaches their terminal from there. Unconfirmed items are labelled as such.
R. **Runner cleanup on interruption (`runner.py`)**
   - R1 an exception raised while waiting (`KeyboardInterrupt` or any `BaseException`) kills the descendant tree and re-raises; test with a fake executable (`sleep`) that spawns a child, assert both pids are gone
   - R2 existing runner tests unchanged and green
A. **Store additions**
   - A1 `TaskStore.list()` returns tasks sorted by id from `*.json` files (temp files excluded); empty or missing directory gives `[]`; a file with an unsafe stem or corrupt JSON is skipped and reported by name, not raised
   - A2 `TaskStore.create(task)` writes with `O_EXCL` semantics: an existing id raises `TaskExists`, so two `new` calls in the same second cannot overwrite each other
   - A3 `new_task_id(now, suffix)` returns a safe `YYYYmmdd-HHMMSS`-style id; the CLI retries with a suffix on `TaskExists`
B. **Settings (`settings.py`)**
   - B1 `Settings` dataclass with defaults: `repo` (git toplevel of the cwd; error if not in a repo), `state_dir`, `worktree_root`, `project_subdir`, `test_argv`, `cap_usd`, `gate_timeout_s`, `runner_timeout_s`, `claude_executable` (default `claude`), implementer model, budget, allowed tools, `max_turns`. Ledger path is `state_dir/ledger.jsonl`; tasks live in `state_dir/tasks`
   - B2 `load_settings(path | None)` reads TOML over the defaults. Only `repo`, `state_dir` and `worktree_root` resolve against the config file's directory; `project_subdir` stays relative to the worktree. No path given: use `./factory.toml` if present, else defaults; an explicit missing file raises
   - B3 unknown keys, wrong types and an invalid `project_subdir` (absolute or containing `..`) are rejected with a message naming the key
   - B4 `build_stages(settings, deps)` returns `(task_id) -> Task` callables bound to their configs; tested with a recording runner asserting which config each stage receives: `PLAN_CONFIG` to plan, `REVIEW_CONFIG` to review, `AUDIT_CONFIG` to audit, and the implementer config (the only one with write tools) to `run_task` only
C. **Advance driver (`advance.py`)**
   - C1 action table: `proposed`->plan, `planned`->review, `queued`->implement, `agent_done`->verify, `verified`->audit. Stop states: `reviewed` (needs a human), `audited`, `failed`, `running` (reported as "no driver holds the lock; the agent may still be alive")
   - C2 `advance(task_id, store, stages)` runs actions in order and returns the steps taken plus the final task; stages are injected `(task_id) -> Task` callables
   - C3 a stage that leaves the state unchanged stops the loop with the stage name and the recorded error field; no retry loop
   - C4 a step cap guards against a table that does not terminate (only a cycling fake can trigger it)
   - C5 exceptions from stages are caught, reported with the stage name and the exception itself (never a possibly stale `*_error` field), leaving the task as the stage left it: `BudgetCapReached`, `MissingBudget`, `InvalidTransition`, `WorktreeExists`, `BranchExists`, `subprocess.CalledProcessError`, `ProjectSubdirEscapes` (a settings error); `WorktreeNotPristine` is reported as "needs human action: worktree dirty or moved". Unknown exceptions propagate
   - C6 the driver module does not import `approve_plan` / `reject_plan`
D. **Single-driver lock (`lock.py`)**
   - D1 `state_lock(state_dir)` creates the directory, opens the lock file with `os.open(O_CREAT|O_RDWR)` (never truncating), holds an exclusive non-blocking flock on the open descriptor for the whole run, optionally writes the pid; a second holder in another process raises `AlreadyRunning` (pytest with a subprocess holder and a pipe handshake); released on exit and on exception. Only `advance` takes it
E. **CLI (`cli.py`; `main(argv, io, deps)` with injectable streams, TTY flag and deps)**
   - E1 `new "<goal>"` creates a `proposed` task (`prompt=""`) with `state_dir/tasks` created if missing, and prints the id
   - E2 `list` prints id, state and failure reason per task, noting any skipped files
   - E3 `show <id>` prints state, goal, plan, risks, review findings, audit findings, errors, worktree and branch, with control characters escaped except newline and tab; unknown or unsafe id -> exit 2
   - E4 `advance <id>` takes the lock, runs the driver, prints each step and where it stopped. Exit codes: `reviewed` and `audited` 0 (at `reviewed` it says to run `approve`); a stopped stage, `failed` or stale `running` 1; usage error, unknown id 2
   - E5 `approve <id>`: refuses unless stdin is a TTY (the same injected stdin the confirmation reads from); loads the task once, displays goal, plan, risks and findings with control characters escaped except newline and tab; the digest passed on is `plan_digest(task.plan)` from that single load, never of the escaped text; only `input().strip() == "y"` calls `approve_plan`; anything else, including EOF, changes nothing; `ValueError` (digest mismatch) -> exit 1 "plan changed since display"; non-`reviewed` task -> exit 1 with the state; unknown id -> exit 2. The tripwire test is edited in this same increment to require exactly one `approve_plan(` call in `cli.py` and none elsewhere in `src/factory` (and to use `rglob`); this is the single allowed edit to a passing test
   - E6 `reject <id>` calls `reject_plan` (no interactivity needed: fail-safe, terminal); non-`reviewed` -> exit 1
   - E7 `costs [<id>]` totals the ledger by stage (entries without `stage` grouped as "unknown") and by task, flagging assumed entries
   - E8 `monkeypatch.setattr("factory.cli.approve_plan", spy)` test: no command except `approve` ever calls it, and `approve` calls it only on `y`
F. **Wiring**
   - F1 `[project.scripts] factory = factory.cli:run`; `run()` builds the real deps (`SubprocessRunner` with `settings.claude_executable`, `WorktreeManager`, `Ledger`, `TaskStore`) from `Settings`, creates `state_dir/tasks`, and turns SIGTERM and SIGHUP into exceptions so cycle R's cleanup runs
   - F2 subprocess tests at $0: `factory --help` exits 0; `new` + `list` + `show` against a temp state dir; and a fake `claude` script on `claude_executable` that picks canned JSON by its `--json-schema` argument and logs its argv, driving `new` -> `advance` (plan, review) against a temp git repo, stopping at `reviewed` with exit 0; the logged argv shows the read-only tool lists for plan and review
G. **Throwaway e2e (human runs it):** `factory new`, `advance`, `approve`, `advance`, `show`, `costs` on a real goal in your terminal. Then run `verify`, update `docs/spike-findings.md`.

## Known gaps (accepted)
- Approval's TTY check is a process control, not a security one: a pty or `script` can satisfy it, and anything with store access can still bypass the digest. The realistic threat is an agent session running `factory approve` itself; cycle 0 checks what a Claude Code Bash call sees.
- An implement-stage infrastructure failure is terminal (new task, new approval). A task stuck `queued` on a dirty worktree needs manual repair. No retry or restart of `failed` tasks.
- Crash recovery beyond the runner cleanup: a hard kill (SIGKILL) still leaves a `running` task and possibly a live agent; reported, never repaired.
- Only the global soft cap; no daily or per-task cap; the ledger totals include assumed costs.
- Single machine, single driver (lock), no concurrency across tasks.
- Plan 3 and 4 gaps unchanged: read-only agents can read outside the worktree, gitignored-state and import-time tampering, the auditor judges against the plan not the goal, orphaned worktrees from rejected plans.

## Out of scope
Merge/release, retry and revise loops, daily caps, worktree GC, crash repair, remote or CI execution, any new agent stage.

## Assumptions (unverified until cycle 0)
- The `uv_build` backend installs a console script via `[project.scripts]`.
- `isatty()` distinguishes a real terminal from a pipe under `uv run`, and from a Claude Code Bash call.
- `uv run --project` preserves the cwd (needed for the `repo` default).
