# Plan 46: rebase refuses when the main checkout is detached or dirty

Option A of three considered (B: also require the branch be named `main`; C: a configurable `main_branch`). A was chosen: copy the merge preview's two checks into the rebase. It does not close the "checkout is on another branch" risk; that stays in the README (C is the later fix).

## Problem

`rebase_task._require_rebasable` (`orchestrator/src/factory/rebase_task.py`) replays the task onto the server checkout's HEAD without checking the checkout itself. `merge_preview` refuses when the checkout is not on a branch ("main is not on a branch") or has uncommitted tracked changes ("main has uncommitted changes") (`merge_task.py:35-38`). A rebase run in either state spends verify and audit budget on a task the merge would then refuse.

The watcher's offer already implies both checks held (the preview runs them before the `MAIN_MOVED` check), so the watcher path only gains in the window between the offer and the 👍. The terminal `factory rebase` gains fully.

## Behaviour

Before rebasing, the rebase refuses (`RebaseRefused`, nothing changed) if the main checkout is detached ("main is not on a branch") or has uncommitted tracked changes ("main has uncommitted changes"). The wording matches the merge preview's. The watcher needs no change: a failed rebase job already posts `rebase-refused` with the error.

## Cycles (TDD, one commit each)

1. Red: a `break_main_detached` row in `test_rebase_task.py` REFUSALS (`git checkout --detach` in the main repo), expecting "not on a branch". Green: the check in `_require_rebasable`, before the `main_head` comparisons.
2. Red: a `break_main_dirty` row (modify a tracked file in the main repo), expecting "uncommitted changes". Green: the second check. The REFUSALS test already proves nothing changes.
3. Docs: README Moved main bullet (the refusal now also covers a detached or dirty checkout; the other-branch risk remains) and the "no branch/clean check" known gap in `docs/spike-findings.md` (narrow it to "not checked that the branch is `main`").

## Tests whose strings this touches (grepped)

`tests/test_rebase_task.py` REFUSALS (:89-92) and the two `break_*` fixtures that reset or merge in the main repo (`break_main_not_moved`, `break_already_in_main`) run with a clean attached checkout, so they are unaffected. `tests/test_watch.py` uses a fake server and does not exercise the real checks. No test asserts the exact text of the new refusals yet.

## Risks and limits

- Does not catch a checkout on another branch (documented; option C).
- Untracked files in the main checkout are ignored, as in the merge preview.
- Not run on a real repo outside the tests.
