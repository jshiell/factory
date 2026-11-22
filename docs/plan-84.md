# Plan 84: clean's last two path leaks

Branch `plan-84-clean-path-leaks`, cut from `main`. Drafted 2026-11-22, revised after plan review (`plan-reviewer`, opus). Follows plan 83's decision D4. **Awaiting approval.**

## Problem
- `clean_tasks.py:99`: an outside-root task reports `skipped: worktree <task.worktree> is outside the worktree root`. `task.worktree` is a host path from the task record.
- `gate_cache.py:76`: a failed cache removal reports `cache <name> partially removed: <OSError>`. The `OSError` text holds a host path. In the read-only-subdirectory test it is the directory `rmtree` was walking (`.../a-failed/sub`), not the file that failed (CPython 3.14 `shutil` sets `filename` on the walked directory; read from source, not run).
- Both reach the CLI (`clean` through the server, escaped on display) and are plain strings in the `/clean` JSON.

## Decisions
- D1 (user): drop the path from the outside-root message, and log (never show) `OSError` detail.
- D2 (user, 2026-11-22): the shared wording `details in the server log` moves into `textsafe` and both modules use it (no second copy of the literal).

## Design
- Outside root: the problem becomes `skipped: worktree is outside the worktree root`. The web preview already flags the same case without the path (`routes.py:118`); the task record still holds it for an operator.
- Partial cache removal: the problem becomes `cache <name> partially removed (details in the server log)`, and `log_failure(f"clean {name} cache removal", error)` writes the error on one escaped line. It is always logged and never shown, with no regex: some `OSError`s carry no filename, and logging unconditionally does not depend on it.
- `failure_detail` handles an `OSError` with no stderr (`getattr(error, "stderr", None)`; `textsafe.py:5-16`), and `textsafe` imports only `sys` and `unicodedata`, so `gate_cache -> textsafe` makes no cycle (both read, not run).
- The constant `WITHHELD_REASON = "details in the server log"` moves from `clean_tasks.py` to `textsafe.py`; `clean_tasks` and `gate_cache` import it. `clean_tasks` imports `gate_cache`, so the constant cannot live in `clean_tasks`.

## Increments (one commit each)
1. **Red: outside-root problem has no path.** Setup of `test_an_outside_root_worktree_does_not_stop_the_cache_removal`. Assert the outcome's problems are exactly `["skipped: worktree is outside the worktree root"]` and `str(tmp_path)` is in no problem. Green: drop `{task.worktree}`. Commit: `Drop the path from clean's outside-root problem`.
2. **Red: partial cache removal has no path and is logged.** Setup of `test_a_removal_failure_is_reported_as_partial_and_the_other_tasks_are_still_cleaned`, with `capsys`. Assert the problems are exactly `["cache a-failed partially removed (details in the server log)"]`, `str(tmp_path)` is in no problem, and stderr is exactly one line holding `clean a-failed cache removal failed`, `str(tmp_path)` and `Permission denied`. Green: `log_failure` in `remove_cache` and the fixed text (the literal inline for now).
3. **Refactor: one home for the wording.** Move `_WITHHELD_REASON` to `textsafe.WITHHELD_REASON`, use it in `clean_tasks`, `gate_cache`. No behaviour change; no test edits (tests assert the literal).
4. **Docs.** One README sentence beside plan 83's (README line ~174): the outside-root skip names no path, and a partial cache removal says `details in the server log` with the error logged on one escaped line. Fill in Deviations.
5. **Gate and audit.** Before increment 1 re-run `grep -rn "partially removed\|outside the worktree root" orchestrator/tests README.md docs` (the reviewer could not search). Then `ruff check`, full `pytest -n auto`, then `commit-auditor` (opus).

## Tests touched
- `test_clean_tasks.py:206` (`"outside"` in a problem), `:307` (`partially removed`) and `:384` (`outside the worktree root`): the new texts still contain these substrings; all stay green and unedited.
- `test_gate_cache.py` has no `remove_cache` test (read by the reviewer).
- No existing test is edited.

## Risks
- `remove_cache` is also called by `remove_orphan_caches` (`clean_tasks.py:188`); the new text covers it through the shared code, with no test of its own (a guard would not be red). Orphan names already passed `require_safe_id`, so the log context is safe.
- Dropping the path makes the outside-root problem less specific when several tasks are outside root; the task id still prefixes each CLI line.
- The read-only-subdirectory setup does not block removal when run as root; the existing test has the same limit.

## Out of scope
- The remaining plan 82 items (`watch` retry cap, `store.load` unreadable rows, unread `recover` and eval-runner `OSError` paths).

## Deviations
_To be filled in during implementation._
