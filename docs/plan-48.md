# Plan 48: retry from GitHub with a 👍

Draft, not yet reviewed.

## Goal

With `watch_retry = true`, a task that ended `failed` gets a retry offer on its issue. An approver's 👍 runs a plain `retry`: a fresh task from the same goal. It plans and reviews again, and the watcher then follows it on the same issue. The new plan needs its own 👍, so a human still approves every plan.

## Decided (by the user)

- Plain retry only. `retry --keep-plan` stays terminal-only (it needs a TTY to confirm the plan).
- New per-repo flag `watch_retry`, default false (not `watch_merge`, not always on).
- Failed tasks only. `Phase.STOPPED` and audited tasks are unchanged.

## Behaviour

- Flag off: nothing changes. The failure comment and `Phase.FAILED` stay as they are.
- Flag on, task `failed`: the watcher posts the existing failure comment, then a `retry-offer` comment (task id, the failure reason in a code fence, "react 👍 to retry, 👎 to leave it"). Phase `AWAITING_RETRY`, `retry_comment_id` saved.
- Approver 👍 (numeric id, never login): `POST /tasks/<id>/retry` (answers 201 `{id}`, not a job). The issue record follows the new task: new `task_id`, phase `NEW`, every comment id and hash reset. The next pass then runs the usual flow: questions, plan comment, 👍.
- Approver 👎 (wins over 👍): post `not-retried`, phase `NOT_MERGED` (the existing ending phase), nothing retried.
- Server refuses (409 `retry refused`): post `retry-refused` with the reason, phase `STOPPED`; finish at the terminal.
- No edit check on the offer comment: the 👍 binds only to the task id, which the comment cannot change.

## Known limits (not fixed)

- A retry at the terminal does not mark the old task, so the offer can still appear and a 👍 would start a second retry. Detecting it needs `retry_of` on the task list or task JSON (neither exposes it today, unverified for `GET /tasks/<id>`). Cost: one extra `proposed` task, no agent spend until advanced.
- A crash between the server's 201 and `save_state` can retry twice the same way.
- Trust: a compromised approver account can now trigger plan and review spend by 👍. It still cannot approve the new plan without a second 👍 on a new comment, but the same account can give it. Add to the README Trust bullet.
- `abandon`, `recover`, `clean`, waiving and `--keep-plan` stay terminal-only.

## Files

`settings.py`, `cli.py:702`, `watch.py`, README (command table `watch` row, "Watching issues" bullets, "Not covered" line, config block, Trust bullet), `docs/spike-findings.md`. No server change.

## Tests touching exact strings

Flag-off tests that must stay green unchanged: `tests/test_watch.py` around :578-640 (failed and stopped phases), :1152-1158, :1211-1218, :1452-1463 (failure comment text, home stripped). The new comment kinds are `retry-offer`, `not-retried`, `retry-refused`. Grep `tests/test_watch*.py` for those names before writing.

## Cycles (one test, one implementation, one commit each)

1. Red: `Settings.watch_retry` is false by default and read from `factory.toml` (bool key). Green: field and `_BOOL_KEYS`.
2. Red: `watch_retry` in the global config is an error. Green: `_PER_REPO_KEYS`.
3. Red: `watch` passes `retry=ctx.settings.watch_retry` to the pass (mirror the `watch_merge` test, `test_cli_watch.py:78`). Green: `cli.py:702`, `run_pass(..., retry=False)`, `_handle_issue(..., retry)`.
4. Red: flag on and task failed, so the failure comment and then a `retry-offer` comment are posted, phase `AWAITING_RETRY`, `retry_comment_id` saved. Green: `Phase.AWAITING_RETRY`, `IssueRecord.retry_comment_id`, `_post_retry_offer`, and an `AWAITING_RETRY` branch before the generic `failed` branch in `_handle_issue`.
5. Guard (not red): flag off and task failed behaves as today (covered by the existing tests; add one that asserts no `retry-offer`).
6. Red: approver 👍 posts `/tasks/<id>/retry` and the record follows the new task, phase `NEW`, ids reset, saved before any other call. Green: `_retry_on_thumbs_up`.
7. Red: approver 👎 posts `not-retried`, phase `NOT_MERGED`, no retry call; 👎 wins over 👍. Green.
8. Guard: a 👍 from a non-approver is ignored (reuses `_approver_reacted`).
9. Red: server refusal posts `retry-refused` with the reason (home stripped) and phase `STOPPED`. Green.
10. Docs, then the full suite in the background.

Afterwards: `verify`, then `/audit-commits` against this plan.

## Risks to check in review

- Resetting the record on 👍 must not leave a stale `result_comment_id` (`_result_comment_id` reuses own comments by kind and would edit the old failure comment instead of posting new ones).
- `AWAITING_RETRY` needs adding to the phase tuple at `watch.py:566` and to any other phase switch.
- The failed-task branch at `watch.py:569` runs for every active phase; the new branch must come first.
- Nothing is run against real GitHub.
