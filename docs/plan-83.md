# Plan 83: clean's branch-deletion reasons stop leaking repo paths

Branch `plan-83-clean-branch-deletion-reason`, cut from `main`. Drafted 2026-11-22. Follows plan 82's flagged gap.

## Problem
- `clean_tasks.py` (`_delete_branch`, `_delete_empty_branch`) shows the first line of git's stderr in the clean problem: `branch factory/<id> kept: git refused to delete it (<line>)`.
- Usual lines carry only a branch name or shas (`not fully merged`, `is at X but expected Y`). A ref-lock failure (`Unable to create '/…/.git/…lock'`) would carry an absolute repo path. Not reproduced; an unverified assumption.
- The reason is useful (an unmerged branch), and two passing tests assert it: `test_clean_tasks.py:535` (`not fully merged`) and `:555` (`expected`).

## Decisions (user, 2026-11-22)
- D1: keep quoting git's first line, but withhold it when it holds an absolute path; a withheld line is logged. Both existing tests stay unedited.
- D2 (user): plan 82's "path-bearing exceptions" item is dropped. `WorktreeExists`, `BranchExists` and `ProjectSubdirEscapes` carry task ids and operator config, not host paths.

## Design
- One helper in `clean_tasks.py`, `_git_refusal_reason(task, error)`, used by both branch-deletion paths (removes the duplicated first-line extraction).
- A line is withheld when it matches an absolute path: `(^|[\s'"(=:])/\S`. `factory/x` and `refs/heads/factory/x` do not match (the `/` follows a word character).
- Withheld: the problem reads `branch factory/<id> kept: git refused to delete it (details in the server log)`, and `log_failure` writes the detail (argv and stderr) on one escaped line.
- Shown: unchanged text.

## Tests touched (grep of `tests/` and docs for `refused to delete`, `kept: git`, `no reason given`)
- `test_clean_tasks.py:535`, `:555`: assert git's words; stay green, unedited.
- `test_rebase_task.py:400` mentions `no reason given` for rebase, unrelated.
- No existing test is edited.

## Increments (one test, one change, one commit each)
1. **Delete-branch path withheld.** Red: `git branch -d` fails with stderr `error: Unable to create '/secret/repo/.git/refs/heads/factory/t1.lock': File exists.` The problem is `branch factory/<id> kept: git refused to delete it (details in the server log)`, no `/secret/repo` in any problem, and the detail is logged. Green: the helper, used in `_delete_branch`.
2. **Empty-branch path.** Same test for `update-ref -d`. Guard if the helper already covers it (commit the test); otherwise use the helper in `_delete_empty_branch`.
3. **Guard: a path-free line is still quoted** (a line holding `factory/t1` and shas is shown whole). Commit the test.
4. **Docs.** README sentence for the clean outcomes; fill in Deviations.
5. **Gate and audit.** `ruff check`, full `pytest -n auto`, then `commit-auditor` (opus).

## Risks
- The path test is a heuristic: a Windows path or a path with no leading separator character slips past. Linux only today.
- A relative path such as `../x` is not withheld.
- Control characters in git's line are not escaped here; the web and CLI escape on display (not re-verified).

## Out of scope
- The remaining plan 82 out-of-scope items (`watch` retry cap, `store.load` unreadable rows, unread `recover` and eval-runner `OSError` paths).

## Deviations
_To be filled in during implementation._
