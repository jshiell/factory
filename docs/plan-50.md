# Plan 50: allow named root files and directories outside the project (`gate_allow_outside`)

Status: draft with the user's decisions folded in; not reviewed; awaiting review and approval.

## Problem

Plan 18 made the gate refuse any change outside `project_subdir` (`outside_project`), so root config can't change what
the tests run. The cost: a goal that edits a root file with no bearing on the tests (a root `README.md`, `LICENSE`,
`CHANGELOG.md`, a `docs/` tree) is refused and needs a human. Found in the real-repo trial (jshiell/factory-scrap): issue
#5 asked for a root `README.md` edit and could not pass.

## Decided (not up for review)

1. The setting works in the project file and the global file. As with `plugins`, a project list replaces the global one.
2. Entries are literal repo-root-relative paths: files and directories. No globs. A directory covers everything under it.
3. A task whose only changes are allowed paths counts as a change (it no longer fails `no_changes`).
4. A symlink at an allowed path (or under an allowed directory) at HEAD is refused as `outside_project`.

## Proposal

`gate_allow_outside`: a list of repo-root-relative paths a task may change without tripping `outside_project`. Default
empty, which is today's behaviour. Anything outside the project and outside the list still fails.

## Facts checked in the code

- The check is `_changed_outside_project` (`orchestrator/src/factory/gate.py:87`), called from `check_gate` (`:49`) after
  the tamper checks, so every earlier refusal keeps its reason. It diffs `base_sha..HEAD` with
  `:(top,exclude,literal)<project>` and fails on any output.
- `check_gate` has two production callers, both in `verify_task.py` (`:112`, `:117`, the unconfined and the confined
  call), plus `cli.py:738` (`_check_corpus`, uses the fixed eval `PROJECT_SUBDIR`; no allowlist, left alone).
- The `no_changes` check (`gate.py:63`) diffs only `project_subdir`. Decision 3 changes it to also count the allowed
  paths.
- `DEFAULT_PROTECTED_NAMES` (`gate.py:11`) and the `[tool.pytest]` / `[tool.uv]` tables are refused anywhere in the tree
  by `_protected_changed` before the outside check, so an allowed directory containing a `conftest.py` or `uv.toml`
  still fails `tests_modified`. Test globs (`test_*.py`, `*_test.py`) are checked only inside the project.
- `has_nested_repo` (`gate.py:28`) walks only the project, so a `.git` under an allowed directory is not caught there;
  the outside diff (`--ignore-submodules=none`) is what reports a gitlink today (`tests/test_gate.py:741`), and an
  allowed directory would now hide it. Decision 4's check therefore also refuses a gitlink (mode 160000) under an
  allowed path.
- Per-repo-only settings: `_PER_REPO_KEYS` (`settings.py:103`). `gate_allow_outside` is not added there. String lists:
  `_STRING_LIST_KEYS` (`:67`). `_check_project_subdir` (`:283`) is the model for path validation.
- The agent's sandbox already lets it write the whole worktree, so no sandbox change is needed.
- Existing tests that assert `outside_project` (must stay green, unchanged): `tests/test_gate.py:680,691,709,730,746,755`
  and `tests/test_verify_fix_loop.py:375,396`. No user-visible string changes, so no output tests are touched.

## Seams

`check_gate` (the gate's public contract), `load_settings` (config parsing), and `verify_task` (wiring, observed through
the task's final state and failure reason). To confirm before writing tests.

## Cycles (TDD, one commit each)

1. Red then Green, `check_gate`: optional parameter `allow_outside: tuple[str, ...] = ()`. A project change plus a root
   `README.md` change, with `README.md` listed, passes. Existing `outside_project` tests untouched.
2. Red, `check_gate`: a root change to a file not listed, beside a listed one, fails `outside_project`.
3. Red, `check_gate`: a listed directory covers a nested file (`docs/guide/a.md`), and an unlisted sibling directory
   still fails. Assumed, to be checked in this red: a literal pathspec on a directory matches the files under it.
4. Red, `check_gate`: with only a listed path changed (no project change) the gate no longer returns `no_changes`; it
   goes on to run the tests. With no listed or project change it still returns `no_changes` (existing test stays).
5. Red, `check_gate`: an allowed path that is a symlink at HEAD, a symlink under an allowed directory, and a gitlink
   under an allowed directory each fail `outside_project`. One test per case, written one at a time.
6. Guard (mark as such when written): a listed directory containing a changed `conftest.py` or `uv.toml` fails
   `tests_modified`, because the protected-name check runs first. Not red if cycle 1 already covers it.
7. Red, `load_settings`: `gate_allow_outside = ["README.md", "docs"]` loads from the project file and from the global
   file; a project list replaces the global one. Each of `/x`, `../x`, `a/../b`, `a/*.md`, `""`, `.`, a path equal to
   or above `project_subdir`, a path under `project_subdir`, and a protected name (`uv.toml`) is a `SettingsError`
   naming the key. A trailing `/` is accepted and normalised.
8. Red, `verify_task` wiring: with the setting on, a task changing a project file and the root `README.md` ends
   verified; with it off, `FAILED` with `outside_project`. Both `check_gate` calls pass the setting.
9. Docs: README (settings and the gate section; the Trust bullet) and a `docs/spike-findings.md` entry ("no spike; read
   from source"). The README must say: no globs; a directory covers everything under it; symlinks and nested repos
   under an allowed path are refused; do not list anything the tests read (a doctest glob, `--doctest-glob`, a test that
   opens the file), because the gate cannot tell; a task that changes only allowed paths is verified by running the
   tests on an unchanged project.
10. Full suite in the background, then `verify`; audit against this plan.

## Risks / unverified

- The gate can't know which allowed files the tests read. A listed file or directory the tests read lets the agent change
  what the tests see. Directories widen this (decision 2); documented, not detectable.
- Decision 3: a task that changes only allowed files passes with the tests run on an unchanged project, so a "verified"
  result says nothing about those files. Documented.
- Whether `git diff --name-only` reports a type change (file to symlink) at the same path: assumed yes, to be checked
  in cycle 5's red.
- Whether a literal pathspec on a directory matches the files under it: assumed yes, checked in cycle 3's red.
- Exact repo-root-relative paths with `/` separators; a case-insensitive filesystem can make `readme.md` and `README.md`
  differ in git but not on disk. Not handled.
- A global setting applies to every repo that does not set its own, and a repo with no such file or directory simply
  never matches. An entry that is wrong for one repo is silently inert there.

## Not covered

- `project_subdir = "."`, rejected by `_resolve_inside` regardless (a different rule). Its error message is cryptic
  (`ProjectSubdirEscapes: .`); worth its own small plan.
- Globs.
- Whether the agent should be told which root paths it may touch (the prompts say nothing about the project boundary
  today; not checked in this plan).
