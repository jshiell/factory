# Plan 94: remove `factory watch`

Drafted 2026-11-27, revised after a plan review (2026-11-27). Approved and implemented (increments 1 to 6 in 417e34d..a758ab3).
Source: `todo.md` open item "remove watch".
Base: `main`.

## Problem
`factory watch` turns GitHub issues into tasks and takes approvals from reactions. The web UI now covers approving, answering, merging, rebasing and retrying, so the watcher is redundant. It is a ten-module package, a CLI command, three settings, a GitHub client and a comment renderer. Nothing else uses them.

## Decisions (settled with the user, 2026-11-27)
1. **Old `watch_*` keys** (`watch_approvers`, `watch_merge`, `watch_retry`) are refused with a dedicated message, like `gate_allow_outside` (`settings.py:274-278`): "no longer read: `factory watch` was removed; remove it", naming the file. Not silently ignored, not a bare "unknown setting". Why: `_load_repos` catches the `SettingsError` and `factory serve` then skips the whole repo, so the message has to say what to do.
2. **`docs/spike-findings.md` and `spike/`** are history, like `docs/plan-*.md`. Not edited.
3. **`review_findings` on `POST /tasks/{id}/approve`** stays as JSON API. Only watch sent it (the CLI does not); to check whether `web_actions` sends it, and record the result in Deviations.
4. **Leftovers** (`<state_dir>/watch.json`, the `watch` lock file, already-posted GitHub comments) are left alone and noted in the README. No cleanup code.

## What goes (from a read-only map, checked by a plan review; line numbers are rechecked as each increment starts)
- `src/factory/watch/` (10 files, ~980 lines).
- `src/factory/github.py` (imported only by `cli/admin.py`, `cli/base.py` and `watch/`). `GITHUB_LOGIN` in `settings.py` (used only by `github.py` and the `watch_approvers` check).
- `src/factory/comments.py` (imported only by `watch/`).
- CLI: the `watch` parser (`cli/__init__.py:114-117`), its `_COMMANDS` entry and `_watch` import, `_positive_int` (`cli/__init__.py:71-75`, used only by `watch --interval`), `_watch` and `_watch_locked` in `cli/admin.py` and its `GitHubError` import, and the `Context` fields `watch_pass`, `github`, `origin_slug` and `sleep` (`cli/base.py`) with their imports (`time`, `GitHub`, `origin_slug`, `run_pass`). Grep tests for `_positive_int`, `ctx.sleep` and `sleep=` first.
- Settings: the three fields and list entries (`settings.py:48-50, 116, 118, 260, 387-389`), `GITHUB_LOGIN` (`:119`), and the comment at `init_config.py:6`.
- Tests, by name rather than line range: `test_watch.py`, `test_cli_watch.py`, `test_progress.py`, `test_issue_thread.py`, `test_github.py`, `test_comments.py`, `fixtures/gh_issues_paginated.json`. In `test_settings.py`: the global-file rejection tests for the three keys (319-340), the `watch_approvers` default/casefold/bad-login tests (545-557), the `watch_merge` default (574-578) and the `watch_retry` default (631-635). The `main_branch` tests at 560 and 567 stay. In `test_lock.py` the named-lock test (40-47), only if increment 5 fires. The `github.py` entry in `test_subprocess_sites.py:14` (the file list is compared exactly) and the exclusion at `test_git_hardening.py:40`.

## What stays
- The server routes watch called (`/approval`, `/questions`, `/answer`, `/approve`, `/reject`, `/merge-preview`, `/merge`, `/rebase`, `/retry`, `/advance`). The CLI calls every one (`cli/tasks.py`, `cli/recovery.py`), per the plan review.
- `clone_repo.py` and its `gh repo clone` (`github_slug` lives there).
- Stripping of `GH_TOKEN`, `GITHUB_TOKEN` and the enterprise variants from agent and gate environments: defence in depth, and `clone` still uses `gh`. The runner is not touched. README lines 361-362 stay.
- `docs/plan-*.md` (39 mention watch), `docs/spike-findings.md`, `spike/`.
- The Task row format: no field is written only by watch, so old rows load unchanged.

