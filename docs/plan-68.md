# Plan 68: record the sandbox in the eval meta

Reviewed; findings folded (2026-11-19). Follows plan 67 (option B there). Deferred from plan 63.

## Problem
An eval's meta records the harness but not the sandbox. A run under another sandbox (once one exists) would resume into, and compare against, an eval made under nono with no warning, though the sandbox can change what the auditor can read. Plan 67 left this open because several facts were unverified; they are settled below.

## Verified (2026-11-19)
- `run_eval` has one real caller found so far, `server.py:377-378` (`ctx.settings.harness` is the last positional argument). `cli eval-audit` only posts to the server. Increment 0 greps the rest (other `cli/*`, `spike/`, `docs/`).
- The recorded name matches what runs, by code reading: the real `ctx.eval_runner` is `_real_eval_runner` (`cli/base.py:51`), which calls `agent_runner(settings, ...)`, which uses `sandbox_named(settings.sandbox, ...)` (`cli/base.py:63`); the server passes the same `ctx.settings`. Increment 7b pins it with a test. `eval_runner` is injectable in tests, so the meta records the setting, not a fake's behaviour (same as harness today).
- Old evals read as `nono`: the `sandbox` setting and registry were added on 2026-11-19 (`437e54a`, `7ba2292`); before that nono was hard-coded.
- `with_defaults`/`META_DEFAULTS` (plan 67) is the one place a missing field's meaning is written; `run_eval`, `shareable_meta` and `compare` read through it.
- No schema change: `SCHEMA = 1`, `_bundle_run` checks only that, and `shareable_meta` copies named keys. Old bundles read as `nono`; a newer bundle read by an older build drops the unknown key.
- `sandbox.py` imports only the standard library, so `audit_eval` importing `DEFAULT_SANDBOX` makes no cycle.
- The server tests' fake runner never calls `sandbox_named`.

## Design
- `META_DEFAULTS` gains `"sandbox": DEFAULT_SANDBOX` (from `factory.sandbox`).
- `run_eval(..., harness=DEFAULT_HARNESS, sandbox=DEFAULT_SANDBOX)`; `_meta` writes `"sandbox"` (both `_meta` calls); the server passes `sandbox=ctx.settings.sandbox`.
- A resume under a different sandbox raises `ConfigChanged`, checked after the harness check and before the meta is written, so a refused resume leaves the meta untouched.
- `shareable_meta` keeps `sandbox` through `with_defaults`; compare prints `  sandbox differs` through `with_defaults`; the markdown header line gains `, sandbox <name>` after the harness, read through `with_defaults` (a bare `meta['sandbox']` would raise on a hand-built bundle).
- Only the name is recorded, not the sandbox's version, profile, grants or `sandbox_executable`. "Same name" is not "same confinement"; the README says so.

## Approval needed and given (2026-11-19)
Increment 1 cannot go green without editing one passing assertion: `test_with_defaults_fills_a_missing_harness_and_keeps_a_given_one_without_mutating` (`test_audit_eval.py`, added in plan 67) asserts `with_defaults(bare) == {"runs": 1, "harness": "claude"}`. Approved: change it to `== {"runs": 1, "harness": "claude", "sandbox": "nono"}`. Nothing else in that test changes.

## Increments (one commit each; red = failing test first; guard = passes before the green)
Reference tests by name; line numbers drift.

