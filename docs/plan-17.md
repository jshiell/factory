# Plan 17: three loose ends from plans 14 and 15

## Spike (done, $0; scratchpad repos, git on this machine)
- A conflicted rebase leaves the worktree dirty (`UU f`); plain `git worktree remove` refuses it. `clean` runs `worktree remove --force` (`worktrees.py:45`), so a mid-rebase worktree is force-removed after the preview flags it dirty. The branch ref stays at the old tip during a rebase. Only reachable after a SIGKILL (`rebase_task` aborts on failure). The full `clean` over such a worktree is not run.
- Rebase conflict output: `CONFLICT (content): ...` is on **stdout**; stderr holds `Rebasing (1/1)error: could not apply ...` (progress glued to `error:`, no newline) and hints. With `LC_ALL=de_DE.UTF-8` it is translated (`KONFLIKT`, `Fehler:`), so the locale must be pinned.
- `git branch -D` refuses a branch checked out in a worktree. `git update-ref -d refs/heads/<b> <sha>` does **not**: it deleted a worktree's checked-out branch. It does refuse a wrong expected sha (atomic compare-and-swap) and works with main detached.

## Decided (not up for review)
uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test except via the approved edit list below. Never push. User choices: fix the rebase message, fix the detached-HEAD preview, delete refused empty branches, and use the compare-and-swap delete.

## Behaviours
1. **Rebase refusal message** (`rebase_task.py`, `cli.py`):
   - Pin `LC_ALL=C` in the rebase env.
   - Per line of stderr+stdout, strip a leading `Rebasing (n/m)`; quote the first line that starts with `CONFLICT (`; else the first that starts with `error:` or `fatal:`; else the first non-empty line that is not progress. Never a substring match (commit subjects are agent-authored).
   - Append the "on a conflict run `factory abandon <id>` then `factory retry <id>`" advice only when a CONFLICT line was found; otherwise say only that the rebase failed and nothing changed.
   - The CLI prints the refusal through `escape_control` (the quoted line can hold an agent-chosen filename).
2. **Empty failed branch deletion** (`clean_tasks.py`), failed-and-empty path only:
   - Refuse (report, keep) if `git worktree list --porcelain` shows `branch refs/heads/factory/<id>` checked out anywhere.
   - Otherwise `git update-ref -d refs/heads/factory/<id> <base_sha>`: atomic, deletes only if the ref still equals `base_sha`, so a commit landing at any moment is kept (reported with git's reason). It is independent of whether main is on a branch, which fixes both the "base no longer an ancestor of main" refusal and the detached-HEAD keep.
   - Merged tasks keep `branch -d` with its "main is not on a branch" guard. `branch.<name>.*` config is not removed (minor).
   - `_branch_empty` stays the strict preview and execution check (unset base, missing branch, git error: never empty).
3. **Preview** (`cli.py` `_describe_candidate`): "empty branch will be deleted" only for FAILED tasks (today a MERGED task with an empty branch gets that promise but uses `-d`). A merged branch is shown as before.
4. **Mid-rebase `clean`:** no code; findings note.

## Approved edit list (existing tests that pin the old behaviour; replaced, each with its new version)
`orchestrator/tests/test_clean_tasks.py`:
- `test_a_refused_delete_of_an_empty_failed_branch_is_reported_and_the_branch_kept` (base not an ancestor of main): replaced by the R2a test, which asserts the branch is now deleted without a problem.
- `test_an_empty_failed_branch_is_kept_when_main_is_not_on_a_branch`: replaced by R2b (deleted while main is detached).
- `test_a_refusal_for_an_unmerged_branch_quotes_git`: rewritten to use a MERGED task whose branch holds an unmerged commit, so `-d` still refuses and the message still quotes git.
No other passing test changes. The two checked-out-elsewhere tests (lines ~502, ~514) must stay green unchanged; they are the proof for the worktree check.

## Cycles
R1a (red first): stderr-only `Rebasing (1/1)error: ...` plus stdout `CONFLICT (` line: the refusal quotes the CONFLICT line and names abandon and retry. R1b (guard): no CONFLICT/error/fatal line: first non-progress line, no abandon advice. R1c (red first): under `LC_ALL=de_DE.UTF-8` in the caller's env the CONFLICT line is still found (needs the pin). R1d (red first): a control character in the quoted line is escaped on the CLI.
R2a (red first): an empty failed branch whose base is no longer an ancestor of main is deleted, no problem reported. R2b (red first): deleted while main is detached. R2c (guards, start green): a branch with a commit is kept; a commit added between preview and execution is kept with git's reason; `base_sha` cleared between preview and execution is kept; a merged branch still uses `-d` and keeps the "not on a branch" message; an empty failed branch checked out in a worktree is refused and reported (the existing tests prove it too).
R3 (red first): real-git `main` test through the `git_repo(ctx)` helper: `clean` exits 0 after the deletion (not a spy test).
R4 (red first): a MERGED task with an empty branch is not previewed "will be deleted".
E1: no e2e (real-git tests cover it). Repo-wide grep for other users of `branch -d` and "main is not on a branch" (code and docs), full suite, `verify`, findings note.

## Residual
CAS delete skips git's "fully merged" safety by design (the tip equals the base commit, so nothing is lost). Branch config left behind. Mid-rebase `clean` unrun end to end. Localised git messages elsewhere are unchanged (only the rebase call pins the locale).

## Out of scope
Other known gaps (root `[tool.uv]` etc., import-time monkeypatching tests, orphan agents after SIGKILL).
