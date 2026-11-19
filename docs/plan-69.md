# Plan 69: a browser UI on `factory serve`, starting with a live task list

Reviewed (2026-11-19); findings folded in. The decisions below are the user's.

## Problem
Everything the factory knows is reachable only through the CLI or GitHub. A browser page served by `factory serve` that shows every task and its state, and updates as states change, is the first step. Later plans add actions (answer, approve, merge, retry, advance), so the infrastructure chosen here must suit an interactive UI even though the first page only reads.

## Decisions (user, 2026-11-19)
| Question | Chosen | Rejected, and why |
|---|---|---|
| Rendering | Jinja2 rendered on the server (`starlette.templating`) plus htmx, with htmx vendored as a static file | SPA (Svelte or React with Vite and TS): a node toolchain, a build step and a second test stack. Vanilla JS on the JSON API: gets messy once forms and previews arrive |
| Browser auth | A one-time login link, redeemed for an HttpOnly, SameSite=Strict session cookie scoped to `/ui`. The JSON API stays Bearer-only | Token pasted into the page: page JS could read it, so an XSS would mean full control. No auth: a dead end once actions arrive |
| Live updates | Server-Sent Events from the first increment | Polling from the browser, manual refresh |
| Mount | Same app and port, under `/ui`, always on | An opt-in flag, or a second listener |
| Change feed | A listener hook on `SqliteTaskStore` | Wrapping `ctx.store`, which misses stage writes; wrapping in `build_context`; `PRAGMA data_version` polling |
| Shutdown with streams open | An app `stopping` event that streams watch, plus `timeout_graceful_shutdown` as a backstop | Timeout only; event only |
| Getting a link | `factory ui` only; `serve` prints nothing new | A startup link: it would break `test_cli_serve.py:37` and leave a live code in scrollback |
| Static files | `/ui/static/*` needs no cookie, but still gets the Host check and the security headers | Cookie required, which would leave the 401 page unstyled |

## Verified (2026-11-19)
- `build_context` (`cli/__init__.py:307-313`) creates one `SqliteTaskStore` and passes it to both `build_stages` (through `Deps`) and `Context`. `stages.py:85-106` captures `deps.store`. So a hook on that one instance sees every task write in the server process, while replacing `ctx.store` would not.
- No other process writes tasks while `serve` runs. The CLI commands and `watch` go through `ctx.server`. `migrate` takes `state_lock`, as `serve` does (`admin.py:14`, `server.py:504`). Agents cannot reach `state_dir` (plan O7).
- `_Guard` (`server.py:458-484`) ignores the path and refuses any `Origin`. `create_secured_app` wraps the whole app and copies `app.state` (`server.py:487-492`). A path branch inside `_Guard` therefore works.
- uvicorn 0.54 (h11; httptools is not installed): `timeout_graceful_shutdown` defaults to `None` (`config.py:236`). `shutdown()` waits for open connections before it runs the lifespan shutdown (`server.py:281-311`), and it sends no `http.disconnect`. An open SSE stream therefore blocks SIGTERM and SIGHUP indefinitely, and so would a "stopping" flag set in the lifespan. The event must be set from `handle_exit` and the SIGHUP handler.
- Starlette 1.10: `TemplateResponse(request, name, context)` (`templating.py:117`). `Jinja2Templates(directory=…)` uses `select_autoescape()` (`templating.py:95`), which escapes only `.html`, `.htm` and `.xml` names, so this plan passes its own `Environment(autoescape=True)`. `Mount("/ui")` compiles to `/ui/{path:path}`, and `GET /ui` 307-redirects to `/ui/`.
- `TestClient` streams with `iter_body`, and closing it cancels the app (`testclient.py:376-399`). Reading the first SSE event and closing should not hang; the first SSE test confirms this.
- `test_cli_serve.py:37` asserts the whole `out.strip()`, which is why there is no startup line. `test_cli_wiring.py:91` checks the first line only. `test_server_security.py:82-84` (any `Origin` on `/tasks` is 403) is unaffected by a correct path split. `test_cli_client.py:56-66` is unaffected. `main` already handles `ServerUnreachable` (`cli/__init__.py:125-127`).
- `jinja2` is not a dependency yet.

## Design
- **Layout.** A new package `factory/web/`:
  - `auth.py`: `LoginCodes`, `Sessions`;
  - `changes.py`: `TaskChanges`;
  - `routes.py`: the `/ui` routes;
  - `templates/*.html`;
  - `static/`: vendored `htmx.min.js`, the SSE extension, `ui.css`, `ui.js`, and `VENDORED.md` (versions, sha256, the 0BSD licence text).

  The JSON API is unchanged apart from one new endpoint, `POST /ui-login-codes`.
