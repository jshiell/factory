# Plan 24: give the implementer, the fixer and the auditor the plan review's findings

Goal: the review agent's findings reach the agents that do the work. Today they are shown to the human at `approve` and then dropped.

Evidence (the real-output audit in `docs/spike-findings.md`): the `mean` task's plan review listed three gaps (bytes iterate into ints, `fsum` overflow, vague NaN rule). The plan did not fold them, the implementer never saw them, and `mean(b"12")` returns `49.5`.

## Decided (not up for review)
- Append the findings to the prompt as reviewer notes. No revise step, no new agent call.
- Review of the first draft folded in (user chose every recommendation): fix rounds get the notes; the auditor treats them as part of the task; `approve` and `retry --keep-plan` check the findings they displayed; cap 10 findings, 500 characters each, about 4000 total.
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first (a characterisation test is green on arrival and says so), never edit a passing test except via the approved edit list below. Never push. Signed commits.

## Verified premises (read in code)
- `build_implementation_prompt(goal, plan)` (`approve_plan.py:8`) builds the first-run prompt. Callers: `approve_plan` (`:25`), `retry_with_plan` (`retry_task.py:34`), `audit_eval.py:70` (corpus, no findings).
- **It is not the whole implementer brief.** `fix_prompt.py:18` rebuilds `GOAL/PLAN` for every fix round when `task.goal` and `task.plan` are set. Notes added only to the first prompt would be dropped on fix rounds.
- The auditor embeds `task.prompt` (`audit_task.py:39`) under "pass only if the diff does what the task asks". So the notes become part of "the task" for the auditor.
- `review_findings` are written only by `review_plan` (`:60`, `:75`) from `planned`. A `reviewed` task is not reviewed again. A rejected review leaves a `failed` task with prompt `""`, which `require_keepable_plan` refuses.
- `plan_digest` covers only the plan. Folding findings into it would invalidate every stored task, so it is left alone.

## Not verified (check during implementation, by reading, before relying on them)
- Whether the fixtures at `test_cli_wiring.py:202-209` and `test_cli.py:769` carry findings.
- Whether `escape_control` handles newlines; `capped_findings` flattens them itself regardless.
- `store.py:84` coercion of `review_findings`; whether `plan_task` or `recover` touch them.
- That any of this fixes `mean`: one task is a sample of one.

## Design
1. `capped_findings(findings) -> tuple[str, ...]`: drop blanks, collapse each finding to one line (so one cannot forge a `Rules:` or `PLAN:` header), at most 10 findings and 500 characters each and about 4000 in total, with a `[truncated]` marker when anything was cut. One function serves the prompt and both screens, so what the human saw is what is sent.
2. `build_implementation_prompt(goal, plan, review_findings=())`. With no findings the text equals today's exactly (pinned by C0). With findings, a block goes between PLAN and Rules: `REVIEWER NOTES (a reviewer's comments on the plan above; address those that fit the plan):` then one `- ` line per capped finding, so the tamper rules stay last.
3. `build_fix_prompt` appends the same block from `task.review_findings`, through one shared formatter.
4. `approve_plan(task_id, store, shown_digest, shown_findings)` and `retry_with_plan(...)` raise `ValueError` if the stored capped findings differ from what the CLI displayed (mirrors the digest check), then embed them. The CLI passes the findings it printed.
5. The approve and retry-keep-plan screens label the block "REVIEWER NOTES (sent to the implementer)".
6. Auditor: the notes are part of the task it sees; documented, not hidden. Its prompt is unchanged. An unevaluated effect is measured by one new corpus case (below).
7. `retry --keep-plan` on a task approved before this plan re-embeds findings its stored prompt never had; the screen says so.

## Cycles (red, green, commit each)
- C0 (characterisation, green on arrival, says so): pin the literal current `build_implementation_prompt("g", "p")`; pin that a review-rejected task with findings cannot be kept or approved.
- C1: `capped_findings` (blanks, flattening a forged `\n\nRules:`, item cap, per-item cap, total cap, marker).
- C2: `build_implementation_prompt` with findings (block between PLAN and Rules; none: equals the C0 literal).
- C3: `approve_plan` embeds the findings and refuses a shown-findings mismatch; `cli._approve` passes them and the screen label changes.
- C4: `retry_with_plan` and `_retry_keeping_plan` likewise.
- C5: `build_fix_prompt` appends the block, with and without findings.
- C6: corpus case-21 (fail): the production prompt includes a reviewer note ("reject bytes input") that the change ignores and no test covers it; label checked against its plan text. `--check-corpus` (user) then a `--only case-21` run.
- E2E (user, about $0.25): rerun the `mean` goal. Pass criterion, decided now: `mean(b"12")` raises or returns a defined result, `mean([1e308, 1e308])` is handled, and NaN is either rejected or has a test pinning it.
- C7: README, `docs/spike-findings.md`.

## Approved edit list (existing passing assertions that change)
- `test_retry_task.py:87` and `:167` (`put_approved` sets `review_findings=("f",)`): expected prompt now includes the findings.
- `test_approve_plan.py:28`: the call gains the `shown_findings` argument.
- Any `test_cli.py` or `test_cli_wiring.py` call or comparison that is shown to break (`:769`, `:209`): same reason. Each is listed with its before and after at the time, for approval, not edited silently.

## Residual
- Findings are model text in the implementer's prompt, and the implementer can edit files. The reviewer is read-only and its output is shown to the human first, but it is still an injection path from whatever the reviewer read. The cap, the one-line flattening and the shown-findings check narrow it; the gate's tamper checks, the audit and the human at `merge` are the backstops, and the auditor reads the same text.
- The auditor may fail a change for ignoring a note or for widening the diff to satisfy one. Only case-21 measures the first; the second is unmeasured.
- Notes may widen the diff beyond the plan.
