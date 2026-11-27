# Plan 93: agent-written failure diagnosis in the web UI

Drafted 2026-11-27, revised after a plan review (2026-11-27). Approved 2026-11-27.
Source: `todo.md` open item "diagnose errors".
Base: `main`.

## Problem
A failed task shows a canned sentence and next step per failure code (`failure_codes.py`, rendered at `_task.html:32`). It cannot say *why* this run failed: which tests failed and why, what the agent reported. Some evidence is kept (`audit_findings`, `review_findings`, `denials`, `fix_rounds_used`, the plan, the stage errors, ledger cost per stage). The rest is not: agent result text and stderr, and gate output (only captured when `fix_rounds > 0`, `verify_task.py:51`), live in memory or the server log.

## Decisions (settled with the user, 2026-11-27)
1. A diagnosis is written by a read-only agent (a diagnostician stage), not assembled deterministically.
2. Scope: task failures only (tasks in FAILED). Job errors, repo settings errors and server startup problems are out.
3. Shown inline on the task page, in the failure block.
4. On demand: a "Diagnose" action on a failed task enqueues a job. Not automatic.
5. The agent may read the worktree even when it holds uncommitted edits (the implementer is told "Do not commit", `stages.py:61`, so every implement-stage failure leaves some). So the read-only stage gets an **"unchanged" variant**: it fingerprints the worktree before and after instead of requiring it clean.
6. A failed diagnosis is reported through the job's refusal channel (a `RuntimeError`, shown as the job error). No new failure codes and no new task field for it.
7. A task can be re-diagnosed; the new diagnosis replaces the old one.
8. The raw evidence is not shown in the UI. Only the diagnosis is.
9. No JSON API endpoint, no CLI/Slack/watch surfacing, no per-stage budget settings.

