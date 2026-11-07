# Plan 18: the gate refuses any change outside the project subdir

Goal: close the "files outside the project that influence a run are unwatched" gap (root `[tool.uv]`, `uv.toml`, `sitecustomize.py`, `.python-version`, `.env`, and anything not yet thought of). Plan 16 watched a fixed list of pytest config; this inverts it: the agent's change set must live inside `project_subdir`, so the list stops mattering.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test except via an approved edit list. Never push.
- Option 1 chosen: refuse every change outside the project. A task that must edit a root file (README, root config) is refused and needs a human.
- Only `gate.py` changes (plus tests, README known-limits, spike-findings).

## Behaviour
1. After the existing tamper checks (so every current refusal keeps its current reason), if `git diff --name-only --no-renames base_sha HEAD -- . ':(exclude)<project>'` is non-empty, the gate fails with a new reason `outside_project`. `<project>` is the resolved path relative to the worktree root (so `./proj` and `proj/` work). `--no-renames` so a file moved out of the project shows both paths.
2. Not run when `no_changes`, `no_commits`, `not_descendant` apply (they come first, as today).
3. `outside_project` is not retried by a fix round (`verify_task` only retries `tests_failed`), like every other non-test reason.
4. The plan-16 protected-name and pyproject-table checks stay as they are: they still decide the reason for protected files (`tests_modified`) and cover protected config *inside* the project. They are now redundant outside the project but are kept, to avoid a rewrite and keep reasons stable.

## Cycles
O1 (red): a project change plus a new root `README.md` gives `outside_project`. Add the check.
O2 (guards, start green): a project change plus a sibling-dir file; a file renamed from the project to outside; a deleted outside file; `./proj` and `proj/` forms; a nested subdir `a/b` with a change in `a/other.py`; a project-only change still passes; a root `pytest.ini` still reports `tests_modified` (order).
O3 (wiring): through `verify_task`, an outside change fails with reason `outside_project`, the sentinel `test_argv` is not run, and no fix round is spent.
O4: docs: README known-limits and tamper-checks text, `docs/spike-findings.md` note. Full suite, `verify`. No e2e needed: pure git diff logic, no TTY, nono, network or home dir.

## Edit list for existing passing tests (needs approval)
Three plan-16 guard tests assert that an outside change passes; that is now false by design:
- `test_guard_an_unrelated_root_change_beside_a_project_change_passes`: expect `outside_project`.
- `test_guard_a_root_pyproject_change_outside_the_pytest_table_passes`: expect `outside_project`.
- `test_guard_a_pytest_table_change_in_a_sibling_directory_passes`: expect `outside_project`.
- `test_guard_the_protected_names_override_applies_repo_wide`: second half (root `pytest.ini` under an override) expects `outside_project` instead of passing.

## Residual exposure / costs
- Legitimate root edits (README, root config, `.gitignore`) are refused; a human makes them.
- Anything the agent writes outside the project that is not gitignored (stray files) now fails the task. The implementer has no Bash, so only its Write/Edit tools can create them.
- Changes inside the project that influence a run (`target/uv.toml`, `target/.python-version`, `target/sitecustomize.py`) remain unwatched: this plan moves the boundary, it does not watch the inside. Honest limit.
- Import-time monkeypatching by a new test file remains (separate gap).

## Out of scope
Watching config inside the project, the other known gaps.

## Folded from the plan-reviewer (supersedes the text above where they differ)
Design sound. Could not run git; the points marked "tests settle it" are unverified until run.

**Verified by reading code (issue 3 and 10 closed):** the gate sandbox grants write to the project dir and a private uv cache only (`gate_sandbox.py:34-36`), so tests cannot leave files outside the project between fix rounds. The implementer has `Bash` and `Bash(git *)` disallowed (`stages.py`), so only Write/Edit can create outside files. No pinning test for stray test-written files; recorded as a residual note only.

**Check details:** placed after the `frozen_sha` block, just before the tests run. Command: `git diff --name-only --no-renames --ignore-submodules=none base HEAD -- . ':(top,exclude,literal)<resolved rel path>'`. `--ignore-submodules=none` because `HARDENED_GIT` sets `diff.ignoreSubmodules=all`, which may hide an added gitlink outside the project (tests settle it; if the flag does not help, widen `has_nested_repo` to the whole worktree).

**Prompts (decided (a)):** one sentence in the implementer system prompt (`stages.py`) and in `fix_prompt.py`: "Only change files inside the project directory; changes anywhere else are refused." This is outside the earlier "only gate.py" scope and is now in scope. Test-first: a prompt contains the sentence.

**Edit list (four tests, replaces the "three" above; needs approval):**
1. `test_guard_an_unrelated_root_change_beside_a_project_change_passes`: expect `reason == "outside_project"`.
2. `test_guard_a_root_pyproject_change_outside_the_pytest_table_passes`: same.
3. `test_guard_a_pytest_table_change_in_a_sibling_directory_passes`: same.
4. `test_guard_the_protected_names_override_applies_repo_wide`: only the second assertion (root `pytest.ini` under an override) expects `outside_project`; the first (`locked.cfg`, `tests_modified`) is unchanged.
These land in the same commit as the O1 green step, so no commit has a red suite.

**Cycles (replaces the list above)**
- O1 (red): edit 1 above shown red; add the check; apply edits 2-4 in the same green commit. Full `test_gate.py` green.
- O2: a gitlink at the root (`git update-index --add --cacheinfo 160000,<sha>,sub`) plus a project change gives `outside_project`. Red if the submodule hiding is real; fix by the flag, else widen `has_nested_repo`.
- O3 (guards, start green, labelled): sibling dir file; `proj2/x.py` prefix boundary; file moved from the project to the root; deleted root file; `./proj` and `proj/`; nested `a/b` with a change in `a/other.py`; project-only change passes. (Root `pytest.ini` order is already pinned by `test_a_new_root_pytest_ini_fails_tests_modified` and `test_verify_task.py`.)
- O4 (wiring, `test_verify_fix_loop.py` fixtures): `fix_rounds=1`, the agent's commit includes a root file: `failed` / `outside_project`, sentinel `test_argv` not run, runner not called, `fix_rounds_used == 0`.
- O5: prompt sentence in both prompts (test-first).
- O6: README (tamper bullet: "every change must be inside the project"; known limits: "files inside the project that influence a run"), `docs/spike-findings.md` note, full suite, `verify`.
