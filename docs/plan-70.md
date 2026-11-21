# Plan 70: a task detail page in the web UI

Reviewed (2026-11-21); findings folded in. Branch `plan-70-task-detail`, cut from `plan-69-web-ui` (plan 69 is not merged).

## Problem
The web UI lists tasks but shows nothing about one. To see why a task failed, what it plans, or what it asks, you run `factory show` and read the terminal. This plan adds a read-only page per task, `/ui/tasks/{id}`, reachable from the list. It adds no actions. It is also the first test of whether plan 69's choices (Jinja, htmx, SSE, the `/ui` guard) carry a second page without new machinery.

## Decisions (recommended options; D1-D3 stand unless the user says otherwise)
| # | Question | Chosen | Alternative |
|---|---|---|---|
| D1 | Does the page update live? | Yes: its own SSE stream, `/ui/tasks/{id}/events`, sending an event only when the rendered fragment changed | Static page. Drops increments 9-12 and the SSE wrapper |
| D2 | Show spend? | No. The ledger has no change hook, so spend would go stale while the rest is live. It belongs in the costs view | Read once per render, stale until the next task change |
| D3 | Which fields? | Parity with `factory show` (`cli/tasks.py:_describe`, `:34-55`), plus `attempts`, `waived_findings` and a count of `denials`. A task with `goal=None` shows its prompt, as the list does | Everything in `task_to_dict` |
| D4 | Unknown or malformed id | HTML 404 in the `/ui` layout with a link to `/ui/`; it never echoes the id. The events route gives the same 404 | The API's JSON 404 |

## Verified (2026-11-21)
- `SqliteTaskStore` has `save`, `create`, `list`, `load` and no delete (`sqlite_store.py`). `clean` only saves the task with `worktree=None` (`clean_tasks.py:121`), which notifies like any save. A task therefore never vanishes while the server runs.
- `load` calls `require_safe_id` (`ids.py:11`, raising `InvalidTaskId`, defined at `:4`; pattern `[A-Za-z0-9_-]+`), raises `TaskNotFound` for a missing row, and lets `json.loads` and `task_from_dict` errors (`ValueError`, `KeyError`, `TypeError`) escape. `list` catches those three and skips the row (`:59-68`). So an unreadable row would be a 500 on the detail page.
- The API's 404 handler is JSON (`server.py:75`), so the UI routes handle `TaskNotFound` and `InvalidTaskId` themselves.
- Path params are percent-decoded before routing (`TestClient` does `unquote`, `starlette/testclient.py:284`), so `%2f` never matches `{task_id}`. **Unverified:** that uvicorn decodes the same way, and how httpx and browsers treat a `..` segment.
- `_events` is at `routes.py:58` and is hard-wired to `render_tasks`. Any store write bumps `TaskChanges` (`web/changes.py`), whichever task it touches.
- `_describe` runs agent text through `escape_control` (`textsafe.py`); `web/routes.py` does not use it. Jinja autoescape stops HTML injection (`routes.py:14`) but not control and bidi format characters.
- Cookie `Path=/ui` covers `/ui/tasks/...` and its `/events`. CSP `connect-src 'self'` covers the new stream. The guard adds the five headers to every `/ui/*` response, including Starlette's own 404.
- `test_web_tasks_page.py` asserts ids and goals appear (`:42`, `:50`) and the head tags (`:86-91`), not `<td>` markup, so linking the id breaks nothing. `test_web_events.py`'s `read_events` and `read_until_end` helpers are hard-wired to `/ui/events`.
- **Unverified:** Starlette's behaviour when an exception reaches a handler after the response started (avoided by design: the route loads the task before building the stream); browser per-host connection limits; whether `pytest-repeat` is installed. No browser is available, so layout is unchecked, as in plan 69 increment 22.

