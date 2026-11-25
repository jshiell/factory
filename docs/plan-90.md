# Plan 90: plain-English failure descriptions (web, CLI, watch)

Drafted 2026-11-25, revised the same day after plan review (`plan-reviewer`, opus). Awaiting approval, including the wording table below.
Source: `todo.md` open item "improve error messages" (untracked in git; edit the working tree only).
Scope: Phase 1 only (static descriptions, no model call). A Claude-written diagnosis is deferred until this lands.
Decisions (user): advice depends on the field; CLI shows code first; all surfaces in scope; codes move to shared constants; watch gets a separate unfenced section.

## Problem
The web task page, the CLI and the watch comments print raw machine codes (`failure: tests_failed`, `plan error: plan_malformed`, `stopped: plan: no progress (timeout)`). Job errors (`RuntimeError` text, rebase refusals) are already sentences and stay as they are.

## Design
- New module `factory/failure_codes.py`:
  - Constants for every code (below), imported by the emitting stages in place of their string literals. Values are unchanged, so stored rows, the JSON API, `audit_gaps.py:27` and `audit_eval.py:61` keep working.
  - `explain(field, code) -> Explanation | None` (sentence + next step). `field` is one of `failure_reason`, `plan_error`, `review_error`, `audit_error`. Per-field entries where the advice differs, a generic entry otherwise. The composed `fix_<reason>` is handled by stripping the prefix and prefixing the sentence ("While fixing the failing tests: ..."). Unknown code or unknown field returns `None`; callers show the raw value unchanged.
  - Named `explain` to avoid clashing with `cli/tasks.py:_describe` and `clean_tasks.py:_describe`.
- Display only. Storage, `server.py` and `web_actions.py` payloads stay raw; mapping happens in the renderers.
- Agent-written text stays escaped (`_safe` / `escape_control`); the description is our own text.
- Formats:
  - Web: description first, raw code kept beside it (the free-text path is byte-identical to today, so `FAILURE_GROUP` in `test_web_task_groups.py:11` stays green unedited).
  - CLI: code first, `failure: tests_failed - <sentence>`, so greps and existing prefix assertions keep working.
  - Watch: the raw reason stays in its fenced, home-stripped block (`No reason recorded.` keeps its fence, `test_watch.py:1871/1881`); the sentence goes in a separate unfenced section.
- Surfaces:
  1. Web task page: `failure_reason`, and the plan/review/audit error facts (`web/routes.py` `_task_detail`, `_task.html`).
  2. Web `stopped:` (`_job_view`, `_task.html`) and the web tasks list (`_tasks.html:9`, `routes.py:56`).
  3. CLI `show` (`cli/tasks.py:_describe`, failure plus the three `*_error` lines), `retry --keep-plan` (`cli/recovery.py:50`, "FAILED BECAUSE"), `advance` summary `state: failed (code)` (`cli/tasks.py:82-83`) and its `stopped:` line, and the `tasks list` line (`cli/tasks.py:39`).
  4. Advance stop reason `"{stage}: no progress ({reason})"` (`advance.py:67-68`): it is built at the source and reaches web, CLI and watch. Keep the code in the stored/returned text; render the explanation at the display layer by parsing nothing: add a structured `stopped_code` alongside `stopped` in `AdvanceResult`, to be confirmed against `web_actions.py:46` and `server.py` payload shape in the cycle.
  5. Watch: `watch/__init__.py` `_failure_text` (~203), `_post_failure` (call sites ~231, ~274), the retry offer body, and the `Stopped` comment (~266).
- Out of scope: job errors, refusal pages, rebase refusals (`rebase_task.py`), the eval label `infra:` (`audit_eval.py:63`, never shown on these surfaces), any model call.

## Code table (about 37 codes plus the `fix_` family)
Emitters read from source on 2026-11-25; wording below is a proposal for you to approve or edit before cycle 5. "Fields" says where the code is stored.

