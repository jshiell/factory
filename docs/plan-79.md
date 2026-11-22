# Plan 79: task ids shown by the web UI

Branch `plans-77-79-known-gaps` (shared by plans 77-79), after plan 78. Drafted 2026-11-22. **Draft, not approved.** Small; do last.

## Problem
Plan 75 listed two id gaps and left them: "the detail page renders `task.id` raw (`_task_detail`, the `_task.html` heading, `| urlencode`)" and a lone-surrogate id. Plan 71 fixed lone surrogates in goals and similar text with `escape_control`; ids were not covered.

## What I read (2026-11-22)
- `require_safe_id` allows only `[A-Za-z0-9_-]+` with `fullmatch` (`ids.py:9-12`), and `SqliteTaskStore.save`, `create` and `load` all call it (`sqlite_store.py:34,44,66`). So an id that reaches the UI through the store cannot hold a surrogate, control character, or markup.
- `_task.html:1` prints `{{ task.id }}` (autoescaped by the template engine: not verified in this pass) and the links use `| urlencode`.

## Unverified (increment 0, decides the plan)
- Whether any path puts an unvalidated id in front of the templates: a row written directly into the database (`list()` reads rows without `require_safe_id`, `sqlite_store.py:55-64`, so a bad row's id reaches the list page through `skipped` and the tasks list), the file store's directory names, `migrate`, and the 404/500 pages (which do not echo ids).
- Whether Jinja autoescape is on for these templates (the `task_id` is shown with `{{ }}` in many places).

## Decisions (recommendations accepted by the user 2026-11-22; the plan is not yet reviewed)
| # | Question | Proposal |
|---|---|---|
| D1 | If increment 0 finds no reachable bad id | Close the gap as "unreachable by construction", record it in the README and Deviations, add guard tests that pin `require_safe_id` on every store entry point, and change no code. |
| D2 | If a path exists (most likely `list()` and `skipped`) | Validate ids when rows are read: `list()` puts a row with an unsafe id into `skipped` instead of returning it; the UI renders skipped ids with `escape_control`. |

## Increments (test first)
0. Probe: insert a row with id `bad\ud800<b>` straight into the database; request `GET /ui/`, `GET /ui/tasks/<urlencoded>`, `GET /tasks` and `factory list`; record what each does.
1. Red (only if increment 0 shows a failure): the failing request from the probe, as a test. Green: D2.
2. Guards: a test that `save`, `create` and `load` refuse an unsafe id (they may already exist; grep first).
3. Docs: README note, Deviations here.
4. Gate: ruff, `pytest -q`, `verify`, `audit-commits`.

## Out of scope
Rejecting surrogates in goals at write time; changing id format; unreadable-row handling beyond ids.

## Deviations
Recorded after implementation.
