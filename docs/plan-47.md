# Plan 47: a configurable `main_branch` that merge and rebase enforce

Option C from plan 46. Decisions already made: unset means no branch check (existing setups unchanged); merge and rebase both enforce it.

## Problem

`merge` and `rebase` act on whatever branch the server checkout has out. If it is on a feature branch, `merge` fast-forwards that branch and `rebase` replays onto it (README Moved-main bullet; spike-findings plan 44 and 46 known gaps). Plan 46 only checks that the checkout is on *a* branch.

## Behaviour

- New per-repo setting `main_branch` (string, default unset). Per-repo only, like `watch_merge`: rejected in the global config. An empty string is rejected.
- When set, `merge_preview` (so `factory merge` and the watcher's merge), `merge_task` (through the preview) and `rebase_task` refuse with `main checkout is on {branch}, expected {main_branch}` if the checkout is on another branch.
- Order inside `main_checkout_problem`: detached, then wrong branch, then dirty.
- Unset: nothing changes.
- The watcher needs no change: the refusal is not `MAIN_MOVED`, so it posts `merge-refused` (merge) or `rebase-refused` (rebase) with the text.
- The crash-recovery path (`_already_fast_forwarded`) does not preview; it only records a merge that already happened, so it is not affected.
- `clean_tasks.py:128` is untouched.

## Design

`main_checkout_problem(repo, main_branch=None)` in `worktrees.py`. `merge_preview`, `merge_task` and `rebase_task` take `main_branch: str | None = None` and pass it on. `server.py` passes `ctx.settings.main_branch` at its three call sites (rebase at :183, merge-preview at :242, merge at :257). `factory merge` and `factory rebase` reach these only through the server, so the CLI needs no change.

## Cycles (TDD, one commit each)

1. Settings: `main_branch` is None by default and is read from `factory.toml` (`tests/test_settings.py`, next to the `watch_merge` test at :638). Green: a `Settings` field and `_STRING_KEYS` entry.
2. Settings: an empty string is rejected. Green: reject it in `_typed`.
3. Settings: rejected in the global config (guard if adding `main_branch` to `_PER_REPO_KEYS` in cycle 1 already covers it; otherwise red then green, like `test_watch_merge_is_rejected_in_the_global_config` at :402).
4. `main_checkout_problem` refuses a checkout on another branch (`tests/test_worktrees.py`): returns "main checkout is on X, expected Y". Guard rows: the matching branch passes; unset passes on any branch; detached still reports "not on a branch" first when `main_branch` is set; wrong branch is reported before dirty.
5. `rebase_task` refuses on the wrong branch and changes nothing (`tests/test_rebase_task.py`, a dedicated test that also asserts the checkout is still on that branch; REFUSALS rows call `rebase_task` without the argument). Green: the parameter and its pass-through.
6. `merge_preview` refuses on the wrong branch, and `merge_task` too, before any fast-forward (`tests/test_merge_task.py`: assert main did not move).
7. Server wiring (`tests/test_server_merge.py` and the rebase server test): with `main_branch` set in the settings and the checkout on another branch, the merge-preview endpoint answers 409 `merge refused`, and a rebase job fails with `rebase refused: ...`. Green: pass `ctx.settings.main_branch` at the three sites.
8. Docs, then the full suite in the background:
   - README: the config block (next to `watch_merge`, line ~134), the Moved-main bullet (the other-branch warning now applies only when `main_branch` is unset), and the watcher Trust note if it mentions the other-branch risk.
   - `docs/spike-findings.md`: a Plan 47 section; remove the other-branch known gap from plans 44 and 46 (leave the entries, narrow the text).

Each cycle also runs `tests/test_rebase_task.py`, `tests/test_merge_task.py` and `-k "rebase or merge"` in `tests/test_cli.py` and `tests/test_server.py`.

## Tests whose strings this touches (grepped)

- `tests/test_rebase_task.py:105-106` rows "not on a branch", "uncommitted changes": unchanged (the new argument defaults to None).
- `tests/test_clean_tasks.py:152` "main is not on a branch": `clean_tasks` has its own check; unchanged.
- `tests/test_settings.py:402` and `:638`: the `watch_merge` tests are the pattern; nothing existing changes. Any test that lists all settings keys or the per-repo keys must be checked in cycle 1 (grep `_PER_REPO_KEYS` and "per-repo").
- No test asserts the new wording yet.

## Risks and limits

- A repo whose default branch is not `main` sets `main_branch = "master"` (the name is the branch, not literally "main").
- The check compares names only; it does not verify the branch exists or that HEAD is that branch's tip.
- Still not caught: an in-progress `git am`, a clean-index `merge --no-commit`, submodule changes, main moving between check and use (plan 46 gaps).
- Not run on a real repo outside the tests.
