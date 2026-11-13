# Plan 38: `factory watch`

Goal: a command that watches a cloned repo's GitHub issues. A new issue by an approver becomes a task and is advanced to a reviewed plan; the task ID and plan are posted back on the issue. An approver's 👍 on the plan comment approves it and advances the task; 👎 rejects it. Every advance's output is posted to the issue. Revised after review (plan-reviewer, 2026-11-13).

## Decisions (agreed)
- Approvers: allowlist of GitHub logins, `watch_approvers` in `factory.toml`. Only issues opened by an approver create tasks.
- Approve = `+1` reaction by an approver on the plan comment the factory posted. Reject = `-1` there. If both are present, 👎 wins.
- Transport: `gh api` via subprocess, polling loop. Merge stays human (`factory merge`).
- Required review notes: approve with them kept (`waive: []`), exactly like `factory approve` answering `y`; the audit enforces them. Waiving stays terminal-only. The comment lists the required notes.
- Public repos: refused unless `--allow-public` (plans, failure reasons and worktree paths would become public).
- `watch_approvers` is repo-only (`_PER_REPO_KEYS`), so a global config cannot grant approval across projects.
- The loop is serial: one long advance delays a 👍 on another issue. Documented, accepted.

## Premises checked against the code (by review; line numbers as of today)
- The server's approve has no TTY check; only `cli.py:325` does. This is a deliberate, documented weakening of "human-only by process", limited to the allowlist.
- The server passes `task.review_findings` as the shown findings itself (`server.py:196`), so the findings check never fails over HTTP; the digest covers the plan text only. The watcher therefore also records a hash of the findings it posted and compares it with `/approval`. (Closed since: `/approve` takes an optional `review_findings`, which the watcher sends from the `/approval` it checked, so a change in between is a 409.)
- `create_task` takes only `goal` (non-empty, no length cap).
- A job result is `{steps, state, failure_reason, stopped}`. Advance stops at REVIEWED and AUDITED. A failed plan or review ends `failed` with `stopped=None`. A job can end `failed` or `interrupted` (`_run_job` returns None).
- `serve` holds an exclusive flock on `<state_dir>/lock`; `state_lock(dir)` always uses a file named `lock`, so the watcher needs a lock-name parameter (`state_lock(state_dir, name="watch")`).
- `_STRING_LIST_KEYS` must be non-empty, so `watch_approvers = []` is a settings error (exit 2); only a missing key reaches the watcher's own exit 1. Verify in increment 2.
- `parse_repository` returns `(url, name)` with no owner; `clone_repo.py` imports `settings.harness_home`, so the slug lookup cannot live in `settings.py`.
- Grep of `tests/` for README text, `_KNOWN` and unknown-key messages: none exist. The only existing test to edit is `test_subprocess_sites.py`.

## Spike findings (gh 2.104.0, 2026-11-13)
- Read side, verified on a public repo: the issues list includes PRs (`pull_request` key); reactions return `content` (`+1`, `-1`, ...) and `user.login`; `--paginate` returns one merged JSON array (6 items with `per_page=2`); `-f` is raw, `-F` converts types, so bodies use `-f`.
- Write side, verified on private `jshiell/factory-scrap`: posting a comment returns an integer `.id` and echoes `.body`; a body starting with `@path` is literal; the HTML marker survives; one user can hold both `+1` and `-1`.
- `since` filters by updated time, not creation. Newness = number not in `watch.json` and `created_at >= watching_since`.
- Still unverified: that reactions don't change an issue's `updated_at`; whether the nono profile lets an agent read the `gh` token (see "Before going live").

## Design
State, `<state_dir>/watch.json` (atomic replace): `watching_since`, `cursor` (largest `updated_at` seen, minus a margin), and per issue: task id, plan comment id, posted digest, plan-comment body hash, findings hash, phase.
Phases: `new`, `awaiting_approval`, `approved`, `audited`, `failed`, `stopped`, `rejected`, `changed`. Each is final except `new`, `awaiting_approval` and `approved`.

