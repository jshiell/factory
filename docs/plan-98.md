# Plan 98: advance with one click

Drafted 2026-11-28. Not yet approved.
Source: `todo.md` ("remove approval set on advance"). Base: `main`. Follows plan 97.

## Goal
Clicking "Advance this task" on the task page queues the advance at once. Today it opens a confirmation page
(`GET .../advance`) and a second click on "Advance task" posts. Only advance changes; the other actions keep their
confirmation pages.

## Findings
- `_ADVANCE` (`web/routes.py:517`) is one of six `_TaskAction`s. Its GET page renders `action_confirm.html`; its POST
  (`_action_post`) checks state and unfinished jobs, reads an empty form, re-checks, enqueues and answers 303 to the task page.
- The task page link comes from `_ACTION_LINKS` through `_offered_actions` into `_task.html` (a plain `<a href>`). The same
  partial is rendered by the live-update fragment, so a change there reaches both.
- A link cannot queue work: GET must not change state, and the `Origin` guard for writes only covers non-GET methods
  (README, "The web UI guard"). So "immediate" has to mean a POST form with a button on the task page, not a GET that acts.
- The confirmation text told the user that advance "runs until the next decision point and may spend money on several stages
  (up to 10 steps)". Removing the page removes that warning unless the button carries it.
- Tests touching this: `tests/test_web_advance.py` (GET 404 for a bad id, line 56; GET and POST refused without a session or
  from a foreign origin, line 78), `tests/test_web_task_actions.py` (`link(action)` matches `<a href=".../advance">`, so it
  breaks for advance), `tests/test_web_action_routes_guard.py` (`ACTIONS` includes advance), `tests/test_web_task_action_group.py`.
  README line 52 names advance among the actions that have a confirmation page.

## Decided
- Advance only. No change to the JSON API, the job, or `enqueue_advance`.
- The POST route and every refusal in it stay as they are (wrong state, unfinished job, bad form, missing id, no session or
  wrong origin).

## Decided by the user (2026-11-28)
1. **Mechanism.** The task page shows a small POST form with an "Advance this task" button in place of the link. The button
   carries a `title` and a short note beside it: "Runs until the next decision point. May spend money on several stages."
2. **The GET route.** Remove `GET .../advance` (it would 405 like any POST-only route) and the `_action_page(_ADVANCE)` wiring.
   `action_confirm.html` stays for the other actions.

## Increments
Each is one test, one implementation, one commit.

1. **Red:** the task page for an advanceable task contains a form posting to `.../advance`, and no `<a href=".../advance">`.
   **Green:** `_task.html` renders advance as a form button; `_offered_actions` still decides whether it appears (so the
   state and unfinished-job rules are unchanged). Update `link(action)`/`shown()` in `test_web_task_actions.py` so the
   exactly-the-valid-actions tests still cover advance (match the form action for advance, the link for the rest).
2. **Guard (not red if 1 covers it):** the live fragment shows the same form (`_task_fragment`); the existing parametrised
   fragment test, once `shown()` knows about the form, covers it.
3. **Red:** `GET .../advance` is 405 (or 404), not a confirmation page. **Green:** drop the GET wiring for advance. Adjust the
   GET cases in `test_web_advance.py` (bad id 404, and the unauthenticated/foreign-origin pair) and `ACTIONS` in
   `test_web_action_routes_guard.py` so the guard still covers the advance POST.
4. **Red:** the button's note warns about spending (`may spend money`). **Green:** add the note. (This pins the wording the
   user loses with the confirm page.)
5. **Docs:** README "Task actions" bullet: advance no longer has a confirmation page; say it posts from the task page.
   `docs/api.md` is unaffected.
6. **Final:** `ruff`, full suite in the background, verify, commit audit against this plan.

## Risks
- One click can spend money on up to 10 steps with no second prompt. That is the point of the request; the note on the
  button is the only warning left.
- A double click or a stale tab posts twice; the second is refused by the unfinished-job check (409 page), as today.
- Bookmarks of `.../advance` stop working (nothing links to them).
- The htmx live fragment replaces the nav while a user is about to click; a form inside it behaves like the existing links.

## Deviations
(none yet)
