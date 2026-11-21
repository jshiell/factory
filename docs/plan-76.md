# Plan 76: answer questions and approve a plan from the web UI

Branch `plan-76-answer-approve`, cut from `main` (plans 69-75 are merged and pushed). Facts below come from a read-only pass over the code on 2026-11-21 (file:line cited) and a plan review; anything not read is listed under Unverified.

## Problem
The UI can reject a reviewed plan (plan 72) but not accept it, and shows open questions without letting you answer. Both still need the terminal (`factory answer`, `factory approve`) or the JSON API. This plan adds those two actions. Merge, abandon, retry and waivers stay out (see Out of scope).

## Decisions
| # | Question | Decision |
|---|---|---|
| D1 | Scope | User: answer and approve, **no waivers**. Merge, abandon, retry excluded. |
| D2 | What proves a human | User: the existing `/ui` session and the plan 72 Origin rule, nothing extra. Consequence in Risks (the TTY rule is CLI-only). |
| D3 | Answer form | User: one text field per question, the recommended answer prefilled. A blank field takes the recommended answer, as an empty line does in the CLI (`cli/tasks.py:108-114`). |
| D4 | Branching | User: new branch from `main`, one plan. |
| D5 | Approving with required notes | `y` in the CLI approves with no waiver; required notes stay in the agent prompt and the audit unless waived (`approve_plan.py:57`). The UI passes `waived=()`. Only the *capped* notes reach the agent (`notes_block`, `review_notes.py:71-76`; max 10), so the approve page shows what the CLI preview shows (`server.py:228-232`): capped findings, required notes numbered, and the dropped-required warning when it applies. Its text says required notes are kept in the prompt and the audit "except any listed as dropped" and that waiving is CLI-only (`factory approve`). |
| D6 | Approve binding and failures | The form carries `plan_digest` (hidden). The POST calls `service.approve` (`service.py:68-78`) with `waived=()` and the task's own stored `review_findings`. It maps `NotReviewed` -> 409 wrong-state page and `PlanChanged` -> 409 plan-changed page. User-approved choice (a): `service.approve` turns every `ValueError` into `PlanChanged`, so a row that becomes unreadable between the load and the call also shows the plan-changed page, not the 500 page. A task enters `reviewed` once (`task.py:76-97`), so a stale digest is reachable only with a forged field (to confirm, increment 0). |
| D7 | Answer binding | The form carries `round` (hidden); a stale round is `RoundChanged` -> 409 "questions changed" page (`answer_questions.py:23-24`). Real: a new round can arrive while a tab is open. |
| D8 | Field names | Positional (`q0`, `q1`, ...), never the question `number`: `_parse_questions` does not check uniqueness (`plan_task.py:172-183`). The POST builds `N. text` lines from the task's stored questions in order, joined by newlines, as the CLI does (`cli/tasks.py:116`). Single-line `<input type="text">`. Unknown `q<i>` fields are ignored. |
| D9 | Form parsing | `python-multipart` is not installed (absent from `pyproject.toml:7-12` and `uv.lock`), so `request.form()` is out and no dependency is added (user choice). Parse by hand, rules: read `request.stream()` and stop at a cap of 64 KiB (above 3 x 20,000 bytes, as percent-encoding triples non-ASCII bytes, so the answer-size 422 stays reachable); Content-Type other than `application/x-www-form-urlencoded` -> 422; decode ASCII (failure -> 422), then `parse_qs(..., encoding="utf-8", errors="replace", max_num_fields=<n>)`, mapping its `ValueError` to 422; a repeated `round`, `plan_digest` or `q<i>` -> 422; `round` must be `isascii() and isdigit()` (as `waiver.py:45`). Over the cap -> 422. |
| D10 | Routes | `GET` and `POST` on `/ui/tasks/{id}/answer` and `/ui/tasks/{id}/approve`; the task fragment gets links, never forms (as `_task.html:4-6`). Each POST redirects 303 to the task page. |
| D11 | Refusal pages and check order | HTML in the `/ui` layout, never echoing the id: 404 missing or invalid, 500 unreadable (reuse `_load_task`), 409 wrong state, 409 changed (plan or round), 422 bad input. One new `task_wrong_state.html` takes a message, since `task_not_reviewed.html` says "cannot be rejected"; the reject pages are untouched. POST order: answer = load -> state -> parse body -> round and answers -> domain call; approve = load -> state -> digest present -> domain call. So a wrong-state task posted a bad body gets the 409 page. `except AnswerRejected` comes before the `_UNREADABLE` tuple (`routes.py:21`), since `AnswerRejected` is a `ValueError` (`answer_questions.py:13`). |
| D12 | Domain calls | Answer: `answer_questions.answer_questions`. Approve: `service.approve`. The routes catch every domain exception themselves so nothing reaches the JSON handlers (which echo the id), as plan 72 D5. |
| D13 | Default answer | One helper, `" ".join(escape_control(recommended).split())`, produces both the prefilled value and the blank fallback, so submitting the page unchanged and submitting blanks store the same text (browsers strip CR/LF from a text input without a space; `escape_control` is in `textsafe.py`). |
| D14 | Answer page questions | A separate builder for the answer page using `question.get("body", "")`. `_task_detail` is not changed: existing fixtures build questions without `body` (`test_web_task_sections.py:34-35,70,86`, `test_web_task_question_numbers.py:12-13,27`). |

