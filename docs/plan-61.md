# Plan 61: run the suite in parallel

## Problem
The full suite takes about 75s (2034 tests, none over 3s), so every verification gate is slow. There is no slow tail to cut; a marker subset would hide regressions.

## Measured (spike, nothing committed)
`uv run --with pytest-xdist pytest -q -n 8` on 8 cores: about 26s, 2034 tests. In 5 runs, 3 failed, always `tests/test_verify_task.py::test_the_gate_test_run_is_recorded_as_a_gate_process_while_it_runs_and_the_record_goes_after`, and once also `tests/test_runner.py::test_the_agent_record_names_the_task_and_its_state`. Both read `<state>/procs/*.json` from inside the child process and find nothing: the child runs before the parent has written its process record. Serial runs pass because the parent usually wins the race. Cause not yet proven: either a real window where an agent runs unrecorded (a product race in the crash-recovery guarantee) or a test assumption the code never promised.

## Decided
Option 1: add `pytest-xdist` and run the gate with `-n auto`. Out of scope: marker subsets.

## Increments
1. Diagnose (no edit): find where the record is written relative to spawn (`process_registry.py`, `process.py`, the runner), and prove which of the two causes it is. Stop and report if it is a product race: that needs its own plan and a decision, not a quiet test fix.
2. Red/green per outcome of 1: if a test assumption, make the two tests wait for the record rather than assume it; if a product race, only after approval. Check with 20 consecutive `-n 8` runs without a failure.
3. Add `pytest-xdist` to `[dependency-groups] dev`, refresh `uv.lock`, `uv lock --check`; put `-n auto` in `addopts` or the documented gate command (decide: `addopts` also affects single-test runs, which then pay xdist start-up).
4. AGENTS.md: update the gate and Tests sections (about 25s; keep the background and Monitor advice only if still needed; note the distinct-nonce rule applies per worker). README Development line. CI: `uv sync --locked` already installs the group; confirm the runner's core count gives a gain.

## Verification
20 consecutive parallel full runs green; ruff; a serial run (`-p no:xdist`) still green; `uv lock --check`.

## Risks
- Hidden shared state (ports, home directory, fixed paths) beyond the two seen; the 20 runs are the check.
- Loaded machines make time-based tests flaky; `-n auto` may use more workers than helps.
- GitHub runners have 4 cores or fewer, so the CI gain is smaller.

## Files
`orchestrator/pyproject.toml`, `orchestrator/uv.lock`, the two tests (or product code, see 1), `AGENTS.md`, `README.md`, `docs/plan-61.md`.