## Design
- **Terminal state.** FAILED has no outgoing transition (`task.py:76-97`) and retry creates a new task, so a stored diagnosis never goes stale. There is no `diagnosed_failure` field.
- **Task fields** (defaults empty, so existing rows load): `failure_evidence: str = ""` and `diagnosis: dict | None = None`. Tasks are a JSON blob (`store.py:33`, `sqlite_store.py:31`); there is no schema migration to write. An older build cannot read rows carrying the new keys (`Task(**data)`); noted as a rollback cost.
- **Evidence** is bounded text built by one helper: a tail cap per part, a total cap, and the values of the secret environment keys (`STRIPPED_ENV`, whichever the gate strips) redacted. It is only for the prompt and the stored field; the UI never shows it.
- **What goes in the prompt:** goal, failure code and its canned explanation, `failure_evidence`, the persisted findings and denials, the plan, and the line that all of it is data, not instructions. The prompt is trimmed to fit `MAX_PROMPT_BYTES` the way `audit_task._room_for_diff` does.
- **Output schema:** `{"cause": str, "evidence": [str], "next_step": str}`, `additionalProperties: false`, `maxItems` and string length limits in the schema, capped again on parse (as `review_notes.capped_findings`).
- **Config:** `DIAGNOSE_CONFIG`: read-only tools as `AUDIT_CONFIG` plus `WebFetch` and `WebSearch` disallowed, `confine=True`, budget 0.30, 8 turns. The cost goes on the ledger under `diagnose`.
- **Cap:** `cap_usd` is per repo (`Ledger.would_exceed`, `ledger.py:26`), not per task. A diagnosis spends the shared cap and can block real work, and a timeout or runner error is charged at twice the budget (`agent_call.py:42`). The confirm page says so.
- **Evidence by failure source:** `budget_exhausted`, `error`, `unparseable`: `Outcome.result`, `.errors`, `.subtype` plus the stderr tail. `timeout`: the stderr tail. `runner_error` and `prompt_too_large`: a fixed sentence, never `str(error)` (it can hold paths and command lines the UI policy keeps out, `web_actions.py:15-17`). `tests_failed`, `tests_timeout` and `fix_*`: the last gate output (and the fixer's result for fix rounds). `plan_rejected`, `audit_rejected`, `*_malformed`: the persisted findings and stage errors, no new capture. `interrupted`, `abandoned`: no evidence, only the code and explanation.
- **When Diagnose is offered:** the task is FAILED, `task.worktree` is set and exists, and no job is queued or running for it. A missing worktree (after `factory clean`, or a task abandoned before one existed) is refused with a plain sentence, not as `Modified`.
- **Worktree fingerprint** (increments 12-14): HEAD, `git status --porcelain`, and a `write-tree` from a temporary index after `add -A`, which covers untracked files. The agent must leave all three unchanged. Ignored files are not covered (see Risks).
- **Untrusted output:** the agent's text is rendered escaped (`_safe`), labelled "Diagnosis (written by an agent)". That label is the only mitigation against a diagnosis telling the user to run a hostile command, and the evidence comes from tests the agent itself wrote.

## Increments (TDD, one red test, one implementation, one commit each)
1. `Task` gains `failure_evidence` and `diagnosis`. Red: a new round-trip test (as `test_a_task_with_grilling_state_loads_back_equal`). Guard: a stored row without the new keys loads.
2. `bounded_evidence(parts)`: per-part tail cap and total cap. Red.
3. `bounded_evidence` redacts the values of the secret environment keys. Red.
4. Outcome failures (`budget_exhausted`, `error`, `unparseable`) store result, errors, subtype and the stderr tail as evidence. Red.
5. `timeout` stores the stderr tail. Red.
6. Guard: `runner_error` and `prompt_too_large` store a fixed sentence, no exception text.
7. Always capture gate output (today only with `fix_rounds > 0`) and store it on `tests_failed`. Red. Re-check the tests that pin `capture=None`.
8. `tests_timeout` carries and stores its output tail (`GateResult` gains it; touches `gate.py`). Red.
9. Fix-round failures store the last gate output and the fixer's result (`_fix_round` returns it). Red.
10. `can_diagnose(task)`: FAILED, worktree set and present. Red per refusal, each with a plain sentence.
11. Worktree fingerprint function (HEAD, porcelain, write-tree of a temporary index). Red: changes to a tracked file, a staged file, an untracked file and a commit each change it.
12. `run_readonly_stage` gains the "unchanged" variant: tolerates a dirty start, returns `Modified` if the fingerprint differs after. Red with tamper tests (edit, add, commit, `.git` rewrite). The existing "clean" behaviour is unchanged; its tests are the guard.
13. Guard: the variant still returns `Modified` for a worktree whose `.git` does not match.
14. `diagnose_task` refuses a task that cannot be diagnosed (`InvalidTransition`-style refusal for non-FAILED). Add `diagnose_task` to `STAGE_MODULES` in `test_failure_code_literals.py:7-12` here.
15. Build the prompt with the pieces above and trim to fit. Red.
16. Parse the capped schema and save with `store.update`. Red.
17. Malformed output, `Modified` and `Infra` raise a refusal `RuntimeError` with a plain sentence; the task is untouched. Red.
18. Guard: state and `failure_reason` unchanged; cost on the ledger under `diagnose`; `check_budget` refuses at the cap (covered by increment 16's call, so not a red).
19. `DIAGNOSE_CONFIG` disallows the web tools. Red: assert the tuple. Register `diagnose` in `build_stages`.
20. `WebActions.enqueue_diagnose`, with `BudgetCapReached`, `MissingBudget` and the refusal in the refused tuple (`web_actions.py:64-68`). Red: a job at the cap shows the readable message.
21. `_DIAGNOSE` `_TaskAction` with GET confirm (states the shared-cap cost) and POST, 409 on wrong state or an unfinished job. Modelled on `_ADVANCE` with `_action_refusal`, not on retry. Red per route.
22. The nav link: offered for a failed, diagnosable task, hidden while a job is busy. First list the tests that assert the nav (`_ACTION_LINKS`, `_offered_actions`, `routes.py:121-133`; probably `test_web_task_page.py`, `test_web_task_job.py`). Red.
23. Render the diagnosis in the failure block, on the page and in the SSE fragment. Red.
24. Escaping guards: `<script>`, a bidi character, CR in SSE. Likely guards, since autoescape and `_safe` already cover them.
25. Docs: README section, this plan's Deviations.

## Risks and unknowns
- **Fingerprint gaps:** ignored files are outside it, so an agent that writes only a gitignored file is not caught; the same gap the clean variant has. The tamper tests should say so.
- **Secrets:** redaction covers only the keys in `STRIPPED_ENV`; a secret printed under another name is stored and sent to the agent. Not verified what the gate's environment passes (`base_env=dict(os.environ)`); to check in increment 3.
- **Not verified:** whether any JSON or export path serialises a whole task with `task_to_dict` (evidence and diagnosis would leak there); how the reaper and `recover` treat a diagnose process orphaned by a crash (the run is recorded under stage `task.state.value`, `runner.py:67`); whether `factory clean` writes task rows; that `plan_task.py`, `abandon_task.py` and `approve_plan.py` set `failure_reason` only through `apply`; `OUTPUT_LIMIT`'s value; which tests pin `capture=None`.
- **Accuracy:** the agent can be wrong; the UI keeps the canned explanation and raw code visible beside it.

## Tests that touch the strings this changes
`test_web_task_failure_text.py`, `test_web_task_page.py`, `test_web_task_job.py`, `test_web_task_events.py`, `test_failure_codes.py`, `test_failure_codes_catalogue.py`, `test_failure_code_literals.py`, and whatever pins `capture=None` (increment 7).

## Deviations
- **Stage-names test.** Registering `diagnose` made `test_stage_names_are_exactly_the_pipeline` fail; it became `test_stage_names_are_the_pipeline_and_the_on_demand_diagnosis` with `"diagnose"` in the expected set (a spec change, not a compile fix). The gate test also changed (increment 8, ac5dafb): a timed-out run now returns its output.
- **Increment 6** was planned as a guard but needed source changes (`RUNNER_ERROR_EVIDENCE`, `PROMPT_TOO_LARGE_EVIDENCE`), so it was a red.
- **Increment 22** nav tests: `test_web_task_actions.py` (state-driven links, fragment, busy) and `test_web_task_action_group.py`.
- **JSON task body.** The "Not verified" risk came true: `GET /tasks/{id}` returns the whole task, so it carries `failure_evidence` and `diagnosis`. Decision (user): keep it and document it in the README rather than strip the fields.
- **Increments 11 and 12a** were written without a separate red run.
- **Increment 20.** The budget stops (`BudgetCapReached`, `MissingBudget`) are translated to two plain sentences in `WebActions.enqueue_diagnose` (`CAP_REACHED`, `NO_BUDGET`); the stage's own `RuntimeError`s pass through the job refusal channel unchanged.
- **Increment 21.** The confirm page was red per route; the POST route was registered with the page, so its tests are marked guards (green on arrival). `can_diagnose` (failed with a worktree) gates the route and the nav; a worktree cleaned since is refused by the job with a plain sentence.
- **Increment 22.** `diagnose` was added to the `PREDICATES` of `test_web_task_actions.py` and `ACTIONS` of `test_web_action_routes_guard.py`.
- **Increment 24.** All three escaping tests were guards (green on arrival): `_safe` and autoescape already covered them.
- **Prompt cut.** The launch-limit cut is a byte cut of the tail, so the goal and plan are lost first.
- **Still unverified** (not checked during implementation): the Risks list above, other than the secrets and fingerprint gaps now stated in the README and the JSON task-body risk, confirmed above.
