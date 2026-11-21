# Plan 73: a configurable bind address for `factory serve`

Reviewed (2026-11-21); findings folded in. Branch `plan-73-bind-address`, cut from the main branch (no file overlap with plans 69-72 except `README.md`, so a trivial conflict is possible).

## Problem
`factory serve` always binds `127.0.0.1` (`cli/admin.py:59`). You want to bind `0.0.0.0`. This plan makes the address a per-repo setting and a flag, with `127.0.0.1` as the default, so the wide bind is an explicit choice. It does **not** make the server usable from another machine: the guard refuses any request whose `Host` is not loopback (`server.py:565`), for the API and for `/ui`. Remote use is a separate plan (an allowed-host setting, with its cookie, Origin and TLS questions).

## Decisions
| # | Question | Chosen | Alternative |
|---|---|---|---|
| D1 | Shape | Setting `server_host` plus `serve --host`; the flag wins, as `--port` does over `server_port` (`admin.py:52`) | Flag only; env var |
| D2 | Default | `127.0.0.1`: nothing changes unless asked | `0.0.0.0` |
| D3 | Accepted values | Exactly `127.0.0.1` or `0.0.0.0`, one constant `BIND_HOSTS` in `settings.py` used by both the settings check and `--host choices=`. A specific LAN address would break the CLI (it connects to `127.0.0.1`, `cli/__init__.py:306`, which that bind does not accept) and the guard would refuse the LAN `Host` anyway. `localhost` can resolve to `::1` only | Any IP literal or hostname; add `::1` and `::` |
| D4 | Warning | A bind other than `127.0.0.1` prints one line on stderr, after the state-dir lock is claimed (a refused start does not warn): `warning: bound to 0.0.0.0, so port N is reachable from the network; requests whose Host is not loopback are still refused (403)` | No warning |
| D5 | The stdout line | Unchanged, `serving on http://127.0.0.1:{port}; token in ...`, whatever the bind. A printed `http://0.0.0.0:N` would be a dead link: its `Host` is refused by the guard (and browsers may block 0.0.0.0, unverified). Scripts that read stdout stay stable (user choice: option a) | `(bound to 0.0.0.0)` note on stdout; print `0.0.0.0` |
| D6 | Where the setting may live | **Per-repo only**, in `_PER_REPO_KEYS` (`settings.py:115`), an error in the global file, as `gate_allow_dependency_changes` is ("a standing opt-out of a security check: never global"). A global `0.0.0.0` would open every project's server (user choice) | Global, like `server_port` |
| D7 | The CLI client | Unchanged: it keeps connecting to `127.0.0.1`, which `0.0.0.0` also accepts (kernel behaviour, expected, not run) | Derive from `server_host` |
| D8 | Guard | Unchanged. Existing tests already pin it: `test_server_security.py:72` (non-loopback `Host` incl. `0.0.0.0` gives 403 on `/tasks`) and `test_web_ui_guard.py:80` (on `/ui`) | Relax it (the later plan) |

## Verified (2026-11-21)
- `_serve` (`cli/admin.py:45-65`) prints the stdout line (`:57`) and calls `ctx.serve(app, "127.0.0.1", port)` (`:59`); `Context.serve` is `Callable[[object, str, int], None]` (`base.py:99`) and `_serve_with_uvicorn` passes `host` to `uvicorn.Config` (`base.py:84`).
- `serve` has only `--port` (`cli/__init__.py:79-80`). `_server_client` hardcodes `http://127.0.0.1:{server_port}` (`:306`).
- `server_port` is `settings.py:37`, in `_INT_KEYS` (`:76`); `_STRING_KEYS` is `:71` and accepts any string (only `main_branch` rejects empty), so a non-string already fails with "wrong type for setting: server_host". Restricted string values are checked after construction (`harness` and `sandbox`, `settings.py:100-103`, format `harness 'x' is unknown; known: ...`, pinned at `test_settings.py:791`).
- `_PER_REPO_KEYS` (`settings.py:115-118`) is refused in the global file (`:123`); `test_settings.py:743` (`test_gate_allow_dependency_changes_is_rejected_in_the_global_config`) is the model.
- The guard's `Host` check is the first line of `_Guard._refusal` (`server.py:565`), before the `/ui` branch; `_LOOPBACK_HOSTS` is `server.py:473`.
- The `/ui-login-codes` link is `f"http://{request.headers['host']}/ui/login?code=..."` (`server.py:416`) and `factory ui` goes through `_server_client`, so its `Host` is always `127.0.0.1:<port>`: the link does not change with the bind.
- `factory init`'s template (`init_config.py`) does not list `server_port`, so it needs no edit. `Settings` is built by keyword in tests, positionally only for its first three fields, so a new defaulted field is safe.
- README: `:115` ("listens on `127.0.0.1` only"), `:186` (the per-repo key list), `:196` (`server_port`).
- **Unverified:** a `0.0.0.0` socket accepting connections to `127.0.0.1` (kernel behaviour; no test, since it would bind a real wide port on dev and CI machines); Chrome blocking `0.0.0.0` navigation; the wording of argparse's "invalid choice" message on Python 3.14 (tests do not depend on it); that nothing outside the files read calls `asdict(settings)`.

