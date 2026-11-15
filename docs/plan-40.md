# Plan 40: grill the design before planning (`open_questions`)

Goal: before a plan is written, the planner interviews the user with the `grilling` skill. In the CLI the user answers before the task advances. Under `watch` the approver answers in issue comments. Decisions agreed; revised after review (plan-reviewer); awaiting approval to implement.

## Decisions (agreed)
1. **Skill source: load via plugin dir**, not an embedded copy.
2. **Termination: frontier empty, capped.** Rounds continue until the planner reports an empty frontier, then it writes the plan with no further gate. After `grill_max_rounds` (default 3) it must write the plan, and leftover assumptions go under `risks`. `grill_max_rounds = 0` means no grilling.
3. **GitHub answers: approver comments after the questions comment** (numeric id, never login), concatenated in list order. Any approver comment counts, even "later"; unresolved questions are re-asked next round, and the cap bounds the cost.
4. **Default on, opt out:** `grill = false` in `factory.toml`, or `factory new --no-grill`.
5. **Review follow-ups (agreed with recommendations):** plugin dirs are chosen per task (only `grill=True` tasks load the skill); a deleted questions comment makes watch post a stopped result; `open_questions` and `grill_log` are plain dicts, no new dataclasses.

## Spike (increment 0, throwaway, flagged as such)
Unverified assumptions, not to be relied on until `docs/spike-findings.md` records them. Run with the real `PLAN_CONFIG` flags, `--setting-sources ""`, and the nono `--read` of the plugin dir:
- The skill loads from `--plugin-dir` and runs via the `Skill` tool in `-p` mode with no permission mode and no `--allowedTools`. `Skill` is not in `permission_denials`.
- The skill does not try `AskUserQuestion`, Bash or Glob (denied or unavailable in this stage, which would end as `SUCCESS_WITH_DENIALS` with no output).
- One Skill call fits in `max_turns=8` and the $0.30 budget.
- `--json-schema` accepts a `oneOf`. Fallback: one object with `status: "questions"|"plan"`.
Stop and return to the user if any fails.

**Resolution (recorded in `docs/spike-findings.md`, 2026-11-15):** the skill loads and runs with no allow rule and no denials. A top-level `oneOf` is refused by the API, so the flat schema is used (one object, `status: "questions"|"plan"`, optional `plan`, `risks`, `questions`). A questions round used 7 of 8 turns, so grilling rounds use `max_turns` of about 12. The sandbox `--read` grant was not re-tested here; the earlier plugin spikes cover it.

## Design
**State.** `TaskState.OPEN_QUESTIONS = "open_questions"`, events `QUESTIONS_ASKED` and `ANSWERED`:
- `PROPOSED --QUESTIONS_ASKED--> OPEN_QUESTIONS`
- `OPEN_QUESTIONS --ANSWERED--> PROPOSED` (the plan stage re-runs with the Q&A)
- `OPEN_QUESTIONS --ABANDONED--> FAILED`, added to the abandonable set
- `advance` has no action for it (`advance.py:47-49` returns with `stopped=None`; the PROPOSED to OPEN_QUESTIONS move is one recorded step, not "no progress").
- `clean_tasks` only touches MERGED and FAILED, so the worktree stays until the task is abandoned. Accepted.

**Task fields** (defaults keep old rows and existing fixtures unchanged):
- `grill: bool = False`. `service.create_task` sets it from `settings.grill` (default True) unless the request overrides it. Old stored tasks therefore never start grilling.
- `grill_rounds: int = 0`
- `open_questions: tuple[dict, ...] = ()`, each `{number, title, body, recommended}`
- `grill_log: tuple[dict, ...] = ()`, each `{round, questions, answers}`
- **Store:** `store.py:37` has a hard-coded list of tuple fields and nothing rebuilds nested values, so the new fields must be added to `task_from_dict` (tuples of plain dicts). Both `TaskStore` and `SqliteTaskStore` use it, and so does `cli._show` on the server JSON.

