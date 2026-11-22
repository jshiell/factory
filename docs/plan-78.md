# Plan 78: web guard hardening

Branch `plans-77-79-known-gaps` (shared by plans 77-79), after plan 77. Drafted 2026-11-22 from plan 74's Deviations and a read of `server.py:484-600`; revised after plan review. **Draft, not approved.**

## Problem
Gaps recorded in plans 73-76 and not fixed:
1. **Text after the port passes.** `_host_name` is `host.partition(":")[0]` (`server.py:484-487`), so `nas.lan:x:y` is read as `nas.lan`. It also lets `[::1]junk` through the bracket branch.
2. **Duplicate `Host` headers.** `_Guard` builds `headers` as a dict, last wins (`server.py:566`); Starlette's `request.headers['host']` is the first, so the checked host can differ from the one used for the login link and cookie name. Not verified whether uvicorn rejects duplicates.
3. **Entry check is not strict IPv4.** `_HOST_NAME` accepts `1.2.3` (`settings.py:125`).
4. **Session idle expiry** is absent (plans 70, 72).
5. **No `Secure` cookie.** Plain HTTP is the supported transport; `Secure` needs TLS.

Dropped after review:
- Sessions tied to a host: a stolen cookie is replayed by a client that sets any `Host`, and browsers already scope cookies by host, so it blocks no attack.
- Loopback reject POST lacking `Sec-Fetch-Site`: plan 76 has no such section and Chromium sent `same-origin` on every POST. Reduced to a single check in the browser step (increment 8).

## Decisions (recommendations accepted by the user 2026-11-22; review findings folded in)
| # | Question | Decision |
|---|---|---|
| D1 | Items 1-3 | `Host` parser: name, optional `:` plus ASCII `[0-9]+` (use `re.fullmatch(r"[0-9]+")`, not `isdigit`); keep the `[::1]` branch and accept only `[::1]` and `[::1]:<digits>`. Anything else fails closed. Refuse duplicates of every header the guard reads (`Host`, `Origin`, `Sec-Fetch-Site`). `server_allowed_hosts` entries must be a real IPv4 literal (four octets 0-255) or hostname labels. |
| D2 | Item 4 | Idle expiry, fixed 8 hours since last use, not configurable. `Sessions` gets a default clock (`Sessions()` takes no arguments today; a clock parameter defaults to `time.monotonic`), "use" = any request the guard authenticates, expired ids are pruned on lookup. Open SSE streams outlive their session (documented, not fixed). |
| D3 | Item 5 | Not fixed; move from "known gaps" to "unsupported" in the README. |
| D4 | Breaking config | Tightening item 3 makes entries like `1.2.3` a `SettingsError` at startup. The README says so. |

## Tests touched (list before increment 1; no existing test is edited)
Grep for the exact strings and status codes first: candidates are `test_server_allowed_hosts.py`, `test_web_allowed_hosts.py`, `test_settings_allowed_hosts.py`, `test_cli_ui_host.py`, `test_web_login.py`, `test_web_post_origin.py`, the `test_web_*_guard.py` files, and every test that constructs `Sessions()`. Record the grep result here.

## Unverified (increment 0, throwaway, flagged)
- uvicorn/h11 behaviour on duplicate `Host` (reject, first, last), via real uvicorn.
- `Sec-Fetch-Site` on the loopback reject POST in Chromium.

## Increments (test first, one commit each; guards marked)
0. Probes above.
1. Red: `nas.lan:x:y`, `nas.lan:`, `[::1]x`, `[::1]:x:y` and non-ASCII-digit ports get 403; `[::1]`, `[::1]:8765`, `nas.lan:8765` pass. Green: strict parser.
2. Red: two `Host` headers get 403 (a red, not a guard: the in-app test client skips uvicorn). Same for duplicate `Origin` and `Sec-Fetch-Site`. Green: refuse duplicates.
3. Red: `server_allowed_hosts = ["1.2.3"]` is a `SettingsError`; `1.2.3.4` and `nas-1.lan` are accepted. Green: stricter check.
4. Red: a session unused for 8 hours is refused (fake clock); use refreshes it; expired ids are pruned. Green: expiry in `Sessions` with a default clock.
5. Docs: README Web UI risks rewritten (`Secure`/TLS unsupported; idle expiry; the `1.2.3` breaking note; SSE outlives session); Deviations here.
6. Browser check (Chromium only): loopback sign-in, reload, a reject POST with headers recorded via `allHeaders()`, and one request under an allowed non-loopback name if the user can. Results under Deviations.
7. Gate: ruff, `pytest -q`, `verify`, `audit-commits`.

## Out of scope
TLS, `Secure`, proxies and `X-Forwarded-*`, wildcards, host-bound sessions, rate limiting of login codes, any server-side check of `factory ui --host`.

## Tests touched (recorded 2026-11-22)
No existing test was edited. Existing tests that exercise the changed paths and stayed green: `test_server_allowed_hosts.py`, `test_server_security.py` (loopback `[::1]:8765`), `test_web_allowed_hosts.py`, `test_settings_allowed_hosts.py`, `test_cli_ui_host.py`, `test_cli_serve_allowed_hosts.py`, `test_web_login.py`, `test_web_auth.py` (the only test constructing `Sessions()`), the `test_web_*_guard.py` files. New: `test_server_host_parse.py`, `test_server_duplicate_headers.py`, `test_settings_ipv4_hosts.py`, `test_web_session_expiry.py`.

## Deviations
Implemented 2026-11-22.

- **Increment 0 (probe, throwaway).** Real uvicorn answers 400 to a duplicate `Host` before the app sees it; a duplicate `Origin` reaches the app. So the duplicate-`Host` refusal is defence in depth (the in-app test client skips uvicorn), and duplicate `Origin` and `Sec-Fetch-Site` are the live cases.
- **Increment 2.** One guard check covers all three headers, refusing before any other rule.
- **Increment 3.** The rule is "an entry made only of digits and dots must be a valid IPv4 address" (so `123` and `1.2.3` fail). Leading zeros fail (`ipaddress` behaviour, Python 3.14).
- **Increment 4.** `Sessions(clock=time.monotonic)`; `valid()` is the use that refreshes; expired ids are dropped on `create` and `valid`. Added `Sessions.count()` so the pruning test needs no private access. Ruff rewrote one test assertion into constant-first order.
- **Test for non-ASCII ports** sends the header as latin-1 bytes (the client cannot encode it as ASCII); it uses `²`, which `str.isdigit` accepts.
- **Increment 6 (browser check):** see below once run.
