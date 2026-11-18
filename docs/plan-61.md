# Plan 61: run the suite in parallel

## Problem
The full suite takes about 75s (2034 tests, none over 3s), so every verification gate is slow. There is no slow tail to cut; a marker subset would hide regressions.

## Measured (spike, nothing committed)
`uv run --with pytest-xdist pytest -q -n 8` on 8 cores: about 26s, 2034 tests. In 5 runs, 3 failed on `tests/test_verify_task.py::test_the_gate_test_run_is_recorded_as_a_gate_process_while_it_runs_and_the_record_goes_after`, and once also `tests/test_runner.py::test_the_agent_record_names_the_task_and_its_state`. Both have the child process read `<state>/procs/*.json` before the parent has written its record.

## Diagnosis (settled by review)
`runner.py:52-64` and `process.py:58-63` call `Popen` first and then `tracker.launched(...)`, which writes the record (`process_registry.py:244`). Nothing makes the child wait. README:355 already lists the crash-recovery side as a known limit ("one launched in the last instant before the kill is unrecorded"). The product window is known and accepted; the tests assume an ordering the code never promised. The fix is test-only. Closing the window (the child waits for the record) would be a separate plan.

## Decided
- Option 1: `pytest-xdist` with `-n auto`, in the documented gate command only (not `addopts`; single-test runs stay serial).
- CI stays serial on purpose: every push is then a free serial check, and the gain is small on a 2 to 4 core runner. `ci.yml` is not changed.
- Out of scope: marker subsets, closing the record window.

## Increments (TDD where a behaviour changes; one commit each)
1. Add `pytest-xdist` to `[dependency-groups] dev`, refresh `uv.lock`, `uv lock --check`. Confirm plain `uv run pytest -q` is still serial and green. (Safe first: `-n` is not the default.)
2. Audit, no edit; list the findings here when done:
   - children that read `procs/`: grep `procs`, `records=`, `STATE` in `orchestrator/tests`. Known so far: `test_runner.py:463-482` (three tests, `ls` and `cat` of `$STATE/procs`), `test_process.py:97` (`LIST_RECORDS`), `test_verify_task.py:109`.
   - `pgrep -f` patterns unique across the suite (known: `sleep 29.4712`; `test_process.py` markers come from the caller, check they are per-test paths).
   - fixed ports (checked: the one `bind` uses port 0), `Path.home`, `expanduser`, absolute `/tmp/` paths.
3. One cycle per racy test (or one shared helper if the change is the same):
   - Red: pass a records wrapper that sleeps (about 0.5s) before calling `FileProcessRecords(state).write`, so the race happens every time; the current child script fails.
   - Green: the child polls for `*.json` in a bounded loop (up to 5s) then cats or lists it. The test still proves the record exists while the child is alive. A half-written record cannot be read: the temp file `.<pid>.tmp` does not match `*.json` and `os.replace` is atomic.
4. Fix any audit finding the same way; mark it a guard if nothing needs fixing.
5. Stability: run the fixed tests about 100 times under `-n 16`; then 20 full runs of the gate command `uv run pytest -q -n auto` in a background loop logging failures to the scratchpad (about 9 minutes, past the tool timeout). One failure means back to step 2.
6. Docs: AGENTS.md gate command becomes `uv run pytest -q -n auto`, time about 25s (serial about 75s); keep the background and Monitor advice (a loaded machine can exceed the tool timeout). README Development line gets the same command. No nonce note: nonces are iterators inside one process and each worker is its own process.

## Audit findings (increment 2, done)
- Children that read the process records, all fixed with `RecordsWrittenLate` plus a bounded wait: `test_runner.py` (two tests), `test_process.py` (`LIST_RECORDS`), `test_verify_task.py` (the gate test).
- `test_cli_wiring.py` fake-claude and fake-auditor scripts also read records, but run through the real CLI, so no slow-records object can be injected. Treated as a guard: 40 runs at `-n 16` over those files and the four fixed ones, then 20 full `-n auto` runs, none failed.
- `pgrep -f` patterns are unique (`sleep 29.4712`; `test_process.py` markers come from per-test paths); the one `bind` uses port 0; `Path.home` and `/tmp/` hits are only strings.

## Verification
Ruff; a serial full run; the 20 parallel runs above; `uv lock --check`; the `verify` skill.

## Risks
- Hidden shared state beyond what the audit finds; a rare overlap may survive 20 runs (20 runs catch a 5% flake only about 64% of the time), hence the 100x stress run on the known racy tests.
- Time-based tests are flakier on a loaded machine; `-n auto` may use more workers than help.
- Not verified: `uv lock --check` flag in this uv version (use `uv lock --locked` if not); that `-n auto` resolves to 8 here.

## Files
`orchestrator/pyproject.toml`, `orchestrator/uv.lock`, `orchestrator/tests/test_runner.py`, `test_process.py`, `test_verify_task.py` (and any audit finding), `AGENTS.md`, `README.md`, `docs/plan-61.md`.