## Verified (2026-11-21, by reading)
- Answer: `answer_questions(task_id, store, round_number, text)` (`answer_questions.py:17`); empty or over 20,000 bytes -> `AnswerRejected` (`:19-20`); wrong state -> `InvalidTransition` (`task.py:80`); round mismatch -> `RoundChanged` (`:23-24`); success moves to `proposed`, clears `open_questions`, appends `{round, questions, answers}` to `grill_log` (`:25-28`).
- Questions: dicts with `number`, `title`, `body`, `recommended` (`plan_task.py:41-52`); `grill_rounds` is the round (`:155`); max 10 (`:29`).
- Approve: `approve_plan` applies the transition, then checks the digest, then the findings (`approve_plan.py:51-56`); the digest is sha256 of the plan text only (`digest.py:4-5`); the preview is built inline at `server.py:224-233` with a fresh `plan_digest(task.plan)`; approve needs fresh == shown == stored.
- Web UI: `_HUMAN_GATE_STATES` (`routes.py:20`); reject link, confirm page and POST (`routes.py:87-108`, `reject.html`, `_task.html:4-6`); guard write rules (`server.py:500-521`). `factory ui` has no TTY check (`cli/tasks.py:20-25`).
- README says the TTY rule exists "so an agent or a script cannot approve itself" (`:392-395`), "its only action is the Reject link" (`:153`), "Reject is the only action so far" (`:163`).

## Unverified (increment 0 or the manual check)
- That nothing writes `review_findings` or `plan` of a `reviewed` task without going through `apply`.
- Whether the sample repo's `seed.py` exists and can seed an `open_questions` task.
- How a browser sends these forms: `Origin`, `Sec-Fetch-Site` (plan 72 found it absent on the loopback reject POST), and that CR/LF stripping behaves as D13 assumes.
- Existing pins listed below still pass after the fragment gains links.

## Tests touched (CLAUDE.md step 2)
- `_task.html` gains links, so every fragment assertion is re-read: `test_web_reject_link.py` (reject link only in `reviewed`; no `<form`, `<script`, `<style` for every state, `:27-31`), `test_web_task_sections.py:33-41` (`1. which database`, `recommended: sqlite`), `test_web_task_question_numbers.py`, `test_web_task_needs_you.py:12`. The new links must not alter those substrings.
- Models for the new tests, not edited: `test_web_reject_page.py`, `test_web_reject_ids.py`, `test_web_reject.py`, `test_web_reject_guard.py`, `test_web_post_origin.py`; helpers `tests/webposts.py`, `tests/webhelpers.py`. Approve fixtures must set `plan_digest` (`add` does not).
- No existing assertion is expected to change. If one must, stop and re-plan.

