# Plan 50: allow named root files outside the project (`gate_allow_outside`)

Status: draft, not reviewed, awaiting decisions and approval.

## Problem

Plan 18 made the gate refuse any change outside `project_subdir` (`outside_project`), so root config can't change what
the tests run. The cost: a goal that edits a root file with no bearing on the tests (a root `README.md`, `LICENSE`,
`CHANGELOG.md`) is refused and needs a human. Found in the real-repo trial (jshiell/factory-scrap): issue #5 asked for a
root `README.md` edit and could not pass.

## Proposal

A per-repo setting `gate_allow_outside`: a list of exact repo-root-relative file paths that a task may change without
tripping `outside_project`. Default empty, which is today's behaviour. Everything else outside the project still fails.

## Facts checked in the code

- The check is `_changed_outside_project` (`orchestrator/src/factory/gate.py:87`), called from `check_gate` (`:49`) after
  the tamper checks, so every earlier refusal keeps its reason. It diffs `base_sha..HEAD` with
  `:(top,exclude,literal)<project>` and fails on any output.
- `check_gate` has two production callers, both in `verify_task.py` (`:112`, `:117`, the unconfined and the confined
  call), plus `cli.py:738` (`_check_corpus`, uses the fixed eval `PROJECT_SUBDIR`; no allowlist, left alone).
- The `no_changes` check (`gate.py:63`) looks only at `project_subdir`. A task whose only change is an allowed root
  file therefore still fails `no_changes`. That stays (see Decision 3).
- `DEFAULT_PROTECTED_NAMES` (`gate.py:11`) are refused anywhere in the tree by `_protected_changed` before the outside
  check, so allowing one of them would never take effect.
- Per-repo-only settings are listed in `_PER_REPO_KEYS` (`settings.py:103`); string lists in `_STRING_LIST_KEYS`
  (`:67`); `_check_project_subdir` (`:283`) is the model for path validation.
- The agent's sandbox already lets it write the whole worktree, so no sandbox change is needed.
- Existing tests that assert `outside_project` (must stay green, unchanged): `tests/test_gate.py:680,691,709,730,746,755`
  and `tests/test_verify_fix_loop.py:375,396`. No user-visible string changes, so no output tests are touched.

## Decisions (user)

1. **Where the setting may live.** Per-repo only (recommended): which root files are safe depends on the repo, as with
   `watch_merge`. Alternative: also allow it in the global file, which would apply one list to every repo.
2. **Literal paths or globs.** Literal repo-root-relative file paths only (recommended; no `*`, no directories, no
   `..`, not absolute). Globs and directories make it easy to allow something that reaches the tests (`docs/**` that a
   doctest glob collects).
3. **A task that changes only allowed root files.** Keep failing as `no_changes` (recommended: the factory is for
   changes to the project; a README-only task stays human work). Alternative: count allowed files as changes, which
   needs a second change to the `no_changes` check and a new case.
4. **Symlinks.** Refuse the task as `outside_project` if an allowed path is a symlink at HEAD (recommended; one extra
   cycle). Alternative: skip it and document, since the tests are unlikely to follow a root symlink.

## Seams

`check_gate` (public, the gate's contract), `load_settings` (config parsing), and `verify_task` (wiring, observed through
the task's final state and failure reason). Each cycle's test sits at one of them. To confirm before writing tests.

## Cycles (TDD, one commit each)

1. Red then Green, `check_gate`: new optional parameter `allow_outside: tuple[str, ...] = ()`. A change to a root
   `README.md` listed in it, plus a project change, passes. Existing `outside_project` tests untouched.
2. Red, `check_gate`: a root change to a file not listed, beside a listed one, still fails `outside_project`.
3. Red, `check_gate`: a listed file's change plus a root `uv.toml` change fails (`tests_modified` via the protected
   names, unchanged order). Guard if cycle 1's implementation already covers it; mark it so when written.
4. Red, `check_gate` (only if Decision 4 = refuse): an allowed path that is a symlink at HEAD fails `outside_project`.
5. Red, `load_settings`: `gate_allow_outside = ["README.md"]` loads from a project file; a list in the global file is an
   error; each of `/x`, `../x`, `a/*.md`, `docs/` (a directory entry, trailing slash), `""` and a protected name such
   as `uv.toml` is a `SettingsError` naming the key; a path under `project_subdir` is an error (it is already allowed).
6. Red, `verify_task` wiring: with the setting on, a task that changes a project file and the root `README.md` ends
   `VERIFIED`; with it off, `FAILED` with `outside_project`. Both `check_gate` calls pass the setting.
7. Docs: README (settings table or sandbox/gate section and the Trust bullet), `docs/spike-findings.md` entry
   ("no spike; read from source"). The README must say: literal files only; do not list a file the tests read
   (a doctest glob, a `--doctest-glob` option, a test that opens it); a root-only task still fails `no_changes`.
8. Full suite in the background, then `verify`; audit against this plan.

## Risks / unverified

- The check can't know which root files the repo's tests read. A listed file the tests read (a doctest collecting
  `README.md`) lets the agent change what the tests see. This is an operator decision, documented, not detectable.
- Whether `git diff --name-only` reports a type change (file to symlink) at the same path: assumed yes, to be checked
  in cycle 4's red.
- Comparison is on exact repo-root-relative paths with `/` separators; a case-insensitive filesystem may let `readme.md`
  and `README.md` differ in git but not on disk. Not handled.

## Not covered

- `project_subdir = "."`, which is rejected by `_resolve_inside` regardless (a different rule). Its error message
  is cryptic (`ProjectSubdirEscapes: .`); worth its own small plan.
- Allowing directories or globs.
- Letting a root-only task through the gate (Decision 3).
