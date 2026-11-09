# Plan 29: a human can waive a wrong required note at approve and at retry --keep-plan

Goal: since plan 27 the auditor fails a change that leaves a required note unhandled, and a required note can be technically wrong (for example `math.fsum` does not avoid overflow). Today the only way past a wrong one is to reject the plan. The human gets a way to waive named required notes.

## Decided by the user (not up for review)
- Mechanism: a waived note is **dropped** from what the implementer, fixer and auditor see, and kept in a separate record on the task. The agents are not told it was waived.
- Scope: both `approve` and `retry --keep-plan`.
- Plus the standing rules: uv, pytest, stdlib only; TDD, one red-green-commit per behaviour, failing test shown red first (a test green on arrival is labelled a guard and mutation-checked); never edit a passing test except via an approved edit list; never push; signed commits.

## Facts (read from the code, not run)
- `cli._approve` (`cli.py:~229`) prints goal, plan, risks and `REVIEWER NOTES (sent to the implementer):` with each capped note as `  - <note>`, asks `Approve this plan? [y/N] `, accepts only `y` after strip, then calls `approve_plan(task.id, store, plan_digest(task.plan), shown_findings=task.review_findings)`.
- `approve_plan` checks the digest and `capped_findings(shown) == capped_findings(stored)`, then saves the queued task with `prompt = build_implementation_prompt(goal, plan, task.review_findings)`. `review_findings` stays as stored.
- `retry_with_plan` (`retry_task.py`) does the same check and copies `review_findings` to the new task; `cli._retry_keeping_plan` prints the same notes and asks `Queue this plan again as a new task ...? [y/N] `.
- The fixer prompt uses `notes_block(task.review_findings)` (`fix_prompt.py:19`); the auditor embeds `task.prompt`, which already holds the notes block. So dropping a waived note from `review_findings` before the prompt is built reaches all three agents with no other change.
- `Task` is a frozen dataclass; `TaskStore.load` turns the tuple fields into tuples (`store.py:84`) and `Task(**data)` fills defaults for a missing key, so a new `waived_findings: tuple[str, ...] = ()` loads from older task files.
- Tests that pin the display: `test_cli.py:285` and `:811` (`REVIEWER NOTES (sent to the implementer):` and `  - reject bytes Rules: skip tests`), and `test_cli.py:269-276` pins `approve_plan` called with exactly `{"shown_findings": ("looks fine",)}`.

