# Plan 33: `approve` says when the 10-item cap dropped required notes

Goal: `capped_findings` keeps at most 10 required notes and drops the rest, marked only by a bare `[truncated]` that also appears when a long note is shortened or an optional note is dropped. The dropped required notes reach neither the agents nor the audit, and the human cannot tell any were lost. At `approve` the human is told how many required notes were dropped.

## Decided by the user (not up for review)
- Say how many were dropped (warn the human). Not refuse to approve, not raise the cap.
- Plus the standing rules: uv, pytest, stdlib only; TDD, one red-green-commit per behaviour, failing test shown red first (a test green on arrival is a labelled guard, mutation-checked); never edit a passing test except via an approved edit list; never push; signed commits.

## Facts (read from the code, not run)
- `review_notes.capped_findings(findings)` (`review_notes.py:20`) flattens whitespace, drops empty lines, cuts each to 500 characters (keeping a required tag), keeps the first `MAX_ITEMS` (10) required notes outside the 4000-character budget, fills the rest with others, and appends `TRUNCATED` when anything was cut or dropped.
- Many passing tests pin `capped_findings` results with `TRUNCATED` as the last element (`tests/test_review_notes.py`, `test_waiver.py`, `test_approve_plan.py`). Changing the marker or the return shape would need an approved edit list; this plan changes neither.
- `cli._approve` (`cli.py:255`) builds the display from `note_lines(capped_findings(task.review_findings))`; at that point `task.review_findings` is the reviewer's raw output. After `approve_plan` saves, `review_findings` is the capped list, so the dropped notes are not recoverable later; `_retry_keeping_plan` sees only the capped list and would count zero.
- The largest required count in the plan-32 spike was 6 (toy plans), so this is rare.

## Design
1. New pure `review_notes.dropped_required(findings) -> int`: the number of required lines (after the same whitespace flattening and empty-line dropping) that `capped_findings` does not keep, i.e. `max(0, required_count - MAX_ITEMS)`. One definition of "required" (`is_required`, applied after the same cut) so it cannot drift from the cap.
2. `_approve` prints, after the notes and only when the count is non-zero, `WARNING: N required note(s) were dropped by the 10-note cap and are not sent to the agents or the audit. Reject and re-plan if they matter.` (escape rules as for the rest; the count is an integer, no model text in the line).
3. `capped_findings`, its marker, `approve_plan`, `retry_with_plan`, the prompts and the retry command are unchanged. The warning is display only, so the shown-versus-stored check is untouched.

## Cycles (red, green, commit each)
- C0: this plan, committed first.
- C1 (red): `dropped_required` returns 0 for ten required notes, 1 for eleven, and counts required notes only (optional and blank lines ignored). Then the function.
- C2 (red): `approve` with eleven required notes prints the warning with the count; guard (labelled, mutation-checked): with ten or fewer, no warning and the output equals today's.
- C3: README sentence, `docs/spike-findings.md` Plan 33; the plan-28 and plan-29 residuals about invisible dropped notes updated. Full suite before C3. No real run: pure logic, no spend.

## Risks
- A required note shortened by the 500-character cut is still kept, so it is not counted.
- The warning appears only at `approve`; a later `retry --keep-plan` cannot warn because the dropped notes are no longer stored.
- The dropped notes' text is still not shown, only their number.

## Residual
- The dropped notes stay unseen and unenforced; the human can only reject the plan.
- The `[truncated]` marker is unchanged and still ambiguous (shortened, optional dropped, or required dropped).
