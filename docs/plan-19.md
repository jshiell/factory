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