- **Paths.** `/ui/` is the canonical page. A top-level `Route("/ui")` answers 308 to `/ui/`, so no Starlette 307 surprises.
- **Guard split.** A path is under the UI when it is exactly `/ui` or starts with `/ui/`; `/ui-login-codes` is API. Outside the UI, `_Guard` is unchanged. Inside it:
  1. The Host must be loopback (shared with the API).
  2. `Origin` must be absent or equal `"http://" + Host`. Host is already loopback-checked, so the port needs no new parameter and `[::1]` is covered.
  3. `Sec-Fetch-Site`, when present, must be `same-origin` or `none`. This refuses other loopback ports, which count as same-site for cookies.
  4. A valid session cookie, except on `/ui/login` and `/ui/static/*`.

  A missing or invalid cookie gets a 401 HTML page that says to run `factory ui`. Bearer does not open `/ui`, and a cookie does not open the API.
- **Security headers on every UI response**, including the guard's 401 and 403 pages and the 308. The guard adds them, so nothing escapes a mount-level middleware:
  - `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`;
  - `Referrer-Policy: no-referrer`;
  - `Cache-Control: no-store`;
  - `X-Content-Type-Options: nosniff`;
  - `Cross-Origin-Resource-Policy: same-origin`.

  htmx gets `{"includeIndicatorStyles": false, "allowEval": false, "allowScriptTags": false, "historyCacheSize": 0}` through `<meta name="htmx-config">` (key names checked in increment 0). `historyCacheSize: 0` stops htmx keeping page snapshots, which contain task text, in `sessionStorage`. Templates use no `style=` attributes and no `hx-on`.
- **Login.**
  - `LoginCodes` issues a random single-use code with a 5-minute expiry (injected clock) and prunes expired codes on each issue.
  - `POST /ui-login-codes` (Bearer) returns `{"url": "http://<Host>/ui/login?code=…"}`, built from the request's Host. `factory ui` prints that URL.
  - `GET /ui/login?code=…` redeems the code and creates a session (a random id held in memory, so a restart logs everyone out). It sets `factory_session_<port>` (port taken from Host; HttpOnly; SameSite=Strict; Path=/ui) and answers 200 with a page whose only content is a same-origin link to `/ui/`. No redirect: a Strict cookie may not be sent on a redirect that starts from a cross-site click (from a terminal or chat app; not measured), but it is always sent on a same-origin click (user, 2026-11-19).
  - A link-preview prefetcher can use up the code; get another with `factory ui`.
- **Task page.** `GET /ui/` shows a table of id, state, truncated goal and failure reason, newest first. Human-gate states (`open_questions`, `reviewed`, `audited`) are marked, and skipped rows are listed. The table is `templates/_tasks.html`, rendered both into the page and into SSE events.
- **Change feed.**
  - `SqliteTaskStore` gets `subscribe(listener)`. `save` and `create` call the listeners after the transaction commits, never when the write raised. A listener that raises is logged and swallowed, so it cannot fail a committed save.
  - In the lifespan, `_build_app` subscribes `TaskChanges.notify` and captures the running loop.
  - `notify` may run on the job worker thread or on the loop thread. It does `loop.call_soon_threadsafe(bump)` and does nothing when there is no loop yet or the loop is closed. That case happens because `runner.stop()` joins for only 5 s, so a stage may still write after shutdown.
  - `bump` runs on the loop thread only. It increments the version and wakes the waiters, so the version has a single writer thread.
  - `wait_past(version)` returns as soon as the version is higher, so a write between render and wait is not lost.
- **SSE.** `GET /ui/events` answers `text/event-stream`:
  - On connect it sends `event: tasks` with the rendered table, then sends it again after each change.
  - It sends a `: keepalive` comment every 15 s.
  - It ends when the app's `stopping` event is set.
  - Fragment lines are split on CR, LF and CRLF (`str.splitlines()`), and each becomes its own `data:` line, so a `\r` in a task cannot end an event.
  - The page uses `hx-ext="sse" sse-connect="/ui/events" sse-swap="tasks"`. `ui.js` (static, so the CSP allows it) shows a "disconnected; run `factory ui`" banner when the stream errors, because EventSource gives up on a 401.
