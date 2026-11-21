# Plan 72: reject a reviewed plan from the web UI

Reviewed (2026-11-21); findings folded in. Branch `plan-72-reject-plan`, cut from `plan-71-escape-list` (plans 69-71 are not merged).

## Problem
The UI is read-only. A task at a human gate says "needs you", but you must switch to the CLI or the JSON API to act. This plan adds the first action: rejecting a task in state `reviewed`, through a confirm page. It is also the first state-changing route under `/ui`, so it tests the guard, the cookie and the headers on a POST before approve (digest, findings, waivers) is attempted.

## Decisions
| # | Question | Chosen | Alternative |
|---|---|---|---|
| D1 | Scope | Reject only, `reviewed` state (user) | Approve and reject; all three gates |
| D2 | Submit style | Plain HTML form POST, then `303` to the task page (user) | htmx POST |
| D3 | CSRF and the `Origin: null` problem | Non-GET/HEAD requests under `/ui` need `Origin`: exactly `http://<Host>`, or the string `null` **only with** `Sec-Fetch-Site: same-origin`. Needed because every `/ui` response sends `Referrer-Policy: no-referrer` (`server.py:509`), and per the Fetch spec (read by the reviewer; not tested in a browser) a form POST then carries `Origin: null`. GET rules are unchanged (`null` stays refused on GET, as `test_web_ui_guard.py:61` pins). Fails closed on a browser without Fetch Metadata | Change the header to `same-origin` (edits two passing test constants); a per-session form token |
| D4 | Confirmation | A confirm page: the task page links to `GET /ui/tasks/{id}/reject`, which shows the goal and a form; that form POSTs. A mis-click costs a fresh plan and review run: a rejected task is terminal, `retry` only makes a new `proposed` task from the goal (grill answers are lost), and `retry --keep-plan` refuses (`retry_task.py:12`, `:88`) | One click |
| D5 | Refused or stale request | HTML page in the `/ui` layout that never echoes the id: 404 (missing, invalid), 500 (unreadable), 409 (not `reviewed`, including a race). The route catches `InvalidTransition`, `TaskNotFound`, `InvalidTaskId` and the unreadable errors itself, so nothing reaches the JSON handlers (which would echo the id) | JSON errors |
| D6 | Which function rejects | `approve_plan.reject_plan` directly, after `_load_task`: its `apply` raises `InvalidTransition` for any state but `reviewed`, giving one 409 path. The route redirects; it never renders from the function's return value | `service.reject_plan` (loads twice; separate `NotReviewed`) |
| D7 | Known limit | The store is `INSERT OR REPLACE` with no compare-and-swap (`sqlite_store.py:41`): a write landing between the load and the save is overwritten. The CLI and JSON reject share this. Accepted, documented, not fixed here | Add a compare-and-swap (separate plan) |
| D8 | Manual browser check | Blocks merge of plans 69-72, not just noted | Note only |

## Verified (2026-11-21)
- `approve_plan.reject_plan` (`approve_plan.py:66`): loads, applies `HUMAN_REJECTED` (state `failed`, reason `plan_rejected_by_human`), saves. `reviewed` is entered at most once per task and left only for `queued` or `failed` (`task.py:84` and `_TRANSITIONS`), so Reject on a stale page hits the same plan or gets the 409. **Unverified:** that no code writes `state` without `apply`.
- `_Guard._refusal` is at `server.py:555-566`; `_ui_refusal` and `_is_same_origin_request` (`:489-499`) take headers, not the method. The guard runs before routing, so a POST to `/ui/` with a session and no `Origin` gets 405 today.
- CSP already has `form-action 'self'` (`server.py:507-508`), and an existing test pins it. The cookie is `HttpOnly`, `SameSite=Strict`, `Path=/ui` (`web/routes.py:173-174`).
- UI routes are all GET (`web/routes.py:182-185`). No existing test posts to a `/ui` path (except `/ui-login-codes`) or matches `<form`. `ui.js` only toggles the banner, and `base.html` sets no `hx-boost`.
- Domain errors reach the JSON handlers unless caught: `NotReviewed` gives a JSON 409, `TaskNotFound` and `InvalidTaskId` a JSON 404 that includes the id (`server.py:96`, `:441`).
- **Unverified:** how real browsers set `Origin` and `Sec-Fetch-Site` on this form; whether `python-multipart` is installed (the route reads no body, so it is not needed); concurrent `client.post` while a TestClient stream is open (avoided).

### Tests that touch what changes
- `test_web_ui_guard.py:61` (`Origin: null` refused) stays green because the new allowance is POST-only. `test_web_ui_guard.py:95` and `webhelpers.py:7` are not edited.
- `test_web_task_needs_you.py`, `test_web_task_page.py`, `test_web_task_events.py` (fragment text; no `<form` assertions; the new link adds an `<a>`), `test_web_page_wiring.py` and the no-inline-script/style assertions. All expected to pass unedited.
- TestClient: use `follow_redirects=False` to see the 303; every new POST test sends `Origin: http://127.0.0.1:8765` through one helper, except where `Origin` is the subject.

## Increments
One failing test (or one parametrized behaviour), the minimal change, one commit each. "Guard" means earlier code already satisfies it and the test is committed with that label.

