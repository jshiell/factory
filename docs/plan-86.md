# Plan 86: cap the watcher's per-issue retries

Drafted 2026-11-23, revised after plan review (`plan-reviewer`, opus). Approved.
Source: plan 85 "Out of scope" and `todo.md`. Decisions (user): stop the issue and post one comment; cap 20; a module constant, not a setting. Decisions folded from review (recommended options, accepted by "Fold"): `ServerUnreachable` neither counts nor resets; one phase-neutral next step in the stop comment; the counter lives in a separate map, not on `IssueRecord`.
Base: `main`. Branch `plan-86-watch-cap`.

## Problem
`run_pass` (`watch/__init__.py:147-153`) catches `GitHubError`, `ServerRefused` and `ServerUnreachable` per issue, logs one line and leaves the issue for the next pass. Nothing counts. An error that never clears (a deleted task, a deleted retry-offer comment, a task the server keeps refusing) is retried and logged on every pass for ever. README lines 189, 191 and 194 say failures are always retried.

## Design
- `WatchState.failed_passes: dict[int, int]` (issue number to consecutive failed passes), saved as its own top-level key in `watch.json`. `load_state` reads it with `document.get("failed_passes", {})`: it indexes the other keys directly and a `KeyError` is not caught by its `except ValueError`, so an old file would otherwise crash. Not on `IssueRecord`: about 12 existing tests compare whole records after a failing pass (`test_watch.py` 387, 760, 813, 1027, 1466, 1487, 1565, 1643, 1722, 1860, 1937, 1961) and the retry path replaces the record (`retrying.py:64`).
- A pass that raises `GitHubError` or `ServerRefused` for an issue that has a record: count + 1, save. A pass that finishes without raising resets the count (save only if it was non-zero); "finishes" includes passes that only wait for a reaction. `ServerUnreachable` means nothing is listening, so it hits every issue at once: it neither counts nor resets, and stays logged and retried as today.
- At `>= GIVE_UP_AFTER` (20; not `==`, so a count that is already at or past the cap still stops): post one `gave-up` comment, set `STOPPED`, save. `STOPPED` is skipped by `_handle_issue` (`__init__.py:173`), so later passes make no server or GitHub comment call for it.
- Register `gave-up` in `KINDS` (`comments.py:5`). Without it `marker()` raises `ValueError` (`comments.py:59-60`), which `run_pass` does not catch: the 20th failure would kill `factory watch`, and every restart.
- Where the stop runs: after the per-issue exception is caught, look up `state.issues[issue.number]` fresh (not a reference captured earlier), build the `IssueThread` and `Progress` from it, and post through `_post_result` (adopts an earlier `gave-up` comment by marker, so a crash between post and save does not double post). The whole stop, including its GitHub calls, is wrapped so nothing escapes `run_pass`.
- Comment text (render with `render_comment`): the watcher stopped retrying this issue after 20 failed passes; the last error in a code fence (`strip_home`, escaped); the watcher no longer acts on reactions to its earlier comments on this issue; next step phase-neutral: inspect the task at the terminal (`factory show <id>` or the real equivalent; the implementer reads the CLI command list in the README and reports which command exists).
- If the stop post itself fails (GitHub down): set `STOPPED`, save, log one escaped line. The stop is then silent on GitHub.
- Saving on the failure path persists any unsaved in-memory record changes (for example `_post_audited` sets `result_comment_id` before `_post_merge_comment` raises, `merging.py:31-32`). Harmless, because a result comment is re-adopted by marker. `_post_result` also overwrites `result_comment_id` (for example the audited result's id in `AWAITING_MERGE`); harmless, `STOPPED` is terminal.
- With `--interval` 60 (the README says 60; the implementer confirms `cli/__init__.py:86`), 20 passes is about 20 minutes.

## Not covered by the cap (stated in README)
- An issue with no record yet (the `POST /tasks` that creates the task fails): no task id for a comment, so it still retries every pass.
- Errors that escape `run_pass` and end the loop: `InvalidTaskId`, a non-JSON reply (`client.py:42`), a `KeyError` on a missing response field, an `OSError` from `save_state`, `WatchStateError` from `load_state`. These are crash loops, not retry loops.
- `ServerUnreachable` (see above).
- A stopped issue never resumes. Deleting its record makes the watcher create a new task for the issue (`__init__.py:167-170`). A closed issue keeps its count, so a reopened one carries on from it.
- The `merge check failed` refusal (README line 189) is re-raised (`merging.py:85-86`) as a `ServerRefused`, so it now counts and stops after 20 passes.

## Tests (TDD, one increment per commit)
Before editing user-visible text, grep the tests for `KINDS`, `left for the next pass`, `retried on the next pass`, `every pass retries` and `retry-refused`, and list the hits here. Not verified: whether any test pins the `KINDS` tuple. No test reads `README.md` (plan 88). Not verified: whether `POST /advance` on a task with a job in flight is refused (409, which would count against a merely busy issue) or queued; the implementer checks and reports.
1. Red: a `watch.json` without `failed_passes` loads with an empty map; the map round-trips through save and load.
2. Red: a `GitHubError` or `ServerRefused` on an issue with a record increments its count and persists; a later normal pass resets it (saves only if non-zero). Existing tests stay green unedited.
3. Red: `ServerUnreachable` leaves the count unchanged (neither counts nor resets).
4. Red: add `gave-up` to `KINDS`; at `>= 20` post one `gave-up` comment, phase `STOPPED`; the next pass makes no server call and no GitHub comment call for the issue.
5. Red: at the cap, a `GitHubError` from the post itself: phase `STOPPED`, saved, one log line, no raise.
6. Red or guard: an issue at `AWAITING_RETRY` whose offer comment is deleted (`reactions` raises) stops after 20 passes; a later 👍 does nothing. This is the motivating case.
7. Guards (not red): an earlier `gave-up` comment is adopted, not posted twice; 19 failures, a success, then 19 more does not stop.
8. README lines 189, 191, 194 rewritten with the "not covered" list above; Deviations updated.
Any existing test that must change is reported to the user first.

## Process
- Base gate on `main` first. Gate: `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto` (background, Monitor), then the `verify` skill and `commit-auditor`.
- Sonnet `implementation` agent for 1-7 (ordinary code), README inline. Commits short, Sonnet 5.5 trailer, no file lists. Sign unsigned commits at the end (stop after the first failure). Never push.

## Risks
- The cap counts consecutive failed passes, so an issue that alternates failure and success never stops. Intended.
- A partial GitHub failure (per-issue comments or reactions, a secondary rate limit, a locked issue) can stop an issue; a full outage mostly fails at `self_user()` or `open_issues()` before the loop and counts nothing. A stop whose comment cannot post is silent on GitHub; the log is the trace.
- An issue stopped while waiting for a 👍 leaves the old "React 👍" comment in place, now ignored; the `gave-up` comment says so.

## Deviations
(none yet)
