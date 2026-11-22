# Plan 78: web guard hardening

Branch `plans-77-79-known-gaps` (shared by plans 77-79), after plan 77. Drafted 2026-11-22 from plan 74's Deviations and a read of `server.py:484-600`. **Draft, not approved.**

## Problem
Gaps recorded in plans 73-76 and not fixed:
1. **Text after the port passes.** `_host_name` is `host.partition(":")[0]` (`server.py:484-487`), so `nas.lan:x:y` is read as `nas.lan` (plan 74 Deviations).
2. **Duplicate `Host` headers.** `_Guard` builds `headers` as a dict, so the last `Host` wins (`server.py:565`); Starlette's `request.headers['host']` is the first, so the checked host can differ from the host used for the login link and cookie name (plan 74). Not verified whether uvicorn rejects duplicates.
3. **Entry check is not strict IPv4.** `_HOST_NAME` accepts `1.2.3` (plan 74 Deviations, `settings.py:125`).
4. **Loopback reject POST lacks `Sec-Fetch-Site`.** Reported in plan 76's residual gaps; not described further in the docs I read, so increment 0 re-derives it.
5. **Sessions are not tied to a host** (cookie name is `factory_session_<port>` only, `web/auth.py:7`): a cookie stolen on one name works on every other name and on loopback (plan 74).
6. **No session idle expiry** (plans 70, 72).
7. **No `Secure` cookie.** Plain HTTP is the supported transport, and a `Secure` cookie is likely refused there (unverified, plan 74 D7).

## Decisions (recommendations accepted by the user 2026-11-22; the plan is not yet reviewed)
| # | Question | Proposal |
|---|---|---|
| D1 | Items 1-3 | Fix: parse `Host` strictly (name, optional `:digits` only; anything else fails closed), refuse duplicate `Host` headers, accept only real IPv4 literals or hostname labels in `server_allowed_hosts`. |
| D2 | Item 4 | Probe first (increment 0), then fix or record as not reachable. |
| D3 | Item 5 | Bind a session to the host it was issued for (store the host with the session, refuse use under another). A change in `web/auth.py` and `Sessions`. |
| D4 | Item 6 | Idle expiry with a fixed default (proposal: 8 hours since last use, not configurable). Needs a clock, which `ctx.clock` supplies. |
| D5 | Item 7 | Not fixed: needs TLS. Stays documented. Move it out of "known gaps" into "unsupported". |

## Unverified (increment 0)
- uvicorn's handling of duplicate `Host` headers (reject, first, or last).
- What exactly lacks `Sec-Fetch-Site` on the loopback reject POST, and whether browsers send it there (Chromium did for plan 76's answer and approve POSTs, `same-origin`).
- Whether tying sessions to a host breaks `factory ui --host NAME` sign-in (the cookie is set on the response of `/ui/login`, so the Host there decides it).

## Increments (test first, one commit each, guards marked)
0. Probes: duplicate `Host` through real uvicorn; the loopback reject POST headers in Chromium.
1. Red: `nas.lan:x:y` and `nas.lan:` get 403. Green: strict Host parser (name plus optional port digits).
2. Red: two `Host` headers get 403. Green (or guard if uvicorn already refuses).
3. Red: `server_allowed_hosts = ["1.2.3"]` is a `SettingsError`; `1.2.3.4` and `nas-1.lan` are accepted. Green: stricter `_HOST_NAME`.
4. Increment for D2 after the probe.
5. Red: a session issued under one host is refused under another; the login flow for an allowed host still works. Green: host bound to the session.
6. Red: a session unused for the idle limit is refused (fake clock); use refreshes it. Green: expiry in `Sessions`.
7. Docs: README Web UI risks list rewritten, `Secure` and TLS moved to unsupported; Deviations here.
8. Browser check: Chromium, loopback sign-in, reload, a reject POST, and (if the user can) one request under an allowed non-loopback name. Results under Deviations.
9. Gate: ruff, `pytest -q`, `verify`, `audit-commits`.

## Tests touched
Grep the guard tests (`test_web_post_origin.py`, `test_web_*_guard.py`, `test_settings*`, the server Host tests) for the exact strings and status codes the changes touch; list them before increment 1. No existing test is edited.

## Out of scope
TLS, `Secure`, proxies and `X-Forwarded-*`, IPv6 hosts, wildcards, rate limiting of login codes, any server-side check of `factory ui --host`.

## Deviations
Recorded after implementation.
