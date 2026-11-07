# Plan 16: the gate watches protected config anywhere in the repo, not only under the project subdir

Goal: close the known gap "tracked config above the project subdir is unwatched". The agent can write to the whole worktree (`--add-dir <worktree>`). pytest run from `target/` takes its ini file from the nearest ancestor that has one, so a committed root `pytest.ini` or root `pyproject.toml` pytest table can change what runs (`addopts = --collect-only` exits 0 without running a test), and once a root ini exists a root `conftest.py` is loaded as well.

## Spike (done, $0)
Throwaway repo in the scratchpad, pytest run from `target/` (uv, same as the gate):
- root `conftest.py` alone: not loaded (rootdir stays `target/`).
- root `pytest.ini` with `addopts = -k nomatch`: applied. Root `pyproject.toml` pytest table: applied.
- root `pytest.ini` plus root `conftest.py`: the root conftest ran.
- a `target/pytest.ini` present: the root config is ignored (nearest ini wins).
- `-c /dev/null --rootdir=. --confcutdir=.` neutralises root config, but also ignores a legitimate `target/pytest.ini`, and `test_argv` need not be pytest: rejected.
Not verified: other root-level files that affect a run (`sitecustomize.py`, `uv.toml`, `.python-version`): out of scope, see below.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test (except an approved edit list). Never push.
- Option 1 chosen: widen the existing protected check; no new refusal reason (`tests_modified`, as today), no new setting, no new state.
- Only `gate.py` changes (`_protected_changed`).

## Behaviour
1. **Protected names anywhere:** the diff `base_sha..HEAD` is checked with pathspec `:(glob)**/<name>` for each protected name, over the whole worktree (it still matches under the project subdir, and now also the root and sibling directories). A change (add, modify, delete) to any of them gives `tests_modified`.
2. **pyproject pytest table on the path:** for every directory from the worktree root down to the project subdir (inclusive), compare the `tool.pytest` table of that directory's `pyproject.toml` at `base_sha` and `HEAD`. Any difference gives `tests_modified`. Reuses `_pytest_table` (unparseable, absent and present-without-table keep their current meaning). A pyproject elsewhere in the repo (not an ancestor of the project) cannot affect the run and is not compared.
3. **Not changed:** test-file checks stay under the subdir; changes to other root files (README, etc.) stay allowed; `protected_names` override still replaces the default names and applies repo-wide.

## Cycles (each: failing test shown red, minimal change, commit)
R1. A new root `pytest.ini` (and each other default protected name, parametrized) fails `tests_modified`; the existing under-subdir tests stay green.
R2. A modified and a deleted root protected file fails `tests_modified`.
R3. A root `pyproject.toml` whose pytest table is added or changed fails `tests_modified`; one that changes only `[project]` does not.
R4. For a nested subdir (`a/b`), a pytest table change in `a/pyproject.toml` fails; a change in a sibling `c/pyproject.toml` does not.
R5. A root `README.md` change alone still passes (guard: the widening is not a blanket refusal).
R6. The `protected_names` override applies repo-wide (a root file of the custom name fails; a root `pytest.ini` no longer does).
R7. Wiring (existing fakes through `verify_task`): a task that commits a root `pytest.ini` fails `tests_modified` without running the tests.
E1. Throwaway e2e is not needed: pure git diff logic, covered by real-git tests; no TTY, nono, network or home-dir involvement. Final `verify` and a note in `docs/spike-findings.md`.

## Residual exposure
- Other root-level files that influence a run (`sitecustomize.py`, `uv.toml`, `.python-version`, `.env`) remain unwatched; a change-outside-subdir refusal (option 2) was declined.
- A legitimate task that must edit a root pytest config is refused and needs a human.
- Gitignored config is not a gap: plan 13's clean-checkout gate (`git clean -ffdx`) removes it before the tests run (confirmed in `docs/spike-findings.md`, plan 13 section).

## Out of scope
Pinning pytest's rootdir/config in argv, refusing all changes outside the subdir, the other known gaps.

## Folded from the plan-reviewer (supersedes the text above where they differ)
Reviewer found the design sound; the cycles needed fixing. It could not run git, pytest or uv.

**Cycles (replaces the list above)**
- R0 (green characterisation): a root-only change gives `no_changes` (`check_gate` returns it when nothing under the subdir changed; intended). Add helpers: a project change base, and a root-file commit.
- R1 (red): a new root `pytest.ini` plus a `proj/code.py` change gives `tests_modified`. This is the first red test and also verifies the `:(glob)**/name` premise. Change the pathspec in `_protected_changed` to `:(glob)**/{name}`. Then parametrized **guards that start green, labelled as such**: every default name at the root, a sibling `docs/tox.ini` (the accepted false-refusal cost, also for tracked vendored dirs), modify, delete, rename onto a protected name (`--no-renames` stays), the `protected_names` override at the root (and root `pytest.ini` no longer refused under it), and a root `README.md` change plus a project change passes. Run the full suite after R1 (no existing test was found that breaks; other test files and e2e scripts were not searched by the reviewer).
- R3 (red, root only): a new root `pyproject.toml` with a pytest table plus a project change gives `tests_modified`. Green guard: a `[project]`-only change passes.
- R4 (red): for subdir `a/b`, a pytest-table change in `a/pyproject.toml` gives `tests_modified`. Generalise to a loop over the resolved `project_dir.relative_to(worktree.resolve())` parents plus the project dir itself (not string splitting of `project_subdir`); the old subdir-only lines go. Green guards: a sibling `c/pyproject.toml` change passes; `./proj` and `proj/` as `project_subdir` still work.
- R7 (wiring): through `verify_task`, a root `pytest.ini` plus a `proj/code.py` change fails `tests_modified` and a sentinel `test_argv` is not run.
- E1: no e2e (pure git diff logic). Full suite, `verify`, and a findings note.

**Stated semantics**
- The check is conservative: a root `pytest.ini` is refused even if `target/pytest.ini` exists and would win. Accepted.
- A task whose base already has a root pytest config and edits it is refused. Accepted.

**Residual additions (unverified, uv discovery not tested):** a root `pyproject.toml` `[tool.uv]` table (workspace, sources, overrides), `uv.toml`, `sitecustomize.py`, `.python-version`, `.env`.

**Not verified:** git's `**/` match at the repo root (R1 settles it); pytest's ancestor discovery order beyond the spike; uv's upward discovery.