0. **Branch and baseline.** Cut `plan-72-reject-plan`; run the gate; no commit.
1. **POST needs an Origin (red).** A non-GET, non-HEAD `/ui` request with a session and no `Origin` gets 403 with the five headers. Sub-assertions: the exact `Origin` still reaches routing (405 on `/ui/`); GET without `Origin` still passes. Change: `_ui_refusal` takes the method.
2. **`Origin: null` on a POST (red).** `null` with `Sec-Fetch-Site: same-origin` reaches routing (405 on `/ui/`); `null` without the header, with `cross-site`, with `same-site`, and a foreign origin all get 403. Sub-assertion: GET with `null` is still 403.
3. **Confirm page (red).** `GET /ui/tasks/{id}/reject` for a `reviewed` task: a page in the layout with the goal (escaped), a form `method="post"` to the same path (`| urlencode`) with one submit button and no other field, a link back to the task, no inline script or style.
4. **Confirm page, other states (red).** Every state but `reviewed` gets the 409 page with no form, no id, task unchanged (parametrized).
5. **Confirm page, bad ids (red).** Missing, invalid and unreadable ids give 404, 404 and 500 pages with no id echoed (reuse `_load_task`).
6. **Reject (red).** `POST /ui/tasks/{id}/reject` for a `reviewed` task answers 303 to `/ui/tasks/{id}`; the task is `failed` with reason `plan_rejected_by_human`. Change: `approve_plan.reject_plan`; no body is read.
7. **Reject, other states (red).** Every state but `reviewed` gets the 409 page; task unchanged (parametrized). Change: catch `InvalidTransition`.
8. **Reject, bad ids (red).** Missing, invalid and unreadable ids give the same 404, 404 and 500 pages; a store wrapper that raises `TaskNotFound` or `ValueError` after `_load_task` gives the same pages, never a JSON body or a bare 500; no id echoed; nothing saved. (The store has no delete, so "deleted" is simulated by the wrapper.)
9. **Race (guard, red if 7 did not map `InvalidTransition`).** A wrapper that moves the task out of `reviewed` between `_load_task` and the transition gives the 409 page. Not a claim about concurrent writers (D7).
10. **Guard rules on the new routes (guard).** With a correct `Origin` except where `Origin` is the subject: no cookie 401, foreign `Origin` 403, non-loopback Host 403, `Sec-Fetch-Site: cross-site` 403; the five headers on the 200, 303, 409, 404 and 401 responses; `HEAD` on the POST path 405 with headers.
11. **Link in the fragment (red).** A `reviewed` task's fragment holds `<a href="/ui/tasks/{id}/reject">` (`| urlencode`); no other state's fragment does (parametrized over all states); the fragment never holds a form or inline script or style.
12. **Docs.** README Web UI subsection: the Reject link and confirm page, the Origin rule for writes and why (`no-referrer` makes `Origin: null`), the cost of a rejection (D4), the known limits (D7; the session cookie also goes to any localhost port, since cookies ignore ports, so a hostile local server could replay it); a deviations section here.
13. **Gate.** `cd orchestrator && uv run ruff check`, then `uv run pytest -q -n auto`; run the `verify` skill; run `audit-commits` against this plan. Rerun the race test and the new route tests 10 times with a shell loop. Merge of plans 69-72 waits for the manual browser check of the POST, the 303 and the confirm page with a real `Origin` (D8).

## Deviations
- Increment 5 passed without a code change (increment 3 already reused `_load_task`); committed as a Guard, not red.
- Increment 8: the four id cases (missing, invalid, unreadable on POST) were already green through `_load_task`; only the store-wrapper cases (`TaskNotFound`, `ValueError` after the first load) were red. `_missing_page` and `_unreadable_page` were extracted so both paths share the pages.
- Increment 10: `HEAD` on the reject path is not 405. Starlette serves HEAD from the GET route, so it gets the confirm page's headers with an empty body (200) and changes nothing. The test asserts that instead; HEAD never reaches the POST handler.
- Increments 3 and 4 live in `tests/test_web_reject_page.py`, 5 and 8 in `tests/test_web_reject_ids.py`.
- From increments 1-2 (no change made): the guard builds its header dict from the last copy of a repeated `Origin` or `Sec-Fetch-Site` (pre-existing; non-browser clients can already skip Origin checks); a lower-case method counts as a write (fails closed); a browser without Fetch Metadata gets 403 on the form POST (fails closed, D3).
- Manual check, 2026-11-21 (user, loopback; browser and version not recorded): the reject page and button read well, and rejecting redirected with an acceptable `Origin`. The user saw no `Sec-Fetch-Site` on that POST, though page requests on loopback do carry `same-origin`. This is unexplained (it may have been read off a different request); the POST succeeded through the exact-`Origin` rule, so nothing fails, but D3's expectation of `Sec-Fetch-Site: same-origin` on the form POST is not confirmed.

## Out of scope
Approve (digest, findings, waivers), answering questions, merge, abandon, retry; a per-session form token; a compare-and-swap in the store; changing `Referrer-Policy`; recording that a rejection came from the UI; session idle expiry; `X-Frame-Options` (`frame-ancestors 'none'` already covers modern browsers); any change to the JSON API.

## Risks
- First write route under `/ui`. The Origin rule (D3) rests on browser behaviour that is read from the spec but not tested; a wrong reading fails closed (the button gets 403), which only the manual check will reveal. The tests set the headers by hand.
- Lost update (D7) and the cross-port cookie are residual, documented, and not fixed.
- The confirm page adds a GET that shows a rejectable task's goal; it is behind the same session and guard as every other `/ui` page.
- Layout and behaviour in a real browser stay unchecked until the manual check.
