# Plan 14: `clean` also deletes failed-task branches that hold nothing

Goal: failed tasks leave `factory/<id>` branches behind forever (plans 8 to 11: "failed-task branches stay"). Most are empty (planned, reviewed, abandoned or gate-failed-before-commit tasks never moved the branch). Delete those; keep any branch that holds commits.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test. Never push.
- **Scope (user's choice): `clean` only, zero-commit branches only.** No new command, no force delete. A failed task's branch with commits is kept and listed as such; it stays the human's to inspect or delete by hand.
- A failed task's branch is **empty** only when all of these hold: the branch exists, `task.base_sha` is set, `git rev-list --count base_sha..branch` succeeds and prints 0. Any error or an unset base means not empty (unknown is never deleted). This is a new strict field; the existing `commits_ahead` (which reports 0 on error) is not reused for the decision.
- Deletion uses `git branch -d` (refuses unless merged into main's HEAD; an empty branch's tip is the base commit, so it normally succeeds). A refusal is reported and the branch kept. Main must be on a branch (the existing `_delete_branch` rule and message).
- The branch step runs after the worktree step succeeds or when there is no worktree; a failed worktree step still keeps the branch (existing order). The emptiness is re-checked at execution time, not carried from the preview. The "now <state>" reload check still blocks every step.
- Merged-task branch deletion is unchanged. No change to `merge`, `abandon`, `retry`, `recover`, the gate or the fix loop. Still TTY, y/N preview, state lock.
- Preview shows an empty failed branch as "empty branch will be deleted"; a failed branch with commits keeps today's "branch left behind" flag. A failed task with no worktree but an empty branch becomes a candidate (today it is not).
- Fix the plural nit in the same area: "1 commit ahead of base".

## Cycles
B1. **`Candidate.branch_empty: bool = False`**, computed strictly in `_describe` (tests: empty branch true; one commit false; unknown or unset base false; missing branch false).
B2. **Candidates.** A failed task with no worktree but an empty branch is a candidate; one whose branch has commits, or a failed task with nothing left, is not (unchanged).
B3. **Removal.** `clean_tasks` deletes an empty failed branch (after removing its worktree if present); keeps a branch with commits; reports a `-d` refusal and keeps the branch; keeps it when main is not on a branch; re-checks emptiness at execution (a commit added between preview and `y` keeps the branch); a worktree failure keeps the branch.
B4. **CLI.** The preview says "empty branch will be deleted" for such a task and still says "branch left behind" for one with commits; `y` deletes; anything else changes nothing; "1 commit ahead of base" singular (new test, the "2 commits" test untouched).
E1. **Throwaway e2e (human, $0):** `spike/branch_gc_e2e.py` on a synthetic repo: failed tasks with empty branch (abandoned from queued), a branch with one commit, a dirty worktree, no base, plus a merged task; run `clean` through the real CLI and check which branches survive, and that the real repo is untouched.

## Residual exposure
- A failed branch with commits accumulates until deleted by hand (deliberate).
- `branch -d` can refuse an empty branch if main was reset behind the base; it is reported and kept.
- An empty branch's reflog is gone with the branch (no work lost by definition).

## Out of scope
Deleting branches with commits, a `discard` command, re-planning on a moved main, watching tracked config above the project subdir.
