# Plan 53: CI and lint

## Problem

There is no CI and no lint tooling. The dev group has only `pytest` (`orchestrator/pyproject.toml`). About 25k lines of
Python and ~1000 tests (~100s) are checked only when run by hand. The package, `uv.lock` and `.python-version` live under
`orchestrator/`; the repo root has no `pyproject.toml`.

## Decided (user, this session)

- ruff only, no type checker.
- Rules `E`, `F`, `I`, with E501 ignored (recommendation accepted: `ignore = ["E501"]`).
- `ubuntu-latest` only.
- CI does not run the `target/` suite for now.
- The `ps` reaping test (`test_server_startup.py:98`, skipped under this sandbox) is accepted as first proven in CI.

## Reviewer findings folded in

- "E, F, I pass today" was an unverified premise and is false for `I` (for example `test_runner.py:13-14`) and for E501.
  Cycle 1 measures before anything is committed.
- `AGENTS.md` has no verification section, so the `verify` skill cannot find a gate. Cycle 8 creates one.
- The workflow needs `working-directory: orchestrator`, `uv sync --locked`, and `uv.lock` committed with the ruff addition.
- Skipping a failing test is only for platform-caused failures. A real defect gets a red test and a fix.
- No `--unsafe-fixes`. The full suite runs after every fix commit.

## Cycles

Tooling and config cycles are not red/green. Each is verified by the command named plus a green full suite, then committed.
The full suite takes ~100s: run it in the background and wait with Monitor.

1. **Measure (throwaway, no commit).** From `orchestrator/`: `uvx ruff check --select E,F,I --statistics src tests`, and
   again with `--ignore E501`. Record counts and files. Look for any audit-eval corpus `.py` tree to exclude. If the counts
   under the accepted scope are large, stop and raise it with the user.
2. **CI-parity spike (throwaway, no commit).** Fresh clone in the scratchpad; run the suite with a minimal environment
   (`env -i`, empty `HOME`, `GIT_CONFIG_GLOBAL=/dev/null`, PATH with only `uv`, `/usr/bin`, `/bin`). Record failures and
   skips. The `ps` test still skips here and is listed as first proven in CI.
3. **One cycle per spike failure.** Platform-only: skipif with a reason, like the `ps` one. Real defect: red test, fix.
   One commit each. Skipped if the spike finds nothing.
4. **Add ruff.** `uv add --dev ruff`; `[tool.ruff]` with `src = ["src", "tests"]`, `lint.select = ["E", "F", "I"]`,
   `lint.ignore = ["E501"]`, and `extend-exclude` for any corpus found in cycle 1. Commit `pyproject.toml` and `uv.lock`
   together. Add `.ruff_cache/` to `.gitignore`.
5. **Fix `I`.** `uv run ruff check --select I --fix`, suite green, commit.
6. **Fix `F`.** Safe or manual fixes only. If renaming a duplicate test (F811) revives a test that fails, stop and raise it.
   Suite green, commit.
7. **Fix `E`.** Within the accepted scope. Confirm `uv run ruff check` exits 0 and the suite is green. Commit.
8. **AGENTS.md.** Add `## Verification gate`: `cd orchestrator && uv run ruff check`, then `cd orchestrator && uv run pytest -q`
   (background, per the existing note). Run the `verify` skill to confirm it reads the section. Commit.
9. **Workflow** `.github/workflows/ci.yml`: triggers `push` and `pull_request`; `permissions: contents: read`;
   `runs-on: ubuntu-latest`; `timeout-minutes: 15`; `defaults.run.working-directory: orchestrator`; steps `actions/checkout`,
   `astral-sh/setup-uv` (`enable-cache: true`), `uv sync --locked`, `uv run ruff check --output-format=github`,
   `uv run pytest -q`. Action versions are pinned to versions verified at implementation time, not assumed. Not provable
   locally; the user pushes and reports the first run. Commit.
10. **README.** `docs/plan-1.md … docs/plan-43.md` becomes `docs/plan-N.md` (`README.md:7`); reword `README.md:22` and `:336`
    (CI runs the fake-based suite on Linux; the real sandbox is macOS-only); add the lint command beside `uv run pytest -q`
    (`README.md:319`). Before editing, grep the tests for any README strings. Commit.

## Risks and unverified assumptions

- Not verified: how `test_stages.py`, `test_settings.py`, `test_gate.py` (past line 120) use nono; whether Settings
  resolves `nono` or `claude` on PATH at load. Cycle 2 settles this.
- Not verified: Linux `ps lstart` parsing in `process_registry.py:214-231`; the ~100s runtime on a runner; whether gate tests
  need network for a cold `uv` cache.
- Timing assertions (`< 3` s at `test_runner.py:96`, 3 s polling deadlines) may flake on slow runners. Watch the first runs.
- The nonce-collision hang in `AGENTS.md` is bounded by the job timeout.
- Autofixes can alter behaviour (F401 dropping a side-effect import, F811 reviving a shadowed test); mitigated by the
  full-suite run after each commit.
