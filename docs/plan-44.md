# Plan 44: rebase from GitHub with a 👍

Goal: when a merge is refused only because `main` moved, an approver can react 👍 on the issue to rebase the task onto `main`, so the task goes on to a fresh audit and merge comment without a terminal. Draft; not yet reviewed by `plan-reviewer`; awaiting approval.

## Decisions (agreed)
1. **Trigger: 👍 on a new comment**, the same pattern as plan approval and merge.
2. **After the rebase the watcher carries on:** the normal flow re-verifies and re-audits, then posts a new merge comment. Merging still needs its own 👍.
3. **Scope: only a refusal because `main` moved.** `rebase` itself refuses when `main` has not moved, so every other merge refusal keeps the terminal advice.

## Decisions to confirm
- **A. Gating.** Rebase is on whenever `watch_merge` is on, with no new setting. A rebase spends verify and audit budget (the re-run is not free) but lands nothing. Alternative: a separate `watch_rebase` setting. Recommended: reuse `watch_merge`.
- **B. 👎.** Declines the rebase: posts `not-merged` (existing kind) and stops, phase `NOT_MERGED`. The task stays `audited`. Recommended.

## Behaviour (merge on)
- A merge refusal whose reason is exactly "main moved since the plan; re-run the task" (a preview refusal when posting the merge comment or at 👍 time, or a failed merge job with that error) posts a **`merge-moved`** comment: "`main` moved since the audit. React 👍 to rebase the task onto `main` and re-audit it (this spends agent budget), 👎 to leave it." The reason is in a code fence. Phase `AWAITING_REBASE`; new field `rebase_comment_id`. One save.
- "Main moved" is recognised by **equality with a shared constant** (`merge_task.MAIN_MOVED`, used by the raise), never a substring match. A job error is compared after stripping the `merge refused: ` prefix. Any other refusal text stays `merge-refused` and `MERGE_STOPPED`.
- On a pass, an `AWAITING_REBASE` record with server state `audited` reads reactions on `rebase_comment_id` only. An approver's 👎 wins over 👍. 👍: `POST /tasks/{id}/rebase`, wait on the job.
  - `succeeded`: phase `APPROVED`, no comment. The next pass advances the task and posts the result and a new merge comment, as today.
  - `failed` and the task is now `agent_done`: same as succeeded (a duplicate job, or a race with a terminal rebase).
  - `failed` otherwise (conflict, dirty worktree, "main has not moved"): `rebase-refused` comment with the job's `error` in a fence and terminal advice; phase `MERGE_STOPPED`.
  - `interrupted`: stay `AWAITING_REBASE`, retry next pass.
- `AWAITING_REBASE` + `agent_done` (rebased at the terminal, or our save was lost after the job): `APPROVED`. `AWAITING_REBASE` + `merged`: post `merged`, `MERGED`. `AWAITING_REBASE` + `failed`: the existing failure path.
- **Comment adoption.** `_post_result` adopts an earlier watcher comment with the same marker. A second "main moved" on the same task would adopt the first `merge-moved` comment, which already carries 👍 reactions, so the rebase would re-run with no new approval. Fix: adoption for `merge-moved` only considers comments newer than the record's last known comment id (`merge_comment_id`, the one just superseded). Same rule for `rebase-refused`.
- The result comment from the first audit is adopted again after the re-audit (its marker matches), so it is not re-posted and its steps are stale. The new merge comment carries the current audited commit. Acceptable; stated in the README.