- **Stopping.**
  - `app.state.stopping` is an `asyncio.Event`.
  - `_serve_with_uvicorn` uses a `uvicorn.Server` subclass whose `handle_exit` also sets it (through `call_soon_threadsafe`); the SIGHUP handler does the same. It passes `timeout_graceful_shutdown=5` as a backstop for any other slow request.
  - The event is set only from those two hooks, not from the lifespan, which runs too late.
- **Dependency.** `uv add jinja2`. No node and no build step.

## How later plans build on this
- An action is a form or an `hx-post` to a UI handler that calls the same service or domain function the JSON handler calls.
- Stale-preview checks carry over: the approve form holds `plan_digest` and the findings it showed as hidden fields, the merge form holds `audited_sha`, and a 409 re-renders with "changed since you looked".
- The first POST adds CSRF: a per-session token in a `<meta>`, sent through `hx-headers`, checked along with the exact Origin and `Sec-Fetch-Site`.
- Long actions return a job. The change feed can also carry job changes, as a second SSE event name, through the same listener pattern on `JobStore`.
- Logic shared by JSON and HTML handlers moves to the service layer when its second caller arrives.

## Increment 0 results (2026-11-19; detail in `docs/spike-findings.md`)
- Settled:
  - `uv_build` ships `web/templates/*` and `web/static/*`.
  - htmx 2.0.11 and htmx-ext-sse 2.2.4 (both 0BSD) are pinned. The sha256 values are in the findings.
  - The SSE attributes and the `htmx-config` keys are as the Design says.
  - The extension reconnects with backoff (0.5 s to 64 s) and fires `htmx:sseError`, which `ui.js` uses for the banner.
  - New: `historyCacheSize: 0`.
- Not measured, because no browser is installed:
  - Which `Origin` and `Sec-Fetch-Site` headers browsers send. The guard accepts `Origin` absent or exact, so it does not depend on the answer.
  - Whether a Strict cookie survives a redirect after a cross-site click. Designed around: login answers 200 with a link.

  Increment 22's manual check records the headers browsers actually send, for the later CSRF design.

## Increments (one commit each; red = failing test first; guard = passes before the green)
Refer to tests by name; line numbers drift.