## Design
- `templates/base.html`: the head, the banner, a `content` block, then `ui.js` last. `page.html` becomes a child. `base.html` keeps these byte-identical: the htmx-config meta, the stylesheet link, both script tags, `id="disconnected" hidden`, `ui.js` after the banner, no bare `<script>` and no `style=`. `page.html` keeps `<div id="tasks" hx-ext="sse" sse-connect="/ui/events" sse-swap="tasks">` exactly.
- `templates/task.html` extends it; `templates/_task.html` is the swappable fragment. Sections appear only when they have content. The plan text goes in a `<pre>`. The "needs you" mark uses the existing `_HUMAN_GATE_STATES`.
- `GET /ui/tasks/{task_id}` and `GET /ui/tasks/{task_id}/events` (event `task`) both load the task **before** responding. A missing or invalid id gives the HTML 404; an unreadable row gives an "unreadable" HTML page (no traceback, no raw data, no id). On the events route this happens before the `StreamingResponse` exists, so the status is real.
- `_events` becomes `_stream(request, render)`, shared by the list and the task stream (version captured before rendering, keepalive, stop). The task stream remembers the last fragment it sent and skips an event when a bump leaves it unchanged, so another task's progress does not reset text selection or scroll in the plan.
- Free text on the detail page passes through `escape_control` before rendering.
- Shared test fixtures move to a new `tests/conftest.py`-style helper module (or `conftest.py`) used by the new files only; existing test modules are not edited. The new events tests take the stream path as a parameter in their own copy of the helpers.

## Increments
One failing test (or one parametrized behaviour), the minimal change, one commit each. "Guard" means earlier code already satisfies it, and the test is committed with that label.

0. **Branch and baseline.** Cut `plan-70-task-detail`; run the gate; no commit.
1. **Extract `base.html` (refactor under existing tests).** No new test; every existing test stays green and unedited.
2. **Core fields (red).** `GET /ui/tasks/{id}` with a session shows id, state, goal (or the prompt when `goal` is None) and failure reason. No SSE wrapper yet.
3. **Missing or invalid id (red).** A well-formed missing id, and routable invalid ids (`a.b`, `a%20b`, `%3Cscript%3E`), give a 404 in the layout with a link to `/ui/` and without the id.
4. **Encoded slash (guard).** `%2f` ids give a 404 without the id and with the five headers; Starlette's plain page is accepted.
5. **Unreadable task (red).** A row holding `not json` gives an HTML "unreadable" page with no traceback and no raw data.
6. **Sections.**
   - 6a. Every optional section is absent for a bare task (one parametrized test).
   - 6b. Risks, review findings, audit findings and waived findings appear with their items (parametrized).
   - 6c. The plan appears in a `<pre>`.
   - 6d. Open questions show the round, titles and recommended answers.
   - 6e. Scalar fields: retry_of, fix rounds, attempts, denial count, worktree and branch, the three error fields.
7. **Escaping.**
   - 7a. Markup in every free-text field is escaped (guard: autoescape).
   - 7b. Control and bidi characters are escaped as `escape_control` does (red).
8. **Needs-you mark (red).** Present at `open_questions`, `reviewed` and `audited`; absent elsewhere.
9. **Guard rules on the new routes (guard).** No cookie gives 401, a foreign `Origin` gives 403, a non-loopback Host is refused, and the five headers are on the page, the 404, the unreadable page and the stream.
10. **List links (red).** Each id in the list links to `/ui/tasks/{id}`; the SSE fragment carries the links too. (`urlencode` is not a security control; the id regex is.)
11. **Task stream, first event (red).** `/ui/tasks/{id}/events` answers `text/event-stream` and its first event is `task` with the fragment. Refactor `_events` into `_stream`; the list stream tests stay unedited and green.
12. **Stream refuses before streaming (red).** Unknown, invalid and unreadable ids on `/events` give the HTML 404 or unreadable page, content type `text/html`, headers present, no id echoed.
13. **Stream sends only real changes.** This task's save sends the new state as the next event (guard once 11 re-renders on bump). Another task's save sends no event: the next frame is a keepalive, using the injectable short interval (red; needs the dedupe). A clean-style save with `worktree=None` removes the worktree section.
14. **Stop ends the task stream (guard, in process).** The shared `_stream` already watches `stopping`; no subprocess test.
15. **Page wiring (red).** The task page wraps the fragment in `hx-ext="sse" sse-connect="/ui/tasks/{id}/events" sse-swap="task"`, loads `ui.js` after the banner, and has no inline script or style.
16. **Docs.** README Web UI subsection and a deviations section here. The manual browser check joins plan 69's outstanding one.
17. **Gate.** `cd orchestrator && uv run ruff check`, then `uv run pytest -q -n auto`; run the `verify` skill; run `audit-commits` against this plan. Rerun every stream test 10 times with a shell loop (`for i in $(seq 10); do uv run pytest -q tests/test_web_task_events.py || break; done`) to show determinism.

