# Plan 71: escape control characters on the web UI list page

Reviewed (2026-11-21); findings folded in. Branch `plan-71-escape-list`, cut from `plan-70-task-detail` (plans 69 and 70 are not merged).

## Problem
The task detail page runs agent-written text through `escape_control` (`textsafe.py`). The list page does not. Jinja autoescape stops HTML injection but not control and bidi format characters, so a goal or failure reason can still reorder or hide text in the table. This plan applies the same escaping to the list. It changes the list's output only for strings that contain such characters.

## Decisions (recommended options; stand unless the user says otherwise)
| # | Question | Chosen | Alternative |
|---|---|---|---|
| D1 | Which list fields? | `goal`, `failure_reason`, the task id, and the `skipped` names | `goal` and `failure_reason` only |
| D2 | Order with truncation | Truncate first, then escape. Escape expansions then do not eat the 120-character limit, and truncation (by code point) cannot cut an escape such as `\x0d` in half | Escape first |
| D3 | Where | In `_task_row` and `_tasks_context`, using the existing `_safe`; templates unchanged | A Jinja filter |
| D4 | The id's `href` | Follows the escaped id, so the template stays unchanged. A task whose id holds a control character cannot be opened either way, because `load` rejects it (`require_safe_id`, pattern `[A-Za-z0-9_-]+`) and the link gives the 404 page | A separate raw href field and a template edit; no better, same 404 |

## Verified (2026-11-21)
- `_task_row` (`routes.py:27`) builds `goal` via `_truncated` and passes `failure_reason` as is; `_tasks_context` (`:32`) passes `skipped` as is. `_safe` (`:38`) escapes strings only, so `None` stays `None` and the template's `or ""` still works.
- `_tasks.html` renders `task.id`, `task.goal`, `task.failure_reason` and `skipped | join(", ")`; the `href` is `task.id | urlencode`.
- `escape_control` keeps `\n` and `\t` (`textsafe.py:8`). U+2028 is not Cc or Cf, so it is not escaped; `_event`'s `splitlines()` handles it (plan 69).
- `SqliteTaskStore.list` (`sqlite_store.py:59-68`) never calls `require_safe_id`. A listed task's id comes from the JSON blob (`task_from_dict`), not the `id` column, so the two can differ. `skipped` holds the raw `id` column, which is `TEXT PRIMARY KEY` with no CHECK (`:23`).
- The web UI uses `SqliteTaskStore` (`cli/__init__.py:312`). The file store's `skipped` (`path.name`) is not reachable from the UI.
- **Unverified:** that the installed httpx's `iter_lines` splits on a lone CR; the plan avoids depending on it.

### Tests that touch the list's strings (grepped; all expected to pass unedited)
- `test_web_tasks_page.py`: `:37-39` ("fix the widget", "gate failed", plain); `:46-48` (`"x"*200`: `"x"*120 + "…"` present, `"x"*121` absent); `:61` (id ordering); `:76-80` (`broken-one` in skipped); `:98-102` (`SCRIPT` escaped exactly twice, no control characters).
- `test_web_task_links.py:14` (id links in page and fragment; ids are safe-id strings, so unchanged).
- `test_web_events.py:62` ("first goal"), `:74` (`"one\rtwo\r\nthree"`), `:83-86` ("g").
- `test_web_login.py`, `test_web_ui_guard.py`, `test_web_page_wiring.py`, `test_serve_stopping.py` only check status, headers or wiring.
- **Coverage loss, accepted and compensated by increment 6:** after escaping, the CR test's goal becomes `one\x0dtwo\x0d` plus a real newline plus `three`. The test still passes, but no longer reaches `splitlines()`'s CR branch; a regression to `split("\n")` would go unnoticed.

## Increments
One failing test (or one parametrized behaviour), the minimal change, one commit each. "Guard" means earlier code already satisfies it and the test is committed with that label.

0. **Branch and baseline.** Cut `plan-71-escape-list`; run the gate; no commit.
1. **Goal escaped (red).** A goal holding BEL and U+202E shows escaped in the page and in `render_tasks`. Change: `_safe(_truncated(...))`.
2. **Failure reason escaped (red).** Same for `failure_reason`. Sub-assertion (guard): a task without a failure reason shows no `None` in the page. Change: `_safe(task.failure_reason)`.
3. **Truncation order (guard).** A goal of 121 BEL characters shows `\x07`×120 followed by `…`, and `\x07`×121 is absent. Increment 1 already truncates first.
4. **Skipped names escaped (red).** A raw INSERT with column id `broken\x07one` and data `not json` lists as escaped. Change: `[_safe(name) for name in listing.skipped]`.
5. **Id escaped (red).** A raw INSERT with a safe column id and a blob whose id is `a\x07b` (built with `json.dumps(task_to_dict(...))`) shows the id escaped in the text. The href follows (D4). Change: `_safe(task.id)` in `_task_row`; template unchanged.
6. **`_event` CR defence (guard).** A direct test that `_event("tasks", "a\rb")` gives two `data:`-prefixed lines, so the lone-CR split stays covered now that the CR test no longer reaches it. Optionally also assert the list stream carries `one\x0dtwo` as text. U+2028 is not claimed as escaped.
7. **Docs.** One README line in the Web UI subsection; a deviations section here, recording the CR coverage note and the lone-surrogate risk.
8. **Gate.** `cd orchestrator && uv run ruff check`, then `uv run pytest -q -n auto`; run the `verify` skill; run `audit-commits` against this plan. Rerun the list stream tests 10 times with a shell loop to show determinism.

## Out of scope
Escaping in the JSON API, the CLI (`_describe` already escapes), the detail page (plan 70), actions, the jobs and costs views, and the lone-surrogate follow-up below.

## Risks
- The visible goal can exceed 120 characters when it holds escapes (D2). Accepted; the limit is a layout aid, not a security control.
- **Unverified, follow-up:** a goal holding a lone surrogate (category Cs, for example `"\ud800"` sent through the JSON API) is not escaped by `escape_control`, and UTF-8 encoding of the response could fail and break the whole list page. Not run. Affects the detail page too.
- Wider rows for goals that are mostly escapes; layout stays unchecked until the manual browser check that plans 69 and 70 already owe.

## Deviations
- Increment 2: the "no `None` shown" sub-assertion sits in the red test (it passes before and after), not as a separate guard.
- Increment 6: asserts the exact event text (`event: tasks`, `data: a`, `data: b`), stricter than "two data lines"; the optional list-stream assertion was not added.
- Increment 7: the README sentence is appended to the existing "page at `/ui/`" bullet, not a new bullet.
- Recorded as planned, no change: the CR test's coverage of `splitlines()` is now carried by increment 6; the lone-surrogate risk stays a follow-up (not run).
