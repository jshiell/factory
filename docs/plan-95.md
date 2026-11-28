# Plan 95: waive and reinstate reviewer notes in the web UI

Drafted 2026-11-28, revised after a plan review. Approved and implemented 2026-11-28.
Source: user decision while planning plan 96 ("Waivers: add them to the web forms first").
Base: `main`. Prerequisite of plan 96 (remove the task CLI).

## Problem
The terminal is the only place to waive a required reviewer note (`factory approve`, `factory retry --keep-plan`) or reinstate one waived earlier (`r N`). The web approve page says "run `factory approve` in a terminal instead" and posts `waived=()`; the keep-plan retry catches `InvalidWaiver` as "the UI sends no waivers". Removing the CLI would drop the feature.

## Decided (user, 2026-11-28)
- **One submit button** per form. Whatever is ticked is waived (or reinstated). The page text says what waiving means (not sent to the agents, not enforced by the audit). No confirm step.
- **Select by note text, matched in escaped form.** Checkbox value and label are `escape_control(note)`. The server maps each posted value back to the raw note by comparing it with `escape_control` of each current candidate. A value matching no candidate, or two different raw notes, goes to the refusal page. Reason: raw text in `value=` would 500 the page on a lone surrogate (it renders today), and NUL or format characters would not round-trip.
- **Dropped audit gaps:** a fixed sentence on the retry page: "An audit finding that quotes a waived note is not sent to the new task." (`retry_with_plan` drops such gaps silently.)
- Waive and reinstate are never combined (the domain already refuses it). Notes beyond the cap are not offered (the domain would refuse them).
- Domain code (`waiver.remove_waived`, `service.approve`, `retry_with_plan`, `WebActions.retry`) is unchanged; only routes and templates change. Terminal helpers in `waiver.py` stay until plan 96.

## Findings (plan review, read from source)
- `remove_waived` refuses a note that is not required or not present; one occurrence per entry, so duplicates work. `service.approve` re-raises `InvalidWaiver` before its `ValueError` branch. `WebActions.retry` already accepts `waive` and `reinstate`; `retry_with_plan` refuses waive plus reinstate, an unknown reinstate, and reinstating past `MAX_ITEMS`, all as `InvalidWaiver`.
- Domains compare against `capped_findings(task.review_findings)` (whitespace collapsed, long notes cut with the `SHORTENED_REQUIRED` prefix). The approve page already computes this list and discards it (`lines, _ = note_lines(...)`, `routes.py:421`); the second value is the candidate list. Use `capped_findings` on the retry page too.
- `_single` (`routes.py:369-374`) refuses repeated fields; `waive` and `reinstate` need a multi-value reader.
- The keep-plan page shows no reviewer notes today (`_kept_plan`, `retry_confirm.html`: plan and audit findings only).
- `service.approve` never deals with audit gaps; only `retry_with_plan` does (`retry_task.py:62`).
- Autoescape is on (`routes.py:44`). Form limits: 64 KiB body, 32 fields (about 19 ticked boxes plus fixed fields; more gives the 422 page).
- The routes pass `task.review_findings` loaded at POST time, not what the page showed, so only the plan digest binds the form. A stale waive value fails closed through `remove_waived`. Known limit.
- `InvalidWaiver` is a `ValueError`: in `_approve` its handler must come before `_UNREADABLE`. `_retry` already orders them correctly.

## Tests that assert strings this touches (grepped 2026-11-28)
- `tests/test_web_approve_waive_hint.py:17` ("To waive a note, run <code>factory approve ...</code>"): replaced in cycle 1.
- `tests/test_web_approve_page.py` (`test_the_approve_page_shows_what_the_cli_preview_shows...`): pins the `<pre>` findings block with `[1] ...` and `  - Recommend (optional) ...` lines, `"factory approve" in response.text`, exactly one form with exactly two inputs/buttons. All change in cycle 1; edited deliberately and said so in the commit.
- `tests/test_web_retry_page.py:48,62,63,80` ("Retry from scratch", "Retry keeping the plan", `plan_digest` absent from the plain form): the from-scratch form must stay as is; the keep form gains inputs.
- `tests/test_web_task_sections.py:9,14` ("Review findings", `<pre>`): task page, unaffected unless `_task_detail` changes.
- Other approve and retry tests (`test_web_approve*.py`, `test_web_retry*.py`): rerun as the guard; any that count form fields or pin text are edited with the same explicit note.

