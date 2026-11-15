# Plan 43: merge from GitHub with a 👍

Goal: with `factory watch` running, an approver lands an audited task by reacting 👍 to a merge comment on the issue, so a task can go issue to `main` without a terminal. Reviewed by `plan-reviewer`; its findings are folded in. Awaiting approval to implement.

## Decisions
1. **Gesture: 👍 on a merge comment**, same pattern as plan approval. Agreed.
2. **Scope: merge only.** `retry`, `rebase`, `abandon`, `recover`, `clean` and waiving review notes stay terminal-only. Agreed.
3. **A1, opt-in and per-repo.** New bool `watch_merge`, default false, repo-only (like `watch_approvers`; an error in the global file, since a global `true` would grant merge-by-reaction to every repo). Taken as the recommendation when the user said "fold"; say if you want always-on instead (it would rewrite at least five existing tests).
4. **B1, 👎 declines.** Post "not merged" and stop; the task stays `audited` and can still be merged at the terminal. Abandon is not triggered from GitHub (outside scope).

## Behaviour (merge on)
- After an approved task audits, the watcher posts the result comment (steps, state, `factory merge <id>` hint kept as the terminal fallback) and a **merge comment** (kind `merge`): audited sha, branch, diffstat, audit findings, code-fenced, "React 👍 to merge, 👎 to leave it unmerged". Content from `GET /tasks/{id}/merge-preview`. Both comments are posted, then **one save**: phase `AWAITING_MERGE`, `merge_comment_id`, `merge_body_hash`, `merge_sha`.
- Every ending has its **own comment kind** (`_post_result` adopts the watcher's earlier comment of the same task and kind, so reusing `result` would silently adopt the audited comment and post nothing): `merge`, `merged`, `merge-refused`, `not-merged`, `merge-changed`.
- Routing of a record by phase, before the existing `audited`/`merged` shortcut in `_handle_issue`:
  - `AWAITING_MERGE` + server `audited`: read reactions on `merge_comment_id` only (never the plan or result comment).
  - `AWAITING_MERGE` + `merged` (merged at the terminal, or a save lost after our merge): post a `merged` comment (adopted by marker if present), phase `MERGED`.
  - `AWAITING_MERGE` + `agent_done` (a terminal `rebase`): back to `APPROVED`; the re-audit posts a fresh merge comment and updates `merge_comment_id`.
  - `AWAITING_MERGE` + `failed`: the existing failure path.
  - `APPROVED` + server `audited` (crash after advance, or a failed result post): post the result and merge comments, each adopted if already present, then `AWAITING_MERGE`.
- An approver's 👍 (numeric id, never login; 👎 wins; a non-approver's reaction fetches nothing):
  1. Merge comment body hash unchanged and a fresh preview's `audited_sha` equals `merge_sha`; otherwise `merge-changed`, terminal.
  2. A preview `ServerRefused` (for example `main` moved) posts `merge-refused` with the reason and `factory rebase <id>` advice, terminal.
  3. `POST /tasks/{id}/merge {audited_sha: merge_sha}`, then wait with a dedicated `_merge` helper. The merge job result is `{state, failure_reason}`, with no `steps` or `stopped`, so `_advance` is not reused.
  4. Job `succeeded`: `merged` comment ("commits are unsigned: sign them before pushing"), `MERGED`.
  5. Job `failed` (real refusals such as `main` moved, dirty tree, non-ff arrive this way, not as HTTP 409): re-GET the task. `merged` means success (duplicate job or a race with a terminal merge). Otherwise post the job's `error` as `merge-refused`, terminal. Job `interrupted`: stay `AWAITING_MERGE`, retry next pass (safe: `merge_task._already_fast_forwarded`).
  6. HTTP 409 on enqueue (stale sha) is handled as 5's refusal.
- 👎: `not-merged`, new terminal phase `NOT_MERGED`, no merge request.
- Records saved as `AUDITED` by an older version get no retroactive merge comment. With merge off, behaviour is today's.
- After `merge-changed`, `merge-refused` or `not-merged` the task stays `audited`; GitHub has no way back in, only the terminal (README says so).

## Design
- `comments.KINDS` gains the five kinds. `Phase` gains `AWAITING_MERGE`, `MERGED`, `NOT_MERGED`, `MERGE_STOPPED` (for `merge-refused`/`merge-changed`). `IssueRecord` gains `merge_comment_id`, `merge_body_hash`, `merge_sha`, all `None` by default.
- Older build reading the new record fields raises `TypeError` (only `ValueError` is wrapped in `WatchStateError`); an older build reading a new phase value raises `WatchStateError`. The README says so.
- `run_pass` gets `merge: bool = False`; `cli._watch_locked` passes `ctx.settings.watch_merge`. `watch_merge` is added to the bool keys and the per-repo keys in `settings.py`.
- Shared approver-reaction check extracted in its own refactor commit, not inside a Red cycle.

