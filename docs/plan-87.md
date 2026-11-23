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
- Verified (spike, git 2.53, see Deviations): `:(glob)./**/x` matches root files, and `literal` combines with `icase`.
- Unverified: whether `uv --no-config` ignores a root `.python-version`; `discover_uv_tooling` runs the real uv unsandboxed in the project dir before the tamper checks. The same exposure exists today for `target/.python-version`; not fixed here.
- Case-insensitive file systems: `.FACTORY/` committed on Linux becomes `.factory` on macOS; covered by the `icase` checks (increments 6, 7); checked through git's `icase` on Linux only, not on a real case-insensitive file system.
- Not verified how mise trust and Claude Code project hooks treat changed root files after merge; the preview flag (19) only warns.
- Accepted (user, 2026-11-23): files outside the project are editable, so a root `sitecustomize.py` (or any module on the path the test command uses) can change what the tests run. No deny list is added.
- The nested-repo walk now covers the whole worktree; a large untracked tree outside the project slows it. A nested `.GIT` on a case-insensitive file system is not flagged (as before).
- Existing users: tasks and state under an old in-repo `.factory/` need the migration message from increment 17.
- The suite's current state is assumed green (last known 3336 passed, plan 85).

## Out of scope
- Protecting `.git` or `.factory` in the sandbox (impossible with Landlock, spike above).
- The `.python-version` exposure (above), the `watch` retry cap (plan 86).

## Deviations
- Increments 1-5: the expected gitdir needs the main repo, which `verify_task`, `run_task`, `run_readonly_stage` and `audit_task` did not have. Each takes an optional `repo`: with it, git is pinned and the `.git` file verified; without it, behaviour is unchanged. `merge_preview` already had `repo` and is pinned always.
- Extra cycle 1b: the fix round's `rev-parse`/`status` after the fixer are pinned too (they read a rewritten `.git` before the next gate).
- Increment 5 was red, not a guard: the audit diff ran before the read-only stage, so it is now pinned and a mismatch fails `worktree_mismatch`.
- Only the gate's commit is pinned inside the gate; `clean_worktree` and `check_gate` run right after the `.git` check with nothing writing in between (reasoning, not a test).
- The merge refusal uses a fixed sentence (the mismatch message quotes agent-written text); a missing worktree still raises `WorktreeMissing` (an `OSError`), as before.
- `stages.py` passes `repo=settings.repo` to every stage (plan, review, implement, verify, audit); `plan_task` and `review_plan` gained the parameter. `repo` is now required (`Path | None`, no default) on `plan_task`, `review_plan`, `run_task`, `verify_task`, `audit_task` and `run_readonly_stage` (and the gate and fix-round helpers), guarded by a signature test. Unit tests on the main checkout pass `repo=None`.
- Approved test edits beyond the plan: the `test_stages.py` shared `save` helper now makes a real linked worktree (covers the read-only stage cases, the plan round-cap test and the plugin-dirs test), and two `test_cli_wiring.py` gate tests run in a linked worktree. No assertion changed.
- `audit_eval.run_one` passes `repo=None` explicitly: the eval audit stays unpinned (user decision).
- Phase B order: 6, 7, 8, 11 (tests half, in two cycles: the base and the fix-round comparison), 12, 13 (the `.factory` refusal and the nested-repo walk), 14, then 9, 10, 11 (the `pyproject.toml` half) and 13 (accepting the root) after the user approved their test edits.
- 6-8 and 12 fail with the existing reason `outside_project`, now read as "outside the editable area", so the existing link tests stay green. The denied names are fixed pathspecs, not part of the overridable `protected_names`, and `allow_outside` cannot lift them. 6-8 are checked before `no_changes`; links keep their old place, after the tamper checks.
- 13: the `.factory` refusal lives in `_resolve_inside` (the first resolved path part, casefolded, so a symlink into `.factory` is refused too) and raises `ProjectSubdirEscapes`. `has_nested_repo` walks the whole worktree and skips the root `.git` without reading it: the caller's `worktree_git` verified it just before (increment 1). It still refuses an escaping `project_subdir` before anything is committed.
- 14: the pathspec is `:/` plus `:(exclude,top,literal,icase).factory`; `:(top).` matches nothing. `audit_task` keeps its now unused `project_subdir` parameter (dropping it edits passing tests); remove it with increment 20.
- Spike (git 2.53): `:(glob)./**/x` and `:(glob)**/x` both match a root file and deeper ones; `literal` combines with `icase` and with `exclude,top`; `literal` and `glob` are refused together; `diff --raw` gives the new mode (120000, 160000) for added and type-changed links.
- 9: `_changed_outside_project` is gone; `outside_project` now comes only from the fixed names and links. Approved test edits: the outside-project guards in `test_gate.py` assert a pass (renamed from "fails"/"refuses" to "passes"/"allows"); in `test_verify_fix_loop.py` an outside change verifies the task, and a fixer's outside edit is gated by the tests (the second round finds nothing to change: `fix_no_changes`).
- 10: the `no_changes` pathspec is `:/`. `check_gate` keeps `allow_outside` but no longer uses it (removed in increment 20). Approved edits: the three `no_changes` tests (:72, :813, :1011) assert a pass, a pass, and `tests_modified` for a root `pytest.ini`.
- 11: `_reaches_project` is gone. The sibling pytest-table test now asserts `tests_modified` (renamed to "fails"); a new test covers a dependency added to a sibling path dependency.
- 13: the root is accepted by `_resolve_inside`; `.` was removed from the escape-rejection test, and new tests cover the tests running at the root and `has_nested_repo(repo, ".")`. Settings still refuse `project_subdir = "."` until increment 16.