| Code | Fields | Emitter | Proposed sentence / next step |
|---|---|---|---|
| `prompt_too_large` | failure_reason, audit_error | step.py:81, agent_call.py:29 | The prompt for the agent was too large to send. Shrink the goal or plan; retrying builds the same prompt. |
| `runner_error` | failure_reason; plan/review/audit_error | step.py:86, agent_call.py:32 | The agent could not be run (a launch or sandbox fault, see the server log). Retry; if it repeats, check the server log. |
| `timeout` | failure_reason; plan/review/audit_error | step.py:88, agent_call.py:37 | The agent ran out of time. As a failure: retry the task. As a stage error: advance again, the task kept its state. |
| `budget_exhausted` | same | classify.py:8 | The agent used up its budget before finishing. Retry with a smaller goal or a higher budget. |
| `error` | same | classify.py:9 | The agent reported an error and stopped. See the server log; retry. |
| `unparseable` | same | classify.py:10 | The agent's output could not be read. Retry. |
| `not_descendant` | failure_reason | gate.py:76,92 | The agent's branch no longer builds on the task's base. Rebase or retry. |
| `no_commits` | failure_reason | gate.py:78 | The agent finished without committing anything. Retry. |
| `outside_project` | failure_reason | gate.py:80,96 | The agent changed files outside the project. Retry; check the sandbox. |
| `no_changes` | failure_reason | gate.py:82 | The agent's commits change nothing. Retry. |
| `tests_modified` | failure_reason | gate.py:84,94 | The agent edited existing tests, which is not allowed. Retry. |
| `protected_file_changed` | failure_reason | gate.py:89 | The agent changed a protected file. Retry. |
| `sandbox_unavailable` | failure_reason | gate.py:100 | The test sandbox could not start, so nothing ran. Check the sandbox setting, then retry the gate. |
| `tests_timeout` | failure_reason | gate.py:102 | The test suite did not finish in time. Look for a hanging test; retry. |
| `tests_failed` | failure_reason | gate.py:104 | The tests failed after the agent's change (and any fix rounds). Read the test output; retry or fix by hand. |
| `worktree_mismatch` | failure_reason | verify_task.py:78,126,164; audit_task.py:66 | The task's worktree is not what the server expects (moved or rewritten). Recover or abandon the task. |
| `worktree_not_clean` | failure_reason | worktree_clean.py:18 | The worktree has uncommitted changes. Clean it, then recover. |
| `nested_repo` | failure_reason | verify_task.py:128 | The agent created a nested git repository. Retry. |
| `gate_modified_tracked_files` | failure_reason | verify_task.py:169 | The test run edited tracked files, so the result cannot be trusted. Fix the tests so they do not write there. |
| `fix_moved` | failure_reason | verify_task.py:95 | A fix round moved the branch head unexpectedly. Retry. |
| `fix_no_changes` | failure_reason | verify_task.py:97 | A fix round changed nothing, so the same tests would fail again. Fix by hand or retry. |
| `fix_interrupted` | failure_reason | verify_task.py:105 | A fix round was cut short by a restart. Recover, then advance. |
| `fix_<reason>` | failure_reason | verify_task.py:89 | "While fixing the failing tests: " plus the sentence for `<reason>`. |
| `plan_modified_worktree` | failure_reason | plan_task.py:145 | The planning agent edited the worktree, which it must not. Retry. |
| `plan_malformed` | plan_error | plan_task.py:176-195 | The plan came back in a form that could not be read. Advance again to re-plan. |
| `questions_too_large` | plan_error | plan_task.py:183 | The planner asked more questions than allowed. Advance again. |
| `plan_too_large` | plan_error | plan_task.py:197 | The plan is longer than allowed. Advance again, or narrow the goal. |
| `plan_changed` | failure_reason | review_plan.py:68 | The plan changed after review started. Re-plan. |
| `review_modified_worktree` | failure_reason | review_plan.py:72 | The reviewing agent edited the worktree. Retry. |
| `plan_rejected` | failure_reason | review_plan.py:84 | The reviewer rejected the plan; the findings list says why. Retry with a revised goal. |
| `review_malformed` | review_error | review_plan.py:79 | The review came back unreadable. Advance again. |
| `diff_too_large` | failure_reason | audit_task.py:69 | The change is too large to audit. Split the task. |
| `audit_modified_worktree` | failure_reason | audit_task.py:72 | The auditing agent edited the worktree. Retry the audit. |
| `audit_rejected` | failure_reason | audit_task.py:84 | The auditor rejected the change; the audit findings say why. Retry or fix by hand. |
| `audit_malformed` | audit_error | audit_task.py:77 | The audit came back unreadable. Advance again. |
| `plan_rejected_by_human` | failure_reason | approve_plan.py:67 | You rejected the plan. |
| `abandoned` | failure_reason | abandon_task.py:27 | The task was abandoned. |
| `interrupted` | failure_reason | recover_task.py:20 | The server stopped while the task was running. Recover it. |

