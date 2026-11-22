# Plan 80: task actions in the web UI

Branch `plan-80-web-actions`, cut from `main`. Drafted 2026-11-22 from a read of `web/routes.py`, `server.py`, `job_runner.py`, `job_store.py` and the CLI parser; revised after plan review (`plan-reviewer`, spot-checked by me). **Approved 2026-11-22.**

## Problem
The web UI lists and opens tasks, follows them live, and handles the three human gates: approve a plan, reject a plan, answer questions. Everything else needs the CLI or the JSON API:

| Missing in the UI | CLI | JSON API |
|---|---|---|
| Create a task | `new` | `POST /tasks` (201) |
| Advance | `advance` | `POST /tasks/{id}/advance` (202, job) |
| Abandon | `abandon` | `POST .../abandon` (202, job) |
| Recover | `recover` | `POST .../recover` (202, job) |
| Rebase | `rebase` | `POST .../rebase` (202, job) |
| Merge an audited task | `merge` | `GET .../merge-preview`, `POST .../merge` with the shown `audited_sha` (202, job) |
| Retry a failed task (`--keep-plan`) | `retry` | `GET .../retry-preview`, `POST .../retry`: **synchronous, 201 with the new task's id; not a job** |
| Cost report | `costs` | `GET /costs` |
| Job list and result | none | `GET /jobs`, `GET /jobs/{id}` |
| Clean up | `clean` | `GET /clean-preview`, `POST /clean` |

Not UI work, by nature: `init`, `clone`, `migrate`, `serve`, `ui`, `watch`, `eval-export`. `eval-audit` is out of scope.

## Facts that shape the design (read in code; the first five also spot-checked)
1. **A wrong-state action is not refused synchronously.** `enqueue` (`server.py:204-209`) only checks the task exists, then queues. `_refusal_as` (`server.py:133-140`) turns the domain refusal into a `RuntimeError` inside the job. Abandon, recover, rebase, merge and advance return 202 even for a wrong-state task. Synchronous refusals exist only for the merge sha pre-check, merge-preview, retry and retry-preview (409/422) and clean's token.
2. **Retry is not a job** (table above). It creates a new task (`retry_of`), so the web POST redirects to the new task. Its double-submit risk is two new tasks. `retry-preview` refuses (409) any task without a keepable plan (goal, plan, matching digest, approved prompt), so a plain retry needs its own page.
3. **`runner.submit` has no dedupe** (`job_runner.py:23-26`). One worker thread runs jobs serially. A duplicate abandon, merge, recover or rebase fails; a duplicate advance runs again from wherever the first stopped (possibly spending again).
4. **`JobStore` has no per-task query** (`create`, `get`, `list`, `mark_running`, `mark_succeeded`, `mark_failed`, `interrupt_unfinished`). Job states are queued, running, succeeded, failed and interrupted (the last at server start).
5. **The task stream wakes only on task-store writes** (`server.py:158-159`). Job state writes notify nobody, and a refused job writes no task, so a job panel would lag a step or never update.
6. The routes already read `request.app.state` (`app.state.runner` is set at `server.py:451`). What they cannot reach: the job store, `ctx.settings`, `ctx.ledger`, `ctx.stages`, `ctx.clock`. The function is `_build_app` (`server.py:153`), wrapped by `create_secured_app`.
7. **Advance** loops up to 10 steps to the next human gate or a terminal state, never refuses (it does nothing and succeeds on REVIEWED, OPEN_QUESTIONS, AUDITED, MERGED, FAILED), and can succeed with `result["stopped"]` set (budget, no progress, dirty worktree, RUNNING). Valid states are the `ACTIONS` keys: PROPOSED, PLANNED, QUEUED, AGENT_DONE, VERIFIED.
8. **State rules are only partly public.** Retry: `RETRYABLE` plus a goal. Advance: `ACTIONS`. Abandon: private `_abandoned`; refused for MERGED, FAILED, RUNNING and when `fix_in_flight`. Recover: RUNNING only, inline. Rebase: AUDITED plus git checks (no preview). Merge: AUDITED plus the git checks merge-preview runs.
9. Each JSON handler keeps its own refusal tuple: `InvalidTransition` and `CalledProcessError` only for rebase, merge and merge-preview; abandon only `AbandonRefused`; recover only `RecoverRefused`; advance none.
10. `service.MAX_GOAL_BYTES` is 20,000 and the non-empty check live in the JSON create closure (`server.py:171-184`); the form body cap (`_FORM_CAP_BYTES`) is 64 KiB. The web route must apply the goal limits itself.

