# Plan 79: task ids shown by the web UI

Branch `plans-77-79-known-gaps` (shared by plans 77-79), after plan 78. Drafted 2026-11-22; revised after plan review. **Draft, not approved.** Small; do last.

## Problem
Plan 75 left two id gaps: the detail page rendering `task.id` raw, and lone-surrogate ids. Review found that most of this is already handled, and the reachable gap is different.

Already handled (read, to be pinned by guards): `require_safe_id` allows only `[A-Za-z0-9_-]+` (`ids.py:9-12`) and `save`, `create`, `load` call it; the list page and skipped names go through `_safe` (`routes.py:33,40`); autoescape is on (`routes.py:22`).

Reachable gap: `load(key)` validates the requested key, but `task_from_dict` takes `id` from the stored JSON unchecked, and `list()` validates neither. A row whose key is fine but whose JSON `id` is unsafe (or differs from the key) can reach the detail page (likely a 500) and the CLI (`cli/tasks.py:40,42`, `cli/admin.py:40`, raw output). Only a direct database write produces such a row; the user judged that unlikely, so this is the cheap fix, not a security fix.

## Decisions (recommendations accepted by the user 2026-11-22)
| # | Question | Decision |
|---|---|---|
| D1 | Store | `load` and `list` check that the stored `id` equals the row key; a mismatch is treated as unreadable: `list()` puts the key in `skipped`, `load` raises the same error unreadable rows raise today (increment 0 finds which). |
| D2 | CLI | `factory list` and the other ids printed in `cli/tasks.py` and `cli/admin.py` go through `escape_control`. |
| D3 | Probe | A surrogate cannot be bound by Python's sqlite3, so the probe uses a row with a control character or markup in the JSON `id` and a plain key. |

## Increments (test first, one commit each)
0. Probe (throwaway, flagged): insert a row with key `ok` and JSON `id` `bad<b>\n`; request `GET /ui/`, `GET /ui/tasks/ok`, `GET /tasks` and `factory list`; record each result under Deviations.
1. Red: `list()` puts such a row in `skipped`. Green: id/key check.
2. Red: `load("ok")` raises the unreadable-row error, and the detail page shows the unreadable-task page, not a 500. Green.
3. Red: `factory list` prints ids with control characters escaped. Green: D2.
4. Guards: `save`, `create`, `load` refuse an unsafe id (grep first; they may exist).
5. Docs: README note; Deviations here.
6. Gate: ruff, `pytest -q`, `verify`, `audit-commits`.

## Tests touched
Grep tests asserting `skipped` contents, list output or unreadable-row messages before increment 1 and record them here. No existing test is edited.

## Out of scope
Rejecting surrogates in goals at write time; changing id format; other unreadable-row handling.

## Deviations
Recorded after implementation.