Startup (all exit 1 with a message): `watch_approvers` missing; `gh` missing (`FileNotFoundError`) or not logged in (`gh api user` fails, which also gives the watcher's own login and numeric id); repo private flag (`gh api repos/o/r` `.private`) false without `--allow-public`; second watcher (`state_lock(state_dir, name="watch")`). Approver logins are resolved to numeric user ids once and compared by id; a deleted user (`ghost`) fails safe.

Each pass, per issue: **decide from the server's task state** (GET `/tasks/{id}`), not just the local phase.
- No task yet (new approver issue): POST `/tasks` (goal = title + body, escaped and capped), record the mapping before any comment.
- PROPOSED/PLANNED: run advance; REVIEWED then posts the plan comment from GET `/approval`.
- Advance result is classified by `state` plus `stopped`: `failed` or `stopped` posts the reason once and goes final; a job that ends `failed` or `interrupted` changes nothing and is retried next pass.
- REVIEWED + plan comment: re-fetch the comment and compare its body hash and the findings hash. If it differs, or the server answers 409 "plan changed", post once and go to `changed`. Otherwise read reactions: approver 👎 rejects via `/reject`; approver 👍 approves via `/approve` with the posted digest and `waive: []`, writes `approved` before advancing.
- QUEUED or later (including a task the human approved at the terminal): advance, post steps, final state and "merge with `factory merge <id>`". FAILED, AUDITED and MERGED are final.
- A GitHub failure or a server outage/unexpected refusal on one issue logs (escaped) and leaves it for the next pass; it never stops the pass.
- `--interval` sleeps after each pass finishes.

Comments: every watcher comment carries `<!-- factory:task=<id>:kind=plan|result|rejected|changed -->` outside the fence. Before any re-post the watcher searches for its marker, adopting only comments by its own account id on that issue. Plan, findings, warning (`/approval`'s dropped-required-notes warning) and output go inside a code fence longer than any backtick run in the text, capped at 60,000 characters (limit 65,536) before fencing, with a note where cut. So `@mentions`, `#N`, images and fake markers in the text are inert. Failure reasons have the home path stripped.

## Files
- `clone_repo.py`: `github_slug(spec) -> (owner, name)`; `parse_repository` refactored onto it.
- `lock.py`: `state_lock(dir, name="lock")`.
- `settings.py`: `watch_approvers` (casefolded tuple, login-format check), in `_PER_REPO_KEYS`.
- New `github.py`: `GitHub(run, owner, name)`: `self_user`, `repo_is_private`, `open_issues`, `comments`, `comment`, `reactions`, `origin_slug()` (`git remote get-url origin`). Argv lists, `stdin=DEVNULL`, owner/name validated, ids integers.
- New `watch.py`: state file, pass logic, comment rendering.
- `cli.py`: `watch` parser (`--interval`, `--once`, `--allow-public`); `Context` gains a GitHub factory and a sleep.
- `tests/test_git_hardening.py`: add `github.py` to the exclusion tuple (`origin_slug` runs `git remote get-url` in the user's own clone, not a worktree; same precedent as `clone_repo.py`, ea2a892).
- `tests/test_subprocess_sites.py`: `github.py` entry ("`gh api` and `git remote get-url` for the repo's own issues; no worktree, no agent code").
- README: command row, the approval caveat, the public-repo note, `watch_approvers`.

## Before going live (not blocking increments)
Spike, run by you: can an agent under the nono profile read `~/.config/gh/hosts.yml` or the keyring, and can `gh` authenticate under nono at all? If an implementer agent can reach the token it could 👍 its own plan. Do this before running `watch` against a real repo.

## Increments (one test, one impl, one commit each)
Tests use a fake `gh` runner, a fake server client and an injected sleep; any nonce is a distinct-value iterator (AGENTS.md). A captured real two-page `--paginate` response is a fixture.
1. Red: `github_slug` gives `(owner, name)` for the forms `parse_repository` accepts. Refactor `parse_repository` onto it (existing tests stay green). Guard: a local or other-host origin is refused.
2. Red: `watch_approvers` loads as a casefolded tuple; a bad login is a settings error; repo-only. Guard: existing unknown-key tests.
3. Red: `GitHub.comment` builds the argv (`-f body=`, `stdin=DEVNULL`) and returns the integer id.
4. Red: `GitHub.reactions` and `GitHub.comments` parse the paginated fixture.
5. Red: `GitHub.self_user` returns login and id; `GitHub.repo_is_private`; a bad owner, name or id is refused before anything runs. Update `test_subprocess_sites.py`.
6. Red: `parse_issues` skips PRs, closed issues, non-approvers (by id) and issues created before `watching_since`.
7. Red: `watch.json` round trip with atomic replace. Red: first run records `watching_since`.
8. Red: `state_lock(dir, name)` lets `watch` coexist with the server's `lock`; a second `watch` is refused.
9. Red: `factory watch` parser and `--once` wiring through `Context`; missing `watch_approvers` exits 1.
10. Red: `gh` missing or not logged in exits 1. Red: a public repo exits 1 without `--allow-public`.
11. Red: a new approver issue creates a task (goal escaped, capped) and records the mapping before any comment. Guard: a non-approver issue creates nothing.
12. Red: comment rendering fences plan, findings and warning, caps the length, puts the kind marker outside the fence. Red: `@user` and a fake marker inside the plan stay inside the fence.
13. Red: a REVIEWED result posts the plan comment from `/approval` and records comment id, digest and hashes.
14. Red: a failed plan or review (`failed`, `stopped=None`) posts the reason once and goes `failed`; `stopped` goes `stopped`. Red: a job `failed`/`interrupted` leaves the phase unchanged.
15. Red: an approver 👍 (by id) approves with `waive: []` and the posted digest and writes `approved`. Guard: a 👍 from a non-approver does nothing.
16. Red: an `approved` issue advances and posts steps, state and the merge hint; phase from the result (`audited`, `failed`, `stopped`).
17. Red: a 409 "plan changed", or a changed comment body or findings hash, posts once and goes `changed`. Guard: a second pass posts nothing.
18. Red: an approver 👎 rejects and posts once. Guards: a 👎 from a non-approver does nothing; 👍 plus 👎 rejects.
19. Red: the server task state wins over the local phase (QUEUED while `awaiting_approval` advances; FAILED goes final). Guard: a task the human approved at the terminal is not approved twice.
20. Red: on restart an issue missing its comment record adopts a marker comment by the watcher's own id; a marker comment by anyone else is ignored and a fresh one posted. Same for result comments.
21. Red: a GitHub failure keeps the phase and logs escaped stderr. Red: a server outage or unexpected refusal on one issue does not stop the pass.
22. Red: the loop sleeps `--interval` after each pass (injected sleep, stops after N passes).
23. README. Full suite in the background with Monitor; `verify`.

## Out of scope
Auto-merge, webhooks, label gating, reactions other than 👍 and 👎, multi-repo watching, re-planning on issue edits, waiving notes from GitHub. Accepted risk, documented: a write-access collaborator can edit an issue body before the first poll.