## Decisions (accepted by the user 2026-11-22)
| # | Question | Decision |
|---|---|---|
| D1 | Scope | Both phases. A: new task, advance, abandon, recover, rebase, merge, retry, job panel. B: cost page, jobs list, clean. |
| D2 | Shared logic | Extract into a module (`web_actions.py`, name to choose) built in `_build_app` from `ctx` and `runner` and put on `app.state` with the job store. It holds `enqueue_<kind>` for abandon, recover, rebase, merge(shown_sha), advance (each with its own refusal tuple, fact 9), `create(goal, grill)` with goal validation, and `retry(...)`. The JSON closures call it; JSON status codes and bodies do not change. |
| D3 | Confirmation | A confirmation page for every action except new task (a form). Advance's text says it runs until the next decision point and may spend on several stages. Merge's page comes from merge-preview. Waive and reinstate stay CLI-only, as on the approve page. Keep-plan retry's page says advancing the new task spends money. |
| D4 | Feedback | POST redirects (303) to the task page, which shows the latest job (kind, one of the five states, escaped and capped error, advance's `stopped`). Retry redirects to the new task and shows no job. |
| D5 | Buttons | Only actions valid for the task's state, using public predicates (fact 8). Abandon is also hidden when `fix_in_flight`. Recover is hidden while the task has an unfinished job. The domain still refuses a stale request. |
| D6 | New task | Goal plus a "grill" checkbox prefilled from `settings.grill`, always sent. Creating does not start the task. Applies `MAX_GOAL_BYTES` and the blank check. |
| D7 | Wrong-state pre-check | In the web layer only: it checks state before enqueueing and answers with a wrong-state page. The JSON API still returns 202 and a failed job. No existing test is edited. |
| D8 | Double submit | The web layer refuses an action POST while the task has a queued or running job (the check and `submit` run on the event loop with no `await` between them, so they are atomic with respect to other requests). The JSON API is unchanged. Retry accepts duplicates (a second new task). |

## Increments (test first, one commit each)
0. **Tests-touched list.** Grep tests for the routes, strings and statuses this changes (task-page tests, `test_web_*_race.py`, and every `test_server_*` test through the closures D2 extracts: abandon, recover, rebase, merge's three exceptions, the merge sha pre-check, retry's 409/422, create's 422s). Record the list here. Add guard tests, in their own commit, for any refusal path not yet covered. Check whether the tests run the lifespan (runner thread) and use `runner.wait_idle()`.
   - **Result (done).** Tests that must stay green through increment 1, and that cover each extracted closure's refusals: `test_server.py` (create 422s incl. goal-byte cap and surrogate, advance, abandon and recover refused-as-failed-job, rebase wrong state, retry 409/422 and keep-plan/waive/reinstate, costs, clean token 409/422), `test_server_merge.py` (preview, sha 409, 422, 404, failed job), `test_server_surrogate.py`, `test_server_reject_race.py`, `test_server_security.py`. Web tests that increments 4-5 touch: `test_web_changes.py`, `test_web_task_events.py`, `test_web_task_page.py`, `test_web_task_wiring.py`, `test_web_task_sections.py`, `test_web_page_wiring.py`, and the approve/reject/answer `*_race`, `*_stale` and `*_guard` tests as the patterns to copy. Tests use `served(ctx)`/`client`/`app` with `TestClient` as a context manager (runner thread runs) and `app.state.runner.wait_idle()`.
   - **Gaps.** Advance never refuses by design, so it has no refusal path to guard. No other uncovered refusal path found, so no guard commit.
1. **Refactor (D2), flagged, no red.** Extract and wire as D2; set `app.state.actions` and `app.state.jobs`. The existing server tests are the safety net.
2. Red: `JobStore.latest_for_task(task_id)`. Green.
3. Red: `JobStore.unfinished_for_task(task_id)` (queued or running). Green.
4. Red: each job state write calls an `on_change` callback, wired to `changes.notify` (thread-safe, `changes.py`); the task fragment re-renders after `mark_succeeded` and `mark_failed`, including a refused job that writes no task. Green. `_task_fragment` gets the job store; `_unless_unchanged` dedupes the combined fragment.
5. Red: the task page and fragment show the latest job (D4), including interrupted and `stopped`. Green.
6. Red: new-task form GET, and POST (303 to the task). Covers over-long goal, blank goal, the grill default, a repeated field (`_BadForm`). Green.
7. Red: public predicates in the domain (`can_abandon` including `fix_in_flight`, `can_recover`, `can_advance`, retry from `RETRYABLE` plus a goal); the domain functions use them so refusals cannot drift. One commit each.
8. Actions, one red/green each, in this order: abandon, recover, advance, rebase, merge, retry. Each covers: the confirmation page; the wrong-state page (D7); refusal while a job is unfinished (D8); the POST queues the job and redirects 303; missing and unreadable task pages; escaping. Additions:
   - Merge: the confirm GET turns `MergeRefused`, `InvalidTransition` and `CalledProcessError` into an HTML refusal (not the app-wide JSON 404/409); the POST carries `audited_sha` and checks it synchronously, so a mismatch gets a wrong-state page, not a failed job. The git runs on the event loop, as the JSON preview does.
   - Retry: synchronous; redirects to the new task; a plain-retry page when there is no keepable plan; a keep-plan page from the preview data; no waivers.
9. Guard: one parametrised test that every new `/ui` GET and POST refuses a request with no session and a cross-origin POST (the guard decides on the `/ui/` prefix and method). Likely a guard, not a red.
10. Red per state group: the task page's buttons (D5), including `fix_in_flight` hiding abandon and an unfinished job hiding recover.
11. Phase B: cost page (needs `ctx.ledger`), jobs list, clean (extract `clean_scan` and `clean_view`; preview, token round-trip, confirm).
12. Docs: README (what the UI can and cannot do), Deviations here.
13. Chromium check (headless, as plan 78): create, advance, abandon through the real UI; the panel updates without a reload.
14. Gate: ruff, `pytest -q -n auto`, `verify`, `audit-commits`.

## Risks
- One worker runs every job serially, so a UI action queues behind a long advance, even for another task. The panel must show "queued".
- Merge, rebase and advance run git and spend money; D8 covers a double submit, not two people acting at once. The domain refuses the loser and the panel shows it.
- Job failure text comes from subprocess errors: escape it (`_safe`) and cap its length. `CalledProcessError`'s text does not include stderr; `RebaseRefused` includes one git line. Not verified for every path.
- The merge-preview GET runs git synchronously on the event loop.

## Unverified
Which existing tests expect 202 for a wrong-state request, and whether any run the runner thread (increment 0). Contents of `reject.html`, `base.html`, `stages.py`, `worktrees.py`, `clean_tasks.py`.

## Out of scope
`eval-audit`; the CLI-only commands; waive and reinstate in the web UI; multi-user accounts; changing the task state machine; deduping in the JSON API; TLS.

## Deviations
- Increment 0 found no uncovered refusal path, so it made no guard commit.
- Increment 7 added `can_*` predicates for every action. `rebase_task` now repeats the `fix_in_flight` rule inside its own refusal (a duplicate of `can_rebase`'s).
- Merge's web refusal for a fix agent in flight lives in the web layer only (the domain merge does not refuse it).
- Increment 9's guard needed no source change (a guard, not red); the per-action guard files already existed.
- Increment 10: the task page offers no gated action while the latest job is queued or running, not only recover (D8 applied to every button). Retry is still offered.
- Increment 11 split into three commits: costs, jobs, clean. `clean_preview` and `enqueue_clean` closures stay in `server.py` (tests patch `factory.server.clean_candidates` and `clean_tasks`) and are exposed on `app.state`. `CleanChanged` is new. The JSON merge preview still calls `merge_preview` directly for the same reason.
- Jobs page has no per-job detail page; it shows the latest 50.
- Open follow-ups: a lone surrogate in a job error passed to `mark_failed` could break the runner; a missing worktree directory gives a 500.