## Increments
One failing test (or one parametrized behaviour), minimal change, one commit each. "Guard" means earlier code already satisfies it; the commit says "Guard:".

0. **Branch and baseline.** Cut `plan-76-answer-approve` from `main`; ruff and the full suite; settle the Unverified items code can settle; no commit.

Answer:
1. **Link in the fragment (red).** An `open_questions` task's fragment holds `<a href="/ui/tasks/{id}/answer">` (`| urlencode`); no other state's does (parametrized).
2. **Answer page (red).** `GET` for an `open_questions` task: layout page with the goal, each question's number, title and body (escaped), one `<form method="post">` to the same path with hidden `round` and one `q<i>` input per question prefilled with the D13 default, one submit button, a back link; no inline script or style (D14 builder).
3. **Answer page, other states (red).** Every other state gets the 409 page: no form, no id, task unchanged.
4. **Answer page, bad ids (guard if 2 reused `_load_task`, else red).** Missing, invalid, unreadable ids give 404, 404, 500 pages.
5. **Answer POST (red).** The right round and every field: 303 to the task page; task `proposed`; `grill_log` holds the round, the questions and `N. text` lines in order.
6. **Blank means default (red).** A blank or missing `q<i>` becomes the D13 default; surrounding whitespace stripped; a newline in a value becomes a space.
7. **Unchanged equals blank (guard if 6 used the D13 helper, else red).** Posting the page's prefilled values (including a control-character and a multi-line recommended answer) stores the same `grill_log` entry as posting blanks.
8. **Bad input (red, parametrized, one 422 page).** Missing or non-ASCII-digit `round`; wrong Content-Type; body over the cap; a repeated field; too many fields; answers over 20,000 bytes while under the cap; task unchanged. Change: the D9 parser and `AnswerRejected` (caught before `_UNREADABLE`).
9. **Stale round (red).** 409 "questions changed" page; task unchanged.
10. **Other states (red).** A non-`open_questions` task posted a valid or bad body gets the 409 page; nothing saved.
11. **Store failures (red).** A wrapper that raises `TaskNotFound` after the first load gives the 404 page; `ValueError` gives the 500 page; never a JSON body; no id echoed.
12. **Race (guard).** A wrapper that moves the task out of `open_questions` between the load and the call gives the 409 page.
13. **Guard rules (guard).** No cookie 401, foreign `Origin` 403, non-loopback Host 403, `Sec-Fetch-Site: cross-site` 403, `POST` without `Origin` 403, `GET` without it passes; the five security headers on the 200, 303, 409, 404, 422 and 401 responses.

Approve:
14. **Link in the fragment (red).** A `reviewed` task's fragment holds the approve link beside the reject link; no other state's holds it (parametrized); the reject link test stays green.
15. **Approve page (red).** `GET` for a `reviewed` task: goal, plan in a `<pre>`, risks, capped findings with required notes numbered, the dropped-required warning when it applies, the D5 sentence, a `method="post"` form to the same path with hidden `plan_digest` and one button; escaped; no inline script or style.
16. **Approve page, other states (red).** Every other state gets the 409 wrong-state page (no form, no id, unchanged).
17. **Approve page, bad ids (guard if 15 reused `_load_task`, else red).** 404, 404, 500.
18. **Approve POST (red).** The shown digest: 303 to the task page; task `queued`; `waived_findings` empty; the capped required notes are in the implementation prompt; the fixture sets `plan_digest`.
19. **Missing digest (red).** Missing or empty (or repeated) `plan_digest`, wrong Content-Type: 422 page; unchanged.
20. **Wrong digest (red).** 409 "plan changed" page, distinct from wrong-state; unchanged.
21. **Other states and double submit (red).** Every non-`reviewed` task gets the wrong-state 409 page; a second POST after approval too. (Two truly concurrent POSTs may both succeed: plan 72 D7 lost update; harmless, documented.)
22. **Store failures (red).** `TaskNotFound` after the first load -> 404 page; a `ValueError` -> the 409 plan-changed page (D6 choice a); never JSON; no id echoed.
23. **Race (guard).** A wrapper that moves the task out of `reviewed` between load and call gives the wrong-state 409 page.
24. **Guard rules (guard).** As increment 13 for the approve routes.

