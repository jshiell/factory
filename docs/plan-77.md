# Plan 77: a task update that cannot overwrite a concurrent write

Branch `plans-77-79-known-gaps` (shared by plans 77-79), cut from `main` (plan 76 is merged). Drafted 2026-11-22; revised after plan review the same day. **Draft, not approved.** Plans 78 (guard) and 79 (ids) follow it and are independent.

## Problem
Human-gate actions and `abandon_task` each load a task, check its state, then `store.save` it (`approve_plan.py:62,68`, `answer_questions.py:27`, `abandon_task.py:10-18`). `save` is `INSERT OR REPLACE` (`sqlite_store.py:30`), so a write landing between the load and the save is overwritten.

Who can actually race (from review, partly unverified, see increment 0):
- Two human submits (CLI, JSON, UI) all reach the server over HTTP. The handlers are `async` and do not yield between load and save, and `state_lock` stops a second server on the same database, so two human submits are probably already serialised. This plan does not claim to fix them; it adds a test that pins it.
- The single `factory-jobs` worker thread runs `abandon_task`, merge, rebase and recover as jobs, concurrently with the event loop. Example: `abandon_task` loads a task as `reviewed`, an approve commits, then `abandon_task` saves `failed` over the approved task. This is the reachable race.

README "Known limit" ("no compare-and-swap") is rewritten at the end to say exactly this.

## Decisions (recommendations accepted by the user 2026-11-22; review findings folded in)
| # | Question | Decision |
|---|---|---|
| D1 | Mechanism | `update(task_id, change)` on the store: opens a transaction, issues `BEGIN IMMEDIATE` itself (`transaction()` in `sqlite_db.py` does not; a deferred transaction would let the loser fail with "database is locked", a 500), loads, applies `change(task) -> Task`, writes, commits. `change` is pure. Exceptions from `change` propagate and roll back. No schema change. |
| D2 | Scope | `reject_plan`, `approve_plan`, `answer_questions` and `abandon_task` use `update`. Other stages, `step.py`, merge, retry, rebase and recover keep load then save (out of scope). |
| D3 | Lost race | The loser sees what it would see had it run second: `InvalidTransition` (UI 409 page, JSON 409). `service.reject_plan` must map `InvalidTransition` to `NotReviewed` as `service.approve` does (it does not today, so a lost JSON reject would be a 500). |
| D4 | File store | `TaskStore` (`store.py:61`, used by `audit_eval` and `migrate`) gets `update` over its atomic write, to satisfy the type the actions are written against. Single-process; its race is not fixed. Increment 0 checks which store the domain tests use. |
| D5 | Listeners | `update` notifies subscribers once, after commit, never on failure. |
| D6 | Existing test fakes | Six passing tests wrap the store with only `load` and `save`: `test_web_reject.py`, `test_web_reject_ids.py`, `test_web_approve_race.py`, `test_web_answer_race.py`, `test_web_approve_store_failures.py`, `test_web_answer_store_failures.py`. The user accepted a named exception: add an `update` passthrough to those wrappers. No assertion in them changes. Each edit is its own commit stating the exception. |

## Unverified (increment 0, throwaway, flagged)
- That `transaction()` has no `BEGIN IMMEDIATE` (read: it does not; Python's legacy sqlite3 mode starts a deferred transaction only before DML).
- That two human submits cannot interleave (probe: threads against the real app).
- That the worker thread can interleave with a handler (probe: `abandon_task` vs `approve` with the event gates used in increment 4).
- That the `change` callbacks are pure (read `approve_plan.py`, `answer_questions.py`, `abandon_task.py`).
- Which test files construct stores or call these actions: `grep -ln "approve_plan\|answer_questions\|abandon_task\|reject_plan" tests` and list the result here before increment 7.

## Increments (test first, one commit each)
0. Probes above. Record results under Deviations.
1. Red: `SqliteTaskStore.update` applies `change`, persists, returns the new task. Green: the method (with `BEGIN IMMEDIATE`).
2. Red: missing id raises `TaskNotFound`; unsafe id `InvalidTaskId`. Guard if already covered.
3. Red: an exception in `change` leaves the stored row unchanged. Guard if the transaction already rolls back.
4. Red: two threads, each with its own store on one file, run `update` with a state-checking `change` that blocks on a `threading.Event` once it has read the task. Release in order. Exactly one succeeds, the other raises `InvalidTransition`; neither sees "database is locked". Event-gated, no reruns needed for determinism.
5. Red: subscribers called once per successful `update`, not on failure.
6. `TaskStore.update` (file store), first test only.
7. Tests-touched step: add `update` passthrough to the six wrappers in D6, one commit. Then red: `reject_plan`/`approve_plan` use `update` (a wrapper whose `save` raises proves the path; approve-vs-reject leaves one winner). Green: switch. Includes the `service.reject_plan` `NotReviewed` mapping, with its own red test.
8. Red: `answer_questions` likewise.
9. Red: `abandon_task` vs `approve` through event gating: the loser's `abandon_task` raises or no-ops, and the approved task is not overwritten with `failed`. Green: switch.
10. UI and JSON: the loser gets the 409 page or JSON 409, not a 500.
11. Docs: README "Known limit" and any test asserting its wording; Deviations here.
12. Gate: `cd orchestrator && uv run ruff check`, `uv run pytest -q`; `verify`; `audit-commits`.

## Out of scope
Stage and runner saves; a version column; merge, retry, rebase and recover actions; cross-process locking beyond SQLite's.

## Risks
- A `change` that does I/O would hold the write lock; a test asserts the callbacks do not touch the store.
- `BEGIN IMMEDIATE` inside the existing `transaction()` context manager: confirm no implicit BEGIN conflicts (increment 1 test covers it).

## Deviations
Recorded after implementation.
