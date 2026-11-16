# Plan 50: allow named root files and directories outside the project (`gate_allow_outside`)

Status: reviewed (opus plan-reviewer), findings folded, decisions made; awaiting approval to implement.

## Problem

Plan 18 made the gate refuse any change outside `project_subdir` (`outside_project`), so root config can't change what
the tests run. The cost: a goal that edits a root file with no bearing on the tests (a root `README.md`, `LICENSE`,
`CHANGELOG.md`, a `docs/` tree) is refused and needs a human. Found in the real-repo trial (jshiell/factory-scrap): issue
#5 asked for a root `README.md` edit and could not pass.

## Decided (not up for review)

1. The setting works in the project file and the global file. As with `plugins`, a project list replaces the global one.
2. Entries are literal repo-root-relative paths: files and directories. No globs. A directory covers everything under it.
3. A task whose only changes are allowed paths counts as a change (it no longer fails `no_changes`).
4. A symlink or gitlink that a task adds or changes under an allowed path is refused as `outside_project`. An unchanged
   pre-existing symlink is tolerated (only changed paths are checked).
5. An empty list is a settings error, as for every string list today; a project cannot override a global list with
   nothing.
6. Validation also rejects `uv.lock`, `.gitattributes`, `.gitmodules` and any entry with a `.git` component, at any level.

## Proposal

`gate_allow_outside`: a list of repo-root-relative paths a task may change without tripping `outside_project`. Default
unset, which is today's behaviour. Anything outside the project and outside the list still fails. The agent's prompts
name the allowed paths when the setting is on.

## Facts checked in the code

- The check is `_changed_outside_project` (`orchestrator/src/factory/gate.py:87`), called from `check_gate` (def `:48`)
  after the tamper checks and the frozen-sha check (call at `:74`), so every earlier refusal keeps its reason. It diffs
  `base_sha..HEAD` with `:(top,exclude,literal)<project>` and fails on any output.
- `check_gate`'s only production caller is `verify_task.py` (`:112` unconfined, `:117` confined). `verify_task` takes
  individual values, not `Settings`; its only caller is `build_stages` (`stages.py:90-97`). `cli.py:738`
  (`_check_corpus`, fixed eval `PROJECT_SUBDIR`) gets no allowlist. `test_cli.py:1113,1130` monkeypatch `cli.check_gate`
  with `**kw`, so a new optional parameter does not break them.
- The `no_changes` check (`gate.py:63`) diffs only `-- project_subdir` (no pathspec magic).
- `DEFAULT_PROTECTED_NAMES` (`gate.py:11`) and the `[tool.pytest]` / `[tool.uv]` tables are refused anywhere in the tree
  (`:(glob,icase)**/name`, `:135`) by `_protected_changed` before the outside check, so a protected file under an allowed
  directory still fails `tests_modified`. Test globs are checked only inside the project (`:99`, `:105`).
- A literal directory pathspec already covers the files under it: project-only changes pass (`test_gate.py:794,809`) and
  a same-prefix sibling `proj2/x.py` fails (`:750`).
- `has_nested_repo` (`gate.py:28`) walks only the project. `HARDENED_GIT` (`worktrees.py:8`) sets
  `diff.ignoreSubmodules=all`, so a gitlink shows only with `--ignore-submodules=none` (as `_changed_outside_project`
  does; `test_gate.py:741`).
- The implementer's system prompt (`stages.py:59-61`) and the fix prompt (`fix_prompt.py:39`) both say "Only change
  files inside the project directory; changes anywhere else are refused." (plan 18, `docs/plan-18.md:45`; a real
  implementer obeys it, `docs/spike-findings.md:289`). Tests pin it: `tests/test_stages.py:200`,
  `tests/test_fix_prompt.py:73`. Not checked: whether the planner and reviewer prompts steer away from root files.
- Settings: `_PER_REPO_KEYS` (`settings.py:103`; the new key is not added), `_STRING_LIST_KEYS` (`:67`, rejects `[]`),
  `_check_project_subdir` (`:283`, called after the merge, the model for the new check).
- Tests that assert `outside_project` and must stay green, unchanged: `tests/test_gate.py:680,691,709,730,746,755,779,788,
  797,814`; `tests/test_verify_fix_loop.py:375,396`. `no_changes` tests that must stay green: `test_gate.py:84,635`.

## Seams

`check_gate` (the gate's contract), `load_settings` (config parsing), `verify_task` (wiring through the final task state
and failure reason), `build_stages` (the stage lambdas, observed through the gate's effect), and the two prompts (the
text handed to the runner). To confirm before writing tests.

## Cycles (TDD, one commit each; mark guards as guards)

1. Red then Green, `check_gate(allow_outside=("README.md",))`: a project change plus a root `README.md` change passes
   (it fails `outside_project` today). Green: exclude `:(top,exclude,literal)<entry>` per entry.
