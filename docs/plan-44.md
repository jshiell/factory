# Plan 44: rebase from GitHub with a 👍

Goal: when a merge is refused only because `main` moved, an approver can react 👍 on the issue to rebase the task onto `main`, so the task goes on to a fresh audit and merge comment without a terminal. Reviewed by `plan-reviewer`; findings folded in; awaiting approval.

## Decisions (agreed)
1. **Trigger: 👍 on a new comment**, the same pattern as plan approval and merge.
2. **After the rebase the watcher carries on:** the normal flow re-verifies and re-audits, then posts a new merge comment. Merging still needs its own 👍.
3. **Scope: only a refusal because `main` moved.** Every other merge refusal keeps the terminal advice.
4. **Gating:** rebase is on whenever `watch_merge` is on; no new setting. The README config line says `watch_merge` now also lets a 👍 spend agent budget. A record already in `AWAITING_REBASE` still acts if `watch_merge` is later turned off (same as `AWAITING_MERGE`).
5. **👎** declines the rebase: `not-merged` kind with its own text ("declined the rebase"), phase `NOT_MERGED`. The task stays `audited`.

## Behaviour (merge on)
- A merge refusal whose reason equals `merge_task.MAIN_MOVED` ("main moved since the plan; re-run the task") posts a **`merge-moved`** comment: "`main` moved since the audit. React 👍 to rebase the task onto `main` and re-audit it (this spends agent budget), 👎 to leave it." The reason and the **current `audited_sha`** are in the body. Phase `AWAITING_REBASE`; new fields `rebase_comment_id` and `rebase_sha` (the audited sha shown). One save.
  - Sources: a preview refusal when posting the merge comment, a preview refusal at 👍 time (shared helper), a failed merge job with error `merge refused: <MAIN_MOVED>`. The job error is compared after stripping that prefix. Equality with the constant, never a substring match. Other text stays `merge-refused` and `MERGE_STOPPED`.
  - The watcher gets `audited_sha` from the task dict `_handle_issue` already fetched (`GET /tasks/{id}` is `asdict(task)`, verified).
- **Adoption by exact body.** `_post_result` adopts an earlier watcher comment with the same marker. Because the body carries the audited sha and every rebase produces a new one, an earlier round's `merge-moved` (which already has a 👍) can never be adopted. No dependence on comment-id order. `rebase-refused` is terminal (`MERGE_STOPPED`), so plain marker adoption is fine.
- **Phase handling.** `AWAITING_REBASE` goes in the phase allowlist (watch.py ~485) and its handlers run before the `audited/merged → AUDITED` shortcut (~500); otherwise the record silently becomes `AUDITED`.
- **On a pass**, an `AWAITING_REBASE` record with server state `audited` reads reactions on `rebase_comment_id` only. An approver's 👎 wins over 👍. On 👍:
  - If the task's `audited_sha != rebase_sha` (already rebased and advanced elsewhere): no request; phase `APPROVED`.
  - Else `POST /tasks/{id}/rebase`, wait on the job.
    - `succeeded`: `APPROVED`, no comment. The next pass advances, re-audits and posts a new merge comment (existing path, same as test ~817).
    - `failed` and the task is now `agent_done` or its `audited_sha` differs from `rebase_sha`: success (duplicate job, or race with a terminal rebase).
    - `failed` otherwise (conflict, dirty worktree, "main has not moved", "audited commit already in main"): `rebase-refused` with the job error (through `strip_home`, in a fence) and terminal advice; `MERGE_STOPPED`.
    - `interrupted`: stay `AWAITING_REBASE`, retry next pass.
- `AWAITING_REBASE` + `agent_done`: `APPROVED` (rebased at the terminal, or our save was lost). + `merged`: post `merged`, `MERGED`. + `failed`: existing failure path.
- The result comment from the first audit is adopted again after the re-audit, so it is not re-posted and its steps are stale. The new merge comment carries the current commit. Stated in the README.

## Tests affected (listed up front)
- `tests/test_watch.py` ~912-926 (`test_a_refused_merge_preview_posts_a_merge_refused_comment_even_with_the_audited_result_already_on_the_issue`) uses the exact main-moved text. **Option B:** swap its text for another refusal (e.g. "the worktree is dirty") so it keeps checking that merge-refused is not swallowed with the result comment present; cycle 2 adds a new merge-moved test with the result comment present. Flagged in that commit.
- `tests/test_watch.py` ~770-792 use "main moved since the audit" (different text): must stay green unchanged (guard).
- `tests/test_watch.py` ~817 (rebase at the terminal, then a new merge comment): must stay green.
- `tests/test_watch.py` ~99 round-trip gains `rebase_comment_id` and `rebase_sha` (cycle 1). ~899 is the merge-changed test; the ~1358 terminal list needs no change (no new terminal phase).
- `tests/test_comments.py`: check whether it covers `KINDS`; add the two new kinds if it does not.
- `tests/test_cli.py` ~457-462 and `tests/test_merge_task.py`: merge text unchanged (moved into a constant); must stay green.
- README: "Watching issues" Merge bullet, Serial, Not covered (`rebase` terminal-only), "Moved main" (~275-277, "never called by `advance`"), Commands rows for `rebase` and `watch`, `watch_merge` config line, the "Humans hold the two irreversible steps" bullet and the Trust bullet.

