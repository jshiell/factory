# Plan 4: plan and review stages, human approval gate

Goal: put the front half of the workflow (plan, plan review, human approval) in front of implementation. A goal-first task reaches `queued` only through `approve_plan`, after a reviewed plan. Pipeline: goal -> plan -> review -> approve -> implement -> verify -> audit. Legacy tasks created directly as `queued` still bypass the front half (see Known gaps).

## Decided (not up for review)
- uv, pytest, stdlib only. OAuth. Isolation flags on. TDD, one red-green-commit per bullet, never edit a passing test. Failing test first, shown red.
- The orchestrator owns state, commits and gates. `failed` is terminal. `step`'s behaviour, the `Runner` Protocol and the existing audit behaviour do not change.
- Planner and reviewer are read-only, like the auditor: `Read Grep Glob` allowed, `Write Edit NotebookEdit Bash` disallowed, no permission mode, `--json-schema`. Worktree clean before and HEAD unchanged and clean after, else a `*_modified_worktree` failure.
- Infrastructure failure (timeout, budget, error, malformed output, oversized plan) leaves the task in its current state with the reason recorded, and a retry clears it; only a real verdict moves the task.
- **Approval is human-only by process, not by code.** Only the e2e script calls `approve_plan`, after a stdin `y`. The digest binding is TOCTOU protection ("approve exactly the text I was shown"), not access control: `plan` and `plan_digest` live in the same JSON, so anything with store access can bypass it.
- Anything that merges or releases must require `audited`, not `verified`.
- **Open decisions, settled by review:** (1) a `revise` verdict fails the task `plan_rejected` with findings, a human resubmits; no auto loop. (2) the planner runs in a worktree created at plan time and reused by implementation. (3) no human plan edits; the human approves or rejects.

## Cycles
0. **Spike (throwaway, flagged; exit codes saved).** `spike/plan_probe.py`. Check and record in `docs/spike-findings.md`:
   - a read-only planner in a worktree returns `{"plan": str, "risks": [str]}` as `structured_output` with a multi-paragraph plan; record plan size in bytes
   - the plan names real files (spot-check by hand), cost per plan, whether `max_turns` 8 is enough
   - the planner starts in `worktree/<project_subdir>` (the runner fixes cwd); can `Read` reach the rest of the worktree, and outside it?
   - a read-only reviewer given a deliberately flawed plan (deletes a test, names a nonexistent file, ignores the goal) returns `revise` with a relevant finding; given a sound plan returns `approve`
   Unconfirmed items are labelled as such; a missed detection is a finding, not a blocker.
A. **Housekeeping**
   - A1 `audit_error` cleared when the audit ends in `audit_rejected`, `audit_modified_worktree` or `diff_too_large`
   - A2 `Ledger.append(..., stage=None)` writes `stage` to the entry; `step` passes `"implement"`, audit `"audit"`, later plan `"plan"` and review `"review"`. One test pins the stage and the absent-stage case (existing ledger/step tests untouched)
B. **Extract the read-only stage runner (refactor, audit tests unmodified)**
   - B1 `run_readonly_stage(task, stage_prompt, runner, config, ledger, stage)` returns `Modified | Infra(reason) | Ran(structured_output)`. Keeps audit's exact order: clean check before, run `replace(task, prompt=...)`, exception/timeout (assumed cost, return `Infra`), classify and append cost, post-run HEAD/dirty check (`Modified`), non-success kind (`Infra`). Parsing stays per stage; the caller maps `Modified` to its own failure. Own tests: pre-dirty, post-dirty, post-commit, timeout, exception, non-success kind, success
   - B2 `audit_task` rewritten on the helper; `diff_too_large` stays before it; all existing audit tests stay green unmodified
C. **Task states and fields**
   - C1 states `proposed`, `planned`, `reviewed`. New defaulted fields: `goal`, `plan`, `plan_risks`, `plan_digest`, `plan_error`, `review_findings`, `review_error`; both tuples restored in `store.load`, old JSON loads. Goal-first tasks are built with `prompt=""`; default state stays `queued`
   - C2 transitions: `proposed --plan_done--> planned`; `proposed --plan_failed(r)--> failed`; `planned --review_passed--> reviewed`; `planned --review_failed(r)--> failed`; `reviewed --approved--> queued`; `reviewed --human_rejected--> failed`
   - C3 each new event rejected from every other state (parametrised); `START` rejected from `proposed`, `planned`, `reviewed`
   - C4 new fields round-trip through the store
