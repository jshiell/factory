# Plan 91: sentence case in the web UI

Drafted 2026-11-25, revised after plan review. Awaiting approval.
Source: `todo.md` open item "use sentence case in ui".
Base: `main` (plan 90 merged, unpushed). Branch `plan-91-sentence-case`.

## Problem
The web UI mixes styles: some prose is proper English ("New task. Creating it does not start it."), but headings, labels, links and facts are lower case: `<h2>jobs</h2>`, `goal: ...`, `state: reviewed`, `back to the task list`, `approve this plan`, `needs you`, `nothing to clean`. States, stages, job kinds and job states print as raw enum values (`open_questions`, `implement`, `succeeded`).

## Rule
Sentence case everywhere in the web UI: first letter of every heading, label, link, button, table header, phrase list item and message is capitalised; proper nouns and data are untouched. Whole sentences end with a full stop; short labels, headings, link text and fragments (for example "Nothing to clean") do not. Page title and `<h1>` become "Factory", on every page including the inline login, 401 and 403 pages.
Left exactly as stored: task ids, branch names, shas, paths, `<code>`/`<pre>` content, goal and plan text, agent-written text, failure codes (plan 90 shows the code beside its sentence), job error text.
Screen-reader text (`aria-label`) follows the same rule.

## Decisions (settled)
1. Web only. CLI output, JSON payloads and logs are unchanged.
2. States, stages, job kinds and job states are humanised for display only by a Jinja filter (`open_questions` becomes "Open questions", `succeeded` becomes "Succeeded"). Stored values, URLs, attributes, conditions and JSON stay raw.
3. Inline labels stay ("Goal: x"); no `<dl>` restructure.
4. `InvalidInput` texts ("goal must be valid text", ...) are shared with the JSON API and stay unchanged; the web route adapts them at its boundary (capitalise, add a full stop).
5. Badge renders as "Reviewed (needs you)" style: the state, then the badge in brackets, capitalised ("Needs you").
6. Out of scope: Starlette default 404/405 plain text, README prose beyond one style line, stored values.

## Surfaces
- Templates in `orchestrator/src/factory/web/templates/` (all, including `_task.html` and `_tasks.html`).
- `web/routes.py`: `_ACTION_LINKS`, `_task_detail` fact labels, `_job_view`'s `stopped_description` (label comes from the same source as the fact labels), `_TaskAction` questions/buttons, wrong-state messages, the new-task `InvalidInput` display (`str(error)` at ~659), inline login HTML (~761, ~763).
- `server.py` inline 401 and 403 HTML (~499, ~503), including their `<title>`.
- Templates strings the first draft missed: `clean.html` ("orphaned gate caches", "skipped", candidate state), `merge_confirm.html` ("branch:", "audited commit:", "root tool config", "audit findings:"), `retry_confirm.html` ("audit findings the new task is told about:", "starts at proposed"), `new_task.html` ("grill the goal first"), `answer.html` `aria-label`.
- `static/ui.js`: verified it matches no text; no change expected.

## Tests
Step 0 greps every web test (and `serving.py`) for the strings above, covering positive asserts, negatives (`not in`), regex constants, `index`/`count` and SSE `any(... in line)`, and records each hit here as fails, vacuous or unaffected. Starting set from the review:

Fail (positive assertion of old wording): `test_web_tasks_page.py:72`; `test_web_task_needs_you.py:17`; `test_web_task_events.py:160,162,181-182,202,203,256,257`; `test_web_task_job.py:31,41,54,82,107`; `test_web_task_groups.py:10-11` (`JOB_GROUP`, `FAILURE_GROUP`); `test_web_task_failure_text.py:19,20,36,37,46,69`; `test_web_task_sections.py:14-15,21,41,47-54`; `test_web_costs.py:17-20,27`; `test_web_jobs.py:17,27`; `test_web_events.py:88-89`; `test_web_task_actions.py:80`; `test_web_task_list_link.py:7,15`; `test_web_clean.py:44,52`; `test_web_merge_page.py:96`; `test_web_new_task.py:85,101` (unchanged, because the web route adapts at its boundary; confirm what they assert).

Vacuous (negative assertions that keep passing and stop guarding): `test_web_task_sections.py:9-11,101-105` (ABSENT_FOR_A_BARE_TASK), `test_web_task_job.py:47,89,96`, `test_web_task_list_link.py:17`, `test_web_task_events.py:184`, `test_web_merge_page.py:101`, `test_web_events.py:90`, `test_web_task_needs_you.py:17`. Already vacuous today because "gate failed" contains "failed": `test_web_tasks_page.py:41`, `test_web_task_page.py:19`. Each is edited in the same commit as the wording change, to the new wording or a case-insensitive check, and listed under Deviations.