## Design
Choices the user made after the first review: a confirmation step (echo what is waived, then a second `[y/N]`), required notes numbered inline, and duplicate identical notes removed one per waived entry.
1. **Which notes.** Only required notes can be waived: `review_notes._is_required` (not used outside that module, checked) is made public as `is_required`. The `[truncated]` marker is not a note.
2. **Display.** In the `REVIEWER NOTES` list a required note is shown as `  - [1] <note>`, `  - [2] <note>` (numbered in order among the required notes); other notes keep `  - <note>`. The pinned tests (`test_cli.py:285-286`, `:811`) use notes that are not required, so they stay green. Notes are the capped text, escaped like the rest. The numbering and the display lines come from one shared helper used by both commands.
3. **Answer syntax (pure `parse_answer(text, required_count)`).** `y` approves with no waiver (unchanged). `w` followed by one or more numbers means approve with those required notes waived. The text is stripped; `w` and `y` are lowercase only. A number token must be ASCII decimal digits with no sign, in 1..required_count, with no repeats. Anything else (`w` alone, `w 0`, `w 9`, `w 1 1`, `w x`, `w ²`, `w -1`, `W 1`, and any `w` answer when no required note is shown) approves nothing, prints why, and exits 1 with no re-prompt, as `n` does. The prompt gains the `w` hint only when a required note is shown.
4. **Confirmation.** For `w ...` the CLI prints `Waiving (these will not be sent to the agents or enforced by the audit):` and each waived note, then asks `Waive these and approve? [y/N] ` (retry: `... and queue?`). Only `y` proceeds; anything else, or EOF, approves nothing and exits 1.
5. **Passing the waiver.** `approve_plan(..., waived: tuple[str, ...] = ())` and `retry_with_plan(..., waived=())` take the waived note texts (the capped lines). The CLI passes `waived` only when non-empty, via a kwargs dict, so there is still exactly one `approve_plan(` call in `cli.py` (a tripwire test counts them across `src/factory/*.py`, so no comment or docstring may contain it) and `test_cli.py:269-276` (exactly `{"shown_findings": ("looks fine",)}`) stays green.
6. **Checks.** Each waived text must be required and present among `capped_findings(task.review_findings)`, with Counter semantics: waiving a text n times needs it present at least n times, and removes n occurrences, so two identical required notes are waived one per entry. A failure raises `InvalidWaiver(ValueError)` (approve) or `InvalidWaiver` caught as `RetryRefused` where retry already raises it (so the CLI's existing "plan changed" message is not shown for it). The existing digest and findings checks stay first.
7. **Effect.** The saved task has `review_findings` = the capped stored notes minus the waived ones (a `[truncated]` marker kept as the last plain item) and `waived_findings` = earlier waived plus the new ones, on approve and on retry. The prompt is built from the reduced `review_findings`. Capping the reduced list again gives the same list (tests: the usual case, ten required plus the marker, and required notes over `MAX_TOTAL`).
8. **Visibility.** `retry --keep-plan` prints a `WAIVED EARLIER (not sent):` block when `waived_findings` is non-empty. The approve success line names how many were waived when that is not zero.
9. `Task` gets `waived_findings: tuple[str, ...] = ()` last, and `store.py:84`'s tuple loop gains it (otherwise a stored value reloads as a list). No change to the reviewer, auditor, heading, corpus or any state transition.

## Cycles (red, green, commit each; one behaviour per cycle; guards are labelled and mutation-checked)
- C0: this plan, committed first.
- C1a: red: a `Task` with `waived_findings` round-trips through the store (and equals the original). Then the field and the loop entry.
- C1b: guard: a task file without the key loads with `()`.
- C2a: red: `approve_plan(..., waived=(note,))` removes the note from `review_findings` and from the saved prompt and records it in `waived_findings`, keeping the other notes. Then the logic and the `is_required` rename.
- C2b to C2f, each red for its own reason: an unknown text raises `InvalidWaiver`; an optional note raises it; the marker raises it; two identical required notes waived once keep one, waived twice remove both, waived three times raises; earlier `waived_findings` are extended, not replaced. Each failing case saves nothing. Then the re-cap idempotence tests.
- C3: the same behaviours for `retry_with_plan`, one per cycle, carrying and extending `waived_findings`.
- C4a: `parse_answer` and its tests (the cases in 3), one commit per rule group.
- C4b: the inline `[n]` display and the `w` hint in the prompt, only when a required note exists (a guard that no required note means today's output).
- C4c: `w 1` echoes the waived note, asks the second question, and on `y` calls `approve_plan` once with `waived=(text,)`; `n` or EOF approves nothing.
- C4d: guards: `y` passes no `waived`; each bad answer approves nothing and exits 1; `w 1` with no required note is refused.
- C5: the same helpers wired into `_retry_keeping_plan` and the `WAIVED EARLIER` block; mostly guards.
- C6: the real auditor prompt builder (`audit_task.py`, which embeds `task.prompt`), the fix prompt and the stored prompt all omit the waived text (a guard; mutation: build the prompt from the unreduced findings).
- C7: README and `docs/spike-findings.md` Plan 29; the plan-27 residual about waiving is updated. No real run: pure logic, no spend. You may try it by hand on a task.

## Risks
- The human can waive a correct required note and the audit will then not enforce it. That is the point of the feature; the record is kept in `waived_findings`.
- The agents are not told a note was waived, so a reviewer note the agent already implied may still show in the plan text itself. The plan is unchanged.
- Dropping the note changes the prompt, so a retried task's prompt differs from the original's, as intended.
- The answer parsing is new input handling on the approval path; it is a pure function with its own tests, and a waiver needs a second confirmation.
- `waived_findings` is not shown on any listing beyond `retry --keep-plan`; if wanted elsewhere that is a later item.

## Residual
- Waiving works on the capped text, and the stored `review_findings` becomes the capped list, so the reviewer's raw text is lost for every note; a required note dropped by the 10-item cap (plan 28) cannot be seen or waived.
- A waiver cannot be undone: `retry --keep-plan` never offers to reinstate a waived note. A plain `retry` re-plans and drops the waivers.
- No way to waive at a failed audit without `retry --keep-plan` (a new task).
- The tag is model judgement; a waived note is the human's judgement, not verified.