## Tests affected (listed up front; no assertion changes with merge off)
`tests/test_watch.py` (line numbers approximate):
- ~99 save/load round-trip: extended with the new fields and phases.
- ~703 `test_an_approved_task_that_audits_gets_one_result_comment_...`, ~946 (`..._human_approved_at_the_terminal_...`), ~750 (`test_guard_a_failed_audited_post_...`), ~990-1002 (parametrized `audited`/`merged`), ~1077-1095 (`ENDINGS["audited"]` adoption), ~1116 (terminal-phase list): all stay unchanged and green with merge off; merge-on variants are new tests. The `merged` row at ~990 and the terminal-phase list gain new entries.
- Fakes: `FakeGitHub.comment` returns incrementing ids from 1 (existing single-post tests keep `result_comment_id == 1`); `FakeServer.post`/`get`/`wait` dispatch by path and job kind (today `post` asserts on unknown paths, `wait` always returns `advance_result`, and `get` of `/merge-preview` returns the task dict).
- `tests/test_cli_watch.py`: the fake `watch_pass(github, server, state_dir, approver_ids)` (~line 40) and the exact 4-tuple asserted at ~70 change to carry `merge`. Said so in that commit.
- Settings tests (file name unverified, probably `tests/test_settings.py`). `tests/test_comments.py` does not test `KINDS`.

## Cycles (one test, one implementation, one commit each)
0. Test support only, no assertion changes: incrementing comment ids; fake server dispatch by path and job kind.
1. Red: `watch_merge` setting (bool, default false, repo-only, refused in the global file).
2. Red: `watch` passes `merge=` to the pass (fake and tuple updated). Guard: `merge=False` leaves the existing tests unchanged.
3. Red: with merge on, an approved task that audits posts the result comment then the merge comment, one save to `AWAITING_MERGE` with id, hash, sha.
4. Red: an `AWAITING_MERGE` record with server state `audited` is neither advanced nor collapsed to `AUDITED`.
5. Red: a preview 409 at posting time posts `merge-refused` even though the audited `result` comment is already on the issue; terminal.
6. Red: approver 👍 on an unedited comment with the same sha posts `/merge {merge_sha}`, waits, posts `merged`; `MERGED`.
7. Guard: a non-approver's 👍, or a 👍 on the plan or result comment, does nothing and fetches nothing (cycle 6's id filter and comment id cover it).
8. Red: edited merge comment, or a different preview sha: `merge-changed`, no merge request.
9. Red: a preview 409 at 👍 time: `merge-refused` with rebase advice.
10. Red: merge job `failed`, task still `audited`: `merge-refused` with the job's error. Guard: an `interrupted` job leaves `AWAITING_MERGE`.
11. Red: merge job `failed` but the task is `merged`: `merged` comment.
12. Red: `AWAITING_MERGE` record, server `merged`: `merged` comment adopted by marker (merged at the terminal, or a lost save).
13. Red: `APPROVED` record, server `audited`: result and merge comments each adopted if present, then `AWAITING_MERGE` (replaces the old "guard" cycle 9).
14. Red: approver 👎 (wins over 👍): `not-merged`, `NOT_MERGED`, no merge request.
15. Red: `AWAITING_MERGE` record, server `agent_done` (terminal rebase): `APPROVED`; after re-audit a new merge comment, `merge_comment_id` updated.
16. Refactor: extract the shared approver-reaction check (behaviour unchanged).
17. Docs: README "Watching issues" (merge section, Trust, Serial, Not covered, no-downgrade, no way back from GitHub after a stop); the `watch` row in Commands ("with `factory merge <id>` for the human step"); "How agents are kept in check: Humans hold the two irreversible steps" (says both refuse without a TTY and `merge` stays terminal-only); intro lines 5 and 9; the `watch_merge` config line; `docs/spike-findings.md` entry. Then `verify` (full suite in the background, about 100s).

## Risks
- **Trust.** A compromised approver account can now put code on `main`. Mitigations: audit and gate already ran; the sha binding is enforced by the server at enqueue and in the job; fast-forward only; opt-in per repo. The body hash only guards edits to the shown diffstat.
- **Main checkout.** The merge fast-forwards the server's checkout of `main`, which may be the user's working copy. Tracked changes are refused; colliding untracked files fail the ff and arrive as a refusal. Stated in the README.
- **Serial pass.** `server.wait` has no timeout and the merge job queues behind any terminal job on the single worker, so a pass can stall for minutes (already true of advance). Noted in the README's "Serial" bullet.
- **Races with a terminal `factory merge`.** The worker serialises them; the loser fails with "merge refused: merged not allowed from merged", which cycle 11 turns into a `merged` comment.
- **Unverified:** whether a write-access collaborator can edit the bot's comments (the hash check guards either way); how `cli._run_job` reports a failed job (does not affect the watcher).

## Out of scope
Auto-merge, other reactions, `/merge` comment commands, PR-based merging, failure recovery or abandon from GitHub, several repos, reviving a stopped task from GitHub.
