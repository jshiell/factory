# Plan 28: required reviewer notes are not dropped by the caps

Goal: plan 27 makes a required note enforceable, but `capped_findings` can still drop one whole (past 10 items or 4000 characters, or after a long untagged note), and then the implementer, fixer and auditor never see it. Required notes get the budget first; everything else fills what is left.

## Facts (read from the code, not run)
- `review_notes.capped_findings` flattens each finding, cuts it to `MAX_ITEM` (500, `_cut` keeps a required tag), then keeps notes in order until `MAX_ITEMS` (10) or `MAX_TOTAL` (4000) would be passed, and appends `[truncated]`.
- Callers of `capped_findings`: `approve_plan.py:27`, `retry_task.py:34`, `cli.py:238` and `:304`, and `notes_block`, which `approve_plan.py:12` (implementer prompt) and `fix_prompt.py:19` (fixer) call; the auditor gets the notes through `task.prompt`. All use the one function, so the screen and the prompts keep agreeing (`test_approve_plan.py:171` pins that).
- In the 24 plan-27 probe runs no run reached 10 items or 1.6k characters, so today this only bites on an unusually long review. The fix is for correctness of the guarantee, not for a seen failure.

## Decided (not up for review)
- Only a required note (its tag in the cut line, case-insensitive, as `_cut` already tests) is protected. Optional and untagged notes share one class and keep the old behaviour.
- Output keeps the reviewer's original order; only the choice of what is kept changes.
- `[truncated]` is still appended whenever any note was dropped or cut.
- No change to the heading, the reviewer text, the auditor prompt, the corpus or the CLI.
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test except via an approved edit list. Never push. Signed commits.

## The change
User choices after review: required notes are exempt from `MAX_TOTAL` (choice 3b), so only the item cap can drop one; with that, skip-and-continue (choice 2b) has nothing to skip.
`capped_findings` works on indexed, flattened, cut lines (blank lines skipped before indexing, so the index is the position among kept lines).
1. Required notes (tag in the cut line, case-insensitive, so a `[shortened]` note counts) are taken in order up to `MAX_ITEMS`, with no total cap.
2. Other notes are then taken in order into the remaining item slots and into `MAX_TOTAL` minus the length of the required notes (at least 0), stopping at the first that does not fit, as today. If pass 1 used all slots (or dropped a required note), no other note is kept, so an optional note is never kept in place of a required one.
3. The kept notes are returned in original order. `[truncated]` is appended if any note was dropped or any note was cut (a cut sets it even if everything else is kept, as today).
Bound: the prompt grows by at most `MAX_ITEMS * MAX_ITEM` = 5000 characters from notes (was 4000 + marker); that is the cost of choice 3b.

## Cycles (red, green, commit each)
- C0: this plan, committed first.
- C1 (red): 10 optional notes then 1 required: the result is 9 optional, the required one, then `[truncated]`, in original order. Then the two-pass selection with indices.
- C2 (red): untagged notes would fill 4000 characters and a required note of 300 characters comes last, then the required note is kept and the others shrink to fit. If C1's implementation already makes it green, it is a guard, and I prove it real by a mutation check (restore the total cap on required notes, see it go red, undo).
- C3 (red): 11 required notes: the first 10 are kept, no optional note is kept, `[truncated]` is last. A required note set whose total is over 4000 characters is kept whole (3b).
- C4 (guards, green on arrival, one commit each): everything fits, so the output is unchanged; duplicate identical notes keep order; a cut required note with nothing dropped still gets `[truncated]`; the tag is matched in mixed case on a short uncut line; a `[shortened]` note counts as required; one `notes_block` assertion (11 notes, the last required, so the prompt contains it and the fix prompt shares it). The 11 existing tests in `test_review_notes.py` stay unedited; any needed edit is listed for approval first.
- C5: README sentence on the caps, `docs/spike-findings.md` Plan 28 section. No real run: pure logic, no spend.

## Risks
- A required-heavy review can crowd out optional notes. That is the intended trade.
- The note budget in the prompt rises from about 4000 to at most 5000 characters (choice 3b).
- The "required" test is a substring match on the tag, so a note that merely quotes the tag in its text is also protected. The same match exists in `_cut`.

## Residual
- More than 10 required notes still drop the later ones, marked only by `[truncated]`; a `mean` run had 4-5.
- The tag is model judgement; a wrong required note still cannot be waived (a separate item).
