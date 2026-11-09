# Plan 34: undo an earlier waiver at `retry --keep-plan`

Goal: a waived required note can't come back. `retry --keep-plan` shows "WAIVED EARLIER (not sent)" but offers no way to reinstate one, so a waiver made on one attempt binds every later retry. Let the human put an earlier-waived note back when queuing a kept-plan retry.

## Decided by the user (not up for review)
- Only at `retry --keep-plan`; no new command, no in-place edit of a task.
- A reinstated note goes back into the new task's prompt and the audit, and comes off the new task's waived list. The 10-item cap applies as normal. `r` and `w` can't be combined in one answer.
- Plus the standing rules: uv, pytest, stdlib only; TDD, one red-green-commit per behaviour, failing test shown red first (a test green on arrival is a labelled guard, mutation-checked); never edit a passing test except via an approved edit list; never push; signed commits.

## Facts (read from the code, not run)
- `cli._retry_keeping_plan` prints the `WAIVED EARLIER (not sent):` lines from `task.waived_findings` (unnumbered), then `_ask_with_waiver(...)`, which uses `waiver.parse_answer(text, required_count)` (`y`, `w N...`, else refuse) and returns the notes to waive or None.
- `retry_with_plan(task_id, store, clock, shown_digest, shown_findings, waived=(), shown_audit_gaps=None)` builds `notes = remove_waived(capped_findings(task.review_findings), waived) if waived else task.review_findings`, sets `waived_findings=task.waived_findings + waived`, and calls `split_quoting_waived(gaps, waived)` against the newly waived notes only.
- `task.waived_findings` holds the full text of each waived note (required lines). `task.review_findings` after a waived approve or retry is the capped list minus the waived notes (plan 29), so a waived note is in neither `review_findings` nor the prompt.
- `approve_plan` has no earlier waivers to undo (a REVIEWED task has an empty `waived_findings`).
- `capped_findings` keeps at most `MAX_ITEMS` (10) required notes, required first.

## Design
1. `waiver.parse_answer(text, required_count, waived_count=0)` also accepts `r N...` (plain digits, 1..`waived_count`, no repeats) and returns `Reinstate(numbers)`; `r` with no numbers, bad numbers, or `waived_count == 0` refuses with a reason. `r` mixed with `w` in one answer refuses (the answer is one verb). `y` and `w` behave exactly as today.
2. `waiver.approval_prompt` keeps its text when there is nothing to reinstate; when there are earlier waivers the hint adds `r 1` (reinstate earlier-waived note 1).
3. `cli._retry_keeping_plan` numbers the `WAIVED EARLIER` lines (`[1] ...`) and, on `Reinstate`, prints `Reinstating (these will be sent to the agents and enforced by the audit again):` plus the notes, asks `Reinstate these and queue? [y/N]`, and passes `reinstated=` to `retry_with_plan`. Approve is unchanged.
4. `retry_with_plan(..., reinstated: tuple[str, ...] = ())`: each reinstated note must be in `task.waived_findings` (one occurrence per entry, else `InvalidWaiver`); `notes = capped_findings(task.review_findings) + reinstated`; `waived_findings` = the old list minus the reinstated notes. If the result would hold more than `MAX_ITEMS` required notes it refuses (`InvalidWaiver`) rather than let the cap drop one silently. `waived` and `reinstated` together are refused.
5. The audit-gap filter is unchanged (it filters against newly waived notes only); the reinstated note is not filtered.

## Cycles (red, green, commit each)
- C0: this plan, committed first.
- C1 (red): `retry_with_plan` with `reinstated=(note,)` puts the note in the new task's prompt and `review_findings`, and removes it from `waived_findings`. Then the parameter.
- C2 (red): reinstating a note that is not waived refuses (`InvalidWaiver`) and creates nothing; reinstating twice a note waived once refuses.
- C3 (red): reinstating when it would exceed 10 required notes refuses; at exactly 10 it succeeds.
- C4 (red): `parse_answer("r 1", 3, 2)` is `Reinstate((1,))`; guards for `r` alone, `r 3` with 2 waived, `r 1 1`, `r a`, `r` with `waived_count=0`, and `y`/`w` unchanged; `r` plus `w` in one answer refuses.
- C5 (red): `retry --keep-plan` on a task with an earlier waiver shows numbered `[1]` waived lines, `r 1` then `y` queues a task whose prompt has the note; `r 1` then `n` queues nothing; a task with no earlier waiver does not mention `r`. Then the CLI wiring and prompt hint.
- C6: README sentence, `docs/spike-findings.md` Plan 34, plan 29's "no undo" limit updated. Full suite after C5. No real run: pure logic, no spend.

## Risks
- A reinstated note was waived because the human judged it wrong; the audit enforces it again and may reject a task for it. That is the point, not a defect.
- The waived gaps were filtered at the earlier retry; a gap that quoted the reinstated note was dropped then and doesn't come back (the audit will raise it again if it matters).
- `parse_answer`'s signature gains a defaulted parameter; existing callers and tests stay green unedited.

## Residual
- Undo exists only at a kept-plan retry, not on an already-queued task.
- Waived notes are still not visible at `approve`'s first approval (nothing is waived yet).
- Paraphrased waivers (plan 29/30 limit) are unchanged.
