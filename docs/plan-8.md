# Plan 8: retry, recover and clean (human-run housekeeping)

Goal: remove the manual repairs left after plan 7: a failed task cannot be tried again, a task left `running` by an interrupted driver is stuck forever, and merged, failed or rejected tasks leave worktrees and branches behind. Three human-run commands. No state-machine change. Folded from the plan-reviewer's findings.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test. Never push.
- **`factory retry <id>`**: for a `failed` **or `audited`** task that has a `goal` (audited so that a task whose merge was refused because main moved can be re-run). Creates a **new** `proposed` task with the same goal (plan, review and human approval again, in a fresh worktree). The old task is untouched. The new task records `retry_of` (new optional `Task` field; old JSON loads with the default). No TTY needed: it grants no authority. `show` prints `retry_of`.
- **`factory recover <id>`**: only for a `running` task, and only while it can take `state_lock` (`AlreadyRunning` gives exit 1, nothing changes). Applies the existing `Event.FAILED` with reason `interrupted`, and appends the **assumed cost** to the ledger exactly as `step`'s `runner_error` path does (2x the implement budget, `assumed=True`, stage `implement`), so the cap does not undercount an interrupted run. Prints: the dead driver's pid (read from the lock file), the worktree path, that an agent may still be alive if the driver was SIGKILLed (Ctrl-C, TERM and HUP do kill the agent), and that uncommitted agent work in the worktree is lost if `clean` is run. Human-run, never called by `advance`.
- **`factory clean`**: interactive TTY, `y/N`, lock held from computing the preview to finishing; each task is reloaded before acting and skipped if it is no longer terminal.
  - Candidates: tasks in `merged` or `failed` with `worktree` set, **plus** `merged` tasks whose branch `factory/<id>` still exists (a successful merge removes the worktree but keeps the branch).
  - The worktree path comes from one source, `task.worktree`, and must resolve under `worktree_root` or the task is skipped and reported.
  - Preview per candidate: id, state, directory exists or not, dirty or not, commits ahead of `base_sha`.
  - Action order: remove the worktree (`worktree remove --force`; if the directory is missing, `git worktree prune`), clear `task.worktree`, then for `merged` tasks `git branch -d factory/<id>`. `-d` checks against the HEAD of the main checkout, so branch deletion is only attempted when main is on a branch; otherwise it is reported and skipped. A refused `-d` is reported and the branch kept.
  - A failure in one task (removal error, locked worktree) is reported, that task's field and branch are kept, and clean continues with the others.
  - **Branches of failed tasks are kept.** Non-terminal tasks (including `audited`) are never touched.
- None of the three is called from `advance` or `build_stages`; a tripwire pins that.
- No change to `approve_plan`, `merge_task` or the existing tripwires.

## Cycles
R0. Pin the existing `_new` id collision suffix if `test_cli.py` lacks it (passes at once).
R1. **retry** (`retry_task.py`). Extract the id-with-suffix loop as `create_with_fresh_id(store, clock, build: Callable[[str], Task])`, shared by `_new` and retry (refactor on green).
   - a. refuses a task that is not `failed` or `audited`
   - b. refuses a task with no goal
   - c. happy path from `failed`: new `proposed` task, same goal, `retry_of` set, old task unchanged
   - d. happy path from `audited`
   - e. two retries in the same second get distinct ids
   - f. CLI: exit 0 and the new id on stdout
   - g. CLI: exit 1 with the reason on refusal
   - h. `show` prints `retry_of`
R2. **recover** (`recover_task.py`).
   - a. refuses a task that is not `running`
   - b. `running` becomes `failed` with reason `interrupted`
   - c. an assumed ledger entry (2x implement budget, stage `implement`) is appended
   - d. CLI: lock held gives exit 1, nothing changes, nothing appended
   - e. CLI success prints the pid, the worktree path and the work-loss warning
R3. **clean** (`clean_tasks.py`), real git in tmp repos. `clean_candidates(store, repo, worktree_root)` and `clean_tasks(candidates, ...)`.
   - a. candidates are the merged and failed tasks with a worktree
   - b. a merged task with no worktree but a live branch is a candidate
   - c. a running, audited or proposed task is never a candidate
   - d. flags: directory exists, dirty, commits ahead of base
   - e. a worktree path outside `worktree_root` is skipped and reported
   - f. clean removes the worktree and clears `task.worktree`
   - g. a missing directory (still registered with git) is pruned, the field cleared, and the merged branch then deletes
   - h. a merged task's branch is deleted; a failed task's branch is kept
   - i. a refused `branch -d` is reported, the branch kept, nothing raises
   - j. main not on a branch: branch deletion skipped and reported
   - k. a removal failure on one task is reported, its field and branch kept, the others still cleaned
   - l. a task that is no longer terminal at execution time is skipped
   - m. CLI: TTY required
   - n. CLI: anything but `y` changes nothing
   - o. CLI: lock held gives exit 1
   - p. CLI: nothing to clean says so and exits 0
R4. **Tripwire** (new file): neither `advance.py` nor `stages.py` references `retry_task`, `recover_task`, `clean_tasks` or `clean_candidates`.
R5. **Throwaway e2e (human, real terminal).** `spike/housekeeping_e2e.py`: a synthetic repo with a merged task (branch left), a failed task with a dirty worktree, an audited task and a stale `running` task. Run `recover`, `retry` (failed and audited), `clean`; check states, ledger entry, worktrees, branches, the retry link, that the audited task's worktree is untouched, and that the real repo's worktrees and branches are untouched ($0).

## Residual exposure (accepted)
- flock fds are not inherited and the agent runs in its own session, so a SIGKILLed driver frees the lock while the agent may still be alive; `recover` then succeeds. The printed warning and pid are the only control. Ctrl-C, TERM and HUP kill the agent but also leave the task `running`.
- `clean` uses `worktree remove --force`: a dirty failed worktree is discarded after the human saw the dirty flag. Work of a recovered task is uncommitted (commits happen at verify), so `clean` destroys the only copy.
- Failed-task branches accumulate; deleting them is a manual `git branch -D`.
- An `audited` task that is retried keeps its worktree and branch forever (never a clean candidate). `queued` and `agent_done` tasks stopped by a not-pristine worktree have no command at all.
- Worktrees with no task record are not found.
- The assumed cost on `recover` may overstate what an interrupted run really spent.
- Legacy tasks with no `goal` cannot be retried. The merge message "re-run the task" is now honest only through `retry`.

## Out of scope
An `abandon` command or any state-machine change, reopening a task at its stage, auto-recovery, automatic cleanup, deleting failed branches, tasks with no record, confining the implementer, re-planning on a moved main.