D. **Plan stage (`plan_task.py`)**
   - D0 pure: `PLAN_SCHEMA` (`plan` string, `risks` string list), `PLAN_CONFIG` (read-only tools, budget below the implementer's, `max_turns`), `build_plan_prompt(goal)`
   - D1 non-`proposed` task -> `InvalidTransition` before budget or git
   - D2 missing budget / cap reached -> raise, nothing created
   - D3 create the worktree only when `task.worktree` is unset, persist `worktree` and `base_sha` before the run; creation failure leaves the task `proposed`. Test: planner infra failure, then a successful re-run reuses the worktree
   - D4 runner gets `replace(task, prompt=planner_prompt)`; stored `prompt` stays empty
   - D5 valid output -> `planned` with `plan`, `plan_risks`, `plan_digest = sha256(plan.encode())`, cost recorded under `"plan"`, `plan_error` cleared; a plan over `MAX_PLAN_BYTES` -> stays `proposed`, `plan_error="plan_too_large"`
   - D6 empty or non-string plan, malformed output, `ERROR`, `BUDGET_EXHAUSTED`, timeout, runner exception -> stays `proposed`, `plan_error` set, cost recorded (assumed when cut off)
   - D7 worktree modified by the planner -> `plan_failed("plan_modified_worktree")`
E. **Review stage (`review_plan.py`)**
   - E0 pure: `REVIEW_SCHEMA` (`{"verdict": "approve"|"revise", "findings": [str]}`), `REVIEW_CONFIG`, `build_review_prompt(goal, plan)` with the plan marked untrusted
   - E1 non-`planned` task -> `InvalidTransition` first
   - E2 digest recomputed from `task.plan`; mismatch with `plan_digest` -> `review_failed("plan_changed")`, no cost (runs before budget and runner)
   - E3 `approve` -> `reviewed` with `review_findings`; `revise` -> `failed("plan_rejected")` with findings; any verdict clears `review_error`
   - E4 infrastructure failures as D6: stays `planned`, `review_error` set
   - E5 worktree modified by the reviewer -> `failed("review_modified_worktree")`
F. **Approval (`approve_plan.py`)**
   - F1 `approve_plan(task_id, store, shown_digest)` recomputes `sha256(task.plan)`, which must equal both `shown_digest` and `plan_digest`; otherwise it raises and the task is unchanged. Tests: plan altered after review with a stale `plan_digest`; wrong supplied digest
   - F2 sets `prompt` from `build_implementation_prompt(goal, plan)` built from the same loaded `task.plan`: the plan, the test-first rule and the tamper rules (add tests, never edit or remove existing ones, no config changes); must not start with `-`; moves `reviewed` -> `queued`
   - F3 non-`reviewed` task -> `InvalidTransition`
   - F4 `reject_plan(task_id, store)`: `reviewed` -> `failed("plan_rejected_by_human")`
   - F5 tripwire (not a guarantee): a scan of `src/factory/*.py` asserts `Event.APPROVED` and `approve_plan(` appear only in `approve_plan.py`; plus behavioural tests that `plan_task` and `review_plan` never persist `queued` for any runner output
G. **run_task reuse (`run_task.py`)**
   - G1 a task with `worktree` already set skips `create`, after checking the worktree exists, `HEAD == base_sha` and it is clean; otherwise raise and leave the task `queued`. Tasks without a worktree behave as before (existing tests untouched)
H. **Throwaway e2e (`spike/e2e4.py`):** one goal ("add `multiply` with tests") through plan, review, approve, implement, verify, audit. The script prints the plan and review findings, hashes the text it printed, and reads stdin: `y` calls `approve_plan` with that digest, anything else calls `reject_plan`. Never auto-approves. Run `verify`, update `docs/spike-findings.md`.

## Known gaps (accepted)
- Legacy `queued` tasks, or anyone with store access, bypass plan, review and approval; the digest is not access control.
- The auditor judges against the agent-written plan prompt, not the goal; an off-goal plan can still pass audit.
- Planner and reviewer reliability unmeasured beyond cycle 0 samples; the plan text is agent output and may try to steer the reviewer.
- `base_sha` may be old by approval time; no staleness check.
- Every false `revise` or rejected plan means a new task id and an orphaned worktree and branch (GC out of scope).
- No revise loop, human plan edits, retry, concurrency or CLI; scripts drive the pipeline.
- Plan 3 gaps unchanged: gitignored-state and import-time tampering, plan 2 gaps (stderr discarded, SIGKILL no grace, crash recovery).

## Out of scope
Merge/release stage, retry and revise loops, CLI, daily cap, worktree GC, concurrency, base_sha staleness checks, ledger caps per stage.

## Assumptions (unverified until cycle 0)
- A read-only planner can produce a useful plan within `max_turns` 8 and about $0.30.
- Structured output handles a long free-text field reliably, and plans stay well under argv limits.
- The reviewer detects obviously flawed plans; subtle ones unmeasured.
