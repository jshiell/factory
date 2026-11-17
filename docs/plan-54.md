# Plan 54: watch dependency and entry-point changes in the gate

## Problem
The gate runs the project's tests in a sandbox. An agent can add a dependency, a build hook or a
`[project.entry-points.pytest11]` entry and the gate installs and runs that code without complaint.
`gate.py` `_protected_changed` watches only `[tool.pytest]`, `[tool.uv]` and the protected file names, and
plan 20 left dependencies and `uv.lock` editable by choice ("Known gaps", `docs/spike-findings.md` ~line 306).
This plan reverses that choice.

## Decided (user-approved, not up for review)
- Scope A: dependency, entry-point and build-config changes. No docs rewrite, no orphan/`setsid` work, no nono profile change.
- Reuse the failure reason `tests_modified`.
- Fail closed by default. Opt-in is a per-repo bool `gate_allow_dependency_changes` (in `_PER_REPO_KEYS`, strict bool, refused in the global file). It is a standing opt-out, not a per-task approval.
- Watch strategy: everything except an allowlist of inert keys. In `[project]`: all keys except `name`, `version`, `description`, `readme`, `authors`, `maintainers`, `license`, `license-files`, `keywords`, `classifiers`, `urls`, `requires-python`. All of `[build-system]`, `[dependency-groups]`. In `[tool]`: all tables except `ruff`, `mypy`, `black`, `isort`, `pyright`, `coverage` (`pytest` and `uv` stay watched as today).
- Implementer and fixer prompts are in scope.
- The two tests below are reversed on purpose and replaced, not edited to compile:
  `tests/test_gate.py::test_guard_changing_uv_lock_passes` (line 354) and the `("dependencies = []", 'dependencies = ["requests"]')` case in `test_guard_changes_beside_an_unchanged_tool_uv_table_pass` (lines 343-351; the `line-length` case stays). Check other users of `BASE_WITH_UV` (line 311).

## Cycles (one test, one implementation, one commit each)
0. **Spike (throwaway, a copy of `target/` under the real gate sandbox, results into `docs/spike-findings.md`):**
   (a) a new `[dependency-groups]` entry is installed by `uv run pytest -q`; (b) a lock-only in-range version swap is installed;
   (c) `dynamic = ["entry-points"]` via setuptools/`setup.py` loads a `pytest11` plugin; (d) `uv run` writes `uv.lock` when it is missing or stale;
   (e) the gate sandbox has network. Decides the `uv.lock` rule: flag any change, or only modify/delete of a lock that exists in the base.
   Also: grep tests and prompt tests for the `tests_modified` text and prompt strings to be touched; list them here; read `audit_corpus.py` for corpus cases touching dependencies.
1. Red: a `[project].dependencies` change fails `tests_modified`. Green: `_config_tables` returns the watched `[project]` slice.
2. Red: `[project].dynamic` gaining `dependencies` fails (guard if cycle 1 generalises).
3. Red: `[dependency-groups]`, `[build-system]`, `[project.entry-points.pytest11]`, `[project.scripts]` each fail (guards where cycle 1 covers them).
4. Red: `[tool.setuptools]`, `[tool.poetry]`, `[tool.hatch]` fail; `[tool.ruff]` change and `[project].description` change still pass (guards).
5. Red: a non-dict `project` or `build-system` counts as changed (explicit handling like `tool`).
6. Red: a changed `pyproject.toml` below the project (nested path dependency) with a new entry point fails. Green: compare every changed pyproject under the project as well as the ancestors, case-insensitively.
7. Red: an edit to `setup.py` or `hatch_build.py` anywhere fails. Green: new `DEPENDENCY_FILES` tuple checked like the protected names.
8. Red: a `uv.lock` change fails, per the cycle 0 rule. Add a fix-round test that a lock written by round 1's test run does not fail round 2, or document the limitation if cycle 0 shows it cannot be avoided. Remove `uv.lock` from `_ALWAYS_REFUSED_NAMES` only if it becomes redundant (refactor step).
9. Red: `check_gate(..., allow_dependency_changes=True)` passes cycles 1-8. Guard: `[tool.uv]` and `[tool.pytest]` edits still fail with the flag on.
10. Red (`test_settings`): `gate_allow_dependency_changes` defaults to false, parses as a strict bool, and is refused in the global file.
11. Red (wiring): the Settings value reaches `check_gate` through `verify_task` -> `_gate_once` (`verify_task.py` ~121-131), mirroring `gate_allow_outside`.
12. Prompts: implementer and fixer prompts (`stages.py` `_implementer_base`) say not to change dependencies, entry points, build config or `uv.lock`, unless the flag is on; planner and reviewer prompts say a plan needing a new dependency will be refused. Prompt tests first.
13. Docs: README gate paragraph; `docs/spike-findings.md` plan 54 entry (plan 20 gap closed, "editable by choice" reversed, remaining gaps); example `factory.toml` if one exists.
14. Verify: `uv run ruff check`, full `uv run pytest -q` in the background, `factory eval-audit --check-corpus` if runnable. Sign commits.

## Risks and unverified assumptions
- Unverified until cycle 0: that `uv run` installs a changed dependency or lock-only edit, builds dynamic or `setup.py` entry points, writes `uv.lock` in the sandbox, and that the gate sandbox has network.
- Legitimate dependency changes now fail the gate unless the repo sets the opt-in. No fix round follows a `tests_modified`, so prompts matter.
- The `[tool]` inert list is a judgement; an unknown tool table counts as watched, which can give false failures.
- Not covered: a malicious version of an existing pin within its range, and import-time monkeypatching in a new test file.
- Only array order is significant in watched tables; dict key order is ignored.
