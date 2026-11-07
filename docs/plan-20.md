# Plan 20: the gate watches uv config inside the project

Goal: close the "files inside the project that influence a run are unwatched" gap, using only what a spike showed matters.

## Spike (uv 0.13.7, `uv run --offline pytest -q` in a temp project)
- Inert, so not watched: `sitecustomize.py`, `usercustomize.py`, `.env`, a `*.pth` in the project (probe saw no effect; `sitecustomize`/`usercustomize` not in `sys.modules`).
- Change the run, so watched: `.python-version` (interpreter lookup changed), `uv.toml` (`exclude-newer`, `python-preference`, `cache-dir` all took effect), `[tool.uv]` in `pyproject.toml` (`exclude-newer` took effect).
- Not tested: whether `UV_CACHE_DIR` in the gate env overrides a `cache-dir` in `uv.toml` (unverified; the gate sandbox limits writes either way).

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test except via an approved edit list. Never push.
- Scope chosen by the user: uv config only. Dependencies and `uv.lock` stay editable (a malicious dependency is a known residual).
- Only `gate.py` changes (plus tests, README known-limits, spike-findings).

## Behaviour
1. `uv.toml` and `.python-version` join `DEFAULT_PROTECTED_NAMES`. They are matched repo-wide (`:(glob)**/name`) like the pytest names, so any add, edit or delete fails the gate. (A root copy was already refused as `outside_project`; this refuses it inside the project too, and fixes the reason order.)
2. The `[tool.uv]` table of each `pyproject.toml` from the repo root down to the project is compared base vs HEAD, exactly like `[tool.pytest]` today. Any difference fails.
3. Reason stays `tests_modified` (stable, tested, no new retry semantics; the name is now loose, noted in the README).
4. Still checked before the sandboxed test run and not retried by a fix round.
5. `protected_names` is not configurable in settings, so no config migration.

## Cycles
- U1 (red): a project change plus a new `target/uv.toml` gives `tests_modified`. Add `uv.toml` to the defaults.
- U2: same for `.python-version`, plus delete and edit of each, and a nested `target/sub/uv.toml`.
- U3 (red): a project change adding `[tool.uv]` to `target/pyproject.toml` fails; so does editing or removing it. Generalise `_pytest_table` into a table comparison over `("pytest", "uv")`.
- U4 (guards, start green): adding a dependency under `[project]` still passes; changing `uv.lock` still passes; `[tool.ruff]` change still passes; the pytest table checks still fail as before.
- U5 (wiring): through `verify_task`, a `uv.toml` change fails with `tests_modified`, sentinel `test_argv` not run, no fix round spent.
- U6: README (tamper bullet, known limits: inert files measured, dependencies still editable), `docs/spike-findings.md` section with the spike table, full suite, `verify`. No e2e: pure git logic.

## Residual
- A new dependency can run arbitrary code at install/import. Not watched, by choice.
- Import-time monkeypatching by a new test file remains (separate gap).
- Other uv inputs not tested (`UV_*` env is set by the factory, not the project).

## Folded from the plan-reviewer (supersedes the text above where they differ)
Approved by the user: fold all, add the prompt sentence, and the edit list below.

**Spike 2 (uv 0.13.7, case-insensitive APFS):** `UV.toml`, `.Python-Version` and `.python-versions` (plural) are all honoured by uv. `uv python find --no-config` ignores `uv.toml`, `[tool.uv]` and `.python-version`. Not tested: `requires-python`, mixed-case `PyProject.toml`.

**Changes to the behaviour**
1. Protected names add `uv.toml`, `.python-version`, `.python-versions`. All protected names (old and new) match with `:(glob,icase)**/name`, since git globs are case-sensitive and the volume is not. This is stricter for the plan-16 names too.
2. `[tool.uv]` is compared with `[tool.pytest]`: parse each pyproject once and compare the `(pytest, uv)` tuple (non-dict value marked unparseable, as today).
3. The unconfined discovery in `gate_sandbox_for` / `discover_uv_tooling` (`stages.py`) runs `uv python find` and `uv python dir` with the agent's config before the gate refuses it. Add `--no-config` to those calls (option b). In scope: `stages.py` or wherever discovery lives, not only `gate.py`.
4. The implementer prompt and `fix_prompt` gain one sentence: do not create or edit `uv.toml`, `.python-version`, `.python-versions` or `[tool.uv]`; such changes are refused.

**Edit list (approved)**: in `test_guard_any_path_outside_the_project_fails_outside_project`, remove the `uv.toml` and `.python-version` params (they now give `tests_modified`). Lands in the same green commit as U1/U2 respectively, with a new test that a root `uv.toml` / `.python-version` gives `tests_modified`.

**Cycles (replaces the list above; names in fixture terms, `proj`)**
- U1 (red): a project change plus new `proj/uv.toml` gives `tests_modified`. Add `uv.toml`; apply the edit-list param removal and the root test in the same commit.
- U2 (red): same for `proj/.python-version`, with its param removal and root test.
- U2b (red): `proj/.python-versions`.
- U2c (red): `proj/UV.toml` gives `tests_modified`; switch every protected pathspec to `:(glob,icase)`.
- U2g (guards, start green, labelled): edit and delete of each file; `proj/sub/uv.toml`; `a/uv.toml` for project `a/b`.
- U3 (red): base `proj/pyproject.toml` without `[tool.uv]`, HEAD adds `[tool.uv]` gives `tests_modified`; single-parse tuple comparison.
- U3g (guards, labelled): edit or remove `[tool.uv]` fails; a root pyproject `[tool.uv]` gives `tests_modified`; base already containing `[tool.uv]`, changing only `[project].dependencies`, `[tool.ruff]` or `uv.lock` still passes.
- U4 (wiring, `test_verify_fix_loop` fixtures, `fix_rounds=1`): `proj/uv.toml` beside the code change gives `tests_modified`, sentinel `test_argv` not run, runner not called, `fix_rounds_used == 0`.
- U5 (red): discovery argv contains `--no-config` for both calls.
- U6 (red): both prompts contain the new sentence.
- U7: README (tamper bullet; `tests_modified` is now a loose name; `[tool.uv]` freezes `sources`/`override-dependencies` while `[project.dependencies]`, `[dependency-groups]` and `uv.lock` stay editable; known limits), spike-findings section, full suite, `verify`. No e2e needed for U1-U4/U6; U5 changes a real `uv` call, so a short throwaway check that `discover_uv_tooling` still returns the right paths with `--no-config` is run by me (no TTY or network needed).

## Residual (added)
- `[project.entry-points.pytest11]` can register a pytest plugin from the project's own package; not watched. Same family as import-time monkeypatching.
- `requires-python` and mixed-case `PyProject.toml` are unverified.
- Files above the worktree root are on uv's upward search path; safe only while no sandbox lets the agent write there (confirmed for the gate sandbox, not the implementer).