Field differences: `timeout` and the other infra codes as `plan_error`/`review_error`/`audit_error` say "the stage failed, the task kept its state: advance again"; as `failure_reason` they say "the task failed: retry". `prompt_too_large` is a task failure from implement, plan and review (stored in `failure_reason`) and a stage error only from audit.

## Tests (TDD: one test, one implementation, one commit per increment)
Exact-string hits, from reading the tests before drafting (re-run this grep at the start of implementation and list every hit here): `failure: `, `FAILED BECAUSE: `, `plan_error: `, `review_error: `, `audit_error: `, `no progress (`, `state: failed (`, `plan error: `, `review error: `, `audit error: `. Free-text values (`gate failed`, `p broke`, `r broke`, `a broke`, `No reason recorded.`) are unaffected by the unknown-code fallback; fixtures using real codes (`test_cli.py:853` default `tests_failed`, `:1501/:1512/:1521`, `test_watch.py`, `test_verify_fix_loop.py`, `test_retry_task.py`) are checked individually. With code-first CLI format, prefix assertions should survive; any that do not are listed under Deviations, never silently edited.

1. Red: `explain("failure_reason", "tests_failed")` returns a sentence and next step; unknown code and unknown field return `None`. Green: module with the one entry. Commit.
2. Red: the field matters (`explain("plan_error", "timeout")` differs from `explain("failure_reason", "timeout")`); `fix_tests_failed`-style composition. Green. Commit.
3. Constants refactor (behaviour-preserving, no new red): add the constants; replace the string literals stage by stage, one commit per stage module (step/agent_call/readonly_stage, gate/verify_task/worktree_clean, plan_task, review_plan, audit_task, human actions). Guard: a test that every constant has an `explain` entry for each field it can be stored in (the coverage guard; it covers every code in the module, and a new code only skips it if someone writes a literal instead of a constant, which a final guard test greps for in the stage modules). Existing tests unchanged; run the suite after each.
4. Fill the catalogue from the approved table, one commit.
5. Web: red, known-code `failure_reason` shows description and raw code; guard, free text unchanged byte for byte. Green. Commit.
6. Web: plan/review/audit error facts (one parametrised test). Commit.
7. Web: `stopped:` and the tasks list. Commit each.
8. CLI `show` failure and `*_error` lines. Commit.
9. CLI `retry --keep-plan` "FAILED BECAUSE". Commit.
10. CLI `advance` summary and `stopped:`; CLI `tasks list`. Commit each.
11. Advance stop reason: structured code carried on `AdvanceResult` and the server/web_actions payload; renderers use it. Commit.
12. Watch: red, a known code gets an unfenced description section while the raw reason stays fenced and home-stripped; guard, `No reason recorded.` stays fenced; green: `_post_failure` at both call sites, retry offer body and the `Stopped` comment. Commit.
13. README: catalogue, how to add a code (add the constant, then the entries per field), the `fix_` family, display-only mapping. Remove the `todo.md` item (working tree). Commit.

## Process
- Base gate on `main` first: `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto` (background, Monitor). Same gate at the end, then `verify` and `commit-auditor`.
- Sonnet `implementation` agent for cycles; I review wording and the diff of the constants refactor. Short commits, Sonnet 5.5 trailer, sign unsigned commits at the end (stop after the first failure), never push.

## Risks
- Cycle 3 touches about ten modules; it is mechanical but the widest part. Mitigation: one stage per commit, suite green after each.
- Wording can drift if a stage's meaning changes; the guard only checks presence.
- Not verified: whether harnesses other than Claude add outcome kinds (relied on `OutcomeKind`, `classify.py:5-10`), and the exact payload shape for step 11 (confirm in `web_actions.py:46` and `server.py` first).

## Deviations
- The coverage guard moved to cycle 4 and the literal guard to cycle 3, where the code they check landed.
- `Explanation` has a `.text` property (sentence plus next step) so the three surfaces share one rendering.
- Constants were left as literals in `task.py` and `client.py`; the literal guard excludes dict keys.
- Web `stopped:` description moved after cycle 11, once the structured `stopped_error` existed.
- Cycle 11 was split into four commits: `AdvanceResult`, job payload, web, CLI.
- The `stopped_error` payload key is emitted only when set, so the exact-equality assertion at `test_server.py:179` stays unedited.
- Watch shows the description as an unfenced "What it means" section beside the fenced raw reason.
- The `list` test was not observed red before its implementation.
- No assertion on a known code was edited; only new tests were added.
