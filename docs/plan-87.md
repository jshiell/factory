# Plan 87: let tasks edit the whole repo except `.factory/`

Branch `plan-87-editable-repo-root`, cut from `main`. Drafted 2026-11-23, revised after plan review (`plan-reviewer`, opus) and a sandbox spike. **Draft, awaiting approval.** (Number 86 is reserved by plan 85 for the `watch` retry cap.)

## Problem
Edits are confined to `project_subdir` (default `target`) plus the literal paths in `gate_allow_outside`. Wanted: tasks may change anything in the repo, root included, except a fixed `.factory/` directory at the root; every file the factory itself must be protected from lives there.

## Decisions (user, 2026-11-22/23)
- D1: split `project_subdir` (where the agent works and the tests run; new default `.`) from the editable area (the whole repo except `.factory/`).
- D2: `.factory` is a fixed name.
- D3: `.gitattributes` and `.gitmodules` (any depth) stay denied; `.github/` is allowed.
- D4: a root `factory.toml` fails startup with a clear error and fix instructions.
- D5: sandbox option 2: the worktree is writable; `.git` and `.factory` are protected by the factory (git-dir pinning and the gate), not the sandbox.
- D6: any added symlink or gitlink anywhere fails the gate.
- D7: root tool config (`.claude/`, `.mcp.json`, `mise.toml`, `.mise.toml`, `.vscode/`, `.github/workflows`) is allowed but flagged in the merge preview.
- D8: the root-`factory.toml` check looks in the git toplevel; `--config` pointing inside the repo but outside `.factory` is refused. Relative paths in `.factory/factory.toml` resolve against the repo root (the parent of `.factory`).
- D9: test-tamper checks cover the whole repo, and every `pyproject.toml` in it is watched.

## Spike result (nono 0.81, 2026-11-23, run by the user)
- `--allow worktree --read worktree/.factory --read-file worktree/.git`: `.factory` and `.git` stay **writable** (Landlock is additive). So the sandbox cannot protect them inside a writable root.
- File-level grants allow only in-place edits: no new, renamed or deleted files. Per-entry grants would break creating root files, so they are rejected (D5).
- `--allow-cwd` alone is read-only (matches README).

## Why `.git` needs work first
A worktree's root `.git` is a file (`gitdir: …`). Today neither sandbox can write it (grants are `target/` only). With a root project both can, and the factory's own unsandboxed git calls (`add -A`, `status`, the audit diff) would read a gitdir the agent chose; filter drivers there run (README, `spike/git_hardening_probe.py`). `rebase_task.py:119` already compares the claimed gitdir with the expected one; reuse that check.

