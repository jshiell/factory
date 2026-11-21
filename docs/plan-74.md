# Plan 74: remote access to `factory serve` through an allowed-host setting

Reviewed (2026-11-21); findings folded in. Work continues on branch `plan-72-reject-plan` (user choice): plan 73's commits were rebased onto it and the branch fast-forwarded (README conflicts resolved, full suite 2491 passed, ruff clean), so it carries plans 69-73. Needs plan 72's `/ui` (guard, cookie, login) and plan 73's `server_host`.

## Problem
Plan 73 lets `factory serve` bind `0.0.0.0`, but the guard still answers 403 to any `Host` that is not loopback (`server.py:565`), for the API and for `/ui`. So the server cannot be used from another machine. This plan adds a per-repo list of extra host names the guard accepts, makes the web UI sign-in and the reject form work for one of them, and keeps the wire plain HTTP (D7). Supported: direct plain-HTTP access to the listed name, or a tunnel that keeps the `Host`. Not supported: an HTTPS-terminating proxy (M3).

## Decisions
| # | Question | Chosen | Alternative |
|---|---|---|---|
| D1 | Shape | Per-repo setting `server_allowed_hosts`, a list of strings, default empty. Loopback names stay always allowed. An empty list written in the config is the existing "wrong type" error, as for other list keys (`_typed`, `settings.py:249`) | Flag as well; a single string |
| D2 | Where | Per-repo only, in `_PER_REPO_KEYS` (`settings.py:119`), refused in the global file, as `server_host` (a global list would open every project) | Global |
| D3 | Entry format | Lowercase ASCII labels `[a-z0-9-]` separated by dots, or an IPv4 literal; no port, scheme, path, space, wildcard, trailing dot or IPv6. Refused as pointless or ambiguous: `0.0.0.0`, loopback names (`127.0.0.1`, `localhost`), duplicates. Checked after construction, message `server_allowed_hosts '<v>' is invalid: <reason>`. Punycode/IDN names must be written in punycode; a `Host` with a trailing dot fails closed | Accept patterns/CIDR; accept duplicates |
| D4 | Guard | `_refusal` accepts `_host_name(Host)` in loopback ∪ allowed, compared exactly (case-sensitive, so an upper-case `Host` fails closed). Port ignored, as today. Same rule for the API and `/ui`. The guard's and `create_secured_app`'s docstrings ("loopback Host") are updated; the 403 detail text is reworded only after grepping tests and README (M4) | Per-route lists |
| D5 | API rules | Unchanged: Bearer token, any `Origin` refused, whatever the `Host` | Allow same-origin API use |
| D6 | `/ui` writes over plain HTTP | **Change the UI's `Referrer-Policy` from `no-referrer` to `same-origin`.** Reason (review C1; from W3C/MDN/OWASP reading, not run in a browser, so verified by the manual check, increment 12): browsers send `Sec-Fetch-*` only to HTTPS or loopback, so on `http://nas.lan` a form POST under `no-referrer` carries `Origin: null` with no Fetch Metadata and `_is_same_origin_request` refuses it. With `same-origin` a same-origin form POST carries its real `Origin: http://nas.lan:8765`, which the existing exact-Origin rule accepts; cross-origin navigations still get no referrer, so the login `?code=` does not leak to other origins. The `null` branch stays for loopback. An Origin naming a different allowed host is still refused. A missing `Sec-Fetch-Site` defaults to same-origin, so on remote hosts same-site/cross-site reads are not refused by Fetch Metadata; they rest on `SameSite=Strict` and the exact-Origin check on writes (Risks) | Read-only remote UI; accept `null` without Fetch Metadata (CSRF from opaque origins: refused) |
| D7 | TLS and the cookie | No TLS; no `Secure` flag (a `Secure` cookie is likely not stored over plain HTTP from a non-localhost host, unverified, so sign-in would break). Token, login code and cookie are readable by anyone on the path. The stderr warning and README say: use a VPN or SSH tunnel | TLS flags (a separate plan) |
| D8 | Sign-in link for a remote user | `factory ui --host NAME`, **checked on the client**: NAME must be loopback or in the CLI's `server_allowed_hosts`, else a usage error (exit 2, nothing posted, no traceback). The CLI posts as today, then swaps the host in the returned link for NAME, keeping the port from the link (a link host with no port gives `http://NAME`). A login code is not tied to a host (`LoginCodes.redeem` checks the code only), so no server change. The CLI's settings may differ from the running server's: the cost is a link that gets 403. A tunnel with a different external port needs the code pasted by hand (README) | Server-side `host` body field answering 422 (larger; `_ui` would also need to catch `ServerRefused`) |
| D9 | Base branch | `plan-72-reject-plan`, now holding plan 73 (user choice) | Separate branch |
| D10 | Bind address | Unchanged from plan 73 (`127.0.0.1` or `0.0.0.0`). Allowed hosts with a `127.0.0.1` bind are accepted, for a tunnel that preserves the `Host` | Require `0.0.0.0` |
| D11 | CLI client | Unchanged: connects to `127.0.0.1` | Derive from settings |
| D12 | Start-up warning | One stderr line chosen by state. List empty: the plan-73 line, byte-identical (so `test_cli_serve_host.py:48-53`, exact equality, stays green). List set: one line instead, naming the hosts and saying the connection is plain HTTP and to use a VPN or SSH tunnel; with a `0.0.0.0` bind it says the port is reachable from the network, with `127.0.0.1` it says only the listed names are accepted. The plan-73 line is never printed when the list is set (it would be false) | Two lines |

