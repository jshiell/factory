# Plan 7: `factory merge` (human-gated fast-forward of an audited task)

Goal: close the loop from goal to code on `main`. Today an `audited` task is a branch `factory/<id>` in a worktree; nothing lands it. Add a human-only `merge` command. Folded from the plan-reviewer's findings.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per bullet, failing test shown red first, never edit a passing test. Never push.
- Strategy: **fast-forward only**: `git -c core.hooksPath=/dev/null merge --ff-only <audited_sha>` in the main repo (the SHA, not the branch name, so the ref cannot move after the guard; hooks off so agent-written hooks never run). Git allows this while the branch is checked out in a worktree (reviewer's knowledge of git, covered by a real-git test here).
- Guards, all required, checked in `merge_preview` before the prompt and again inside `merge_task` before the merge:
  - state is `audited`
  - the branch tip `factory/<id>` equals `audited_sha`, and the worktree HEAD equals `audited_sha`
  - the worktree is clean
  - main's HEAD is a branch (`symbolic-ref -q HEAD`) and `rev-parse HEAD == base_sha` exactly, so the diffstat shown is exactly what lands
  - main has no tracked changes (`git status --porcelain --untracked-files=no`); untracked files are left to git, whose error is surfaced
- `base_sha` is captured at the **plan** stage. Any commit to main between plan and merge makes the merge refuse. This will be common; the message says "main moved since the plan; re-run the task". Rebase and re-verify are out of scope.
- Human-only by process, like approve: interactive TTY, shows task id, branch, the whole-tree diffstat `base_sha..audited_sha` (`--stat=200`, through `escape_control`) and the audit findings, then `y/N`. `merge_task` takes the SHA the human was shown and refuses on mismatch.
- Order: guards, ff, save `MERGED` (clearing `task.worktree`), then best-effort worktree removal (failure reported, not fatal). The branch is kept.
- `merge.verifySignatures`: do not override. If the user has it set, git refuses the unsigned factory commits and the error is surfaced (no `--no-verify-signatures`).
- Crash recovery: if the task is `audited` and main HEAD, the branch tip and `audited_sha` all agree (an ff happened but the save did not), `merge_task` records `MERGED` without running git merge, after the same y/N.
- `state_lock` is held around the guards, merge and save; `AlreadyRunning` gives exit 1.
- New `TaskState.MERGED`, `Event.MERGED`, transition `(AUDITED, MERGED) -> MERGED`; MERGED is terminal. No `merged_sha` field (under ff-only it equals `audited_sha`). `advance` stops at `audited` as now and never references merge.
- `_merge` builds `WorktreeManager(settings.repo, settings.worktree_root)` itself; `Context` is unchanged (so no passing test is edited).
- Commits stay factory-identity and unsigned; the output tells the human to sign before pushing.

## Cycles
M1. **State** (`task.py`): one test, `(AUDITED, MERGED) -> MERGED` and MERGED rejects every event; implement. (Store needs no change: `asdict` and defaults already round-trip.)
M2. **`merge_task.py`**, real git in tmp repos. `MergeRefused(reason)` exception.
   - a. `merge_preview(task_id, store, repo) -> Preview(branch, diffstat, findings)` returns for a good audited task; each guard refuses with a distinct reason: not audited (InvalidTransition), branch tip or worktree HEAD != audited_sha, dirty worktree, main HEAD != base_sha (moved, or reset backwards), main detached, main tracked changes
   - b. `merge_task(..., shown_sha)` refuses on `shown_sha != audited_sha`; nothing changes
   - c. happy path: main HEAD == audited_sha, task `MERGED` and saved, `worktree` cleared
   - d. worktree directory removed and the branch kept
   - e. removal failure after a successful ff still ends `MERGED`
   - f. crash recovery: main already at audited_sha, branch tip equal, task `audited` gives `MERGED` without a merge
   - g. hooks disabled: a tracked hooks dir set via `core.hooksPath` whose post-merge hook writes a file; the file is absent after merge
   - h. main moved or reset: refuses and main is unchanged
M3. **CLI `merge <id>`** (`cli.py`): TTY required (exit 1 `merge needs an interactive terminal`); loads the preview, prints it, y/N, calls `merge_task` under `state_lock`; maps `MergeRefused`, `InvalidTransition`, `CalledProcessError` and `AlreadyRunning` to exit 1 with a message; on success prints "merged <sha>; commits are unsigned: sign them before pushing". Tests in the `test_cli.py` style with fake stdin, plus a `test_cli_wiring` subprocess test: non-TTY merge changes nothing.
M4. **Tripwire** (new test, edits nothing): `merge_task(` is called exactly once outside `merge_task.py`, in `cli.py`; `Event.MERGED` appears only in `merge_task.py` and `task.py`; `advance.py` does not mention `merge_task` or `merge_preview`.
M5. **Throwaway e2e (human, real terminal).** Not a clone or copy of the real repo (the task JSON holds an absolute worktree path into the real repo and `worktree remove --force` could hit it). Build a synthetic audited task in a tmp repo (script under `spike/`, flagged throwaway) with its own `factory.toml`, run `factory merge`: main HEAD moves, worktree gone, branch kept, second merge refused, and assert afterwards that the real repo's worktrees are untouched.

## Residual exposure (accepted)
- "Human-only" is process, not mechanism (TTY check, as for approve).
- Merged commits are unsigned until the human signs them.
- The auditor judges the plan, not the goal; merge trusts `audited`. `commit_all` stages the whole worktree and the auditor reads only the `project_subdir` diff, so the diffstat at merge covers the whole tree to expose out-of-subdir changes.
- The implementer could in principle make its own commits (only `Bash(git *)` is denied); the tip and `audited_sha` guards plus the whole-tree diffstat are the check.
- No rollback command; `git reset` by hand.
- Main moving during a task makes the merge refuse; no automation for that.

## Out of scope
Rebase and re-verify on a moved main, pushing, signing, deleting branches, GC of orphaned worktrees, retry, confining the implementer, overriding `merge.verifySignatures`.
