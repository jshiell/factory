# Plan 91: sentence case in the web UI

Drafted 2026-11-25. Awaiting plan review and approval. Decisions made by the user: web only; humanise states, stages and job kinds; keep the inline label layout.
Source: `todo.md` open item "use sentence case in ui".
Base: `main` (plan 90 merged, unpushed). Branch `plan-91-sentence-case`.

## Problem
The web UI mixes styles: some prose is proper English ("New task. Creating it does not start it.", "That task does not exist."), but headings, labels, links and facts are all lower case: `<h2>jobs</h2>`, `<h3>by stage</h3>`, `goal: ...`, `state: reviewed`, `back to the task list`, `approve this plan`, `unreadable tasks skipped: ...`, `needs you`, `nothing to clean`. States, stages and job kinds print as raw enum values (`open_questions`, `implement`).

## Rule (proposed)
Sentence case everywhere in the web UI: first letter of every heading, label, link, button, table header, list item that is a phrase, and message capitalised; proper nouns and data untouched. Whole sentences end with a full stop; short labels, headings and link text do not. Page title and `<h1>` become "Factory".
Left exactly as stored (never capitalised or rewritten): task ids, branch names, shas, paths, `<code>` content, goal and plan text, agent-written text, failure codes (plan 90 shows the code beside its sentence), job error text.

## Scope
In: every file in `orchestrator/src/factory/web/templates/` (including the SSE partials `_task.html`, `_tasks.html`), the Python-built UI strings in `web/routes.py` (`_ACTION_LINKS`, `_task_detail` fact labels, `_job_view`'s `stopped_description` label, the `_TaskAction` questions/buttons, wrong-state messages) and `web_actions.py` messages that reach a page (`MERGE_CHECK_FAILED`, `CLEAN_CHECK_FAILED` are already sentences), and `static/ui.js` if it writes visible text.
Out: CLI output, JSON payloads, log text, README prose, stored values.

## Decisions (settled by the user)
1. Web only. CLI output, JSON payloads and logs are unchanged (CLI lines are grep-friendly and asserted by many CLI tests).
2. States, stages and job kinds are humanised for display only, by a small Jinja filter (`open_questions` becomes "Open questions"); stored values, URLs and JSON stay raw.
3. Inline labels stay ("Goal: x"); no `<dl>` restructure.

## Tests: strings that will change
Step 0 (before any code): grep every web test for the exact strings touched, list each hit here as affected or not, and record them under Tests. Known from a first count (needs the full grep, not trusted): matches for "back to the task" (1 file), "needs you" (2), "state: " (1), "job: " (3), "job error" (2), "stopped: " (2), "open questions" (3), "new task" (2), "nothing to clean" (1), "total: " (1), "assumed: " (1), "review findings" (1), "audit findings" (1), `<h2>` (1), plus `test_web_task_sections.py` fact labels (`plan error`, `review error`, `audit error`, `fix rounds`, `retry of`, ...), `test_web_task_groups.py` regexes (`JOB_GROUP`, `FAILURE_GROUP`), and the plan-90 tests (`test_web_task_failure_text.py`).
Rule: an existing assertion is edited only when it asserts the old lower-case wording; each such edit is listed under Deviations with its reason (the intended output changed), never to hide a behaviour change.

## Increments (TDD: one test, one implementation, one commit each)
0. Base gate on `main`; the exact-string grep; list hits above.
1. Red: a guard test that renders every web page and partial (task list, task page in each state, new task, approve, reject, answer, confirm pages, jobs, costs, clean, error pages) and asserts that no visible text node, link, button, heading or table header starts with a lower-case letter (excluding `<code>`, `<pre>`, ids, and a short allow-list of data cells). This is the "does it look like English" guard; it fails on everything. Then make it pass page group by group, one commit per group:
   - a. Base layout, list page, `_tasks.html`, `<title>`/`<h1>` ("Factory"), the "needs you" badge, the "Unreadable tasks skipped" line.
   - b. Task page and `_task.html`: state line, goal, job section, failure, fact labels, section headings (Plan, Open questions, Risks, Review findings, Audit findings, Waived findings), action links ("Advance this task", ...).
   - c. Jobs, costs ("Costs", "By stage", "By task"), clean.
   - d. Approve, reject, answer, new task, retry, merge and the shared `action_confirm.html`.
   - e. Wrong-state, missing, unreadable and form-rejected pages and every message in `routes.py`.
2. Humanise filter: red, a filter test (`open_questions` becomes "Open questions"; an unknown value is capitalised and underscores replaced), then use it for state, stage and job kind on the task, list, jobs and costs pages. Raw values stay in URLs and attributes.
3. Visible-text check in the SSE partial path: the `/ui/events` stream and `/ui/tasks/<id>/events` fragments render the same partials; add a guard that the fragment text matches the full page's text.
4. README/AGENTS: add one line stating the UI style rule (sentence case, what stays raw). Remove the `todo.md` item in the working tree only (untracked; never committed). Commit the docs.
5. Gate: `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto` (background, Monitor), then `verify` and `commit-auditor`.

## Process
- Sonnet `implementation` agent for the increments; I review wording as a table in the diff summary before merge. Commits short, no file lists, Sonnet 5.5 trailer; sign unsigned commits at the end (stop after the first failure). Never push. Merge to `main` by rebase.

## Risks
- Test churn: any test matching on lower-case wording needs a reasoned edit; step 0 sizes it before work starts.
- htmx swaps replace fragments; a capitalisation change must be made in the partial so the full page and the live update agree (step 3).
- Capitalising a data cell by accident (a branch name, a code): the guard's allow-list and the "left as stored" list above protect these; a test per case.
- Plan 90 wording (descriptions and next steps) is already sentence case; only its labels (`stopped:`, `failure:`) change.