## Decided by the user
Base branch, plain HTTP with a warning, per-repo only, `Referrer-Policy: same-origin` (D6), client-side `--host` check (D8), and refusing `0.0.0.0`, loopback entries and duplicates (D3).

## Verified (2026-11-21, on disk, plan-72 branch, plus the reviewer's re-check)
- Guard host check `server.py:565`; `_LOOPBACK_HOSTS` `:473`; `_host_name` `:476`; `_is_same_origin_request` `:492-500` builds `own_origin` from `headers["host"]` and accepts `Origin: null` on a write only with `sec-fetch-site == "same-origin"`; a missing `Sec-Fetch-Site` defaults to same-origin; `_has_valid_session` `:539-542`; docstrings `:546`, `:579`.
- Login link `server.py:416` from `request.headers['host']`; route `:435`; `factory ui` posts via `ServerClient` to `http://127.0.0.1:{port}` (`cli/__init__.py:307`). `ServerClient.post` always sends `{}`. `_ui` is `cli/tasks.py:19-21`; `main()` re-raises `ServerRefused` except for 401 and unknown task (`cli/__init__.py:120-127`).
- `create_secured_app(ctx, token)` has `ctx.settings`; test fixtures build a real `Settings(...)`, so a defaulted field breaks none.
- Cookie: `session_cookie_name(host)` is `factory_session_<port>` (`web/auth.py:7`); set in `web/routes.py:210`, HttpOnly, SameSite=Strict, Path=/ui, no `Secure`.
- `_STRING_LIST_KEYS` `settings.py:77`; `_PER_REPO_KEYS` `:119`; `gate_allow_outside` refuses duplicates.
- Warning code `cli/admin.py:61-63`; `test_cli_serve_host.py:48-53` asserts stderr equals the plan-73 line exactly.
- **Unverified (browser behaviour, spec reading only):** no `Sec-Fetch-*` over plain HTTP to a non-loopback name; a same-origin form POST carrying its real `Origin` under `Referrer-Policy: same-origin`; `Secure` cookies refused over plain HTTP; the `/ui` assets (CORP) on non-secure origins. All checked in increment 12.

### Tests that touch what changes
Strings grepped (to re-run before increments 5, 9, 10): `no-referrer`, `Referrer-Policy`, `host must be loopback`, `loopback`, `403`, `Origin`, `warning: bound to`.
- `webhelpers.py:7` and `test_web_ui_guard.py:94` (the `referrer-policy` entry of the duplicated `UI_SECURITY_HEADERS` dict; `:95` is not it) pin `no-referrer`: **deliberately changed in increment 10**, in the same commit as the red test, and for no other reason. Also grep `webposts.py`, `webfixtures.py` and `test_settings_server_host.py`, not yet read.
- `test_web_post_origin.py:40-54` (`Origin: null` accepted only with `Sec-Fetch-Site: same-origin`): unchanged; the `null` branch stays.
- `test_server_security.py:72-74` (non-loopback `Host` incl. `0.0.0.0` gives 403) and `:82-84` (any `Origin` on the API gives 403): green with an empty list.
- `test_web_ui_guard.py:70-72,80,125`, `test_web_login.py:38`: unchanged, pinned to the default.
- `test_cli_serve_host.py:48-53`: unchanged (empty list, byte-identical).
- Apart from the `no-referrer` pin, no existing assertion is expected to change. If another must, stop and re-plan.

