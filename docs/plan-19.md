# Plan 19: `factory retry <id> --keep-plan`

Goal: after an implementation fails (or an audited task cannot merge), retry without paying for plan and review again. Today `retry` makes a new `proposed` task that starts from the goal: new plan, new review, new approval. With `--keep-plan` the new task starts `queued` with the plan the human already approved.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test except via an approved edit list. Never push.
- The human confirms again: `retry --keep-plan` needs a TTY, shows the plan and its risks and review findings, and asks `[y/N]`, like `approve`. An agent or script can never queue work on its own, not even a previously approved plan. (User chose this over "no confirmation".)
- Plain `factory retry <id>` is unchanged.

## Behaviour
1. `factory retry <id> --keep-plan` (flag on `retry` only).
2. Source task must be `failed` or `audited` (as today) and have a goal, a plan and a plan digest, and its plan must have been approved by a human: `plan_digest(plan) == task.plan_digest` and `task.prompt == build_implementation_prompt(goal, plan)`. Only `approve_plan` ever sets that prompt, so a task that failed in `planned`, a rejected one (`reviewed -> failed`) and a plain retry child (prompt `""`) are refused with "its plan was never approved; use retry without --keep-plan". If the prompt template changes in future, old tasks refuse (safe direction).
3. The new task: fresh id, state `queued`, `prompt = build_implementation_prompt(goal, plan)`, copies goal, plan, plan_risks, plan_digest, review_findings, sets `retry_of`. Everything else is fresh (no worktree, base_sha, fix rounds, audit data, failure reason, spend).
4. The CLI refuses (exit 1, nothing created) without a TTY; prints goal, plan, risks, review findings (escaped, as `approve` does) plus "retry of <id>"; only `y` creates. It binds to the shown digest: the digest of the plan shown must equal the stored one at creation time or nothing is created.
5. Prints the new id and `run: factory advance <new id>`.
6. The source task, its worktree and branch are untouched.

## Cycles
K1 (red): `retry_with_plan` on a failed task with an approved plan returns a queued task with the copied plan fields, the built prompt and `retry_of`, saved in the store.
K2 (red, one per refusal): proposed/planned source with no plan, a rejected task (prompt `""`), a plan digest that does not match the stored plan, a `shown_digest` that does not match, wrong state (running, merged), no goal. Nothing is created in each.
K3 (CLI): `--keep-plan` parses (and is rejected on other commands); non-TTY refuses and creates nothing; `n` creates nothing; `y` creates a queued task and prints its id and the advance hint; the plan is displayed escaped; an unapproved source gives exit 1.
K4 (wiring, existing fakes): the new queued task goes through `advance` to the implement stage with the stored prompt.
K5: README command table and `docs/spike-findings.md`.
E1: throwaway `spike/keep_plan_e2e.py` run by the user in a terminal (needs a TTY), $0, no API: a failed approved task and a rejected one in a scratch repo; `retry --keep-plan` with `y` creates a queued task, with `n` and a non-TTY nothing, the rejected one is refused. Then final `verify`.

## Residual exposure / costs
- Spend is capped per task, so repeated keep-plan retries of one plan reset the cap each time (the human sees each retry's prompt, and `costs` shows spend per task).
- The plan was reviewed against the repo as it was; if main has moved a lot, the plan may be stale. The human sees the plan again at the prompt and can decline.
- Approval is inferred from the prompt equalling the approved-prompt template (no new schema field). A future change to that template makes old tasks refuse; accepted.

## Out of scope
Retrying from `agent_done`/`verified` (resuming a worktree), carrying fix rounds or the old branch over, a flag for plain retry.

## Folded from the plan-reviewer (supersedes the text above where they differ)
Design sound; the reviewer had a read-only helper run the greps and could not run tests.

**Approval inference (user chose (a)):** "approved by a human" means `prompt != ""` plus the plan, plan digest and goal checks. Only `approve_plan` (approve_plan.py:25) ever persists a non-empty prompt (agent_call.py's `replace(task, prompt=...)` is an unsaved copy; `new` and `retry` write `""`). This replaces the template-equality check, which refused 3 of the 4 real tasks on disk because `build_implementation_prompt` changed twice on 2026-11-07. The new task's prompt is built from the current template. It is a policy filter, not a security control: forging it needs write access to `.factory/tasks`, which already allows setting `state: queued`, and the human still confirms.

**Corrected residual (my claim was false):** `cap_usd` is a global spend cap across all tasks (`ledger.would_exceed` sums the whole ledger), so keep-plan retries reset nothing and stop at the global cap. The README says "per task" in two places (lines 72 and 194); fixed in K5.

**Guard order (a bad order crashes `plan_digest(None)`):** state (failed or audited), then goal and plan present, then the stored digest equals `plan_digest(plan)`, then `shown_digest` equals both, then `prompt != ""`. The core reloads the task and binds to `shown_digest` as `approve_plan` does (not a tautology: the CLI computes it from the task it displayed).

**Refusals worth naming:** reviewer-rejected (`planned -> failed`, plan kept, prompt `""`), human-rejected and abandoned-from-reviewed (prompt `""`), failed in proposed (plan `None`, no crash). **Accepted (characterisation, start green):** audited, rebased then audited or failed, abandoned from queued, a keep-plan child that failed again, a task whose prompt came from an older template.

**Confirmation shows** goal, plan, risks, review findings, the source's failure reason, its audit findings if any, "retry of <id>", all through `escape_control`; for an audited source it also says the source stays mergeable (abandon it if you take this retry). Not taking `state_lock` matches plain retry and approve (`create_with_fresh_id` is atomic).

**Cycles (replaces the list above)**
- K1 (red): `retry_with_plan(src_id, store, clock, shown_digest)` on a failed approved task returns a queued task with goal, plan, plan_risks, plan_digest, review_findings copied, `prompt = build_implementation_prompt(goal, plan)`, `retry_of` set, everything else default, saved; the source unchanged.
- K2 (red, one commit each, in guard order): wrong state (running, merged); no goal; failed-in-proposed with plan `None` (no crash); stored digest mismatch; `shown_digest` mismatch; prompt `""` for reviewer-rejected and for human-rejected. In each the store's task count is unchanged.
- K2b (green characterisation, labelled): the accepted cases above.
- K3 (CLI, one commit each): `--keep-plan` parses on `retry` only (exit 2 elsewhere); non-TTY exit 1, nothing created; anything but `y` creates nothing; `y` creates the task and prints the id and `run: factory advance <id>`; the display content above, escaped; an unapproved source exits 1 without prompting; a digest that changed between display and confirm creates nothing.
- K3b tripwire: no command except `retry --keep-plan` calls `retry_with_plan`; plain `retry` with a TTY and "y" never does (like `test_no_command_except_approve_calls_approve_plan`).
- K4 (wiring): the CLI subprocess has no TTY, so seed the keep-plan child in-process, run `factory advance <new>` as a subprocess with the existing fakes, and assert a worktree is created and the implementer's `-p` equals `build_implementation_prompt(goal, plan)`. (The run_task stage already creates the worktree for a queued task without one.)
- K5: README command table row; correct README lines 72 and 194 to say the spend cap is global; `docs/spike-findings.md`.
- E1: `spike/keep_plan_e2e.py`, run by the user in a terminal ($0): a failed approved task, a reviewer-rejected one and an audited one in a scratch repo; `retry --keep-plan` with `y` creates a queued task, with `n` and non-TTY nothing, the rejected one is refused. Then `verify`.
