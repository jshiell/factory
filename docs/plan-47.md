# Plan 47: a configurable `main_branch` that merge and rebase enforce

Option C from plan 46. Decided: unset means no branch check (existing setups unchanged); merge and rebase both enforce it; per-repo-only key; the crash-recovery shortcut checks the branch only (not detached/dirty) and only when `main_branch` is set; only an empty value is rejected; the watcher's Next line is not changed (documented).

## Problem

`merge` and `rebase` act on whatever branch the server checkout has out. If it is on a feature branch, `merge` fast-forwards that branch and `rebase` replays onto it. Plan 46 only checks that the checkout is on *a* branch.

## Behaviour

- New per-repo setting `main_branch` (string, default unset). Rejected in the global config. An empty string is rejected; other odd values (`refs/heads/main`, stray spaces) load and then refuse every merge with a message that names the cause.
- When set, `merge_preview`, `merge_task` and `rebase_task` refuse with `main is on branch {branch}, not {main_branch} (main_branch in factory.toml)` if the checkout is on another branch.
- Order inside `main_checkout_problem`: detached, then wrong branch, then dirty (detached first, or the text would read "on , not Y").
- `merge_task` runs a branch-only check before the `_already_fast_forwarded` shortcut, only when `main_branch` is set. Without it a checkout on another branch at the audited commit would be recorded as merged while the preview refuses. With `main_branch` unset the shortcut behaves exactly as before (no detached or dirty check there).
- Unset: nothing changes.
- `factory merge` and `factory rebase` reach these only through the server (`cli.py` imports neither), so the CLI needs no change. `clean_tasks.py:128` is untouched.
- The watcher routes the refusal correctly (not `MAIN_MOVED`: `merge-refused` / `rebase-refused` with the text). Its `merge-refused` Next line still says to run `factory rebase` or `factory merge`, which does not help here; the same is already true for plan 46's refusals. Documented, not changed.

## Design

`main_checkout_problem(repo, main_branch=None)` in `worktrees.py`, plus a small `wrong_branch_problem(repo, main_branch)` it uses and `merge_task` calls for the recovery path. `merge_preview`, `merge_task` and `rebase_task` take `main_branch: str | None = None`. `server.py` passes `ctx.settings.main_branch` at its three sites (rebase :183, merge-preview :242, merge job :257).

## Cycles (TDD, one commit each)

1. Settings: `main_branch` is None by default and is read from `factory.toml` (`tests/test_settings.py`, next to :638). Green: a `Settings` field and a `_STRING_KEYS` entry.
2. Settings: `main_branch = ""` is rejected and the error names the key. Green: special case in `_typed`.
3. Settings: rejected in the global config (red; pattern of :402). Green: add to `_PER_REPO_KEYS`.
4. `main_checkout_problem(repo, main_branch)` refuses another branch (`tests/test_worktrees.py`, one parametrized test; red row: wrong branch). Guard rows: matching branch passes; unset passes on any branch; detached with `main_branch` set says "not on a branch"; wrong branch and dirty together says the wrong branch.
5. `rebase_task(..., main_branch=)` refuses on a wrong branch; dedicated test asserts the task record, the three HEADs and the current branch are unchanged.
6. `merge_preview(..., main_branch=)` refuses on a wrong branch.
7. `merge_task(..., main_branch=)` refuses on a wrong branch before any fast-forward (`git checkout -b other` at the base; HEAD, task record and worktree unchanged).
8. `merge_task` recovery path refuses on a wrong branch (`checkout -b other`, then `merge --ff-only <audited>`; task still AUDITED, worktree still exists). Green: the branch-only check before `_already_fast_forwarded`.
9. Server: the merge-preview endpoint answers 409 `merge refused` with the new text (`tests/test_server_merge.py`, settings with `main_branch="main"`). Green: wire :242.
10. Server: a merge job on a wrong branch fails with `merge refused: <text>`; main and the task unchanged. Green: wire :257.
11. Server: a rebase job on a wrong branch fails with `rebase refused: <text>` (real git, in `test_server_merge.py`; assert the exact text). Green: wire :183.
12. Docs, then the full suite in the background:
    - README: the config block (~:134), the per-repo list (~:118), the Moved-main bullet (the other-branch warning applies only when `main_branch` is unset), a note that settings are read at `serve` startup so a change needs a restart.
    - `docs/spike-findings.md`: a Plan 47 section; narrow the other-branch gap in plans 44 and 46 to "when `main_branch` is unset"; risks below.

Each cycle also runs `tests/test_rebase_task.py`, `tests/test_merge_task.py`, `tests/test_server_merge.py` and `-k "rebase or merge"` in `tests/test_cli.py` and `tests/test_server.py`.

## Tests whose strings this touches (grepped)

- `tests/test_rebase_task.py:105-106` ("not on a branch", "uncommitted changes"): unchanged, the argument defaults to None.
- `tests/test_clean_tasks.py:152` ("main is not on a branch"): `clean_tasks` has its own check; unchanged.
- `tests/test_settings.py:402`, `:638`: the `watch_merge` pattern tests; nothing existing changes. No test enumerates every settings key (checked).
- No test asserts the new wording yet.

## Risks and limits

- A repo whose default branch is not `main` sets `main_branch = "master"` (the branch name).
- Names only: the branch is not verified to exist or HEAD to be its tip.
- `WorktreeManager.create` bases new tasks on whatever HEAD the checkout has, and `clean`'s `branch -d` compares against that HEAD. Neither enforces `main_branch`; out of scope.
- The watcher's `merge-refused` Next line is unhelpful for these refusals (see Behaviour).
- Still not caught: an in-progress `git am`, a clean-index `merge --no-commit`, submodule changes, main moving between check and use (plan 46).
- Not run on a real repo outside the tests.
