# Plan 85: unreadable rows on JSON routes, and job exceptions that name paths

Branch `plan-85-unreadable-rows-and-job-errors`, cut from `main`. Drafted 2026-11-22, revised after plan review (`plan-reviewer`, opus). Follows plan 82's out-of-scope list. **Awaiting approval, including one test edit (see Tests touched).**

## Problem
1. **Unreadable rows (JSON routes).** `store.load` lets `ValueError`, `KeyError` or `TypeError` escape for a corrupt row (`sqlite_store.py:78-81`, `_read` `:97-101`, id mismatch `:23`; `store.py:33-42`). The web pages catch `_UNREADABLE` (`web/routes.py:44`) and answer with an unreadable page. Every JSON route that loads a task catches nothing, and `_build_app` (`server.py:425`) has no handler for these types, so the error escapes to the server. No path or `str(error)` is placed in a body by our code: a consistency gap, not a leak. The test client re-raises server errors by default, so Starlette's own 500 body is not pinned here and is not claimed.
2. **Job exceptions.** `job_runner.py:47-48` stores `str(error)` for any exception that is not `SubprocessError`/`OSError`, with no log line. `job.error` is shown on `GET /jobs`, `GET /jobs/{id}`, the CLI and the web (the 202 bodies never carry it: the job is still queued). Verified leak: `audit_eval.plugin_hash` raises `ValueError(f"plugin {directory} contains a symlink: {path}")` (`audit_eval.py:141`), reached through `plugin_hashes` (`:148`, `:186`, `:206`) in the eval job, only when the request sends `stage_config: true` and `settings.stage_plugin_dirs["audit"]` (or `plugin_dirs`) names the directory (`stages.py:31-39`).

## Decisions
- D1 (user, 2026-11-22): in a job, `RuntimeError` keeps its message (the refusal channel: `web_actions._refusal_as`, `server.py:332`, `:378-386`). Every other exception is logged with `log_failure` and stored as a fixed sentence. No new exception class.
- D2 (user, 2026-11-22): unreadable rows on JSON routes get a narrow translation, not an app-wide handler: `ApiError(500, "task unreadable", "task record is unreadable")`.
- D3 (user, 2026-11-22, folded from review): the translation wraps only the load, via a helper (not a whole-route decorator, which would relabel any route bug as "unreadable"); it logs one line; the plugin-symlink error becomes the generic sentence (no rewrap into a new `RuntimeError`).

## Design
- `job_runner`: `JOB_ERROR_SENTENCE = "The job failed unexpectedly. The server log has the details."`, distinct from `JOB_FAILED_SENTENCE` (which says Git or the system). The policy lives in the outer `except Exception` only: `RuntimeError` → `str(error)` as now; anything else → `log_failure(f"job {job_id}", error)` and `JOB_ERROR_SENTENCE`. The inner `except (SubprocessError, OSError)` is unchanged. `OSError` and `SubprocessError` derive from `Exception`, not `RuntimeError`, so the branches cannot overlap. `NotImplementedError`, `RecursionError` and `PythonFinalizationError` are `RuntimeError` subclasses and keep their text. A `RuntimeError` raised by `mark_succeeded` is shown (accepted).
- JSON routes: a helper `load_task(task_id)` inside `_build_app` calls `service.get_task`, catches `(ValueError, KeyError, TypeError)` around that call only, logs with `log_failure(f"load task", error)` (no id in the context: ids from the URL are not trusted), and raises the D2 `ApiError`. Routes call it first: `show_task`, approval, approve, questions, answer, reject, merge-preview, merge, retry-preview, retry, advance, abandon, recover, rebase, and `GET /costs?task_id=` (via `service.cost_report`, `service.py:49-50`). For routes that load again through `web_actions` or `service` the helper adds one cheap extra load.
- `service.get_task` is unchanged: the web actions call it and rely on the web layer's own `_UNREADABLE` catch (web pages call `_load_task` first).
- `list_tasks` is untouched (it already reports skipped ids).