## Increments
Deletion is not red. Each increment ends with the full gate (ruff and the suite) green and one commit. The gate is the guard for the deletions: a leftover import fails collection. Reds are only where behaviour changes.
0. **Before increment 1** (no commit): grep `orchestrator/` (src, tests, `conftest.py`), `spike/` and `docs/spike-findings.md` for `watch`, `github`, `GitHub`, `origin_slug`, `comments`, `render_comment`, `strip_home`, `GITHUB_LOGIN`, `_positive_int`, `sleep`, `gh_issues_paginated` and `Per-repo keys`. Grep `src/` for `state_lock(`. Check the files being deleted for `@pytest.mark.smoke`. Record the hits under Deviations.
1. **Remove the command (red).** Test: `main(["watch", "--once"], io, None)` returns 2 with a usage error. This covers the parser only: inside a repo whose config still has `watch_*`, the user sees the settings error first. Remove everything listed under CLI above, and delete `test_cli_watch.py`.
2. **Delete the watch package.** `src/factory/watch/`, `test_watch.py`, `test_progress.py`, `test_issue_thread.py`.
3. **Delete the leaf modules.** `comments.py`, `test_comments.py`, `github.py`, `test_github.py`, the fixture, and the two exemption entries.
4. **Settings (red).**
   - Red, per-repo file: each of the three keys is refused with the exact new message.
   - Red, global file: the same exact message. Today's global-file message says the key is per-repo, so this is red. A bare `SettingsError` assertion would already pass; the tests match the message.
   - Remove the fields, list entries, `GITHUB_LOGIN` and its validation branch, and add the refusal next to the `gate_allow_outside` one. Replace the old global-rejection tests with the new reds; delete the default and load tests listed above. Edit `init_config.py:6` after grepping tests for the template text.
5. **Conditional:** if `state_lock(` has no caller but `_watch` (the reviewer found none), drop its `name=` parameter and the `test_lock.py` named-lock test. Otherwise skip and record "skipped: used by X".
6. **Docs.**
   - README: remove "Watching issues" (182-205). Fix lines 5 (point to the web UI), 68, 70, 77 (command table), 137, 208, 210 (keep the task-row sentence about the grill fields, cut `watch.json` and `WatchStateError`), 218 (says the `watch_*` keys are per-repo and an error in the global file; now they are refused as removed), 232-234 (example config), 401-425 (the merge and approve text naming `watch_merge` and `factory watch`), 437, 449. Re-grep README for `watch` afterwards.
   - Add a note on what is left behind: `watch.json`, the `watch` lock file, posted comments, and that `serve` skips a repo whose config still sets a `watch_*` key.
   - Update smoke-test counts in the README and `AGENTS.md` if they changed.
   - Add a Deviations section to this plan, and tick `todo.md` (never commit it).
7. Run the `verify` skill, then the commit audit.

## Tests that assert strings this touches
- `test_cli_watch.py` (`"watch_approvers" in err`, `"another factory watch holds the lock; nothing changed"`): removed with the command.
- `test_settings.py`: the cases named above, including `"wrong type for setting: watch_approvers"`; the `gate_allow_outside` test at 581 is the model for the new reds.
- `test_lock.py` 40-47.
- Anything pinning README text, `--help` output or the `init_config` template: not yet found; increment 0 greps.
- `test_web_task_events.py:266` (`latest_watched`) and `test_gate.py` ("WATCHED_ADDITIONS") are unrelated false positives.

## Risks and unknowns
- **Not verified** (the review could not read these): whether `server.py`, `web/*`, `conftest.py` or other tests use `Context(github=, origin_slug=, watch_pass=, sleep=)`, `Settings(watch_*=...)`, `GitHubError` or `strip_home`; whether any test pins README, `--help` or the init template text; whether a deleted test carries `@pytest.mark.smoke`; whether `server.py` or `claim_server` calls `state_lock(name=...)`; whether `spike/` imports `factory.github`, `factory.watch` or `factory.comments` (it is outside pytest and ruff, so a break would go unnoticed, but it is history); the count of 39 plan docs.
- **Hard break for existing users.** Anyone running `factory watch` loses it at once, and a repo whose `.factory/factory.toml` still sets a `watch_*` key is skipped by `serve` until the line is removed. The refusal message is the only help; the README note repeats it.
- **No replacement** for approvals from GitHub reactions. The web UI is the only path.
- No dependency changes: `pyproject.toml` and `.github/workflows/ci.yml` do not mention watch, `gh` or GitHub.

## Deviations
- **Increment 0 greps:** the only hits outside the planned deletions were handled in increments 1 to 5 (`Context.sleep`, `_positive_int`, `GITHUB_LOGIN`, the `github.py` entries in `test_subprocess_sites.py` and `test_git_hardening.py`, the `Per-repo keys` init template line). The deleted tests carried no `smoke` marker, so no smoke counts changed.
- **Refusal message:** `<key> in <path> is no longer read: factory watch was removed; remove it`, raised in `settings._layer` before the `gate_allow_outside` check.
- **Increment 5 fired:** with watch gone nothing used a named lock, so `state_lock` lost its name parameter and uses the file `lock`.
- **`review_findings` on `/approve`:** the web layer (`web_actions.py`, `web/routes.py`) passes the findings to the service as an argument, not as a JSON body field. The JSON field stays for API clients.