## Tests affected (listed up front)
- `tests/test_watch.py` ~916-926 (`test_a_refused_merge_preview_posts_a_merge_refused_comment_even_with_the_audited_result_already_on_the_issue`): uses the exact "main moved since the plan; re-run the task" text and asserts `merge-refused`, the "factory rebase t-9" advice and `MERGE_STOPPED`. **This assertion changes on purpose** (it becomes the `merge-moved` case) and is flagged in cycle 1's commit, not edited silently.
- `tests/test_watch.py` ~770-780 and ~784-792 use "main moved since the audit", a different text, so they keep meaning "some other refusal" and must stay green unchanged (guard, cycle 2).
- `tests/test_watch.py` ~899 and ~1359: the terminal-phase parametrization gains `AWAITING_REBASE`'s counterpart endings (`rebase-refused` uses `MERGE_STOPPED`; no new terminal phase); the save/load round-trip (~99) gains `rebase_comment_id`.
- `tests/test_comments.py` parametrizes over `KINDS` (293 passes at the end of plan 43): the new kinds `merge-moved` and `rebase-refused` are covered by it.
- `tests/test_cli.py` ~457-462 and `tests/test_merge_task.py`: the merge error text is unchanged (only moved into a constant); these must stay green.
- README: "Watching issues" Merge bullet, Serial, Not covered ("`rebase`" is listed as terminal-only), the `watch_merge` config line and the "Humans hold the two irreversible steps" bullet.

## Cycles (one test, one implementation, one commit each)
0. Support, no assertion changes: `merge_task.MAIN_MOVED` constant; `FakeServer` answers `POST /rebase` with job `j-rebase`, a `rebase_job` result and a `task_state_after_rebase`.
1. Red: a preview refusal equal to `MAIN_MOVED` when posting the merge comment posts `merge-moved` (new kind), phase `AWAITING_REBASE`, `rebase_comment_id` saved. Changes the ~916 test as above.
2. Guard: a refusal with any other text is still `merge-refused` and `MERGE_STOPPED` (the ~770 test, unchanged).
3. Red: `merge-moved` is not adopted from an earlier comment of the same kind (only newer than `merge_comment_id`).
4. Red: the same two refusals at 👍 time (preview refusal; failed merge job with `merge refused: <MAIN_MOVED>`) post `merge-moved`.
5. Guard: an `AWAITING_REBASE` record with server `audited` and no reaction, or a non-approver 👍, does nothing and fetches nothing else.
6. Red: an approver 👍 on `rebase_comment_id` posts `/rebase`, waits, and on success sets `APPROVED` with no comment.
7. Red: rebase job `failed`, task still `audited`: `rebase-refused` with the job's error, `MERGE_STOPPED`. Guard: `interrupted` stays `AWAITING_REBASE`.
8. Red: rebase job `failed` but the task is `agent_done`: treated as success (`APPROVED`).
9. Red: approver 👎 (wins over 👍): `not-merged`, `NOT_MERGED`, no rebase request.
10. Red: `AWAITING_REBASE` + `agent_done`: `APPROVED`; after the re-audit a new merge comment, `merge_comment_id` updated. Guard: `AWAITING_REBASE` + `merged`: `merged` comment.
11. Refactor: share "approver reaction, then run a job, then classify its outcome" between the merge and rebase handlers; behaviour unchanged. Own commit.
12. Docs (README sections above, a short `docs/spike-findings.md` entry), then the `verify` skill with the full suite in the background (about 70s).

## Risks and unverified assumptions
- **Unverified:** the failed-job `error` format for a merge refusal is `merge refused: <text>` (read from the code in plan 43, never run). If the prefix differs, a failed job with "main moved" posts `merge-refused` (the safe fallback) instead of offering a rebase. Cycle 4 pins the format the watcher expects.
- **Unverified:** what `POST /rebase` returns when the job fails for a conflict: I read `RebaseRefused` and `CalledProcessError` mapped to a failed job, but have not run it. `rebase_task` aborts a half-done rebase and restores the worktree, so a conflict leaves the task `audited`.
- **Cost:** each 👍 re-runs verify and audit. There is no cap on rounds; each round needs a human 👍, so it is bounded by the approvers.
- **Serial pass:** the rebase and the later advance queue behind other terminal jobs on the single worker, so a pass can stall (already true of merge).
- **Trust:** an approver's account can now trigger agent spend from GitHub as well as put code on `main`. No new trust boundary; the README's Trust bullet gets one clause.
- **Not covered:** `retry`, `abandon`, `recover`, `clean`, waiving notes; a rebase conflict has to be resolved at the terminal.
