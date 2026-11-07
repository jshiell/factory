# Plan 17: three loose ends from plans 14 and 15

## Spike (done, $0; scratchpad repo)
- A conflicted rebase leaves the worktree dirty (`UU f`); plain `git worktree remove` refuses it. `clean` runs `worktree remove --force` (`worktrees.py:45`), so a mid-rebase worktree is force-removed after the preview flags it dirty, exactly like any dirty worktree. The branch ref is still at the old tip during a rebase, so no commit is lost. Verified on git's behaviour only; the full `clean` over a mid-rebase worktree was not run.
- git's rebase conflict output starts with progress lines ("Rebasing (1/1)") before `CONFLICT (...)` / `error:`.

## Decided (not up for review)
uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test (except an approved edit list). Never push. The user chose: fix the rebase message, fix the detached-HEAD preview, use `-D` for refused empty branches.

## Behaviours
1. **Rebase conflict message** (`rebase_task.py`): quote the first line of git's output that contains `CONFLICT` or starts with `error:` / `fatal:`; otherwise the first non-empty line (today's behaviour). Still names `factory abandon` then `factory retry`.
2. **Empty failed branch deletion uses `git branch -D`** (`clean_tasks.py`), only on the failed-and-empty path, still re-checked at execution (`refs/heads/factory/<id>` equals `base_sha` immediately before the delete). Merged tasks keep `-d`. `-D` loses nothing here: the tip is exactly the base commit, so there are no unique commits. It fixes the "base no longer an ancestor of main" refusal (after a rebase moved main) that kept `clean` at exit 1 on every run. git still refuses a branch checked out in a worktree; that is reported as today and cannot recur once the worktree is removed first (the order in `_clean_one`).
3. **Detached-HEAD preview:** the `-d` "main is not on a branch" guard exists only because `-d` compares against main's HEAD. With behaviour 2 an empty failed branch is deleted with `-D` whether or not main is detached, so the preview's "empty branch will be deleted" is true in that case; no code change. The merged-task path (still `-d`) is previewed as "branch left behind" when it will in fact be deleted: a different, pre-existing wording nit (conservative, not a false promise), recorded, not changed.
4. **Mid-rebase `clean`:** no code; the findings note records the spike result and that the full `clean` over a mid-rebase worktree is still unrun.

## Cycles
R1. A conflict whose git output has a progress line first: the refusal quotes the `CONFLICT` line, not the progress line (red first); and a failure with no such line falls back to the first non-empty line (guard, starts green).
R2. A failed task whose empty branch `-d` refuses (base no longer an ancestor of main) is deleted and `clean` reports success (red first). Guards (start green): a failed branch with commits is never deleted; a branch that gained a commit between preview and execution is kept; a merged task's branch still uses `-d`; an empty branch while main is on a detached HEAD is deleted.
R3. Wiring: `clean` exits 0 after that deletion.
E1. No e2e: pure git behaviour, covered by real-git tests. Full suite, `verify`, findings note.

## Residual
`-D` is a forced delete for one narrow, re-checked case. Merged-task preview wording. Mid-rebase `clean` unrun end to end.

## Out of scope
The other known gaps (root `[tool.uv]` etc., an import-time monkeypatching test, orphan agents after SIGKILL).
