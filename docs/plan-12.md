# Plan 12: close the test-weakening and pytest-config gaps in the gate

Goal: the gate must not pass a task whose existing tests were edited or deleted by a fix round (gitignored files and new test files remain residual, below). Two known gaps from plans 9 and 10:
1. A fix round can rewrite or delete tests the implementer added, because the gate only compares against `base_sha`, where those tests are brand-new additions with zero deleted lines.
2. Pytest config in files the gate does not watch can change what runs.

## Spike (done, $0)
pytest 9.2.0 (the orchestrator's) reads, and applies, each of: `pytest.toml`, `.pytest.toml`, `.pytest.ini`, and a `[tool.pytest]` table in `pyproject.toml` (a `[pytest]` addopts `-k nothing` deselected the test in the ini and pyproject cases; both toml files were read and rejected a string addopts). The gate today watches `conftest.py pytest.ini tox.ini setup.cfg` and only `[tool.pytest.ini_options]`. Not verified: the target project's own pytest version (the gate runs the target's pytest via uv), so the watch list covers every name any recent pytest reads.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test. Never push.
- **Config watch list:** `DEFAULT_PROTECTED_NAMES` gains `pytest.toml`, `.pytest.toml`, `.pytest.ini`. The pyproject comparison covers the whole `tool.pytest` table (which includes `ini_options`) instead of only `tool.pytest.ini_options`.
- **Fix-round test freeze:** after a fix round, the gate compares test-glob files against the HEAD recorded before that round (not only `base_sha`). Any status other than Added (modified, deleted, renamed, type change) fails the task with the existing gate reason `tests_modified`. New test files remain allowed. Applies per round, so rounds 1 and 2 each freeze what came before.
- Mechanism: `check_gate` gains optional `frozen_sha`; when set, the same tamper check runs against `frozen_sha..HEAD` with a stricter rule (Added only). `verify_task` passes the pre-round HEAD on the gate run that follows a round, and None on the first gate run. A failure here is terminal for the task (no further fix round): a `tests_modified` is never retried, as today.
- No change to merge, retry, abandon, clean, the fix prompt, or the sandbox.

## Cycles
G1. **Config names.** A change to each of `pytest.toml`, `.pytest.toml`, `.pytest.ini` under the project fails with `tests_modified` (three red tests, one green change to the tuple).
G2. **pyproject table.** Adding or changing `[tool.pytest]` (not under `ini_options`) in pyproject fails with `tests_modified`; a change elsewhere in pyproject still passes; existing `ini_options` tests stay green.
F1. **`frozen_sha` in the gate.** With `frozen_sha` set: modifying an existing test file since it fails `tests_modified`; deleting one fails; renaming one fails; adding a new test file passes; with `frozen_sha=None` behaviour is unchanged (existing tests untouched).
F2. **Loop wiring.** In `verify_task`, a fix round that edits a test file the implementer added ends the task `failed` with reason `tests_modified`, with the round counted and no further round; a round that only changes source and adds a new test file proceeds to the gate result as before.
F3. **CLI wiring.** One end-to-end test through the fake claude: a fix round whose fake agent appends to the implementer's test file leaves the task failed with `tests_modified`.
E1. **Throwaway e2e (human, $0):** `spike/test_freeze_e2e.py` on a synthetic repo, real gate and real git, no agents: implementer commit adds `test_a.py`; a simulated fix round rewrites it; the gate rejects. Also each new config name and the `[tool.pytest]` table.

## Residual exposure
- The freeze compares test-glob files only; a fix round can still add a `conftest.py`-less helper module that tests import and neuter through (pre-existing: only watched names and test globs are protected).
- A fix round can add a new test file that shadows nothing but is skipped (e.g. `pytest.mark.skip`); new tests are unchecked by design.
- Pytest plugins selected by env var (`PYTEST_ADDOPTS`, `PYTEST_PLUGINS`) are outside the repo and set by the gate's filtered env, not by the agent.
- Config files in parent directories of the project subdir (above it, inside the worktree) are still unwatched.

## Out of scope
Re-planning on a moved main, branch GC, the "1 commits ahead" nit, new-test quality checks.

## Folded from the plan-reviewer (supersedes the text above where they differ)
- **Store the freeze reference on the task.** New `Task.fix_frozen_sha: str | None = None`, saved with the round counter and `fix_in_flight` before the agent runs (same `store.save`), set to the pre-round HEAD. Every gate run passes `frozen_sha=task.fix_frozen_sha`, so a resumed task (crash between round and gate) is still frozen. No "first run vs later run" tracking. Defaulted field, so old records load.
  - F2a (new red test, a new test not an edit of the existing counter test): the field is saved before the agent runs.
  - F2b: `_gate_once` passes it. Tests: the plan's F2; a resumed task (`fix_rounds_used=1`, `fix_frozen_sha` set, rewritten test in worktree) fails `tests_modified`; a round that deletes an implementer-added test file fails.
- **The gap has three cases**, all tested in F1/F2: rewriting an implementer-added test; deleting one (drops out of `base..HEAD`); removing lines the implementer appended to a pre-existing test file.
- **F1 rule is status-only** (`any(line[0] != "A")` over the test globs, `--no-renames`), a separate function, not a flag into `_tests_tampered`. Plus `_is_ancestor(frozen_sha)`, else `not_descendant` (test).
- **G1:** each new name is tested at `proj/` and `proj/sub/` (git `:(glob)` on dotfiles is unverified; these tests settle it).
- **G2:** a non-table `tool` or `tool.pytest` yields `unparseable` and `tests_modified`, not an exception (red test).
- **F3 dropped** (wiring lives in `verify_task`; CLI proof would need a fake-claude change for little gain).
- **E1** adds a resume case: simulate a round, commit, fresh gate call.
- **Check `retry`** builds a fresh Task and does not carry `fix_frozen_sha` (test if it copies).
- **Residual additions:** (a) gitignored files: the agent can write `.gitignore` plus `conftest.py`/`test_*.py`, which `git add -A` skips but pytest in the worktree loads; detection must exclude `.venv`; a clean-checkout gate is the real fix, candidate for plan 13. (b) A new test file can monkeypatch the code under test at import time, making existing tests pass untouched.
- Not verified: git `:(glob)` on dotfiles, `pytest_plugins` in test modules, the spike's pytest 9.2 claims beyond what I ran.
