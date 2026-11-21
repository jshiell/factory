# Plan 75: lone-surrogate safety and streams only in visible tabs

Reviewed (2026-11-21, plan-reviewer, opus); findings folded in. Branch `plan-72-reject-plan` (plans 69-74 are stacked there; stay). Both items come from the manual check of plans 69-73 (2026-11-21), recorded in those plans' Deviations.

## Problem
1. A goal holding a lone surrogate (for example `"bad\ud800goal"`) is stored, then `GET /ui/` returns 500 (`UnicodeEncodeError` in `HTMLResponse`, `web/routes.py:120`) and the detail page's connection is reset (why a reset and not a 500 is not known). One such goal breaks the whole list page.
2. The API breaks the same way: Starlette's `JSONResponse.render` does `json.dumps(..., ensure_ascii=False).encode("utf-8")` (`starlette/responses.py:199-206`, read), and `server.py` uses it (27 call sites), so `GET /tasks` and `GET /tasks/{id}` should return 500 for such a row (the reading is verified, the 500 itself is not run). The CLI is an API client (README line 145: every command but init, clone, migrate, serve and eval-export), so `factory list` and `factory show` fail through the API and never reach their `escape_control` call; the watcher polls the same API.
3. Every open `/ui` tab holds one SSE connection. With 7 or more tabs a new page load hangs (the browser's per-host connection cap; seen once, tab mix not recorded).

## Decisions
| # | Question | Decision |
|---|---|---|
| D1 | Tab limit | User, option b: a tab streams only while it is visible. |
| D2 | Surrogate | User: fix it. |
| D3 | Web UI fix site | `escape_control` (`textsafe.py`) escapes category `Cs` like `Cc` and `Cf`, so one change covers the list, the detail page, the reject page and the SSE fragments (`_safe`, `routes.py:39-41`). Output `\ud800` with a literal backslash (code under 0x10000 is written `\uXXXX`). |
| D4 | API and CLI | User (recommendation taken): option a. A `JSONResponse` subclass with `ensure_ascii=True`, used for every JSON response in `server.py`, so a surrogate leaves the server as a `\ud800` JSON escape and the client decodes it back to a str. This changes the wire bytes of all non-ASCII text (JSON-equivalent). Not chosen: rejecting surrogates at write time (rows already stored would still break the API); keeping the plan to the web UI. |
| D5 | Which pages stream-gate | Both `/ui/` and `/ui/tasks/<id>`, in the one shared `ui.js`. |
| D6 | Tab return | User (recommendation taken): accept that each reopen resends the current fragment, which resets text selection and the `<pre>` scroll that plan 70's per-connection dedupe protected. README states it. Not chosen: cancelling unchanged swaps in `ui.js`. |
| D7 | Reaching the `EventSource` | Wrap `htmx.createEventSource` in `ui.js` (reviewer finding; `sse.min.js` calls it at connect time, read). Every source, including the extension's own retries, passes through it. Invariants: at most one live source per page (close any earlier one on creating a new one); a source created while `document.hidden` is closed at once and the page marked paused; on hidden, close every tracked source and mark paused; on visible, reopen only if paused, then clear paused. The wrapper falls back to `new EventSource(url, {withCredentials: true})` when `htmx.createEventSource` is undefined, so extension init order does not matter. |
| D8 | Reopen mechanism | Set a non-empty counter attribute on the `sse-connect` element (for example `data-sse-epoch`), then `htmx.process(el)`. Reviewer's reading of `htmx.min.js` 2.0.11: `process` compares a hash of the element's non-empty attributes with the stored `initHash`, so an unchanged element is a no-op, and a changed hash clears the internal data and fires `htmx:afterProcessNode`, which makes the extension connect again and re-attach the `sse-swap` listener. That path does not fire `htmx:beforeCleanupElement`, so `ui.js` closes the old source itself (D7). A browser confirmation replaces the old spike. Fallback if it fails: `location.reload()` on becoming visible. Also reopen on `pageshow` with `persisted` (bfcache). |

## Verified (2026-11-21)
- `escape_control` (`textsafe.py:4-13`) escapes `Cc` and `Cf` only; `\n` and `\t` are kept. It is used by `web/routes.py:15` and by many CLI, audit and watch modules.
- `sse.min.js` (htmx-ext-sse 2.2.4, `VENDORED.md`) has no pause API; `htmx:sseOpen` bubbles with `detail.source`; `source.close()` does not trigger a retry (only `onerror` does); the extension stores its source in the element's internal data. A source that has not opened has no `detail.source`, which is why D7 wraps creation.
- `_stream` (`routes.py:140`) renders the current state first, then on each change, so a fresh connection needs no catch-up. `_unless_unchanged` keeps `last` per connection (hence D6).
- `ui.js` is 3 lines (`sseError` shows the banner, `sseOpen` hides it). The only content pins are `tests/test_web_page_wiring.py:47-48` (`htmx:sseError`, `htmx:sseOpen`, `disconnected`); `tests/test_web_task_wiring.py` pins only the script tag and its order (`UI_JS`, lines 6 and 22). No test runs JavaScript.
- Tests with non-ASCII in a raw response body, which D4 could change: only `tests/test_server.py:87,99` (a goal of `é` repeated, for the size limit); to be checked by increment 5's grep.

## Unverified (checked by the manual check, increment 11)
- That `close()` fires no `error` event (HTML spec, from memory), so the banner stays hidden across a deliberate close.
- That htmx processes the body after `ui.js` runs, and when the extension's `init` runs (the D7 fallback makes both orders safe).
- That a hidden tab's open connection counts against the cap and that closing frees the slot.
- bfcache eligibility and behaviour; the `pageshow` path.
- Why the detail page gave a reset and not a 500; whether the 20,000-byte goal limit (`POST /tasks`) encodes strictly and so how a surrogate got in at all (the probe wrote the store directly).
- That `GET /tasks` really returns 500 today (increment 5 is red only if it does).

## Tests touched (CLAUDE.md step 2)
- Pinned strings: `tests/test_web_page_wiring.py:47-48` stay (the hooks remain). `tests/test_web_task_wiring.py` is unaffected. `tests/test_web_list_escape.py` stays green.
- D4: increment 5 first greps `tests/` for assertions on raw non-ASCII response text (`test_server.py:87,99` so far) and lists them; none is edited.
- New: a unit test file for `escape_control` (named when written).

## Increments (one commit each; red first unless marked guard)
0. **Baseline:** confirm the branch; run ruff and the full suite; no commit.

Surrogate (web UI):
1. **`escape_control` escapes `Cs`** (red): `a\ud800b`, a `\udcff` case (undecodable argv bytes become `\udc80-\udcff` on Linux, the likelier real source), and two halves written separately (not recombined). Green: add `"Cs"`.
2. **List page** (guard; increment 1 covers it): `GET /ui/` is 200, holds no raw surrogate, and shows `bad\ud800goal` with a literal backslash; `render_tasks` the same.
3. **Detail and reject pages** (guard): the same for `GET /ui/tasks/<id>`, `_task_fragment`, and the reject page of a reviewed task (it renders the goal through `_task_detail`).

API and CLI (D4):
4. **Grep** the API tests for raw non-ASCII assertions and list them in Deviations (no commit).
5. **The API survives a surrogate** (red, if the 500 is real): `GET /tasks/{id}` and `GET /tasks` return 200 for a surrogate goal. Green: the subclass, used by every JSON response in `server.py`.
6. **The client side** (guard if 5 covers it, else red): `factory show` for that task prints the escaped text, asserted on the output, not only "no error".

Stream gating:
7. **Close on hidden** (red pin on the new strings only; `htmx:sseOpen` is already pinned): the served `ui.js` names `createEventSource` and `visibilitychange`. Green: the wrapper with the D7 invariants (one live source, close on hidden, close at creation when hidden).
8. **Reopen on visible** (red pin): `ui.js` names `htmx.process` and the epoch attribute. Green: if paused, bump the epoch and `htmx.process`, on `visibilitychange` visible and `pageshow` with `persisted`.
9. **README and Deviations:** one sentence in the live-update bullet (a hidden tab holds no stream and shows current state on return, with selection and scroll reset); line 153's "only when changed" claim amended; line 154's banner bullet (it now also shows when you return to a tab after a restart); line 164's unchecked list; the API's `\u` escaping of non-ASCII is noted if the user-facing docs say otherwise.
10. **Gate:** ruff, the full suite, the `verify` skill, audit-commits; sign commits; never push.
11. **Manual check (the user runs it, throwaway):**
    - 10 tabs mixing list and detail pages; a new page loads at once.
    - DevTools shows one stream per visible tab.
    - Middle-click a link to open a background tab; it holds no stream until shown.
    - Switch tabs rapidly; no duplicate streams.
    - A hidden tab shows current state on return and the banner stays hidden.
    - Stop `serve` while a tab is hidden; the banner shows on return.
    - List, detail, then Back and forward.
    - Results go under Deviations.

## Out of scope
Surrogates or control characters in task ids (the detail page renders `task.id` raw: `_task_detail` line 53, the `_task.html` heading, `| urlencode`); rejecting surrogates at write time; an HTTP/2 front end (which would lift the cap); cancelling unchanged swaps; the corrupted-row reconnect check; the `Secure` cookie.

## Risks
- Gating is browser behaviour that no test here runs; increments 7 and 8 are substring pins only, so only increment 11 proves it.
- `Cs` is now escaped for every `escape_control` caller. Undecodable argv bytes (`\udc80-\udcff`) that used to survive a round trip now become literal `\udcff` text: harmless, but a behaviour change.
- D4 changes the wire bytes of every non-ASCII API response (JSON-equivalent). A client that compares raw bytes would see a difference; none is known.
- Windows side by side that are all visible still each hold a stream; the cap can still bite there.
- If the reopen mechanism fails in a browser, the fallback is `location.reload()`: simple and resyncing, but it loses scroll position.

## Deviations
Recorded after increment 9. Increment 11 (manual check) is **pending**; its results go here.
- Increment 5: `GET /tasks` did not return 500 for a surrogate goal, because the list view omits the goal; the list case is tested with a surrogate in `failure_reason` instead. `GET /tasks/{id}` returning 500 was confirmed red.
- Increment 6 passed on its first run, so it is a guard, but its test uses the file-based `TaskStore`, not the API client path (`factory show` through the server). The client decode of a `\ud800` escape is not covered end to end.
- Increments 2 and 3 share one test file.
- Increment 4 found no tests asserting raw non-ASCII response text (beyond `test_server.py:87,99`, which do not assert the body bytes).
- Increment 9: no user-facing doc says the API sends raw UTF-8, so no note on the ASCII escaping was added to the README.
- Increment 8's pin names the literal `data-sse-epoch` (`setAttribute`), not the `dataset.sseEpoch` spelling.
- htmx reading (vendored `htmx.min.js` 2.0.11, `sse.min.js` 2.2.4), all confirmed by reading the minified source:
  - `htmx.process` (`Bt`) hashes each element's non-empty attribute names and values (`Le`) and compares with `initHash` (`Ft`); on a change it clears the internal data except `firstInitCompleted` (`De`, which fires no `htmx:beforeCleanupElement`; only `y` does) and runs `Mt`, which fires `htmx:afterProcessNode`. The extension answers that with a fresh connect and re-attaches the element's `sse-swap` listener. Unchanged descendants hash the same and are skipped.
  - The extension calls the global `htmx.createEventSource(url)` at each connect, including its retries (`setTimeout` after an `error` with `readyState` CLOSED). Only `onerror` schedules a retry; neither the extension's nor `ui.js`'s `close()` does.
  - Not in the plan: `htmx.defineExtension` runs the extension's `init` synchronously, so `htmx.createEventSource` is already set when `ui.js` runs (`base.html` loads htmx, then sse, then `ui.js` at the end of the body), and htmx processes the body on `DOMContentLoaded`, after `ui.js`. This settles the "init order" item under Unverified by reading; the fallback stays.
  - Not in the plan: a retry timer scheduled before the tab was hidden still fires. While hidden, the wrapper closes its source at once (and that ends the retry chain, as a closed source raises no error). After a reopen, it replaces the epoch's source, which the wrapper closes, so there is still one live source.
- A throwaway jsdom run (real vendored htmx and sse, fake `EventSource`, not committed) passed 24 checks: one source on a visible load; close on hide with the banner left hidden; one new source on return with the swap working and one `sse-swap` listener; 10 rapid hide/show cycles leave one live source; `pageshow` reopens only when persisted and paused; a background load closes its source and opens on show; a retry while hidden is closed at once; the fallback uses `withCredentials`. It cannot show that a real `close()` fires no `error` event, the connection cap, or bfcache behaviour; those stay with increment 11.
