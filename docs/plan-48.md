# Plan 48: retry from GitHub with a 👍

Reviewed by the opus plan-reviewer; findings folded in.

## Goal

With `watch_retry = true`, a task that ended `failed` gets a retry offer on its issue. An approver's 👍 runs a plain `retry`: a fresh task from the same goal. It plans and reviews again, and the watcher then follows it on the same issue. The new plan needs its own 👍, so a human still approves every plan.

## Decided (by the user)

- Plain retry only. `retry --keep-plan` stays terminal-only (it needs a TTY to confirm the plan).
- New per-repo flag `watch_retry`, default false (not `watch_merge`, not always on).
- Failed tasks only. `Phase.STOPPED` and audited tasks are unchanged.
- Every failed task gets the offer, including one a human stopped at the terminal (plan rejected, abandoned). The failure reason is in the comment.
- A 👎 ends in `Phase.NOT_MERGED` (the rebase-decline precedent); no new phase.
- The double-retry gap is accepted (no server change).

## Behaviour

- Flag off: nothing changes. The failure comment and `Phase.FAILED` stay as they are.
- Flag on, task `failed` (reached from either path: the advance result at `watch.py:612`, or the server already reporting `failed` at `:569`): one helper `_post_failure` adopts-or-posts the failure comment, then adopts-or-posts a `retry-offer` comment (task id, failure reason in a code fence with home stripped, "react 👍 to retry, 👎 to leave it", and that the original goal is reused, not the issue's current text). It then saves once: `result_comment_id`, `retry_comment_id`, phase `AWAITING_RETRY`. If the second post fails, the phase stays active, and the next pass adopts both comments (nothing is posted twice). The offer is adopted with `_own_comment_id` by an exact-body match, as `_post_merge_moved` does.
- Approver 👍 (numeric id, never login): `POST /tasks/<id>/retry` (201 `{id}`, not a job). The issue record is replaced by `IssueRecord(task_id=<new id>)` (phase `NEW`) and saved before any other call, and the pass returns without advancing the new task. The next pass runs the usual flow: questions, plan comment, 👍.
- Approver 👎 (wins over 👍): post `not-retried`, phase `NOT_MERGED`, no retry call.
- Server refuses (409 `retry refused`): post `retry-refused` with the reason (home stripped), phase `STOPPED`; finish at the terminal. This is nearly unreachable (`retry_task` refuses only a task that is not failed or audited, or has no goal), so it is a cheap guard.
- Flag off with a record already in `AWAITING_RETRY` (the flag was turned off meanwhile): a 👍 does nothing. The record falls through to the failure branch, adopts the existing failure comment and ends `FAILED`, with no new comment.
- `AWAITING_RETRY` can key on the phase alone: `failed` has no way out, and a retry at the terminal leaves the old task failed.
- No edit check on the offer comment: the 👍 binds only to the task id, which the comment cannot change.
- Tasks that failed before the flag was turned on stay in `FAILED` and get no offer (as with `watch_merge`).

## Known limits (not fixed)

- A retry at the terminal does not mark the old task, so the offer can still appear and a 👍 would start a second retry. `GET /tasks/<id>` does expose `retry_of`, but it points from child to parent; `GET /tasks` (the list) has no `retry_of`. Closing it needs `retry_of` in the list JSON (one line in `server.py:150`). Cost of the gap: one extra `proposed` task, no agent spend until advanced.
- A crash between the server's 201 and `save_state` can retry twice the same way.
- A 404 (task deleted) or a deleted offer comment makes every pass log and retry, as the rebase flow does.
- Trust: a compromised approver account can now trigger plan and review spend by 👍. It still cannot approve the new plan without a second 👍 on a new comment, but the same account can give it. Add to the README Trust bullet.
- `abandon`, `recover`, `clean`, waiving and `--keep-plan` stay terminal-only.

## Files

`settings.py`, `cli.py:702`, `watch.py`, README (command table `watch` row, "Watching issues" bullets, "Not covered" line, config block, Trust bullet, the downgrade note at `README.md:110`), `docs/spike-findings.md`; tests in `test_settings.py`, `test_cli_watch.py`, `test_watch.py`. No server change.

## Tests touching exact strings

Grep done: `retry-offer`, `not-retried`, `retry-refused`, `awaiting_retry`, `watch_retry` have no hits in `tests/` or `src/`. Existing flag-off tests that must stay green unchanged (they assert one comment and `Phase.FAILED`): `tests/test_watch.py` around :578-640, :1152-1158, :1211-1218, :1452-1463.

Test infrastructure changes (not changes to passing tests): the `watch_pass` fake in `test_cli_watch.py` (:42) gains `retry=False` and records it (as for `merge`); `FakeServer.post` in `test_watch.py` (:228 asserts `path == "/tasks"`) handles `/retry` and returns `next(self.ids)`; `pass_over` (:258) gains `retry=`. `FakeServer.get` returns the same state for every task id, so after a retry the new task also looks `failed`: assert at the end of the 👍 pass and do not run a second pass by accident.

## Cycles (one test, one implementation, one commit each)

1. Red: `Settings.watch_retry` is false by default and read from `factory.toml` as a bool. Green: field and `_BOOL_KEYS`.
2. Red: `watch_retry` in the global config is an error. Green: `_PER_REPO_KEYS`.
3. Red: `watch` passes `retry=ctx.settings.watch_retry` (parametrised like `test_cli_watch.py:76-82`). Same commit, because the real `run_pass` must accept it or production raises a `TypeError` (`Context.watch_pass` defaults to `run_pass`): the fixture fake takes `retry`; `cli.py:702`; `run_pass(..., retry=False)` passes it to `_handle_issue`.
4. Red: flag on and the advance result is failed (`:612` path): the failure comment, then a `retry-offer` comment; the record is `AWAITING_RETRY` with both comment ids. Green: `Phase.AWAITING_RETRY`, `IssueRecord.retry_comment_id`, and `_post_failure(..., retry)` that adopts-or-posts both and saves once. Same commit: `pass_over(retry=)`.
5. Red or guard, depending on cycle 4's build: flag on and the server already reports `failed` (`:569` path, for example from `AWAITING_APPROVAL`) gives the same two comments and phase through the same helper.
6. Red: crash safety. A flaky GitHub fails the second post, the phase stays active, and the next pass posts only the offer and ends `AWAITING_RETRY`.
7. Guards: `AWAITING_RETRY` and `retry_comment_id` survive a save/load round trip (pattern at `test_watch.py:120`); a state file without `retry_comment_id` loads (pattern at :643).
8. Red: approver 👍 on the offer posts `/tasks/t-1/retry`; the record is replaced by `IssueRecord(task_id="t-2")` in phase `NEW`, saved, with no further calls in that pass. Green: `AWAITING_RETRY` in the `:566` allowlist, a phase branch before `:569` gated on `retry`, and `_retry_on_thumbs_up`. Same commit: `FakeServer` handles `/retry`.
9. Guard: no reaction, or a 👍 from a non-approver, posts nothing and makes no retry call.
10. Red: approver 👎, alone or with a 👍, posts `not-retried`, ends `NOT_MERGED`, makes no retry call.
11. Red: a 409 `retry refused` posts `retry-refused` with the reason (home stripped) and ends `STOPPED`.
12. Red: flag off with a record in `AWAITING_RETRY`: a 👍 makes no retry, the existing failure comment is adopted, the phase ends `FAILED`, and no new comment is posted.
13. Docs, then the full suite in the background with Monitor.

Afterwards: `verify`, then `/audit-commits` against this plan.

## Risks to check in implementation

- The failed-task branch at `watch.py:569` runs for every active phase; the new `AWAITING_RETRY` branch must come first and be gated on the flag.
- `AWAITING_RETRY` must be in the active-phase tuple at `:566` and in any other phase switch.
- Unverified: whether `clean` can delete task records (decides the 404 case); real GitHub behaviour (nothing is run against it).