Unaffected: `test_web_task_sections.py:30` (agent markdown), `test_web_retry_page.py:43`, `test_web_new_task_link.py`, and link/action tests that key on hrefs.

Rule: an existing assertion is edited only when it asserts the old lower-case wording or has gone vacuous; each edit is listed under Deviations with its reason.

### Step 0 results (2026-11-25)
Base gate on the branch (identical to `main`): `ruff check` clean, `pytest -q -n auto` 3556 passed. Grep over `tests/test_web_*.py`, `tests/web*.py` and `tests/serving.py` (the plan's `serving.py` is `tests/serving.py`; it holds only the `served` helper and asserts no text, so unaffected). Every starting-set hit was confirmed against the files; no new file turned up in the `*_states`, `*_guard`, `*_race`, `*_post_states` families.

Confirmed fails (old wording asserted): all lines in the starting set. Additions and clarifications:
- `test_web_task_events.py:160,162,202,203,256,257` assert `state: queued`/`state: failed` (state value changes in step 2, label in step 4b); `:181-182` assert `<p>worktree:`/`<p>branch:` labels (4b).
- `test_web_costs.py:17-20,27`: `total:`/`assumed:` labels (4c), `plan: $3.25`/`audit: $0.50` stage values (step 2).
- `test_web_jobs.py:17` `clean, failed`/`advance, succeeded` (step 2); `:19` `error: boom` and `:27` `clean, queued` (4c / step 2).
- `test_web_clean.py:44` `nothing to clean` (4c); `:52` asserts `failed` for the candidate state (step 2); `dirty`, `2 commits ahead` are flags and stay.
- `test_web_task_job.py:31,41,54,82,107`; `test_web_task_failure_text.py:19,20,36,37,46,69`; `test_web_task_groups.py:10-11`; `test_web_task_sections.py:14-15,21,41,47-54`; `test_web_tasks_page.py:72` (`needs you` count); `test_web_task_needs_you.py:17`; `test_web_task_list_link.py:7,15`; `test_web_task_actions.py:80`; `test_web_merge_page.py:96`; `test_web_events.py:88-89`: as in the starting set, step per label or value.
- `test_web_tasks_page.py:19` and `test_web_task_page.py:19` assert `failed` for the state; they pass today only through `gate failed` (vacuous), and stay vacuous after step 2.

Newly found, contradicts the plan: `test_web_new_task.py:85` (`goal must be valid text`) and `:101` (`goal must be at most ... bytes`) are positive, case-sensitive assertions on web response text. Decision 4 capitalises those texts at the web boundary (4e), so both will FAIL then, not stay unchanged. They are edited in 4e and listed under Deviations.
Other `InvalidInput`-shaped hits (`test_web_answer_post_states.py:21`, `test_web_answer_race.py:46`: `no open questions`) are route messages and fail in 4e.

Vacuous: as in the starting set; confirmed `test_web_task_sections.py:9-11` (lower-case labels such as `branch`, `failure`), `test_web_task_job.py:47,89,96`, `test_web_task_list_link.py:17`, `test_web_task_events.py:184`, `test_web_merge_page.py:101`, `test_web_events.py:90`.

Unaffected: `test_web_task_sections.py:30`, `test_web_retry_page.py:43`, `test_web_new_task_link.py`, `test_web_approve_page.py:46`, `test_web_approve_waive_hint.py:17`, `test_web_page_wiring.py:35`, `test_web_ui_guard.py:37` (`factory ui`; revisit at 4a because the title becomes "Factory"), the `from factory...` import lines, and tests keyed on hrefs or ids.

## Increments (TDD: one test, one implementation, one commit each; suite green at every commit)
0. Base gate on `main`; the exact-string grep; fill in the lists above.
1. Humanise filter. Red: unit tests (`open_questions` becomes "Open questions"; unknown value gets only its first letter raised and underscores replaced; `CalledProcessError`-style input keeps its capitals; returns plain `str`, so a `<script>` stage is still escaped). Green: register it. Not `capitalize` (it lowercases the rest).
2. Apply the filter to state, stage, job kind and job state on the list, task page, jobs, costs and clean pages, in output expressions only. Red: `/ui/` shows "Open questions" while the approve, answer and reject links still appear by raw state. Edit the listed asserts in the same commit; one commit per page if large.
3. Guard scaffold: stdlib `HTMLParser` helper (first non-space character of the text of p, li, h1-h3, th, td, button, label, title, a, strong; skipping code, pre, textarea, script, style and the plan/markdown divs), fixtures with distinctive lower-case data values (`zdata-...`) that the guard skips, plus a paired test that those values appear verbatim. One parametrised test with no pages yet; marked as a guard (not red).
4. Page groups. Each commit adds its pages to the guard's parameters (seen red), fixes the wording, edits the listed asserts, and lands green:
   - a. `base.html` (title and h1 "Factory"), `page.html`, `_tasks.html` (badge, "Unreadable tasks skipped").
   - b. `task.html`, `_task.html`: labels, `_task_detail` fact labels, `stopped_description` from the shared label source, section headings, `_ACTION_LINKS`, retry link, approve/reject/answer links.
   - c. Jobs, costs, clean.
   - d. Approve, reject, answer (including `aria-label`), new task, retry, merge, `action_confirm.html`.
   - e. Error pages, every message in `routes.py`, the new-task `InvalidInput` adaptation, inline login/401/403 HTML and titles.
   Fixture coverage: every `TaskState` with all optional fields set; a job in each state, including stopped plus `stopped_description`; clean candidates, orphans and skipped names; the `webmerge` audited git fixture; a keepable retry; the 404, 409, 422, 500 and new-task 422 pages; the login, 401 and 403 pages.
5. Docs: one line in README/AGENTS stating the UI style rule. Remove the `todo.md` item in the working tree only (untracked; never committed). Commit the docs.
6. Gate: `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto` (background, Monitor), then `verify` and `commit-auditor`. Record Deviations.

Dropped from the first draft: the fragment-versus-page guard (page and task templates `{% include %}` the same partials; `test_web_task_job.py:99` already asserts the fragment is in the page).

## Process
- Sonnet `implementation` agent for increments; I show wording as a table in the diff summary before merge. Short commit messages, no file lists, Sonnet 5.5 trailer; sign unsigned commits at the end (stop after the first failure). Never push. Merge to `main` by rebase.

## Risks
- Test churn, sized by step 0; vacuous negatives are the quiet risk, so each is edited deliberately.
- Humanising in the wrong place (view model instead of output) would break `task.state == "reviewed"` conditions and hide the approve/answer/reject links; step 2's test guards this.
- Capitalising a data cell by accident: the guard's sentinel values and the verbatim test protect these.
- Plan 90 wording is already sentence case; only its labels (`stopped:`, `failure:`) change.

## Not verified
- JSON/server tests asserting the `InvalidInput` texts (likely; the plan leaves those texts unchanged).
- Web test files not opened by the review (the `*_states`, `*_guard`, `*_race`, `*_post_states` families) are assumed unaffected; step 0's grep settles it.

## Deviations
Step 2 (state, stage, job kind and job state humanised in output expressions). Each edit changes only the value wording that step 2 changes; label wording stays for step 4.
- `test_web_task_events.py` (6 lines): `state: queued`/`state: failed` become `state: Queued`/`state: Failed`. Reason: state value is humanised.
- `test_web_events.py:88-90`: `queued`/`failed` become `Queued`/`Failed`, including the negative at :90. Reason: state value is humanised; the negative would otherwise go vacuous.
- `test_web_task_job.py:31,41,107`: `job: rebase, {state}` becomes `job: Rebase, {State}`; `job: merge, queued` and `job: advance` become `job: Merge, Queued` and `job: Advance` (keeps the negative guarding); `job: advance, failed` becomes `job: Advance, Failed`. Reason: job kind and state are humanised.
- `test_web_task_failure_text.py:46`: `job: advance, succeeded` becomes `job: Advance, Succeeded`. Reason: same.
- `test_web_task_groups.py:10`: `JOB_GROUP` `job: merge, failed` becomes `job: Merge, Failed`. Reason: same.
- `test_web_jobs.py:17,27`: `clean, failed`/`advance, succeeded`/`clean, queued` become `Clean, Failed`/`Advance, Succeeded`/`Clean, Queued`. Reason: same.
- `test_web_costs.py:19-20`: `plan: $3.25`/`audit: $0.50` become `Plan: $3.25`/`Audit: $0.50`. Reason: stage is humanised.
- `test_web_clean.py:52`: `failed` becomes `Failed`. Reason: candidate state is humanised (`dirty` and `2 commits ahead` are flags, unchanged).
- Not edited, left vacuous as step 0 recorded: `test_web_tasks_page.py:19`, `test_web_task_page.py:19` (pass through `gate failed`).
- Step 2's red test: its raw-state link assertions passed from the start (guards); only the `Open questions` assertion was red.
