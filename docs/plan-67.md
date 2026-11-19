# Plan 67: one owner for the eval meta's defaults

Draft, reviewed; findings folded (2026-11-19). Not approved: the option choice below is the user's. Follows plan 66. Source: architecture review candidate 4, re-checked against the code.

## Problem
The rule "a meta without `harness` means `claude`" is written twice, and read a third time with no default:
- `audit_eval.py:180`: `load_meta(...).get("harness", DEFAULT_HARNESS)` (resume check in `run_eval`).
- `audit_share.py:41`: `shared["harness"] = meta.get("harness", DEFAULT_HARNESS)` (`shareable_meta`).
- `audit_compare.py:76`: `old_meta.get("harness") != new_meta.get("harness")`, no default. Correct today only because both loaders (`audit_compare.py:50,65`) pass the meta through `shareable_meta` first (verified; `_bundle_run` does not bypass it). A future loader that skipped it would print a false "harness differs".

A new meta field touches three files: `audit_eval.py` (`_meta`), `audit_share.py` (`KEPT`, `shareable_meta`, the report line at :102-103) and `audit_compare.py`. Option B would also touch `server.py`, possibly the CLI, and the README.

## Already verified
- Old-meta-reads-as-claude is pinned by `test_audit_eval.py:391`, `test_audit_share.py:54` and `test_audit_compare.py:108`. No new guard is needed.
- `server.py:377-378` passes `ctx.settings.harness` to `run_eval`; it never reads a harness from a meta.
- `audit_share` already imports from `audit_eval`, so a helper in `audit_eval` adds no import cycle.
- `Settings.sandbox: str = DEFAULT_SANDBOX` (`settings.py:30`).

## Decided (user, 2026-11-19)
Option A2. Implementation not yet approved.

## Options considered
- A: `meta_harness(meta: dict) -> str` next to `load_meta` in `audit_eval.py`, used at all three sites. Pure refactor; benefit is small, since a new field still needs its own helper.
- A2 (recommended): as A, but generic: `META_DEFAULTS = {"harness": DEFAULT_HARNESS}` and `with_defaults(meta)`, used by `run_eval`, `shareable_meta` and compare. A new defaulted field is then one dict entry, and B's missing-means-`nono` rule is one more.
- B: A2 plus recording the sandbox name (deferred in plan 63). Resume refuses a different sandbox, compare prints "sandbox differs", the report line names it. About 7 to 8 increments (see Option B).
- C: skip. Two one-liners and one fragile read; defensible.

## Increments (A2; one commit each; A is the same with `meta_harness` in place of `with_defaults`)
0. Inventory, no commit: grep `harness`, `DEFAULT_HARNESS`, `"harness"` across `src/` (including `cli/*`, `audit_report.py`), `tests/`, README. Confirm there is no further reader than the three above (the reviewer could only read the audit and server files).
1. Red: `with_defaults({})["harness"] == "claude"`; a meta that has `harness` keeps it; the input is not mutated. Green: add it next to `load_meta` in `audit_eval.py`.
2. Guard (refactor, no assertion changes): `run_eval` and `shareable_meta` use it. Remove the now-unused `DEFAULT_HARNESS` import from `audit_share.py` in the same commit (ruff F401); `audit_eval.py` keeps it for the `run_eval` default parameter. Run ruff, the three pinning tests, `test_server_eval.py`, `test_cli_eval_export.py`.
3. Guard (refactor): `audit_compare.py:76` compares through it, so compare no longer depends on pre-normalised input. Existing `test_audit_compare.py:102-108` covers it; add one test where a raw meta without `harness` is compared to a `claude` one (no "harness differs"), written first so it is red against the bare `.get`.
4. Gate, not a commit: `uv run ruff check`, full `uv run pytest -q -n auto` in the background, `verify`, then `audit-commits`. Record deviations in this plan.

## Option B (only if chosen; replan before starting)
- Pieces: `run_eval` parameter and `_meta` field; resume refusal plus an old-meta-resumes guard; `shareable_meta`/`KEPT`; compare's "sandbox differs"; the markdown line; server wiring (model: `test_eval_audit_records_the_configured_harness_in_the_meta`); CLI wiring if `eval-audit` calls `run_eval` separately (unverified); README.
- The recorded name must come from the same place that builds the runner (unverified: whether `ctx.eval_runner` uses `settings.sandbox`), or the meta can lie. Assert it in a test.
- Assumption to state: old evals read as `nono` only if every earlier eval ran under nono. Check against plans 63 and 66 before relying on it.
- The server's `ConfigChanged` message ("auditor config or its plugin files differ") would be slightly misleading for a sandbox mismatch, as it already is for a harness mismatch.

## Deviations (recorded after implementation)
- Increment 0 was done inline, no commit: exactly three readers of the meta's `harness` in `src/`, as planned.
- Increment 3's red test patches `factory.audit_compare.load_run` with raw-meta `Run` objects. Both real loaders normalise through `shareable_meta`, so that is the only way to hand `compare` a raw meta. It was red before the change (false "harness differs").
- Commits: `80691e6`, `525d5e6`, `206e7c4`. Full suite 2144 passed; ruff clean. `audit-commits`: no drift.

## Out of scope
Remaining candidate 2 items: the `$HOME` private-set difference (`runner.py:59` vs `gate_sandbox.py:49`) and the duplicated `sandbox_named(...)` at `stages.py:76` and `cli/base.py:63`.

Closed (user, 2026-11-19): not worth doing. The `$HOME` difference matters only when `$HOME` is unset, which is unreachable; the duplicated call is one line in two places. Do not re-suggest them.

## Risks
- A and A2 change no behaviour. Option B changes meta content; older evals must still resume and compare.
