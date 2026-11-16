# Plan 46: rebase refuses when the main checkout is detached or dirty

Option A of three considered (B: also require the branch be named `main`; C: a configurable `main_branch`). A was chosen: apply the merge preview's two checks to the rebase. It does not close the "checkout is on another branch" risk; that stays in the README (C is the later fix).

## Problem

`rebase_task._require_rebasable` (`orchestrator/src/factory/rebase_task.py`) replays the task onto the server checkout's HEAD without checking the checkout itself. `merge_preview` refuses when the checkout is not on a branch ("main is not on a branch") or has uncommitted tracked changes ("main has uncommitted changes") (`merge_task.py:35-38`). A rebase run in either state spends verify and audit budget on a task the merge would then refuse.

The watcher's offer already implies both checks held (the preview runs them before the `MAIN_MOVED` check), so the watcher path only gains in the window between the offer and the 👍. The terminal `factory rebase` gains fully.

## Behaviour

Before rebasing, the rebase refuses (`RebaseRefused`, nothing changed) if the main checkout is detached ("main is not on a branch") or has uncommitted tracked changes ("main has uncommitted changes"). The wording matches the merge preview's. The watcher needs no change: a failed rebase job already posts `rebase-refused` with the error.

## Checks

- Detached: `run_git(repo, "branch", "--show-current")` is empty.
- Dirty: `run_git(repo, "status", "--porcelain", "--untracked-files=no")` is non-empty. Untracked files are ignored, as in the merge preview; without the flag they would block a rebase the merge allows. Staged-only changes count as dirty. Unmerged entries (a conflicted merge, cherry-pick or revert) count as dirty. A rebase in progress in main detaches HEAD.
- Order: detached before dirty. Placed after the worktree/branch checks (after `{branch} is not at the audited commit`) and before the `main_head` comparisons.

## Cycles (TDD, one commit each)

1. Red: a `break_main_detached` row in `test_rebase_task.py` REFUSALS (`git checkout --detach` in the main repo), expecting "not on a branch", and asserting main is still detached afterwards. Green: the detached check in `_require_rebasable`.
2. Red: a `break_main_dirty` row (write tracked `a.txt` in the main repo), expecting "uncommitted changes", asserting main is still dirty afterwards. Green: the dirty check. Two guard rows (not red; the cycle's implementation already covers them): an untracked file does NOT block a rebase (pins `--untracked-files=no`); a staged-only change IS refused.
3. Refactor: a shared `main_checkout_problem(repo) -> str | None` in `worktrees.py`, returning the refusal text; called by `merge_preview` and `_require_rebasable`. No behaviour change. `clean_tasks.py:128` is left alone: its message differs ("branch factory/<id> kept: ...") and it is a different feature.
4. Docs, then the full suite in the background:
   - README Moved-main bullet (~lines 275-283): the refusal also covers a detached or dirty checkout; the other-branch risk remains.
   - `docs/spike-findings.md`: narrow the "no branch/clean check" known gap to "the branch is not checked to be `main`". Add: an in-progress `git am` or a clean-index merge state is not caught (same as the merge preview; whether `merge --ff-only` refuses then is unverified); submodule changes are ignored as in the merge; main can move between the check and its use.

Each cycle also runs `-k rebase` in `tests/test_cli.py` and `tests/test_server.py`, and all of `tests/test_merge_task.py`.

## Tests whose strings this touches (grepped)

`tests/test_rebase_task.py` REFUSALS (:89-93) and the two `break_*` fixtures that reset or merge in the main repo (`break_main_not_moved`, `break_already_in_main`) run with a clean attached checkout, so they are unaffected. `tests/test_watch.py` uses a fake server and does not exercise the real checks. `tests/test_merge_task.py` asserts the existing merge texts and must stay green through the refactor. No test asserts the exact text of the new rebase refusals yet.

## Risks and limits

- Does not catch a checkout on another branch (documented; option C).
- Untracked files in the main checkout are ignored, as in the merge preview.
- Not caught: `git am` stopped on a patch, or `merge --no-commit` with a clean index (shared with the merge preview).
- Main can move between the check and the rebase (an existing window).
- Not run on a real repo outside the tests.
