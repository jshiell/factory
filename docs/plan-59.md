# Plan 59: pin the allow-dependency flag's edges (plan 54 audit gaps)

## Problem
Plan 54's audit listed two cases the `gate_allow_dependency_changes` flag has no test for. The flag is a standing opt-out, so its edges are policy worth stating in tests:
- A nested pyproject (below the project). With the flag on, `_config_tables` (`gate.py:196`) keeps only `[tool.pytest]` and `[tool.uv]` of every pyproject that `_protected_changed` (`gate.py:172-188`) reaches via `_reaches_project` (`gate.py:191-193`). The existing flag tests (`test_gate.py:412-444`) only edit the project's own pyproject.
- A deleted `uv.lock`. With the flag on, `check_gate` skips `_dependency_file_changed` (`gate.py:79`). The flag test for locks (`test_gate.py:424`) edits a lock; the deletion test (`test_gate.py:463`) runs with the flag off.

The review added a third gap: nothing tests that the protected file names (`uv.toml`, `conftest.py`, `.python-version`) still fail with the flag on. The names check is the first branch of `_protected_changed` (`gate.py:176-178`) and every names test runs with the flag off.

The sandbox-spike half of the plan 54 audit item was plan 55.

## Honest framing
Guards A and D only fail on a code path the flag already skips for everyone, so existing tests 416 and 424 already catch their mutations. They are statements of policy, not fixes for a defect window. Guard B's uv case and guard C close real gaps.

## Scope
Tests only, expected green on arrival. If any guard is red, that is a real defect in the flag: stop, report it, and plan the fix separately (no fix inside this plan).

## First
1. `grep -rn 'allow_dependency_changes' orchestrator/tests` and list tests already covering the flag so none is duplicated (including `verify_task` tests).
2. Re-read `tests/test_gate.py:380-480` and the helpers `gate_allowing_dependencies`, `pyproject_base`, `commit_project_edit`, `commit_root_file`, `repo_with_test_file`, `BASE_WITH_UV`, `WATCHED_ADDITIONS`.

## Guards (green on arrival; each mutation-checked; one test, one commit each)
Each carries a `# Guard (starts green): ...` comment, as in the rest of the file.

- **A. Nested pyproject edits pass with the flag on.** Flag on; `proj/sub/pyproject.toml` (base `BASE_WITH_UV`) gains the same dependency and `WATCHED_ADDITIONS` edits as test 416; passes. Adds a small helper `nested_pyproject_base(repo, content, path="sub/pyproject.toml")`, reused by B. Do not move test 389 onto it. Mutation: make the flag branch of `_config_tables` return the full watched tuple; red.
- **B. Protected tables stay watched with the flag on.** Flag on; fails with `reason == "tests_modified"`. Parametrized: nested `sub/pyproject.toml` gaining `[tool.pytest.ini_options]`; nested changed `[tool.uv]` value (`exclude-newer`); root `pyproject.toml` changed `[tool.uv]` via `commit_root_file`, with a `code.py` edit so `no_changes` is not tripped. Asserting the exact `reason` avoids passing via `outside_project`. Mutations: drop `tool.uv` from the flag branch (uv cases red); remove the `project in directory.parents` arm of `_reaches_project` (nested cases red); remove the `directory in project.parents` arm (root case red).
- **C. Protected names stay protected with the flag on.** Flag on; adding or editing `uv.toml`, `conftest.py` or `.python-version` fails `tests_modified`. Mutation: make the names branch at `gate.py:177` `if not allow_dependency_changes and ...`; red.
- **D. A deleted `uv.lock` passes with the flag on.** Flag on; deleting an existing `proj/uv.lock` (the deletion is the only change) passes. Mutation: drop `not allow_dependency_changes and` at `gate.py:79`; red. This writes "the flag allows deleting the lock" into a test (uv re-resolves within the pyproject's ranges, which matches "the lock may be edited"). Drop D if the policy is not wanted.

Test names state behaviour (for example `test_allowing_dependency_changes_passes_a_deleted_uv_lock`), mirror the existing `repo` fixtures, and use no constant nonces.

## Verification
`cd orchestrator && uv run ruff check`, then the full suite in the background (about 100s, per AGENTS.md; wait with Monitor). Check each mutation with a temporary edit, confirm red, then `git checkout` the file; finish with `git diff --quiet orchestrator/src/factory/gate.py`.

## Risks
- A guard may expose a real gap (the point of writing it). That stops the plan.
- Not run by the reviewer: the green and red results were traced, not executed. The mutation checks confirm them.
- Not covered, deliberately: a flag-on ancestor dependency change (`outside_project`, needs `allow_outside`, which `gate_allowing_dependencies` lacks) and a nested `setup.py` (same skipped path as 424).

## Files
`orchestrator/tests/test_gate.py`, `docs/plan-59.md` (this). No source change.
