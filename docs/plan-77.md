# Plan 77: a task update that cannot overwrite a concurrent write

Branch `plans-77-79-known-gaps` (shared by plans 77-79), cut from `main` (plan 76 is merged). Drafted 2026-11-22 from a read-only pass; anything not read is under Unverified. **Draft, not approved.** Plans 78 (guard) and 79 (ids) follow it and are independent.

## Problem
`reject`, `approve` and `answer` each load a task, check it, then `store.save` it (`approve_plan.py:62,68`, `answer_questions.py:27`). The store has no compare-and-swap (`sqlite_store.py:35`, `INSERT OR REPLACE`), so a write landing between the load and the save is overwritten. Two concurrent submits can both succeed (for example approve and reject on one reviewed task), and the loser's effect is lost with no error. The CLI, the JSON API and the UI all share it (README "Known limit").

## Decisions (recommendations accepted by the user 2026-11-22; the plan is not yet reviewed)
| # | Question | Proposal |
|---|---|---|
| D1 | Mechanism | A store method `update(task_id, change)` that loads, applies `change(task) -> Task` and writes inside one SQLite write transaction. `change` is pure (transition, digest and round checks, no I/O), so the lock is short. Exceptions from `change` propagate and roll back. Alternatives: a `version` column checked on save (schema change, every caller carries a version); comparing the raw stored text (a spurious mismatch if a row was written by older code). Recommended: `update`, no schema change. |
| D2 | Scope | The three human-gate actions: reject, approve, answer. The runner's `step.py` saves and the other stages (`run_task`, `verify_task`, `merge_task`, ...) are not changed: they run long agent calls and are not the contention this plan fixes. |
| D3 | Behaviour on a lost race | The loser sees what it would have seen had it run second: the wrong-state error (`InvalidTransition`, UI 409 page, JSON 409) because the winner already moved the state. No new error type. |
| D4 | File store | `TaskStore` (`store.py:61`, used by `audit_eval` and `migrate`) gets the same method over its atomic write, for the protocol; it is single-process and its race is not fixed here. |
| D5 | Listeners | `update` notifies the store's subscribers once, after commit, as `save` does (`sqlite_store.py:29`), so the live UI still refreshes. |

## Verified (by reading, 2026-11-22)
- Every `save` caller in the stage modules is `store.save(...)` after a `load` (grep, 40-odd sites); the three human-gate actions are `approve_plan.py:46-68` and `answer_questions.py:17-27`, via `service.py:60,68`.
- `save` is `INSERT OR REPLACE` inside `transaction(...)` (`sqlite_store.py:35-41`); `create` is the only guarded write (`:44`).
- `load` calls `require_safe_id` and raises `TaskNotFound` (`:66-76`).

## Unverified (increment 0)
- Whether `transaction()` in `sqlite_db.py` opens with `BEGIN IMMEDIATE` (needed so two writers serialise from the read, not from the write). If not, `update` must issue it.
- Whether the server and the CLI really are separate processes sharing one database file when `factory serve` runs (the race needs it); a real two-process probe, not only threads.
- That `change` callbacks are pure in all three actions (read, not run).

## Increments (test first, one commit each)
0. **Probe** (throwaway, flagged): two processes load the same reviewed task and approve and reject at once; record whether both succeed today. Fixes the reproduction used by increment 1.
1. Red: `SqliteTaskStore.update` applies `change` and persists; returns the new task. Green: the method.
2. Red: `update` on a missing id raises `TaskNotFound`; on an unsafe id `InvalidTaskId`. Green or guard.
3. Red: an exception in `change` leaves the stored row unchanged. Guard if the transaction already rolls back.
4. Red: two threads, each with its own store on one file, `update` the same task with a state-checking `change`: exactly one succeeds, the other raises `InvalidTransition`. Rerun 10 times for determinism.
5. Red: subscribers are called once per successful `update`, not on failure.
6. `TaskStore.update` (file store), same first test.
7. Red: `reject_plan` and `approve_plan` use `update`; a race test per action (approve vs reject, approve vs approve) leaves one winner. Green: switch the calls.
8. Red: `answer_questions` likewise (two answers to one round: one wins, the other gets the wrong-state result).
9. UI and JSON: one race test each through the app that the loser gets the 409 page or JSON error, not a 500.
10. Docs: README "Known limit" lines (the store has no compare-and-swap) rewritten to say what is and is not covered (stages still save last-write-wins); a Deviations section here.
11. Gate: `cd orchestrator && uv run ruff check`, `uv run pytest -q`; `verify`; `audit-commits`.

## Tests touched
Grep before changing: tests that assert the `Known limit` wording or that call `store.save` through these actions with a mock store (a mock without `update` would break). List them in the plan before increment 7.

## Out of scope
Stage and runner saves; a version column; merge, abandon, retry, rebase and recover actions (they still load then save); cross-process locking beyond SQLite's.

## Risks
- A `change` that does I/O would hold the write lock; the plan keeps `change` pure and a test asserts the three callbacks do not touch the store.
- Existing tests that fake the store may lack `update`; found by the grep above, not edited, adapted by adding the method to the fakes only if no passing test is modified.

## Deviations
Recorded after implementation.