25. **Docs.** README: the answer and approve pages; what approve does (no waivers; required notes kept except those dropped) and does not do; amend `:392-395` (the TTY rule is CLI-only; the JSON API and now a UI session can approve), `:153`, `:160`, `:162` (a cookie replay from another loopback server could now approve or answer, not just reject), `:163`, and `:63`/`:66` (the TTY rule does not apply to the UI); fill the Deviations section here.
26. **Gate.** `cd orchestrator && uv run ruff check`, then `uv run pytest -q -n auto`; the `verify` skill; `audit-commits` against this plan.
27. **Manual check (the user runs it; I first extend the sample repo's `seed.py` if it exists, to seed an `open_questions` task).** Answer with defaults and with edited answers; submit a control-character or multi-line recommended answer unchanged; approve a reviewed plan with a required note and confirm the note is kept; stale answer tab after a new round shows the 409 page; reload after the POST; `Origin` and `Sec-Fetch-Site` on both POSTs (DevTools, Preserve log). The stale-digest approve case is not reachable from a tab.

## Out of scope
Waivers and reinstating (CLI-only, as the watcher); merge, abandon, retry, rebase, recover; a stronger proof of presence than the session (D2); a findings digest (findings cannot change in `reviewed`); server-side per-question validation (the server accepts any non-empty text; the CLI and UI send every question); question-number uniqueness; a compare-and-swap in the store (plan 72 D7 applies); distinguishing a damaged row from a stale plan in approve (D6); any JSON API change; session idle expiry; the jobs and costs views; a `python-multipart` dependency.

## Risks
- **The README's human-only claim weakens.** The TTY check is in the CLI only (`cli/tasks.py:96,152`); the JSON API already approves for any Bearer-token holder and `factory ui` mints a session with no TTY check, so anything that can read the token can now also approve through a browser session. No power beyond the token, but the README says otherwise and is corrected (increment 25). User choice (D2).
- **Cookie-replay escalation.** Plan 72's accepted risk (another loopback server replaying the session cookie) was "could reject a reviewed task"; it now includes approving a plan (queues work that spends money and later merges code) and answering questions. D2 accepts it; the README says so.
- Prefilled recommended answers make accepting every recommendation one click; the CLI has the same shortcut (Enter).
- The approve page is a review-and-confirm page; only a real browser shows whether it is readable enough to decide from.
- First `/ui` POSTs that read a body: the hand parser (D9) is new code, capped and tested.
- Browser behaviour of the forms is read from plan 72's experience, not tested; a wrong reading fails closed (403), which only the manual check shows.

## Deviations
- Increments 4, 7, 10, 13, 16, 17 and 24 were guards (the code already satisfied them). 16 and 17 were guards because increment 15 included the state check and `_load_task`.
- Increment 8 has four tests, not one: the parametrized 422 test, two guards (content-type parameters and case; a body of exactly the cap), and a direct ASGI test that the reader stops at the cap (the test client sends the whole body at once).
- Increment 8 also added the POST's load-refusal and wrong-state checks (needed for the check order); increments 10 and 11 then covered them.
- Increment 22b (not planned): a `KeyError` from an unreadable row escaped `service.approve` into the JSON handler, so the approve POST also catches `_UNREADABLE` and shows the 500 page.
- Increment 23 was red, not a guard: `NotReviewed` otherwise reached the JSON handler, which echoes the id.
- The stale-round and plan-changed pages reuse `task_wrong_state.html` with their own messages; no new templates beyond `answer.html`, `approve.html`, `task_wrong_state.html` and `form_rejected.html`.
- A recommended answer containing CR/LF is stored with a literal `\x0d` (D13: `escape_control` escapes `\r` before the whitespace split). The prompt then escapes it a second time. Typed answers are whitespace-collapsed only, like the CLI, since the planner prompt already escapes the log.
- Not done: the manual browser check (increment 27) is pending.