**Plan stage** (`plan_task.py`). Existing `PLAN_SCHEMA` and the one-argument `build_plan_prompt(goal)` stay unchanged, so the passing tests at `test_plan_task.py:63-75` are untouched. New: `GRILL_SCHEMA` (plan or questions) and optional prompt parameters, picked per task inside `plan_task`:
- `grill=False`, or `grill_rounds >= grill_max_rounds`: `PLAN_SCHEMA`, and the prompt says "write the plan now". The schema enforces the cap, so no rejected result is paid for twice.
- Otherwise `GRILL_SCHEMA`, the grilling preamble, the fenced `grill_log` and the round counter, plus an instruction to record decisions in a "Decisions" section of the plan (so the digested plan carries them to the reviewer and implementer).
- Plugin dirs: the shipped grilling plugin is added (deduped with `stage_plugins.plan`) only for the grilling schema.
- A questions result is bounded in count and size (inside the 60k comment budget), applies `QUESTIONS_ASKED`, increments `grill_rounds` and saves. The worktree check is unchanged.
- Questions and answers are untrusted. The prompt fences them with a `gaps_block`-style helper and an injected nonce (distinct values per AGENTS.md).

**Shipped plugin.** `src/factory/plugins/grilling/.claude-plugin/plugin.json` plus `skills/grilling/SKILL.md`, a real copy (a symlink would be unreadable in the sandbox, `runner.py:29` grants `--read` on the dir only). The path is found with `Path(__file__)`. `settings._runs_code(shipped_dir)` must be `None`.

**Answering.** `answer_questions(task_id, store, round, text)` appends to `grill_log`, clears `open_questions` and applies `ANSWERED`:
- Wrong state, a round that is not `task.grill_rounds`, or oversize or missing text all raise. It is compare-and-set, like `plan_digest` on approve.
- Server: `POST /tasks/{id}/answer` with `{round, answers}`: 200; 409 `wrong state`; 409 `round changed`; 422 for oversize or missing text. `GET /tasks/{id}/questions` returns the round and questions, 409 when not in `open_questions`.
- `POST /tasks` accepts an optional `grill`. `retry_task` (`retry_task.py:26`) copies `grill`.
- The digest and approve path is unchanged: only the final plan is digested.

**CLI** (`cli.py`):
- `advance` on `open_questions` prints `open questions; run: factory answer <id>` and exits 0.
- `factory answer <id>` needs a terminal. It shows the round and the questions (through `escape_control`). Enter accepts the recommendation, a typed line overrides it, and it posts with the round shown. A 409 prints "questions changed since display; nothing answered".
- `show` lists the open questions. `new --no-grill` sends `grill: false`.

