# Plan 21: reap agents orphaned by a SIGKILLed driver

Goal: after the driver dies by SIGKILL, the next factory command under the state lock finds and kills any agent or gate-test process group it left behind, so `recover` / `abandon` / `advance` act on a task whose process is provably dead.

## Spike (macOS, python 3.14, run by the user outside the sandbox; `ps` cannot be exec'd inside the Claude sandbox)
- A child started with `start_new_session=True` plus a grandchild keeps running after the parent is SIGKILLed; the child's ppid becomes 1; `pgrep -g <pgid>` still lists all three.
- `ps -o lstart= -p <pid>` is stable between calls (`Sun  8 Nov 09:21:57 2026`) but has 1-second resolution.
- `os.killpg(pgid, SIGKILL)` from a fresh process (not the parent) empties the group.
- Spike 2 (inside the Claude sandbox, python): `os.getpgid`, `os.killpg(pgid, 0)` and `pgrep` work; `ps` does not (exec denied). `killpg(pgid, 0)` raises ProcessLookupError once the group is empty. A group whose leader has exited and been reaped is still signalable (`killpg(pgid, 0)` succeeds).
- Spike 3 (user-run, `spike22.py`, done): under `nono run`, the sh, its sleeps and `nono`'s `/usr/bin/log stream` helper all share nono's pgid (the leader's pid). SIGKILL of the nono leader alone leaves the whole group alive (ppid 1); `killpg` then succeeds. `ps -o lstart=` under `LC_ALL=C TZ=UTC0` gave `Sun Nov  8 09:54:32 2026`, matching the launch second; children started a second later. So `killpg` covers the nono tree; `kill_tree` is still used for descendants that left the group.
- Not tested: pid reuse while a group with that id exists; a descendant that called `setsid`.

## Decided (not up for review)
- Option 1 of three offered: record then reap. No watchdog process.
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test except via an approved edit list. Never push.
- Reaping only runs while holding the state lock, so a live driver is never killed (reviewer-confirmed: the lock fd is not inherited by children).
- User answers to the reviewer's questions: a failed probe fails closed; a normal exit kills the tree; start time is verified with `ps` against a launch-time window (no ctypes sysctl).

## Behaviour
1. `process_registry.py` (new). One record per launched process group, `<state_dir>/procs/<pid>.json`: `pid`, `pgid`, `task_id`, `stage`, `launched_after`, `launched_before` (`time.time()` taken just before and just after `Popen`). Written atomically (temp file in the same directory, then rename) immediately after `Popen`; no `ps` at launch. Removed in a `finally` after the process exited and its tree was killed.
2. Validation on read: `pid > 1`, `pgid == pid` (the launch uses `start_new_session=True`) and `pgid != os.getpgrp()`. Anything else, or unparseable JSON, is an invalid record: deleted and reported, nothing killed.
3. `reap_orphans(state_dir, probe, kill)` (both injected). For each valid record the probe returns one of: `Alive(started)`, `Gone`, or raises `ProbeFailed`.
   - `Alive` and `started` inside `[launched_after - 2s, launched_before + 2s]`: `kill(pid)` (which is `kill_tree`: descendants, then `killpg`), poll `killpg(pgid, 0)` up to a bounded few seconds until the group is empty, delete the record, report task and stage.
   - `Alive` with a start time outside the window: pid reuse. Delete the record, kill nothing.
   - `Gone`: if `killpg(pgid, 0)` still succeeds the leader is dead but the group is alive (spike 2): kill the group, wait, delete, report. If the group is empty: delete the record, kill nothing.
   - `ProbeFailed`: keep the record and raise `ReapUncertain`. Commands fail closed (exit 1, "cannot prove orphans dead; nothing changed").
4. The real probe runs `ps -o lstart= -p <pid>` with `LC_ALL=C TZ=UTC0` (the string depends on the caller's locale and zone) and parses it; a non-zero exit with empty output is `Gone`, an exec error or any other failure is `ProbeFailed`.
5. A normal exit now also calls `kill_tree(pid)` in `SubprocessRunner.run` and `run_to_exit` before the record is removed (today only a timeout or exception kills the tree). Behaviour change, own red test.
6. Both launch sites register: `SubprocessRunner.run` (every agent stage) and `run_to_exit` (gate tests, reached via `verify_task` -> `_gate_once` -> `check_gate`). The registry is passed explicitly through `build_context`/`build_stages`; stage and task id come from the call.
7. One helper takes the state lock and reaps, used at all six lock sites (`advance`, `recover`, `abandon`, `rebase`, `clean`, `merge`) so a new site cannot forget it. It prints each orphan killed with task and stage. A held lock (`AlreadyRunning`) means nothing is reaped.
8. Wording: every "may still be alive" message is updated (`advance.py:46`, `cli.py:280` and `cli.py:296`, `abandon_task.py:14` and `:16`, `rebase_task.py:78`, `_fail_interrupted` docstring in `verify_task.py`, `recover`'s printed warning, README). Any asserted string is changed only via an approved test-edit list, shown before I touch it.

## Cycles
- P0 (done): spike 3. Plan approved by the user after it.
- P1 (red): registry writes a record atomically and reads it back; a half-written temp file is never read as a record.
- P2 (red, fakes): validation rejects pid <= 1, pgid != pid, pgid == own group, bad JSON: deleted, reported, no kill.
- P3 (red, fakes, one commit per case): alive in window -> kill, wait, delete, report; alive outside window -> delete, no kill; gone and group empty -> delete; gone and group alive -> kill group; probe failed -> keep, `ReapUncertain`.
- P4 (red): the reaper waits for the group to empty and gives up after the bound without deleting the record.
- P5 (red): real probe adapter parsing (given canned `ps` output via an injected runner; the real `ps` call is covered by the user-run e2e, since `ps` cannot run in the Claude sandbox). No skip marker.
- P6 (red): `SubprocessRunner.run` writes a record while the child runs and removes it after a normal exit, a timeout and an exception; a normal exit also kills a lingering grandchild.
- P7 (red): the same for `run_to_exit`, threaded through `check_gate` and `verify_task`.
- P8 (wiring): stages built by `build_context` write records during a fake slow agent and a fake slow gate test.
- P9: lock-and-reap helper; a parametrised test over all six commands; `advance` and `recover` print what was killed; `ReapUncertain` gives exit 1 and changes nothing; a held lock reaps nothing. Test fixtures for any live process use a separate session, never the test's own pid or group.
- P10: wording sweep with the approved edit list.
- P11: README, spike-findings section, full suite, `verify`.
- E2E (user-run, throwaway script): `kill -9` the driver while (a) a `nono`-wrapped fake slow `claude` and (b) a slow gate test run; confirm both trees are alive; run `factory recover` or `abandon`; confirm every process of both trees is dead, including descendants that changed group.

## Residual
- An orphan lives until the next factory command; nothing kills it sooner.
- A descendant that called `setsid` after its parent died is not found; one whose parent chain is alive is (via `kill_tree`).
- 1-second `lstart` resolution plus the window: a pid reused within about the same few seconds would be killed. Judged negligible; unverified.
- SIGKILL between `Popen` returning and the atomic write leaves an unrecorded orphan (tens of microseconds).
- Short-lived unconfined children (`discover_uv_tooling`, git calls, `pgrep`) stay in the driver's own group with no timeout; a SIGKILLed driver leaves them until they finish.
- `procs/` is outside `gate-cache`, which is the only state subdirectory the gate sandbox can write; a `state_dir` under `$TMPDIR` would be agent-writable, so validation (item 2) is the backstop.
- Spend of an orphaned read-only agent stays unledgered. Mid-rebase worktrees after SIGKILL are not touched.
