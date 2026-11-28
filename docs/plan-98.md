# Plan 98: advance with one click

Drafted 2026-11-28. Not yet approved. Reviewed by plan-reviewer; findings folded in.
Source: `todo.md` ("remove approval set on advance"). Base: `main`. Follows plan 97.

## Goal
Clicking "Advance this task" on the task page queues the advance at once. Today it opens a confirmation page
(`GET .../advance`) and a second click on "Advance task" posts. Only advance changes; the other actions keep their
confirmation pages.

## Findings
- `_ADVANCE` (`web/routes.py:517`) is one of six `_TaskAction`s. Its GET page renders `action_confirm.html`; its POST
  (`_action_post`) checks state and unfinished jobs, reads an empty form, re-checks, enqueues and answers 303 to the task page.
- The task page link comes from `_ACTION_LINKS` through `_offered_actions` into `_task.html` (lines 27-29 render every
  `(name, text)` as `<a href>`; retry is in the same loop). The same partial is rendered by the live-update fragment, so a
  change there reaches both.
- A link cannot queue work: a GET must not change state. So "immediate" means a POST form with a button on the task page.
  A same-origin form POST passes the guard (`server.py` `_Guard`), the CSP (`form-action 'self'`) and the SameSite cookie.
- `_task_routes` (`routes.py:822-831`) builds the GET and POST routes from one `(name, page, post)` tuple.
- The confirmation text said advance "runs until the next decision point and may spend money on several stages (up to 10
  steps)". Removing the page removes that warning unless the button carries it.
- Tests (grep of `orchestrator/tests`, `docs`, `README.md`):
  - `test_web_advance_page.py`: the whole file tests the GET page (200 page text, 409 for wrong state, 409 busy). It goes
    with the route.
  - `test_web_advance.py`: GET 404 for a bad id (line 56); GET and POST refused without a session or from a foreign origin
    (line 78).
  - `test_web_task_actions.py`: `link(action)` and `shown()` match `<a href=".../advance">`; every parametrised
    "exactly the valid actions" test (page and fragment) depends on them.
  - `test_web_action_routes_guard.py`: `ACTIONS` includes advance. The guard runs before routing, so it needs no change.
  - `test_web_task_action_group.py`: uses only `<nav class="actions">`; unaffected while the nav stays.
  - `docs/plan-80.md` (D3) is history; not edited. README line 52 describes the confirmation pages.

## Decided
- Advance only. No change to the JSON API, the job, or `enqueue_advance`.
- The POST route and every refusal in it stay as they are (wrong state, unfinished job, bad form, missing id, no session or
  wrong origin).
- `_offered_actions` keeps returning `(name, text)`; the template special-cases `advance`.
- `_TaskAction` fields `question` and `button` stay on `_ADVANCE` (unused by any page once the GET goes); noted in Deviations.

## Decided by the user (2026-11-28)
1. **Mechanism.** The task page shows a small POST form with an "Advance this task" button in place of the link. The button
   carries a `title` and a short note beside it: "Runs until the next decision point. May spend money on several stages."
   The note drops "(up to 10 steps)"; recorded in Deviations.
2. **The GET route.** Remove `GET .../advance` and the `_action_page(_ADVANCE)` wiring. `action_confirm.html` stays for
   the other actions.

## Increments
Each is one test, one implementation, one commit.

1. **Refactor (green, no new behaviour):** in `test_web_task_actions.py`, `shown()` and `link()` accept either
   `<a href=".../{action}">` or the exact string `<form method="post" action="/ui/repos/proj/tasks/{TASK}/{action}">`.
   Suite stays green.
2. **Red:** an advanceable task's page has that form for advance, no `<a href=".../advance">`, a `title` on the button, and
   the text `May spend money on several stages`. **Green:** `_task.html` renders `advance` as a POST form with the button
   labelled by `text` and a `<small>` note, inside the existing `<p>`; the URL reuses
   `{{ base | default('/ui') }}/tasks/{{ task.id | urlencode }}/advance`. Retry and other actions stay links.
3. **Guard (not red):** the live fragment shows the same form and note. The parametrised fragment test covers the form;
   add one assertion for the note only if the shared partial is not enough.
4. **Red:** with a session, `GET .../advance` is exactly 405, with no confirm page and no id echoed. **Green:** `_task_routes`
   skips the GET route when `page is None`; advance is registered with `None`. Test edits:
   - delete `test_web_advance_page.py` (the page no longer exists; its POST-side behaviour is covered by `test_web_advance.py`);
   - in `test_web_advance.py` remove the GET line in the bad-id test and the GET from the unauthenticated test, and rename
     that test (no "advance page");
   - leave `ACTIONS` in `test_web_action_routes_guard.py` unchanged (its advance GET row is now a route-agnostic guard check).
5. **Docs:** README line 52: advance is a button that posts from the task page, no confirmation page; the other four keep
   theirs. The wrong-state 409 and busy 409 still apply to the POST. Tick the `todo.md` item (not committed).
6. **Final:** `cd orchestrator && uv run ruff check`, full suite in the background, verify, commit audit against this plan.

## Risks
- One click can spend money on up to 10 steps with no second prompt. That is the point of the request; the note on the
  button is the only warning left, and it ships in the same commit as the button.
- A double click or a stale tab posts twice; the second is refused by the unfinished-job check (409 page), as today.
- Bookmarks of `.../advance` now answer 405 (nothing links to them).
- A swap of the live fragment between mousedown and mouseup eats a click; same as the existing links.
- Styling of a form button inside `<nav class="actions">` is cosmetic and untested.

## Deviations
- The review and the first draft missed the rule from plans 72 and 76 that the live fragment holds links, never forms
  (`test_web_reject_link.py`). The user chose to allow the one advance form; that test now strips the advance form before
  asserting no `<form`. Commit `Allow the advance button in the live fragment`, made after the button commit, so the suite was
  red between the button commit and that one.
- Increment 3 (fragment guard) was a new test, not an existing parametrised one; it passed at once.
- `test_web_action_ids.py` also covered the advance GET page; its GET tests now use `PAGE_ACTIONS` (no advance).
- `test_web_advance_page.py` was deleted with the GET route.
- The button note drops "(up to 10 steps)"; `_ADVANCE.question` and `.button` stay unused.