## Increments
One failing test, minimal change, one commit each. "Guard" = earlier code already satisfies it.

0. **Baseline.** Stay on `plan-72-reject-plan`; run the gate (2491 passed at the rebase); no commit.
1. **Setting (red).** `server_allowed_hosts` defaults to `()` and loads a list from the repo config. Field + `_STRING_LIST_KEYS`.
2. **Invalid entries (red).** Parametrized: `""`, `"*"`, `"a b"`, `"h:80"`, `"http://h"`, `"Example.com"`, `"::1"`, `"[::1]"`, `"nas.lan."`, `"0.0.0.0"`, `"127.0.0.1"`, `"localhost"`, and a duplicate raise `SettingsError` naming the key and entry. Post-construction check.
3. **Wrong type (guard).** A string, an int and `[]` give the existing "wrong type" error.
4. **Per-repo only (red).** Refused in the global file; add to `_PER_REPO_KEYS`.
5. **Guard accepts (red).** With the list set, `GET /tasks` with the token and `Host: nas.lan:8765` and `Host: nas.lan` is 200. `create_secured_app` passes `ctx.settings.server_allowed_hosts` to `_Guard`; `_refusal` uses loopback ∪ allowed; docstrings updated.
6. **Guard still refuses (guard).** With the list set: an unlisted host, `NAS.lan`, `nas.lan.`, `0.0.0.0` and `nas.lan.evil.example` are 403. With no list, `nas.lan` is 403.
7. **API Origin (guard).** `Origin: http://nas.lan:8765` on the API is 403.
8. **Warning (red).** List set: one stderr line naming the hosts and saying plain HTTP, per D12, for both binds; the plan-73 line is absent. Empty list: stderr unchanged (existing tests).
9. **UI reads on an allowed host (guard).** Sign-in on `Host: nas.lan:8765` sets `factory_session_8765`; a read with the cookie and no `Sec-Fetch-*` is 200.
10. **UI writes over plain HTTP (red).** The UI sends `Referrer-Policy: same-origin`: update the two pinned header dicts and add the red assertion in one commit. Then a guard commit: a POST with `Origin: http://nas.lan:8765` and no `Sec-Fetch-*` reaches routing; an Origin naming a different allowed host is 403; a remote `Origin: null` without Fetch Metadata is still 403.
11. **`factory ui --host NAME` (red).** An unknown NAME is a usage error (exit 2), nothing posted, no traceback; a loopback or listed NAME prints the link with its host swapped, port kept (`http://NAME` when the link has no port). Without the flag, output is unchanged.
12. **Manual check from a second machine** (throwaway, labelled; the user runs it, I give steps): sign in, watch the live list, reject a plan, confirm the cookie is set and `Sec-Fetch-*` is absent. Results recorded under Deviations.
13. **Docs.** README: the setting and its entry rules, the flag, the guard rule, the plain-HTTP risk, advice to prefer IPv4 literals (names are only as safe as their DNS; mDNS/`.lan` names can be spoofed on the LAN), the unsupported HTTPS-terminating proxy and different-port tunnels, the `Referrer-Policy` change, and the plan-73 sentence "still refused". A Deviations section here.
14. **Gate.** `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto`; run `verify`; run `audit-commits` against this plan.

## Out of scope
TLS and the `Secure` flag; HTTPS-terminating proxies and `X-Forwarded-*`; IPv6 hosts; wildcards/CIDR; specific LAN bind addresses; deriving the CLI client's address; per-host sessions; rate limiting of login codes; a server-side `--host` check.

## Risks
- Anyone on the network path can read the Bearer token, login code and session cookie, and the server can spend money and merge code. Mitigated only by default-off, per-repo scope, the warning and advice (D7).
- DNS rebinding defence now rests on the allowed list: a listed name an attacker can re-point passes the `Host` check; IPv4 literals are immune. Keep entries to names you control.
- Sessions are not tied to a host: a cookie stolen on one name works on every other name and on loopback. Accepted given D7.
- Remote reads lose the Fetch Metadata refusals (a missing `Sec-Fetch-Site` counts as same-origin). Any other service on the same host name (any port) is same-site and receives the `Path=/ui` cookie on its own `/ui` paths. Writes keep the exact-Origin rule.
- `Referrer-Policy: same-origin` still sends the full URL (with a login `?code=`) as a referrer to same-origin requests only; the code is single-use and short-lived (plan 69/72 behaviour, not re-verified here).
- A repo config can leave the list on; the warning shows at every start.
- Browser behaviours are unverified until increment 12.