2. Guard: an unlisted root file beside a listed one fails `outside_project`.
3. Guard: a listed `docs` covers `docs/guide/a.md`; an unlisted `docs2/x.md` fails.
4. Red, `no_changes`: with only a listed path changed, the gate does not return `no_changes` and the tests run (use a
   sentinel `test_argv`). With nothing listed or project changed it still does. Green: add `:(top,literal)<entry>`
   terms to the `:63` diff, keeping the project term as is. Includes: a protected file alone under a listed directory
   now fails `tests_modified`, not `no_changes`.
5. Red, one test at a time: a changed path under an allowed entry whose new mode is `120000` (new symlink; file replaced
   by a symlink) or `160000` (gitlink) fails `outside_project`. Green: one
   `git diff --raw --no-renames --ignore-submodules=none base HEAD -- <allowed pathspecs>`. Guard: an unchanged
   pre-existing symlink under the listed directory does not fail. Assumed, settled in this red: a file-to-symlink change
   is reported at the same path (status `T`), and `--raw` shows mode `160000` under that flag.
6. Guard: a `conftest.py` or `UV.toml` under a listed directory fails `tests_modified`, including as the only change.
   Not red if cycle 1 already covers it.
7. Red, `load_settings`: `gate_allow_outside` loads from the project file and the global file; a project list replaces
   the global one. `SettingsError` naming the key for: `/x`, `../x`, `a/../b`, `a/*.md`, `""`, `.`, `proj` and `./proj`
   (with `project_subdir = "proj"`), an ancestor of `project_subdir` (a global list plus a project `project_subdir`), a
   path under `project_subdir`, `uv.toml` and `UV.toml`, `uv.lock`, `.gitattributes`, `.gitmodules`, `x/.git/y`,
   duplicates, and `[]`. A trailing `/` is accepted and normalised. Validation runs after the merge, beside
   `_check_project_subdir`. Decide in the red whether `?`, `[` and a leading `:` are rejected or taken literally
   (recommended: rejected with `*`).
8. Red, `verify_task(gate_allow_outside=...)` (new keyword, threaded through `_gate_once` to both `check_gate` calls):
   with the setting, a project plus README change ends verified; without it, `FAILED` / `outside_project`. Cover the
   sandboxed call through the existing fake-sandbox fixture (`test_verify_fix_loop.py:339`).
9. Red, `build_stages`: the verify stage passes `settings.gate_allow_outside` (a setting loaded from config, a README
   change passing the gate).
10. Red, prompts: with the setting on, the implementer system prompt and the fix prompt name the allowed paths ("...inside
    the project directory, or these paths: README.md, docs; changes anywhere else are refused."). With it unset, the
    current sentence is unchanged, so `test_stages.py:200` and `test_fix_prompt.py:73` stay green unedited.
    `build_fix_prompt` takes the list as a new argument; `implementer_config` and `fixer_config` already take `Settings`.
11. Docs: README (gate and settings sections around `:267`, the Trust bullet) and a `docs/spike-findings.md` entry
    ("no spike; read from source", plus how the two git assumptions turned out). The README must say: no globs; a
    directory covers everything under it; added or changed symlinks and gitlinks under an allowed path are refused; do not
    list anything the tests read (a doctest glob, `--doctest-glob`, a test that opens it, a uv workspace member, `uv.lock`);
    a task that changes only allowed paths is verified by running the tests on an unchanged project, so "verified" says
    nothing about those files; a fix round that changes only allowed paths re-runs the same failing tests (bounded by
    `fix_rounds`).
12. Full suite in the background, then `verify`; audit against this plan.

## Risks / unverified

- The gate can't know which allowed files the tests read. A listed file or directory the tests read lets the agent change
  what the tests see. Directories widen this; documented, not detectable.
- A task that changes only allowed files passes with the tests run on an unchanged project (decision 3). Documented.
- File-to-symlink type change and `--raw` mode `160000` under `--ignore-submodules=none`: assumed, settled in cycle 5.
- What git reads from an embedded repo during `git add -A`: the exposure exists today for any outside path and is not
  widened; not verified.
- Renames: `--no-renames` splits a move into delete plus add, so moving a test file out of the project fails
  `tests_modified`, and moving from an unlisted path into an allowed one leaves the source deletion, which fails
  `outside_project`. Moving a non-test project file out is allowed and is the same "tests read it" risk.
- Exact repo-root-relative paths with `/` separators; a case-insensitive filesystem can make `readme.md` and `README.md`
  differ in git but not on disk. Not handled.
- A global entry that does not exist in a repo is silently inert there.
- The sandbox write scope (said to cover the whole worktree) was not read for this plan.

## Not covered

- `project_subdir = "."`, rejected by `_resolve_inside` regardless (a different rule). Its error message is cryptic
  (`ProjectSubdirEscapes: .`); worth its own small plan.
- Globs.
- Whether the planner and reviewer prompts mention the project boundary.
