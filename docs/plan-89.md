# Plan 89: the job worker survives a failed `mark_failed`

Drafted 2026-11-23, revised 2026-11-24 after plan review (`plan-reviewer`, opus). Approved.
Source: `todo.md` accepted risk from plan 88 review.
Base: `main` (plans 86 and 88 merged, unpushed). Branch `plan-89-worker-survives`.
Decisions folded from review: wrap lines 49 and 52 only (option a); the original refusal message is not logged on the `RuntimeError` path (accepted, stated).

## Problem
`JobRunner._work` (`job_runner.py:36-55`) calls `mark_failed` from inside its `except` handlers. If `mark_failed` raises (a store fault: `sqlite3.OperationalError` such as "database is locked" after the 5 s timeout, a file that cannot be opened, a full disk), the exception leaves the handler, `finally` runs `task_done()`, and the loop is not re-entered: the worker thread ends. The thread is a daemon and nothing restarts it. Every later job stays `queued` for ever, `wait_idle()` (`queue.join()`) blocks for ever, and the server keeps accepting work it will never run. Plan 88 item 4 removed one cause of the lock error; others remain.

Which calls kill the worker:
- Line 49 (`RuntimeError` handler) and line 52 (`except Exception` handler): a raise ends the worker at once.
- Line 45 (inside the inner `except`): its exception goes to the outer handlers, so a non-`RuntimeError` store error lands in `except Exception`, is logged, and `mark_failed` is called again at line 52. The worker dies only if that second call also fails.
- `mark_running` and `mark_succeeded` failing is caught by the outer `except Exception` (`mark_succeeded` sits in the inner `else`, so only the outer handlers cover it) and leads into `mark_failed`. They cannot kill the worker on their own.
- A raising `on_change` listener cannot cause this: `JobStore._changed` (`job_store.py:92-98`) catches and logs it.

## Design (smallest change)
- One small method, `_record_failure(job_id, message)`: calls `mark_failed`; if that raises `Exception`, logs one escaped line through `log_failure(f"job {job_id}: recording the failure", error)` and returns. Used at lines 49 and 52 only. Line 45 stays as it is, so its existing second chance (line 52) survives.
- The failed job stays `queued` or `running` in the db until the next server start, when `interrupt_unfinished()` (`job_store.py:82`) marks it interrupted. While it is unfinished, README line 170 says its task shows no action links and its action pages answer 409. So the task is blocked until restart. Accepted and stated in README. (Rests on the README text; the callers of the unfinished-job check were not read.)
- No retry added: the connection already waits 5 s and the WAL switch retries (plan 88). Not proposed: a bounded retry with sleep, or restarting the worker thread.
- On the `RuntimeError` path, if recording fails, the refusal message is logged nowhere; the operator sees only the db error. Accepted.
- Still able to end the thread, outside this plan: `log_failure` raising (broken stderr), and a `BaseException` that is not an `Exception` (for example `SystemExit` from a job). Stated, not fixed.

## Tests (TDD; no existing test edited)
Verified by reading: no existing test raises from `mark_failed`; `_FailsToRecordSuccess` fails `mark_succeeded` only; the surrogate test (lines 59-72) covers a cause now fixed.
1. Red, then green, one commit: a `JobStore` subclass `_FailsToRecordFailure` whose `mark_failed` raises `sqlite3.OperationalError("database is locked")` on every call for a configured job id (not a `RuntimeError`, which would change the route) and calls `super()` otherwise. Parametrised over the bad job: raises `OSError`, raises `RuntimeError("refused")`, raises `ValueError`. A good job follows.
   - No `wait_idle()`: a dead worker would block it for ever and there is no pytest-timeout. Poll with a 5 s deadline until the good job is `succeeded`, like the test at line 66. Build the runner by hand, `stop()` in `finally`.
   - Assert: good job `succeeded`; bad job `running` (the accepted limit); stderr has one line containing "recording the failure", the job id and "database is locked", with no raw newline from the error.
   - All three cases red today (the `OSError` case too, because both its `mark_failed` calls raise); confirm each before the fix. Fix: `_record_failure` at lines 49 and 52.
2. Guard (no code change): `mark_failed` fails once for an `OSError` job and then succeeds; the job ends `failed` with `JOB_ERROR_SENTENCE`, showing line 45's second chance survives. One commit.
3. README: one sentence in Known limits (line ~174): if recording a job's failure itself fails, the job stays unfinished and its task's actions are refused until the next server start marks it `interrupted`; the error is logged on one line. `todo.md`: remove the risk, in the working tree only (the user's file, never committed). One commit for the README.

## Process
- Base gate on `main` first. Gate: `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto` (background), then the `verify` skill and `commit-auditor`.
- Sonnet `implementation` agent for 1-2, README inline. Commits short, Sonnet 5.5 trailer, no file lists. Sign unsigned commits at the end (stop after the first failure). Never push.

## Risks
- A job whose failure could not be recorded looks stuck and blocks its task's actions until restart; there is no job timeout today.
- Whatever made `mark_failed` raise probably also breaks the next job's `mark_running`, so a persistent store fault produces a stream of logged double failures instead of one dead thread. Intended: it is visible in the log.
- For a git/OS-failing job whose recording also fails, the log has the original error, the line-51 log of the db error, and the recording-failure line.

## Deviations
(none yet)