## Cycles (one test, one implementation, one commit each)
0. Support, no assertion changes: `merge_task.MAIN_MOVED` (raise site unchanged); `FakeServer` answers `POST /rebase` with job `j-rebase`, a `rebase_job` result, a `task_state_after_rebase`, and its task GET carries `audited_sha`.
1. Red: save/load round-trip with `AWAITING_REBASE`, `rebase_comment_id`, `rebase_sha`.
2. Red: a posting-time preview refusal equal to `MAIN_MOVED` (audited result comment already present) posts `merge-moved` with the audited sha in the body, sets `AWAITING_REBASE`, both fields, one save. Edits ~912 per option B.
3. Guard: any other refusal text stays `merge-refused`, `MERGE_STOPPED` (~770 and ~784 unchanged).
4. Guard: a 👍-time preview refusal with `MAIN_MOVED` posts `merge-moved` (shared helper).
5. Red: a failed merge job with `merge refused: <MAIN_MOVED>` posts `merge-moved` (pins the prefix format).
6. Red: `AWAITING_REBASE` + `audited`, no reaction: not advanced, not turned into `AUDITED`, only `rebase_comment_id` reactions fetched. Adds the allowlist entry and routing before ~500. Guard: a non-approver 👍 does the same.
7. Red: approver 👍 with `audited_sha == rebase_sha`: POST `/rebase`, wait, success sets `APPROVED`, no comment.
8. Red: 👍 but `audited_sha != rebase_sha`: no request, `APPROVED`.
9. Red: rebase job `failed`, task `audited` at `rebase_sha`: `rebase-refused` (error fenced, `strip_home`), `MERGE_STOPPED`. Guard: `interrupted` stays `AWAITING_REBASE`.
10. Red: rebase job `failed` but task `agent_done` or a different sha: success (`APPROVED`).
11. Red: approver 👎 wins over 👍: `not-merged` with the rebase text, `NOT_MERGED`, no request.
12. Red: `AWAITING_REBASE` + `merged`: `merged` comment, `MERGED`. Guards: `+ agent_done` → `APPROVED`, then the re-audit posts a new merge comment and updates `merge_comment_id`; `+ failed` → failure path.
13. Red: round 2. After rebase and re-audit, main moves again at posting: a new `merge-moved` is posted though round 1's (with 👍) is on the issue; `rebase_comment_id` and `rebase_sha` updated; no rebase that pass.
14. Optional refactor, own commit: share "approver reaction, then job, then outcome" between merge and rebase only if it needs no flags.
15. Docs (README spots above, the HEAD-vs-main risk, a short `docs/spike-findings.md` entry), then `verify` with the full suite in the background (70-100s).

## Risks and unverified assumptions
- **Verified by review (read, not run):** a failed merge job's error is `merge refused: <text>`; the preview endpoint returns 409 with the raw text; "main moved" is raised only at `merge_task.py:36`. A rebase on a task in the wrong state is a failed job (`rebase refused: rebased not allowed from <state>`), not an HTTP error.
- **Unverified:** the `/rebase` failed-job result for a conflict (`rebase_task` aborts and restores the worktree, so the task stays `audited`); GitHub comment ids are not relied on.
- **HEAD, not `main`:** "main moved" means the repo's HEAD is not `base_sha`, and `rebase_task` rebases onto HEAD without checking it is on a branch or clean. If the checkout is on another branch, a remote 👍 rebases onto that branch. Same as the terminal today, but now triggered remotely. README risk; no code change here.
- **Misleading offer:** if `main` already contains the audited commit, the offer is made but rebase refuses; it ends in `rebase-refused`.
- **TOCTOU:** `/rebase` takes no shown sha, so a short window remains after the `rebase_sha` check. Acceptable: a rebase lands nothing.
- **Cost:** each 👍 re-runs verify and audit. No cap on rounds; each needs a human 👍.
- **Serial pass:** the rebase and later advance queue behind other terminal jobs on the single worker (already true of merge).
- **Trust:** an approver's account can now trigger agent spend from GitHub as well as put code on `main`. No new boundary; one clause in the README Trust bullet.
- **Not covered:** `retry`, `abandon`, `recover`, `clean`, waiving notes; a rebase conflict is resolved at the terminal.
