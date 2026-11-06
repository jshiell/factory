# Plan 1: orchestrator core (no real agents, no API calls)

Goal: a tested, pure-Python core of the factory: runner-result classification, task state machine, persistence, cost ledger, command building, and a `step()` driver exercised only with a fake runner.

## Decided (not up for review)
- Python via `uv`, pytest, stdlib only for the core. OAuth auth.
- Orchestrator (never the agent) commits, unsigned; gates must check real state, not agent claims (spike finding).
- Isolation: `--setting-sources ""` plus env `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`.
- TDD: each bullet below is one red-green-commit cycle (unsigned commits, signed later).
- Success with permission denials -> task becomes `agent_done`, denials recorded for a later gate.
- Total spend cap (daily cap later). Cap check refuses when `total + next_budget > cap`.
- `failed` is terminal in plan 1; retry/max-attempts come in a later plan.
- The ledger is the single source of truth for spend; `Task` has no cost field.

## Layout
New uv project `orchestrator/` (src layout `src/factory/`, `tests/`, `tests/fixtures/`), `.python-version` pinned to 3.14. `target/` and `spike/` untouched.

## Cycles
0. **Setup (no test):** `uv init --package orchestrator`, `.python-version`, pytest dev dep, one trivial passing test. Commit.
1. **Classification** (pure). `OutcomeKind`: `success, success_with_denials, budget_exhausted, error, unparseable`. `Outcome(kind, cost_usd, subtype, errors, denials, structured_output, result, exit_code)`. Rule order: unparseable -> budget_exhausted -> error -> success_with_denials -> success. Fixtures are trimmed copies of the real spike outputs.
   - 1a empty/non-JSON stdout -> `unparseable` (keeps exit code)
   - 1b JSON non-object or `type != "result"` -> `unparseable`
   - 1c success fixture (probe-q3-git) -> `success` + cost
   - 1d budget fixture (probe-q2-budget) -> `budget_exhausted`, cost 0.0689 (pins precedence over `is_error`)
   - 1e `is_error` true, other/unknown subtype -> `error`, keeps subtype + errors
   - 1f denials fixture (spike-1793998055) -> `success_with_denials`, carries denials + structured_output
   - 1g `is_error` false, unknown subtype -> `error`
2. **Task + state machine.** States `queued, running, agent_done, failed`. `Task(id, prompt, state, attempts, failure_reason)`. Transition table keyed on `(state, event)`.
   - 2a `start`: queued -> running, attempts +1
   - 2b `succeeded`: running -> agent_done
   - 2c `failed(reason)`: running -> failed
   - 2d illegal transition -> `InvalidTransition`
3. **TaskStore** (one JSON file per task).
   - 3a save/load round trip (enum by value)
   - 3b missing id -> `TaskNotFound`
   - 3c atomic write (same-dir temp + `os.replace`; old content survives a failed replace)
   - 3d unsafe id (`/`, `..`) rejected
4. **Ledger** (append-only JSONL).
   - 4a append + `total()`
   - 4b missing file -> total 0
   - 4c `would_exceed(cap, next_budget)`
5. **build_command** (pure; returns `(argv, env_overlay)`). `RunnerConfig(model, budget_usd, permission_mode, allowed_tools, json_schema)`.
   - 5a base argv: `-p <prompt> --output-format json --model M`
   - 5b isolation: `--setting-sources` and `""` as separate elements + env overlay
   - 5c budget
   - 5d permission mode + space-joined allowed tools (only form tested in spike)
   - 5e JSON schema via `json.dumps`
6. **step with FakeRunner** (`Runner` Protocol).
   - 6a queued -> running persisted before `run` is called
   - 6b success -> `agent_done`, cost in ledger
   - 6c budget/error/unparseable -> `failed` with reason, cost still recorded
   - 6d `success_with_denials` -> `agent_done`, denials recorded on the task
   - 6e cap would be exceeded -> `BudgetCapReached`, runner not called
   - 6f non-queued task -> `InvalidTransition`, runner not called
7. **Verify skill**, then update `docs/spike-findings.md`: exit codes weren't recorded; isolation + tool use together is untested (first spike of plan 2).

## Out of scope
Real subprocess runner (incl. `stdin=DEVNULL`), worktrees, gates, concurrency, plan/review/audit stages, retry, daily cap, crash recovery (a stale `running` task after a crash is a known gap), `TaskStore.list()`, CLI.

## Assumptions
- Result-JSON field names come from single spike runs, not a documented contract. Exit codes were not saved; budget-abort exit 1 was seen on the console only.
- `--allowedTools` repeated-arg form and `--` before the prompt are unverified.