## Increments (one commit each; every commit leaves the suite green and the default config unchanged until increment 18)
0. **Pre-flight (done in drafting).** Grep of tests: hits under Tests touched.
1. **Red: a rewritten worktree `.git` file is never trusted.** Agent rewrites `.git` to a fake gitdir with a filter driver; `commit_all` must not run it and the gate fails `worktree_mismatch`. Green: worktree git calls pin `--git-dir`/`--work-tree` and verify the `.git` file against the expected gitdir.
2. **Red/green: `readonly_stage._dirty`**, 3. **`run_task._require_pristine`**, 4. **`merge_preview`**, 5. **audit diff**: each uses the pinned git call (one cycle each; guards if 1 already covers them).
6. **Red: a change under `.factory/` fails the gate**, matched case-insensitively, checked before `no_changes`.
7. **Red: a root `factory.toml` (any case) fails the gate.**
8. **Guard-or-red: `.gitattributes` and `.gitmodules` at any depth fail.** Check first whether the protected-name globs already cover them.
9. **Red: a change outside the project is allowed.** Drop the project exclusion in `_changed_outside_project`, keep the `allow_outside` parameter for now. Rewrite `test_gate.py` outside-project tests (needs approval, see below) in this commit.
10. **Red: changes outside the project count as changes** (the `no_changes` pathspec).
11. **Red: test-tamper covers the repo.** Globs become `**/glob`; a test removed outside the project fails. Separate cycle: every `pyproject.toml` is watched (a sibling path dependency's dependencies change fails).
12. **Red: any added symlink or gitlink anywhere fails.** Replaces `_link_added_under`.
13. **Root project in the gate.** Separate cycles: `_resolve_inside` accepts the worktree root but refuses a target resolving into `.factory`; `has_nested_repo` flags a root `.git` that is not the expected file and any `.git` below the root.
14. **Audit diff `-- . ':(exclude,top).factory'`.** Red: an outside change shows in the audit diff. Guard: the eval-corpus diff text is unchanged.
15. **Sandboxes (option 2).** The agent's write grant is the whole worktree (not `--allow-cwd`); the gate's grant is the project dir (the whole worktree for a root project). Policy-shape tests with the fake sandbox (as `test_harness_wiring.py:38`). README says plainly the sandbox does not protect `.git` or `.factory`.
16. **Settings: accept `project_subdir = "."`.** Refuse `.factory` and anything inside it (case-insensitive, resolved at gate time too).
17. **Config location** (each its own cycle): read `.factory/factory.toml` from the git toplevel; a root `factory.toml` errors with the `git mv` instructions and a note that `.factory/` may be gitignored; old in-repo state (`.factory/factory.db`, token) errors; relative paths resolve against the repo root; `--config` inside the repo but outside `.factory` is refused; the global `<home>/factory.toml` is unaffected. Preceded by a behaviour-preserving refactor: a `write_config(repo_dir, text)` test helper, because ~100 tests write `repo_dir/factory.toml` directly.
18. **Default `project_subdir = "."`**, with a startup warning when it is unset and `target/` exists.
19. **Merge preview flags root tool config** (D7), shown beside the diffstat.
20. **Remove `gate_allow_outside`.** Setting it (project or global; the error names the file) explains why; drop the parameter chain through `stages.py`, `verify_task`, `fix_prompt`; rewrite its tests.
21. **Prompts and docs.** `fix_prompt` boundary rule, planner/reviewer text, README (settings template, gate section, sandbox grants, state-dir upgrade note), AGENTS.md. Fill in Deviations.
22. **Gate and audit.** `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto`, `verify`, then `commit-auditor` (opus).

## Tests touched
- **Passing tests that will be edited (need approval):**
  - `test_gate.py`: the outside-project block (lines ~864 to ~1102) and the `gate_allow_outside` block (plan 50, ~1001 to ~1102), including the one the reviewer flagged at `:72`; the symlink and gitlink tests at ~1025 to ~1057.
  - `test_verify_fix_loop.py:374-494` (outside_project and the `gate_allow_outside=("README.md",)` runs at 390, 400, 427).
  - `test_settings.py:677-732` (`gate_allow_outside` and the `project_subdir = "."` refusal at 722).
  - `test_stages.py:141-154` and `:300-314` (the `gate_allow_outside` wiring and the string `Only change files inside the project directory`), `test_fix_prompt.py:73` (same string).
  - Any test asserting the refused `"."` message `project_subdir must name a subdirectory`.
- **Churn, not behaviour change:** `test_settings.py`, `test_settings_*.py`, `test_cli_wiring.py` (`:69, 205, 261, 332, 349, 383-385`), `test_clone_repo.py` write `factory.toml` at the repo root (moved by the increment 17 helper); `test_clone_repo.py:73` (the reserved name `factory.toml`).
- **Unaffected:** `test_worktrees.py:81`, `test_server_merge.py:183, 198` (`main_branch in factory.toml` message; wording kept unless the README text moves), `test_init_config.py` (global config only).
- **Not yet checked:** eval-audit tests (corpus under `target/`), the web tests that render prompts. Increment 22 will show them; any other passing test to be edited is reported before it is touched.

## Risks and unverified
- Unverified: whether git normalises `:(glob)./**/x` (use `**/glob` for the root), and whether `literal` combines with `icase` in a pathspec.
- Unverified: whether `uv --no-config` ignores a root `.python-version`; `discover_uv_tooling` runs the real uv unsandboxed in the project dir before the tamper checks. The same exposure exists today for `target/.python-version`; not fixed here.
- Case-insensitive file systems: `.FACTORY/` committed on Linux becomes `.factory` on macOS; covered by the `icase` checks (increments 6, 7) once the combination is verified.
- Not verified how mise trust and Claude Code project hooks treat changed root files after merge; the preview flag (19) only warns.
- Existing users: tasks and state under an old in-repo `.factory/` need the migration message from increment 17.
- The suite's current state is assumed green (last known 3336 passed, plan 85).

## Out of scope
- Protecting `.git` or `.factory` in the sandbox (impossible with Landlock, spike above).
- The `.python-version` exposure (above), the `watch` retry cap (plan 86).

## Deviations
(none yet)