If D1 is changed to static: drop 11-15 (keep a non-streaming version of 15 without the wrapper) and renumber.

Optional, only if approved: **escape the list page too.** `render_tasks` lacks `escape_control`. Apply it after `_truncated`, so escape expansions do not eat into the 120-character limit. No existing assertion changes (the CR test checks only the `data:` prefix; `SCRIPT` has no control characters), but the list's output does change.

## Tests touched
No existing test file or assertion changes. Checked by reading: `test_web_tasks_page.py` (substrings and head tags, kept by increment 1), `test_web_events.py` (helpers hard-wired to `/ui/events`; the new files carry their own parameterised copies), `test_web_page_wiring.py` (`ui.js` after the banner; kept). If any existing test needs editing to pass, stop and re-plan.

## Out of scope
Actions (answer, approve, reject, merge, retry, advance), a spend section (D2), the jobs and costs views, task history, worktree diffs, task deletion handling (the store has none), session idle expiry, CSS beyond `ui.css`.

## Risks
- The list page lacks `escape_control` today (optional increment above).
- Every store write wakes every open task stream. Dedupe stops the sends, but each wake still re-renders one row; **unmeasured** with many tabs.
- Two long-lived streams per open list and detail tab. Browsers cap connections per host (HTTP/1.1, commonly six; unverified), so many open tabs could starve page loads. Plan 69 had one stream per tab.
- Anything the plan marks unverified above is untested until the manual browser check.

## Deviations (recorded after implementation)
- (a) Commit 6e (`ac94f5a`, scalar fields) was amended once before moving on.
- (b) The unreadable page returns status 500; the plan was silent (`e16b8e4`).
- (c) A row corrupted mid-stream sends the "That task is unreadable." fragment as a `task` event and keeps the stream open; the plan was silent. Chosen because browser reconnect behaviour is unverified (`a14076f`).
- (d) The stream's `render` may return None to mean "nothing new" (`14feee3`).
- (e) The test helpers live in `tests/webfixtures.py` and `tests/webhelpers.py` (`8220ad8`).
- (f) Increment 9's stream headers and 401 check was deferred to `55dc735`, because the stream route did not exist until increment 11 (`4c4d3ac` covered the page routes).
- (g) The 0.2s keepalive test (`14feee3`) is a weaker regression check than the others: it depends on a short real interval.
- (h) A SQLite error during a re-render is not caught on either stream (out of scope).
- (i) Commit `7771c25` (extract base template) was amended once, message only, no content change.
- (j) Audit follow-ups. `6fcb8f7`: the open-questions list is now a `<ul>` with the number only in the text; it was an `<ol value>` that also printed the number, which would show it twice (expected from how both render; unverified in a real browser). The `<ul>` keeps the real number correct when numbers are non-contiguous, and the existing test pins the "N. title" text. The number now goes through `_safe` like other agent text. `3192cbb`: guard tests that a foreign `Origin`, a non-loopback Host and `Sec-Fetch-Site: cross-site` are refused with 403 and the five security headers on `/ui/tasks/{id}/events`; they passed without new code.
