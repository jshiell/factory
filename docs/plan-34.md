# Plan 34: undo an earlier waiver at `retry --keep-plan`

Goal: a waived required note can't come back. `retry --keep-plan` shows "WAIVED EARLIER (not sent)" but offers no way to reinstate one, so a waiver made on one attempt binds every later retry. Let the human put an earlier-waived note back when queuing a kept-plan retry.

## Decided by the user (not up for review)
- Only at `retry --keep-plan`; no new command, no in-place edit of a task.
- A reinstated note goes back into the new task's prompt and the audit, and comes off the new task's waived list. The 10-item cap applies as normal. `r` and `w` can't be combined in one answer.
- The earlier-waived notes are shown in a separate `REINSTATE with r N:` block (labels `[r1]`, `[r2]`); the existing `WAIVED EARLIER` block stays byte-identical.
- After reinstating, the `[truncated]` marker is recomputed (dropped, then added by `capped_findings` only if the list is cut).
- Plus the standing rules: uv, pytest, stdlib only; TDD, one red-green-commit per behaviour, failing test shown red first (a test green on arrival is a labelled guard, mutation-checked); never edit a passing test except via an approved edit list; never push; signed commits.

## Facts (read from the code, not run; reviewed by plan-reviewer, which could not run anything and did not read audit_task.py, the fixer code, store.py or the test_cli.py helpers)
- `cli._retry_keeping_plan` prints the `WAIVED EARLIER (not sent):` lines from `task.waived_findings`, then `_ask_with_waiver(...)`, shared with `_approve`, which uses `waiver.parse_answer(text, required_count)` (`y`, `w N...`, else refuse) and returns the notes to waive or None; its "Waiving ..." text is hard-coded. `test_cli.py:1341` asserts the exact `WAIVED EARLIER` line.
- `retry_with_plan(task_id, store, clock, shown_digest, shown_findings, waived=(), shown_audit_gaps=None)` builds `notes = remove_waived(capped_findings(task.review_findings), waived) if waived else task.review_findings`, sets `waived_findings=task.waived_findings + waived`, and filters gaps against the newly waived notes only.
- `waived_findings` holds required notes only (`remove_waived` refuses others); identical duplicates are possible (`test_approve_plan.py:234`).
- Every path that writes `waived_findings` stores `capped_findings(...)` minus the waived notes, so stored required notes plus waived notes never exceed 10. Notes the cap dropped before the first waiver are in neither list.
- `notes_block` re-caps what it is given (`review_notes.py:73`); the stored list gets no second pass today. `capped_findings` appends a `[truncated]` marker when it cuts, and treats an existing one as an ordinary note.
- Fresh `audit_findings` of the failed task are never filtered against earlier waivers; only gaps stored in `prior_audit_findings` were filtered when stored.
- `parse_answer` callers use `required_count=` by keyword; the `retry_with_plan` stubs in `test_cli.py` accept `*a, **k`; two tests assert exact kwargs (`:1333`, `:1350`).

## Design
1. `waiver.parse_answer(text, required_count, waived_count=0)` also accepts `r N...` (plain digits, 1..`waived_count`, no repeats) and returns `Reinstate(numbers)`; `r` alone, bad numbers, or `waived_count == 0` refuse with a reason. `r` and `w` in one answer refuse. `y` and `w` are unchanged.
2. `approval_prompt` is unchanged when nothing is waived earlier; otherwise the hint adds `r 1` (reinstate earlier-waived note 1). `approve` passes `waived_count=0`, so `r` refuses there.
3. `_ask_with_waiver` returns a small result (waive, reinstate or none) and takes the wording per verb; approve and the `w` path behave exactly as today.
4. `_retry_keeping_plan` prints, after the unchanged `WAIVED EARLIER` block, `REINSTATE with r N:` with `[r1] ...` lines, only when there are earlier waivers. On `r`: `Reinstating (these will be sent to the agents and enforced by the audit again):`, notes, `Reinstate these and queue? [y/N] `; declining ends `not queued`. `reinstated=` is passed to `retry_with_plan` only when non-empty. A failed reinstate prints its own message (not "cannot waive"), exit 1.
5. `retry_with_plan(..., reinstated=())`: each reinstated note must be required and in `task.waived_findings` (one occurrence per entry, else `InvalidWaiver`); `waived` with `reinstated` is refused. Notes = `capped_findings((*stored notes without the [truncated] marker, *reinstated))`, stored as `review_findings` and sent in the prompt; `waived_findings` = the old list minus the reinstated notes. If the result would hold more than `MAX_ITEMS` required notes it refuses (defensive; not reachable through the app paths).
6. The gap filter is unchanged (`waived=()` filters nothing on a reinstate retry); a fresh gap quoting the reinstated note is still sent.

## Cycles (red, green, commit each)
- C0: this plan, committed first.
- C1a (refactor, green): add an ignored `reinstated=()` to `retry_with_plan` and extract the notes-building into a helper; existing tests unedited.
- C1 (red): reinstating `(note,)` puts it in `new.prompt`, `new.review_findings` and `build_audit_prompt(new, ...)`, and removes it from `waived_findings`.
- C1b (red): with a stored list holding required notes, optional notes and a `[truncated]` marker, the stored `review_findings` equals `capped_findings` of the result, the marker appears at most once and is absent when nothing is cut, and the prompt block equals the stored list.
- C2 (red): a note that isn't waived, or one reinstated twice when waived once, raises `InvalidWaiver` and creates nothing.
- C2b (guard): two identical waived notes, reinstate one: one stays waived, one copy is in the prompt. Reinstating together with `waived` is refused.
- C3 (guard, constructed task, mutation-checked): refuses when the result would exceed 10 required notes; exactly 10 succeeds.
- C3b (guard): a fresh audit gap quoting the reinstated note reaches the prompt.
- C4 (red): `parse_answer("r 1", 3, 2)` is `Reinstate((1,))`. Guards, labelled: `r`, `r 3` with 2 waived, `r 1 1`, `r a`, `waived_count=0`, `r 1 w 2`, and `y`/`w` unchanged.
- C5a (refactor, green): generalise `_ask_with_waiver`'s result and wording; approve behaviour and every existing test unchanged.
- C5b (red): `retry --keep-plan` with an earlier waiver shows the `REINSTATE with r N:` block with `WAIVED EARLIER` unchanged; `r 1` then `y` calls `retry_with_plan` with `reinstated=(note,)` and no other new kwargs.
- C5c (red): `r 1` then `n` queues nothing and ends `not queued`.
- C5d (guard): no `r` hint or block without earlier waivers; `approve` refuses `r`.
- C5e (red): a failed reinstate prints a message that doesn't say "cannot waive", exit 1.
- C6: README sentence, `docs/spike-findings.md` Plan 34, plan 29's "no undo" limit updated. Full suite after C5. No real run: pure logic, no spend.

## Risks
- A reinstated note was waived because the human judged it wrong; the audit enforces it again and may reject a task for it. That is the point, not a defect.
- Gaps stored from an earlier retry that quoted the note were filtered then and don't come back; fresh audit gaps quoting it are sent.
- Reinstated notes lose their original position (required notes sort first anyway).

## Residual
- Undo exists only at a kept-plan retry, not on a queued task.
- Notes the cap dropped before the first waiver stay lost: they are in neither list, `r` can't recover them, and plan 33's warning doesn't repeat for them (the stored list is already capped).
- Paraphrased waivers (plan 29/30 limit) are unchanged.