**Watch** (`watch.py`, `comments.py`):
- `"questions"` joins `KINDS`. The comment heading carries the round. Fencing already works for any kind.
- New phase `AWAITING_ANSWERS`, added to the allowlist at `watch.py:301`. `IssueRecord` gains `questions_comment_id` and `questions_round`.
- After an advance ends in `open_questions`, watch posts the questions comment (adopting only an exact-body copy after a crash, never an earlier round's) and sets the phase.
- Each AWAITING_ANSWERS pass reads the server first. If the task is no longer `open_questions`, or its round differs from `questions_round`, the phase goes to NEW and the pass continues. Failed tasks use the existing branch.
- Answers are the approver comments after the questions comment by list position (`Comment` has no timestamp). Comments whose first line is a factory marker of any kind are excluded. `self_id` is not filtered, because a solo user may be both the gh account and the approver. The text is cut to the server's cap, then posted with the round, then the phase is NEW. A 409 `round changed` resets to NEW without a crash.
- If the questions comment has been deleted, watch posts a stopped result (phase STOPPED), not a re-post.
- Unchanged: the plan comment's 👍/👎 flow.

**Settings:** `grill` (a new bool kind in `_typed`) and `grill_max_rounds` (int).

**Compatibility:** an older build reading a new-format `watch.json` or task row raises TypeError. There is no downgrade path. Noted in the README.

## User-visible strings and tests touched (grepped)
- `plan: no progress`: `test_cli.py:163`, `test_advance.py:101`. Unchanged. Guard that `open_questions` never triggers it.
- `"state": "proposed"` listing: `test_server.py:65`. Unchanged (`:71-78` checks named fields only).
- `test_task.py:94-150`: `test_front_half_events_are_rejected_from_every_other_state` iterates `list(TaskState)`, so the new state is covered. Do not edit `FRONT_HALF`; add new test lists.
- `test_plan_task.py:63-75` (schema and prompt asserts): unchanged by design. `:50, 113, 138, 161` are fixtures and state asserts, unaffected thanks to `grill=False` defaults.
- `KINDS` in `test_comments.py`, and the `watch.json` shape in `test_watch.py`: confirm exact assertions before increments 18 and 23.

## Increments (one test, one impl, one commit each; guards marked)
0. Spike (above). No production code.
1. Red: `OPEN_QUESTIONS` exists; `PROPOSED --QUESTIONS_ASKED--> OPEN_QUESTIONS`; `OPEN_QUESTIONS --ANSWERED--> PROPOSED`. New test list.
2. Red: `OPEN_QUESTIONS` is abandonable. Guard: `advance` from it gives `steps == []`, `stopped is None`. Guard: a plan stage that lands in `open_questions` is one step, no "no progress".
3. Red: the new fields round-trip through `TaskStore` and `SqliteTaskStore`. Guard: an old row loads with defaults. Red: `cli show` reads the server JSON with them.
4. Red: a `grill=True` task uses `GRILL_SCHEMA`, and a questions result moves it to `open_questions`, stores the questions and bumps `grill_rounds`. Guard: `grill=False` uses `PLAN_SCHEMA` and the existing prompt.
5. Red: questions are capped in count and size (`plan_malformed` / `questions_too_large`, stays proposed). Guard: a modified worktree still fails.
6. Red: the grilling prompt carries the fenced, escaped `grill_log` (distinct-nonce iterator) and the "Decisions" instruction.
7. Red: at `grill_rounds >= grill_max_rounds` the stage uses `PLAN_SCHEMA` and "write the plan now".
8. Red: the grilling plugin dir is added to the runner config for the grilling schema only, deduped with `stage_plugins.plan`. Guard: not added for `grill=False` or at the cap.
9. Red: `answer_questions` records and returns to `proposed`. Red: wrong state raises. Red: round mismatch raises. Red: oversize text raises. (Four cycles.)
10. Red: `POST /answer` 200, then 409 `wrong state`, 409 `round changed`, 422.
11. Red: `GET /questions`, with 409 when not in `open_questions`.
12. Red: `POST /tasks` accepts `grill`, defaulting to `settings.grill`. Red: `retry_task` copies `grill`.
13. Red: settings `grill` (bool) and `grill_max_rounds` parse; a wrong type is a `SettingsError`. Wire `grill_max_rounds` into `build_stages`.
14. Guard: the shipped plugin exists with `.claude-plugin/plugin.json`, and `_runs_code(shipped) is None`.
15. Red: CLI `advance` on `open_questions` prints the line and exits 0.
16. Red: `factory answer` refuses without a terminal.
17. Red: `factory answer` shows escaped questions, Enter takes the recommendation, a typed line overrides it, and it posts with the round.
18. Red: `factory answer` handles a 409 with the "questions changed" message.
19. Red: `show` lists the open questions. Red: `new --no-grill` sends `grill: false`.
20. Red: `"questions"` in `KINDS`. Guard: hostile text stays inside the fence.
21. Red: watch includes `AWAITING_ANSWERS` in its allowlist, and posts the questions comment with the round in the heading, saving `questions_comment_id` and `questions_round`.
22. Red: after a crash, watch adopts only an exact-body copy, never an earlier round's comment.
23. Red: an `AWAITING_ANSWERS` pass resets to NEW when the task is no longer `open_questions` or its round differs.
24. Red: watch forwards approver comments after the questions comment, with the round, then sets NEW. Guards: a non-approver is ignored; comments before it are ignored; marker-headed comments are excluded; a second round does not replay earlier answers.
25. Red: forwarded text is cut to the server cap.
26. Red: a 409 `round changed` resets to NEW without a crash.
27. Red: a deleted questions comment posts a stopped result.
28. Guard: an old `watch.json` without the new fields loads.
29. README: `answer`, `grill`, `grill_max_rounds` (`0` = off), the plugin, watch answering, the cost (up to `grill_max_rounds + 1` planner calls), and the no-downgrade note.
30. Full suite in the background with Monitor, then `verify`.

## Risks
- The spike may show the skill can't run in `-p` mode, hits denials, or doesn't fit `max_turns=8`. That changes increments 4 to 8, and I'd come back first.
- Each round is one planner call (about $0.30, charged to the ledger under stage `plan`) and counts against the per-task cap.

## Out of scope
A web UI, answering by reaction, a "confirm shared understanding" gate, per-question threads, and grilling during `retry --keep-plan`.