## Cycles (red first; one test, one change, one commit each; full gate green)
1. **Approve page shows waive checkboxes (red).** One checkbox per capped required note (optional notes stay plain text); value and label `escape_control(note)`, label keeps `[N]`; the waiving explanation is present; the "run `factory approve`" sentence is gone. Notes include a quote, `<`, a lone surrogate, a control character and a bidi mark: status 200. Replaces the hint test and updates the page test.
2. **Approve POST waives (red).** `waive=<escaped note>` approves; the note is in `waived_findings` and not in `review_findings` sent on. Add a multi-value form reader and the escaped-to-raw mapping.
3. **Approve, unknown waive value (red).** 409 refusal page with a fixed sentence (no note echoed), task stays `reviewed`. Catch `InvalidWaiver` before `_UNREADABLE`; a no-match or ambiguous value gets the same page.
4. **Approve, awkward notes round-trip (red or guard).** Waiving the surrogate, control-character and bidi notes succeeds; a note posted twice but stored once is refused; two identical notes both ticked are both removed.
5. **Approve with no `waive` (guard).** Approves everything; cite the existing test if it covers it.
6. **Keep-plan page shows notes and waive checkboxes (red).** Capped notes with required ones numbered, the dropped-required warning, checkboxes for required notes, and the fixed audit-gap sentence. From-scratch form unchanged.
7. **Keep-plan POST waives (red).** New task has the note waived; an audit gap quoting it is absent from `prior_audit_findings`.
8. **Keep-plan, unknown waive value (red).** Refusal page, no new task; replace the "UI sends no waivers" comment and sentence.
9. **From-scratch retry posting `waive` or `reinstate` (red).** 422 `_BadForm`, no new task.
10. **Keep-plan page shows waived-earlier with reinstate checkboxes (red).**
11. **Waive plus reinstate in one POST (red, written before reinstate is wired).** Refused, nothing queued. Then read `reinstate` and pass it through; the domain refuses the combination.
12. **Reinstate POST (red, or guard after 11).** New task has the note active again. Unknown text and reinstating past `MAX_ITEMS` are both refused (the second shows the 422 or refusal page; the 32-field cap is noted).
13. **Docs.** README line 162 (waiver checkboxes; keep the "or with the JSON `waive` field" clause) and line 175 (waive and reinstate on the retry page); note the known limit. Add Deviations. Update plan 96: drop "waived-earlier notes" from its accepted losses (line 13) and its "waive in the terminal" README item. Tick `todo.md` only if it lists this (never commit it).
14. Run the `verify` skill, then the `commit-auditor` against this plan.

## Risks and unknowns
- **Not verified (check in the first cycle that touches each):** that an `InvalidWaiver` raised inside the store update callback propagates unchanged and leaves the row untouched; that `base.html` declares UTF-8 so browsers post UTF-8; that the audit reads `review_findings`, not `waived_findings`; whether `/retry-preview` numbers notes as the page will (only matters for parity with the CLI).
- **Waiving is one click away.** It weakens the audit; the page text is the only guard (decided).
- Ticking about 19 or more boxes hits the 32-field cap and shows the bad-form page; `_MAX_FORM_FIELDS` may be raised with a comment if that is judged too tight.
- The web UI is still unchecked in a real browser (README: plans 69 to 75).
- No dependency changes expected.

## Deviations (implementation, 2026-11-28)
- Cycle 5 is covered by an existing test (guard); cycle 12 is three guards, since cycle 11 wired `reinstate`.
- Cycle 8 changed the refusal sentence to name the waive choice; `tests/test_web_retry.py:127` still passes on its "cannot be retried" wording.
- `test_web_approve_page.py` and `test_web_retry_page.py` were edited deliberately (new "factory approve"-free text, hidden-input filter, control count, leading waive checkbox).
- The README accepted-losses item on waiving in the terminal belongs to plan 96; its README line 341-342 CLI text is left for that plan.
- The 32-field cap risk is nearly unreachable (at most 10 required notes per form, and reinstating past the cap is refused anyway), so no test or cap change was made; the plan estimate of about 19 boxes was wrong.
