# Plan 51: reject `project_subdir = "."` when settings load

## Problem

`project_subdir = "."` passes settings validation (`_check_project_subdir` in `orchestrator/src/factory/settings.py`).
The first failure is at the verify stage, as `ProjectSubdirEscapes: .` from `_resolve_inside` in
`orchestrator/src/factory/gate.py` (reached from `check_gate` and from `has_nested_repo`).
By then at least the implement stage has run, with the repo root as its working directory:
`stages.py:69` and `runner.py:50` build the project path without `_resolve_inside`. The order of the other stages
was not traced.
`_resolve_inside` refuses the repo root on purpose: the gate needs a subdirectory to tell "inside" from "outside".
`PurePosixPath(".").parts == ()`, and so does `"./"`; `"a/."` normalises to `("a",)` (all run).

## Change

- `_check_project_subdir` also rejects any value whose path has no parts (`"."`, `"./"`). It computes
  `PurePosixPath(subdir).parts` once. The final message is
  `project_subdir must name a subdirectory of the repo, not the repo root; the gate treats everything outside it as out of bounds`.
- `README.md`: reword the `project_subdir` line (around line 131): the project must be a subdirectory, not the repo
  root; goals must stay inside it; edits outside are refused unless listed in `gate_allow_outside`; an existing
  config with `"."` must set `project_subdir` to a subdirectory. Cross-reference the `clone` paragraph (around line 60).
- `gate.py` unchanged: its check stays as the second line of defence.

## Behaviour change for existing configs

A `factory.toml` with `"."` used to fail only at verify. It now fails every command that loads settings, including
`clean`, `status` and `serve`. Both CLI paths catch `SettingsError` and print `settings error: <message>` with exit 2
(`cli.py:832,837`), so the failure is clean and needs no new test.

## Decided (not up for review)

- Reject at load, do not support the repo root as a project.
- Leave `"a/."`-style forms alone; do not normalise `project_subdir` (it would touch the gate's pathspecs).

## TDD cycles (one commit each)

1. Guard: add `"."` to the parametrised `ProjectSubdirEscapes` test at `orchestrator/tests/test_gate.py:126`. It passes
   today (`resolved == worktree.resolve()`); it stops the backstop being removed later.
2. Red: add `('project_subdir = "."', "project_subdir")` to the parametrised
   `test_bad_settings_are_rejected_with_a_message_naming_the_key` in `orchestrator/tests/test_settings.py`. Reject empty
   `parts` with the existing message, which already names the key.
3. Guard: add `('project_subdir = "./"', "project_subdir")`; covered by cycle 2.
4. Red: a test that `project_subdir = "."` raises `SettingsError` matching "repo root". Switch to the new message.
5. README: the changes above.
6. Run the full suite in the background and wait with Monitor; then run `verify`.

## Tests touching these strings

`test_gate.py:130` (inside the parametrised test of cycle 1) and `test_advance.py:152` use `ProjectSubdirEscapes`;
`test_advance.py` does not change. The existing `"/abs"`, `"a/../b"` and `""` cases only match the key, so the new
message does not affect them. Tests that build `Settings(...)` directly bypass the check; none uses `"."`.

## Not covered

- Symlinks that resolve to the repo root or escape the worktree: they pass settings and are caught only by the gate.
- Case-insensitive filesystems.
