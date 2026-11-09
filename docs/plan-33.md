# Plan 33: `approve` says when the 10-item cap dropped required notes

Goal: `capped_findings` keeps at most 10 required notes and drops the rest, marked only by a bare `[truncated]` that also appears when a long note is shortened or an optional note is dropped. The dropped required notes reach neither the agents nor the audit, and the human cannot tell any were lost. `approve` and `retry --keep-plan` tell the human how many required notes were dropped.

## Decided by the user (not up for review)
- Say how many were dropped (warn the human). Not refuse to approve, not raise the cap.
- Warn at both `approve` and `retry --keep-plan`.
- Plus the standing rules: uv, pytest, stdlib only; TDD, one red-green-commit per behaviour, failing test shown red first (a test green on arrival is a labelled guard, mutation-checked); never edit a passing test except via an approved edit list; never push; signed commits.

## Facts (read from the code, not run)
- `review_notes.capped_findings(findings)` (`review_notes.py:20`) flattens whitespace, drops empty lines, cuts each to 500 characters (keeping a required tag), keeps the first `MAX_ITEMS` (10) required notes outside the 4000-character budget, fills the rest with others, and appends `TRUNCATED` when anything was cut or dropped.
- Many passing tests pin `capped_findings` results with `TRUNCATED` as the last element (`tests/test_review_notes.py`, `test_waiver.py`, `test_approve_plan.py`). Changing the marker or the return shape would need an approved edit list; this plan changes neither.
- `cli._approve` (`cli.py:255`) and `_retry_keeping_plan` (`cli.py:~329`) build the display from `note_lines(capped_findings(task.review_findings))`. `review_plan` stores the reviewer's raw output. `approve_plan` and `retry_with_plan` (`approve_plan.py:58`, `retry_task.py:39`) store the capped-and-waived list only when a waiver is given; otherwise `review_findings` stays raw. So the dropped count is available at retry after a plain approve, and not after a waived one (the stored list is already capped).
- The audit reads `task.prompt` (not `review_findings`), which holds `notes_block` of the capped notes, so dropped required notes reach neither agents nor audit.
- The largest required count in the plan-32 spike was 6 (toy plans), so this is rare.

## Design
1. New pure `review_notes.dropped_required(findings) -> int`: required lines after the same whitespace flattening and empty-line dropping, minus the required lines in `capped_findings(findings)`. Derived from `capped_findings` itself so it cannot drift from the cap; equals `max(0, required - MAX_ITEMS)`. The shared normalise step is extracted if that is the smaller change.
2. `_approve` and `_retry_keeping_plan` print, before the answer prompt and only when the count is non-zero, a separate line (not inside the escaped block): `WARNING: N required note(s) beyond the first {MAX_ITEMS} were dropped and are not in the agents' prompts or the audit's. Reject and re-plan if they matter.` The count is an integer; no model text.
3. Waiving some shown notes does not promote dropped ones, so the warning stays after a waiver; the plan says so and a test pins it.
4. `capped_findings`, its marker, `approve_plan`, `retry_with_plan` and the prompts are unchanged. Display only, so the shown-versus-stored check is untouched.

## Cycles (red, green, commit each)
- C0: this plan, committed first.
- C1a (red): ten required notes give 0, eleven give 1. Then the function.
- C1b (red): optional and blank lines are not counted (a naive `len - 10` fails it).
- C1c (red): a tag in uppercase or split by whitespace or a newline is counted; a required note over 500 characters is counted.
- C1d: 25 required notes give 15; consistency test: the count equals required-in-input minus required-in-`capped_findings`-output. Likely guards (labelled, mutation-checked). Full suite after C1.
- C2a (red): `approve` with eleven required notes prints the warning with the count, before the prompt. Guard: ten or fewer, no warning and the output equals today's (check first whether existing CLI tests compare the whole stdout). Guard: waiving a shown note still prints the warning once.
- C2b (red): `retry --keep-plan` on a task with eleven stored required notes prints the same warning; guard: a task whose stored list is already capped prints none. Full suite after C2.
- C3: README sentence, `docs/spike-findings.md` Plan 33; the plan-28 and plan-29 residuals about invisible dropped notes updated. No real run: pure logic, no spend.

## Risks
- A required note shortened by the 500-character cut is still kept and not counted.
- After a waived approve or waived retry the stored list is capped, so a later retry cannot warn.
- The dropped notes' text is still not shown, only their number.

## Residual
- The dropped notes stay unseen and unenforced; the human can only reject the plan.
- The warning leaves no record on the task.
- The `[truncated]` marker is unchanged and still ambiguous (shortened, optional dropped, or required dropped).