0. **Spike: done** (see "Increment 0 results"). No spike code was kept: the probes were a throwaway wheel and npm packages read in the scratchpad.
1. **Login codes.** Red: a code from `LoginCodes.issue()` redeems once; a second use, an unknown code and an expired code (via the clock) are refused. Then red: issuing prunes expired codes. Green: `web/auth.py`.
2. **Sessions.** Red: `Sessions.create()` returns an id that `valid()` accepts; an unknown id is refused.
3. **Store hook.** Red: a subscribed listener is called after `save` and after `create`, and not when `create` raises `TaskExists`. Then red: a listener that raises does not make `save` raise, and the row is saved.
4. **Wiring.** Red: a listener subscribed on `build_context(settings).store` is called when a stage from that context's `stages` saves a task. This proves the stages and `ctx` share the observed instance. Guard if it already passes, which is expected: say so and commit it as a guard.
5. **`TaskChanges`** (hand to the implementation-complex agent). Red: `wait_past(v)` returns after `notify()` is called from another thread. Guard: `wait_past(old)` returns at once. Red: `notify()` before a loop is attached, and after the loop is closed, does not raise.
6. **The guard on `/ui`.** Red: `GET /ui/` with no cookie gets 401 HTML naming `factory ui`. Green: the guard's path branch, the `/ui` 308 route, and a placeholder page. Guards (same commit): `/tasks` with a same-origin `Origin` is still 403 (existing test); `/ui/` with only Bearer is 401; `POST /ui-login-codes` without Bearer is 401 once that route exists (move this guard to 8 if needed).
7. **Login route.** Red: `/ui/login?code=<valid>` answers 200 with a link to `/ui/` and sets `factory_session_8765` with HttpOnly, SameSite=Strict and Path=/ui; reusing the code gets 401; with the cookie, `/ui/` gets 200 (the placeholder).
8. **Code endpoint.** Red: `POST /ui-login-codes` with Bearer returns a `url` built from the request's Host whose code logs in.
9. **Origin rules.** Red: with the cookie, a foreign `Origin` gets 403, `Origin: http://<Host>` gets 200, `Sec-Fetch-Site: same-site` and `cross-site` get 403, and `same-origin` and `none` get 200. Guard: a non-loopback Host gets 403.
10. **Security headers.** Red: the 200 page, the guard's 401 and 403 pages and the `/ui` 308 all carry the five headers.
11. **Static files.** Red: `/ui/static/htmx.min.js` is served without a cookie, with the headers, and its sha256 matches `VENDORED.md`. Then red: `/ui/static/../` (path traversal) does not leave the static directory. That one is likely a guard, because Starlette's `StaticFiles` checks it; label it accordingly.
12. **Task page.** Red: the page lists id, state, truncated goal and failure reason, newest first, marks the human-gate states and lists skipped ids. Green: `uv add jinja2`, `Environment(autoescape=True)`, the templates.
13. **Escaping guard.** `<script>` in the goal and in the failure reason comes out escaped in the page and in the `_tasks.html` fragment. Expected to pass already, so it is a guard.
14. **SSE first event.** Red: `GET /ui/events` answers `text/event-stream`, its first event is `event: tasks`, and every payload line starts with `data: `.
15. **CR in task text.** Red: a goal containing `\r` and `\r\n` does not add or end an event; only `data:` lines appear between the event line and the blank line.
16. **Change event.** Red: after a store `save`, the next event carries the new state.
17. **Keepalive.** Red: a keepalive comment arrives after the interval (interval injected).
18. **Stopping, in process.** Red: setting `app.state.stopping` ends an open stream.
19. **Stopping, in uvicorn.** Red, as a subprocess test in the style of `test_cli_wiring`: with an SSE stream open, SIGTERM and SIGHUP make `serve` exit within the backstop, and the lock is released. Green: the `uvicorn.Server` subclass, the SIGHUP handler change and `timeout_graceful_shutdown=5`.
20. **Page wiring.** Red: the page has the `sse-connect` and `sse-swap` attributes and the htmx config meta, and `ui.js` is served. The banner behaviour is checked in the browser in increment 22, not in a unit test.
21. **`factory ui` command.** Red: it prints the URL from `POST /ui-login-codes`. Guard: with no server running it prints `no factory server is running`.
22. **Docs, then a manual check.** README: a "Web UI" subsection under "Server" (`factory ui`, cookie scope, the logout on restart, the port-less cookie risk, what the page shows, the limits); reword the "any `Origin` header is refused" sentence to "outside `/ui`"; add `ui` to the command table and to the "client of this server" sentence. Record deviations here. Then check by hand in a real browser (the `run` skill): log in; record the `Origin` and `Sec-Fetch-Site` headers each request kind carries (add them to `docs/spike-findings.md`); watch a row change during `factory advance`; restart and see the banner; with a tab open, send SIGTERM and close the terminal (SIGHUP), and confirm `serve` exits and releases the lock.
23. **Gate.** `uv run ruff check`, `uv run pytest -q -n auto` in the background, `verify`, `audit-commits`.

## Tests touched (CLAUDE.md step 2)
Every test below is unaffected, so no passing test changes:
- `test_cli_serve.py:37` (whole serve output): unaffected, because `serve` prints nothing new.
- `test_cli_wiring.py:91` (first line `startswith("serving on ")`): unaffected. Its `server.wait(timeout=30)` gets faster, if anything, with the backstop.
- `test_server_security.py:82-84` (any `Origin` on `/tasks` is 403): unaffected; increment 6 keeps it as a guard.
- `test_cli_client.py:56-66` (`bearer token required`): unaffected.

## Out of scope
- A task detail page.
- Every action: answer, approve, reject, merge, retry, advance, abandon, clean.
- Jobs and costs views.
- CSRF tokens, which arrive with the first POST.
- Remote access, TLS, multiple users.
- Idle expiry of sessions (they live until restart).
- Any CSS beyond a small hand-written `ui.css`.

## Risks
- **Cookies ignore ports.** Another loopback server that the browser visits receives `factory_session_<port>`. HttpOnly keeps page scripts from reading it, and the `Sec-Fetch-Site` check refuses requests that pages on other ports make with it. A hostile local server run by another user could still collect it. This is accepted for a single-user machine, and the README says so.
- **Sessions live in memory.** A restart logs the browser out, and `factory ui` gives a new link.
- **Requests with no `Origin` and no `Sec-Fetch-Site`** (old browsers, non-browser clients holding the cookie) pass on cookie alone. That is acceptable while the UI only reads; the CSRF design for the first POST closes it.
- **Re-rendering cost.** Each SSE client re-renders the table on every change. That is fine at today's scale.
- **The 5 s backstop also cuts off other in-flight requests at shutdown.** Today's handlers only queue jobs or return quickly; `merge_preview` and `clean_preview` run git.
- **Vendored htmx runs with page privileges.** It is pinned by version and sha256 and updated by hand.
