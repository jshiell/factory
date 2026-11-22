# Plan 80: task actions in the web UI

Branch `plan-80-web-actions`, cut from `main`. Drafted 2026-11-22 from a read of `web/routes.py`, `server.py:171-420`, `job_runner.py` and the CLI parser. **Draft, not approved.**

## Problem
The web UI lists and opens tasks, follows them live, and handles the three human gates: approve a plan, reject a plan, answer questions. Everything else needs the CLI or the JSON API:

| Missing in the UI | CLI | JSON API (all return 202 and a job, except where noted) |
|---|---|---|
| Create a task | `new` | `POST /tasks` |
| Advance a task one stage | `advance` | `POST /tasks/{id}/advance` |
| Abandon | `abandon` | `POST /tasks/{id}/abandon` |
| Retry a failed task (with `--keep-plan`) | `retry` | `GET .../retry-preview`, `POST .../retry` |
| Merge an audited task | `merge` | `GET .../merge-preview`, `POST .../merge` (needs the shown `audited_sha`) |
| Recover | `recover` | `POST .../recover` |
| Rebase | `rebase` | `POST .../rebase` |
| Cost report | `costs` | `GET /costs` |
| Job list and result | none | `GET /jobs`, `GET /jobs/{id}` |
| Clean up | `clean` | `GET /clean-preview`, `POST /clean` |

Not UI work, by nature: `init`, `clone`, `migrate`, `serve`, `ui`, `watch`, `eval-export`. `eval-audit` is out of scope (long, costly, developer-only).

Facts that shape the design (read, not run):
- The JSON handlers are closures inside `server.py`'s `create_app` and submit to a `JobRunner` held in `app.state.runner` (`server.py:155,451`). The web routes (`web/routes.py`) receive no runner today.
- The JSON handlers hold the refusal mapping (`MergeRefused`, `RebaseRefused`, `RecoverRefused`, `AbandonRefused`, `RetryRefused`, `InvalidTransition`, `CalledProcessError`) and the merge check that the shown sha is the audited sha.
- The existing UI pattern for a gated action is: GET confirmation page showing what will happen, POST carrying the shown value, redirect to the task page, a wrong-state page on refusal (`approve`, `reject`).
- A job's outcome is only visible through `/jobs` today. The task page does not show it.

## Decisions (recommendations; none accepted yet)
| # | Question | Recommendation | Alternatives |
|---|---|---|---|
| D1 | Scope | Phase A: new task, advance, abandon, retry, merge, recover, rebase, plus a job-result panel on the task page. Phase B: cost page, jobs list, clean. | All in one phase (large); A only (costs stay CLI-only). |
| D2 | Where the logic lives | Extract the enqueue-and-refusal logic from the `server.py` closures into a module both the JSON API and the web routes call, so there is one place that maps refusals and checks the merge sha. Pass the runner to the web routes. | Web handlers call the domain functions directly (duplicates the refusal mapping); web handlers call the JSON API over HTTP (a self-call, needs auth plumbing). |
| D3 | Confirmation | Every action that changes or spends something gets a confirmation page like approve/reject: advance (spends money), merge (shows branch, audited sha, diff stat from `merge-preview`), retry (shows the `retry-preview` and the keep-plan choice), abandon, recover, rebase. New task has a form, not a confirm page. | One-click buttons for the cheap ones (abandon, recover). |
| D4 | Feedback | POST submits the job and redirects to the task page, which shows the latest job for that task (kind, running/succeeded/failed, failure reason, escaped) and refreshes over the existing task SSE stream. | Redirect to a `/ui/jobs/{id}` page. |
| D5 | Buttons | The task page offers only the actions valid for the task's state, using the same state rules the domain functions enforce; the domain still refuses a stale request (wrong-state page), as `approve` does. | Show all buttons and rely on refusal. |
| D6 | New-task form | Goal text (capped by the existing body cap) and a "skip grill" checkbox (`--no-grill`). Creating a task does not start it. | Create and advance in one step. |

## Increments (test first, one commit each; sketch, to be refined after D1-D6)
0. Probe (throwaway, flagged): confirm from the code which task states each action accepts, and what `JobStore` exposes for "latest job for a task". Record under Deviations.
1. Refactor, behaviour-preserving: extract the enqueue and refusal mapping from `server.py` (D2). Existing `test_server_*` tests are the safety net; no new red test, flagged as a refactor.
2. Red: `GET /ui/tasks/{id}` shows the latest job's state and escaped failure reason. Green: pass the runner or job store to the web routes (D4).
3. Red: new-task form GET and POST create a task and redirect to it (D6).
4. Red/green pairs, one action each, in this order: abandon, advance, recover, rebase, retry (preview plus keep-plan), merge (sha mismatch refused, as the JSON API does). Each: confirmation page, POST enqueues the job, wrong-state page, escaping, and refusal by the guard without a session.
5. Buttons on the task page per D5, one test per state group.
6. Phase B if approved: cost page, jobs list, clean (preview then confirm).
7. Docs: README (what the UI can and cannot do), Deviations here.
8. Browser check in Chromium (headless, as in plan 78): create, advance, abandon through the real UI.
9. Gate: ruff, `pytest -q -n auto`, `verify`, `audit-commits`.

## Tests touched (list before increment 1)
Grep for the exact strings and routes this changes before starting: the task-page tests (`test_web_task*.py`), anything asserting the route table or the set of buttons, `test_web_*_race.py`, and every `test_server_*` test that goes through the closures D2 extracts. List them here; edits to a passing test need the user's approval.

## Risks
- Merge, rebase and retry run git and spend money; a double submit must not queue two jobs. Check what `runner.submit` does for a second job on the same task before increment 4, and test it.
- Long jobs hold a worker; the page must not block on them (D4 redirects at once).
- Every new POST route sits behind the existing guard and CSRF checks; a test per route proves a request without a session is refused.
- Showing job failure text is new output from subprocess errors: escape it (`_safe`) and cap its length.

## Out of scope
`eval-audit`; the CLI-only commands above; multi-user accounts; changing the task state machine; making the UI work over TLS.

## Deviations
Recorded after implementation.
