# Plan 83: clean's branch-deletion reasons stop leaking repo paths

Branch `plan-83-clean-branch-deletion-reason`, cut from `main`. Drafted 2026-11-22, revised after plan review (`plan-reviewer`, opus; its git claims re-run by me on git 2.53.0, files ref format). Follows plan 82's flagged gap. **Awaiting approval.**

## Problem
- `clean_tasks.py` (`_delete_branch`, `_delete_empty_branch`) shows the first line of git's stderr in the clean problem: `branch factory/<id> kept: git refused to delete it (<line>)`.
- The usual lines carry only a branch name or shas (`'factory/merged' is not fully merged`, `…is at X but expected Y`) and are useful, so they stay.
- Verified on git 2.53.0, these first lines carry an absolute path:
  - `branch -d` on a branch still registered to a worktree: `error: cannot delete branch 'factory/t1' used by worktree at '/…/wt'`.
  - `branch -d` with a stale ref lock: `error: could not delete reference refs/heads/factory/t2: cannot lock ref '…': Unable to create '/…/.git/refs/heads/factory/t2.lock': File exists.`
  - `update-ref -d` with a stale ref lock: `error: cannot lock ref 'refs/heads/factory/t2': Unable to create '/…/.git/refs/heads/factory/t2.lock': File exists.`
- The worktree-registered case is the common one. For a FAILED task `_delete_empty_branch` pre-checks `worktree list --porcelain` (`clean_tasks.py:144-146`) and never runs git; `_delete_branch` (MERGED) has no such pre-check.

## Decisions (user, 2026-11-22)
- D1: keep quoting git's first line, but withhold it when it holds an absolute path; the withheld detail is logged.
- D2: plan 82's "path-bearing exceptions" item is dropped (`WorktreeExists`, `BranchExists`, `ProjectSubdirEscapes` carry task ids and operator config, not host paths).
- D3: a MERGED branch registered to a worktree gets the same pre-check as the FAILED path, so the user reads `it is checked out in a worktree` and not a withheld line.
- D4: the other path-bearing clean problems (below) are out of scope; a follow-up plan.

## Design
- Helper `_git_refusal_reason(task, error)` in `clean_tasks.py`, used by both branch-deletion paths (removes the two copies of the first-line extraction at `:135` and `:150`).
- A line is withheld when it matches `(?<![\w.-])/\S` (an absolute path not preceded by a word character, dot or dash). `factory/x`, `refs/heads/factory/x`, `../x` and `a/b` are quoted; `'/x`, `[/x`, `: /x`, `at /x` are withheld. Every miss fails open, so the cost of a missed shape is a shown line.
- Withheld: the problem reads `branch factory/<id> kept: git refused to delete it (details in the server log)`, and `log_failure(f"clean {task.id} branch deletion", error)` writes argv and stderr on one escaped line.
- The worktree pre-check moves into a shared helper used by `_delete_branch` and `_delete_empty_branch`.

## Tests touched (grep of `tests/`, README and docs for `refused to delete`, `kept: git`, `no reason given`, `checked out in a worktree`)
- `test_clean_tasks.py`: `test_a_refusal_for_an_unmerged_branch_quotes_git` (asserts `not fully merged`) and `test_guard_the_compare_and_swap_itself_refuses_a_branch_that_moved_after_the_recheck` (asserts `git refused to delete it` and `expected`) stay green, unedited: both lines hold `factory/…` and shas, no path.
- `test_a_refused_branch_delete_is_reported_and_the_branch_kept`, `test_a_registered_worktree_with_the_task_field_cleared_makes_git_refuse_and_is_reported` and `test_a_refusal_for_a_registered_worktree_quotes_git_and_does_not_claim_unmerged` use FAILED or unmerged-branch setups that do not hit the new pre-check, and assert only `branch` or `factory/<id>` in the problem or `worktree`; they stay green.
- `test_rebase_task.py:400` (`no reason given` for rebase) is unrelated.
- No existing test is edited.

## Increments (one test, one change, one commit each)
1. **Red, real git: `branch -d` lock failure withheld.** A MERGED task whose branch has a `.git/refs/heads/factory/<id>.lock` file. Assert the problem is exactly `branch factory/<id> kept: git refused to delete it (details in the server log)`, `str(repo)` is in no problem, and `str(repo)` and `cannot lock ref` are in the captured stderr (one line). Green: `_git_refusal_reason` with the regex and `log_failure`, used in `_delete_branch`. Also check in this increment that the web and CLI escape problem text on display; record the result in Deviations.
2. **Red, real git: `update-ref -d` lock failure withheld.** The same for an empty FAILED branch. Green: use the helper in `_delete_empty_branch`, removing the duplicated extraction.
3. **Red: MERGED branch registered to a worktree.** Same setup as `test_a_registered_worktree_with_the_task_field_cleared_makes_git_refuse_and_is_reported` but MERGED (merged state, branch at main, worktree field cleared, worktree still registered). The problem is `branch factory/<id> kept: it is checked out in a worktree`, and no path appears. Green: move the porcelain check into a shared helper used by both paths.
4. **Guard: regex boundaries.** A unit test on the helper: `refs/heads/factory/t1`, `a/b` and `../x` lines are quoted; `[/x`, `'/x` and `: /x` lines are withheld. Commit the test only.
5. **Docs.** README sentence for the branch-deletion problems; fill in Deviations.
6. **Gate and audit.** `ruff check`, full `pytest -n auto`, then `commit-auditor` (opus).

## Risks
- The path test is a heuristic. A relative path with no leading `.` or `/` (`some/dir/file`) and Windows paths are quoted; Linux only today.
- Only the first stderr line is used, so a path in a hint line is never shown.
- Whether the CLI `clean` runs in-process or through the server is unconfirmed, so "the server log" may be the CLI's own stderr; increment 1 checks it.
- Git's wording can change between versions; the regex does not depend on the wording.

## Out of scope
- `clean_tasks.py:98` (`skipped: worktree <task.worktree> is outside the worktree root`) and `gate_cache.py:76` (`cache <name> partially removed: <error>` with an `OSError` path): the same kind of leak, a follow-up plan.
- The remaining plan 82 out-of-scope items (`watch` retry cap, `store.load` unreadable rows, unread `recover` and eval-runner `OSError` paths).

## Deviations
_To be filled in during implementation._