0. Inventory, no commit: grep `sandbox`, `with_defaults`, `META_DEFAULTS`, `"harness"` and `run_eval(` across `src/`, `tests/`, `spike/`, `docs/`, README. Check for tests that compare a full meta or bundle to a literal dict, tests that read the README, an existing test pinning `agent_runner`'s use of `settings.sandbox` (then skip 7b), and that `dataclasses.replace(settings, sandbox="other")` is not validated.
1. Red: `with_defaults({})["sandbox"] == "nono"`. Green: add the `META_DEFAULTS` entry, plus the approved assertion edit above, in the same commit. Guard (same commit): a given sandbox value is kept.
2. Red: `run_eval(..., sandbox="other")` writes `meta["sandbox"] == "other"`, and the default writes `"nono"`. Green: keyword parameter after `harness`, and the `_meta` field in both calls.
3. Red: resuming with a different sandbox raises `ConfigChanged` before anything runs and leaves the meta unchanged (mirror `test_resuming_with_a_different_harness_is_refused...`). Green: the check after the harness check, reading `with_defaults(load_meta(eval_dir))["sandbox"]`. Guard (same commit): a meta with `sandbox` deleted resumes under the default (mirror `test_a_meta_without_a_harness_counts_as_claude`); it would fail with a `KeyError` if the check read the key bare.
4. Red: `shareable_meta` of a raw meta has `sandbox == "nono"`. Green: `shared["sandbox"] = with_defaults(meta)["sandbox"]`. Guard (same commit): a given `"other"` is kept (mirror `test_the_shareable_meta_names_the_harness...`).
5. After 4, because the loaders normalise through `shareable_meta`. Test helper: `make_run` in `test_audit_compare.py` gains a `sandbox=None` keyword (as `harness` did; a helper edit, no assertion changes). Red: two evals with different sandboxes print `sandbox differs`. Green: one more comparison in `compare`, through `with_defaults`. Guards (same commit): a missing sandbox equals `nono`; and a raw meta via the patched `load_run` (mirror plan 67's compare test) is pinned by the `with_defaults` call, so write the naive `.get` first to see it red if you want it as a red.
6. Red: `"sandbox nono" in render_markdown(bundle_of(tmp_path))`. Green: extend the header at `audit_share.py` (the f-string ending `harness {meta['harness']}`) to `harness …, sandbox {with_defaults(meta)['sandbox']}`.
7. Red: `test_eval_audit_records_the_configured_sandbox_in_the_meta`, next to the harness test in `test_server_eval.py`: `ctx.settings = replace(ctx.settings, sandbox="other")` leads to `meta["sandbox"] == "other"`. Green: pass `sandbox=ctx.settings.sandbox` at `server.py:377`.
   - 7b. Guard: `_real_eval_runner` builds its sandbox from `settings.sandbox` (monkeypatch a recording fake into `SANDBOXES`, call it with `replace(settings, sandbox="fake")`). Skipped if increment 0 finds one. Passes before any change; labelled a guard.
   - 7c. Optional, its own red/green: the server's `ConfigChanged` job message reads "auditor config, plugin files, harness or sandbox differ". Existing server tests assert only that `config` or `plugin` and the eval name appear in the message (reviewer's reading; re-check in increment 0).
8. Docs, by line (verify before editing): README 217 sentence "The sandbox is not recorded in eval meta or bundles." is replaced; the line 215 harness paragraph gets the equivalent sentence (the meta records the sandbox name, `--resume` refuses a different one, `--compare` prints `sandbox differs`, a missing one reads as `nono`; only the name, not version, profile or executable); README 108 ("In the bundle: …") names harness and sandbox. Record deviations in this plan.
9. Gate, no commit: `cd orchestrator && uv run ruff check`, then `uv run pytest -q -n auto` in the background, `verify`, then `audit-commits`.

## Tests touched (CLAUDE.md step 2)
User-visible strings: the markdown header line (a suffix) and the compare output (one new line, only on a difference). Pinning tests: `test_audit_share.py` markdown test (substring `harness claude`, unaffected), `test_audit_compare.py` harness-differs test (asserts on `harness differs` only, unaffected), `test_cli_eval_export.py` (startswith and substring checks, unaffected). One assertion edit, approved above. Increment 7c, if done, touches the job message; the server tests check only fragments.

## Risks
- Older shared bundles have no `sandbox`; they read as `nono`, so old exports and compares still match new nono evals.
- One adapter exists, so `sandbox differs` is exercised only with a made-up name (`"other"`), as the harness tests do.
- Known limit: compare treats a new directory with no `meta.json` as `nono`, so it could print a false "sandbox differs" against an old non-nono eval (the same holds for harness). Unreachable with one sandbox.