## Increments (one commit each)
0. **Pre-flight (done in drafting).** Grep of tests for `.error ==`, `["error"] ==`, `job["error"]`: hits listed under Tests touched.
1. **Red: `GET /tasks/{id}` on an unreadable row answers the fixed 500 JSON.** Insert a bad row with `transaction` (as `test_web_task_page.py:35`). Assert status 500, the body equals the D2 `{"error", "detail"}`, no row data or `JSONDecodeError` in the body, and stderr is exactly one line naming the error type. Fails today with a raised exception (the client re-raises). Green: `load_task` in `show_task`.
2. **Red: `POST /tasks/{id}/reject`**, same assertions. Green: `reject` calls `load_task` first.
3. **Red, parametrised: the other 13 routes** (empty bodies suffice: each loads before reading the body). Green: `load_task` first in each. May split into two commits (direct `get_task` routes, then action and costs routes).
4. **Guard: the translation stays narrow.** `/costs` with a ledger entry missing `cost_usd` is not reported as "task unreadable".
5. **Red: an unknown exception in a job is withheld and logged.** A job raising `ValueError("/secret/path")` fails with exactly `JOB_ERROR_SENTENCE` (literal in the test, as `test_job_runner.py:75`), stderr is one line with `ValueError` and the path. Green: the outer handler change.
6. **Guard: a failed result write.** A `JobStore` subclass whose `mark_succeeded` raises `ValueError` gives `JOB_ERROR_SENTENCE`, one log line, and the next job still succeeds (needs `create`, `mark_running`, `mark_failed` too, so subclass `JobStore`).
7. **Edit of a passing test (needs approval): the symlink eval test.** `test_server_eval.py:224` asserts `"symlink" in job["error"]` and `str(plugin / "link") in job["error"]`: it pins the leak. Change it to assert `job["error"] == JOB_ERROR_SENTENCE` and that `str(tmp_path)` is in no job field, and rename it (`…fails_the_job_without_naming_the_symlink`). Done as red-then-green: edit first (fails on the old behaviour only if increment 5 is not yet in; since 5 is, this edit lands with 5's commit or as its own commit right after; one commit, test and change adjacent).
8. **Docs.** README sentence beside plans 83 and 84 (`README.md` ~174): an unreadable task on a JSON route answers a fixed 500, and an unexpected job exception shows a fixed sentence with the detail in the server log. Fill in Deviations.
9. **Gate and audit.** `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto`, `verify`, then `commit-auditor` (opus).

## Tests touched
- **Edited, needs user approval:** `test_server_eval.py:224` (`test_an_eval_whose_plugin_directory_holds_a_symlink_fails_the_job_naming_the_symlink`), see increment 7. It is the only passing test pinning a non-`RuntimeError` job message.
- Stay green and unedited (`RuntimeError` messages): `test_job_runner.py:42` (`kaboom`), `:71` (surrogate), `:91` (`JOB_FAILED_SENTENCE`); `test_server_eval.py:146, 185, 197, 211, 251, 262` (all `RuntimeError` from `server.py`, except `:211`, which is the resume refusal at `server.py:380`; `:185` and `:197` likewise); `test_server_merge.py:172, 183, 198, 213, 224` and `test_server.py:213, 234, 245` (`_refusal_as` refusals and the existing git sentence); `test_job_store.py:14, 40` (store only).
- Unaffected: `test_web_task_page.py:41` and `test_web_task_id_mismatch.py:34` (web 500 pages), `test_server.py:110` (`GET /tasks`).
- Not yet checked: any job test whose error comes from a non-`RuntimeError` such as `KeyError('plan')` from an advance job with `stages={}`. Increment 5's gate run will show it; any such test is reported to the user before it is touched.

## Risks
- `RuntimeError` refusals still carry a path or agent-written text: `rebase_task.py:139` (`{worktree.path}`) and `rebase_task.py:119` (`{claimed}`, read from the agent-writable `.git` file, and `{expected}`). Accepted under D1 and left out of scope; the web escapes them with `_safe`.
- The plugin-symlink message gets less useful for an operator (the log still names it).
- A job-time unreadable row, or a corrupt eval `meta.json`, now shows the generic sentence (fine).
- A race: a row turning unreadable between a route's load and `service.approve` gives `409 plan changed` with the raw parse text (`PlanChanged(str(error))`). Pre-existing and rare; not fixed here.
- Intentional `ValueError`s in jobs: the reviewer found only `plugin_hash` reaching a job unwrapped (`approve_plan` is synchronous; `harness`/`agent_call`/`step` catch theirs; advance, abandon and recover turn theirs into `result.stopped` or check state first). Stage code (plan, review, verify, audit) and `clean_tasks` were not traced.
- Whether the full suite passes today beyond the last green run (3336 passed) is assumed.

## Out of scope
- The `watch` retry cap (needs a design decision on the cap and comment text): plan 86.
- `recover` worktree path on CLI stdout and the eval directory path on the CLI: operator-local, not leaks.
- Rewrapping the symlink error as a path-free `RuntimeError`.
- The path text in `rebase_task` refusals (see Risks).

## Deviations
(to fill in after implementation)