### Tests that touch what changes
Strings grepped: `serving on http://127.0.0.1`, `listens on`, `host must be loopback`, `wrong type for setting`.
- `test_cli_serve.py:35` (host `127.0.0.1`) and `:37` (exact stdout) pin default behaviour and the unchanged stdout line: stay green. `:110` and `test_serve_stopping.py:24`, `test_cli_wiring.py:91` use `startswith("serving on ")`. The `ctx.serve` fake is `(app, host, port)` at `:20`: unchanged. `:115` (`server_port` overridden by `--port`) is the model for the `--host` test.
- `test_settings.py:154` (`server_port`), `:743` and `:791` are models; not edited.
- `test_server_security.py:72,77`, `test_web_ui_guard.py:80,125`: the guard, untouched.
- No existing assertion is expected to change. If one must, stop and re-plan.

## Increments
One failing test (or one parametrized behaviour), the minimal change, one commit each. "Guard" means earlier code already satisfies it, and the test is committed with that label.

0. **Branch and baseline.** Cut the branch; run the gate; no commit.
1. **Setting (red).** `load_settings` gives `server_host == "127.0.0.1"` by default and `"0.0.0.0"` from the repo config. Change: field `server_host: str = "127.0.0.1"` beside `server_port`, the key in `_STRING_KEYS`.
2. **Refused values (red).** `"localhost"`, `"192.168.1.5"`, `""` and `"::"` raise `SettingsError` naming `server_host` and the allowed values. Change: the `BIND_HOSTS` constant and a check after construction, as for `harness` and `sandbox`.
3. **Wrong type (guard).** `server_host = 3` raises "wrong type for setting: server_host" (increment 1's `_STRING_KEYS` entry covers it).
4. **Per-repo only (red).** `server_host` in the global config is refused, as at `test_settings.py:743`. Change: add it to `_PER_REPO_KEYS` with a comment.
5. **Serve uses the setting (red).** With `server_host="0.0.0.0"`, `ctx.serve` gets host `0.0.0.0` and stdout is still exactly `serving on http://127.0.0.1:8765; token in ...`. Change: `_serve` resolves the host.
6. **Flag (red).** `--host` overrides the setting both ways (`0.0.0.0` over `127.0.0.1` and the reverse; parametrized). Change: `serve.add_argument("--host", choices=BIND_HOSTS)`.
7. **Flag refusal (guard).** `--host example.com` exits 2 with a usage error, nothing is served; assert the exit code, `ctx.served == []`, and the substrings `--host`, `127.0.0.1` and `0.0.0.0`, not the whole argparse line.
8. **Warning (red).** A `0.0.0.0` bind prints the D4 line on stderr, printed after the lock is claimed; stdout holds only the `serving on` line. Change: `_serve` prints it.
9. **Default is quiet (guard).** A default bind writes nothing to stderr; a refused start (`StartRefused`) with `0.0.0.0` prints no bind warning.
10. **CLI client unchanged (guard).** `_server_client(settings)` with `server_host="0.0.0.0"` uses base URL `http://127.0.0.1:<port>` (called directly, not through `build_context`).
11. **Docs.** README: `serve [--port N] [--host H]`; `:115` ("loopback by default"); `:186` add `server_host` to the per-repo list; `:196` the example line; a note that a wide bind does not enable remote use and why. A deviations section here.
12. **Gate.** `cd orchestrator && uv run ruff check`, then `uv run pytest -q -n auto`; run the `verify` skill; run `audit-commits` against this plan.

## Deviations
- Increments 3, 7, 9 and 10 passed on first run, as planned guards; each is committed as `Guard: ...`. Increments 1, 2, 4, 5, 6 and 8 were seen red for the expected reason first (missing attribute, DID NOT RAISE, host not passed, unknown flag, no warning on stderr).
- Increment 9 is one commit holding two guards (the loopback bind is quiet; a refused start with `0.0.0.0` does not warn).
- Increment 2's message reads `server_host '<value>' is unknown; known: 127.0.0.1, 0.0.0.0`, the format of the `harness` and `sandbox` checks.
- Increment 8: the warning is printed after the `serving on` line, still inside the lock and before `ctx.serve`. The test asserts it is on stderr before `ctx.serve` is called; "after the lock" is covered by increment 9.
- Increment 10 reads `ServerClient._http.base_url`, a private attribute, as there is no public accessor.
- README line numbers in this plan were from another branch. On this one: the command table, the Server paragraph (`:114`), the per-repo prose list (`:168`) and the `server_port` example line (`:178`). A paragraph on the wide bind was added to the Server section; there is no Web UI section here.
- Audit follow-up: this branch has no `/ui` (plans 69-72 are not merged here), so D8's `test_web_ui_guard.py:80` reference and the plan's `server.py:565` line number (it is `:476` here) do not apply on this branch, and the README no longer says the `Host` rule covers `/ui`. The rule covers `/ui` once the web UI branches merge.
- New tests live in `tests/test_settings_server_host.py` and `tests/test_cli_serve_host.py`; no existing test was edited.

## Out of scope
Remote access (an allowed-host setting, relaxing the guard, TLS, the cookie's `Secure` flag, the writes' Origin rule behind a non-loopback host); IPv6; hostnames and specific interface addresses; an env var; changing what `factory ui` prints; any change to the guard; a real-socket test of the `0.0.0.0` bind.

## Risks
- A `0.0.0.0` bind exposes the port to the network. The Bearer token and the `Host` rule still apply, but the guard was written to keep browsers out, not as a network defence, and the server can spend money and merge code. The default stays loopback, the setting is per-repo, and the stderr line says so (D4, D6).
- A repo's config can leave `0.0.0.0` on. Accepted; the warning appears at every start.
- Someone may expect `0.0.0.0` to "just work" remotely. The README and the warning say it does not.
- The `127.0.0.1` client against a `0.0.0.0` bind rests on kernel behaviour and is not tested here.
