# Plan 52: log the agent's output when its result can't be parsed

## Problem

When an agent run ends with output `classify` cannot parse, the task records only `<stage>_unparseable` (the reason is
`outcome.kind.value`). `SubprocessRunner.run` discards stderr (`stderr=subprocess.DEVNULL`, `runner.py:59`) and nothing
logs stdout, so the cause is invisible. Plan 49's sandbox exit 127 was found this way.

## Decided (user, this session)

- Destination: the existing `factory` Python logger only; no per-task log file, no new setting.
- Scope: unparseable outcomes only. `error`, `budget_exhausted` and non-zero exits with a parseable result are not reported.

## Change

- `RunResult` (`step.py`) gains `stderr: str = ""` as its **last** field (it is built positionally with three arguments in
  tests, so it must follow `timed_out`).
- `process._tail` (`process.py:84`, reads the last N characters of a binary file, at most 4×N bytes) becomes the public
  `tail` and is reused. A pure rename, done first with the suite green.
- `SubprocessRunner.run` sends stderr to a second **binary** temporary file (stdout's is text mode) instead of `/dev/null`
  and returns `tail(file, TAIL_CHARS)` as `RunResult.stderr`. One cap, `TAIL_CHARS = 2000`, for stderr here and for both
  tails in the log line. stdout is still read in full (the result JSON needs it); only its logged tail is capped.
- One helper in `step.py` (which already has the logger and `RunResult`; a separate module would import `step.py` and
  create a cycle): `classify_run(result, task_id, stage) -> Outcome`. It calls `classify`, and when the kind is
  `UNPARSEABLE` logs one WARNING on the `factory` logger with the task id, stage, exit code, and the last `TAIL_CHARS`
  characters of stdout and of stderr, each formatted with `%r` (escapes `\n`, `\t`, control, format, and line/paragraph
  separator characters in one step, so agent output cannot forge a log line; `escape_control` is not enough because it
  leaves `\n`, `\t`, U+2028 and U+2029).
- Both classify sites use it: `step` (`step.py:67`, implement stage) and `call_agent` (`agent_call.py:32`, every other
  stage, which includes the audit-eval harness through `audit_task` and `run_readonly_stage`). These are the only two
  `classify(` calls under `src`.
- The task record, failure reasons, ledger and CLI output do not change. An unparseable run that also changed the
  worktree still logs (then `run_readonly_stage` returns `Modified`); that is intended.
- stderr now lands in a temp file, so a runaway writer can fill the temp directory until the runner timeout kills it;
  stdout already has the same exposure. Accepted.
- README: extend the Server paragraph (the sentence about `runner_error` warnings going to the server's stderr): the
  unparseable diagnostic (exit code, stdout and stderr tails, escaped and truncated) goes there too, eval runs included,
  and the log can contain anything the agent saw or printed, so treat it as sensitive as the worktree.

## Unverified assumptions (check during implementation)

- Nothing under `src` calls `logging.basicConfig`, `dictConfig` or `addHandler` (grep done), and uvicorn is started with
  `log_level="warning"` (`cli.py:64`). So `factory` WARNINGs should reach stderr through Python's last-resort handler,
  once, with no timestamp or level. Confirm under a real `factory serve` (cycle 8). `docs/spike-findings.md` has no
  earlier check of this.
- What `nono` prints on stderr, and whether it can echo the argv (the prompt: plan, diff). The cap limits the exposure.
- No fake `Runner` in tests returns something other than a `RunResult` (it would raise on an unparseable path). Grep
  `def run(self` and `RunResult(` with four positional arguments in tests before cycle 1.

## TDD cycles (one commit each)

Seams: `SubprocessRunner.run`, `call_agent`, `step`, and the log output (`caplog`).

0. Pre-checks, no commit: the greps above; grep `caplog`, `capfd` and `capsys` near unparseable cases.
1. Refactor, guard (suite green): rename `_tail` to the public `tail`.
2. Red: a fake claude (`fake_claude`, `test_runner.py:19`) writes `boom` to stderr and exits 1;
   `SubprocessRunner.run(...).stderr` contains `boom`; a stderr longer than `TAIL_CHARS` returns only its tail.
   Implement `RunResult.stderr` and the binary stderr file read with `tail`.
3. Guard: through the failing confined wrapper (as in `test_runner.py:275`), `result.stderr` contains
   `profile not found`; `test_a_wrapper_banner_on_stderr_does_not_reach_the_captured_stdout` (`test_runner.py:318`)
   stays green.
4. Red, parametrised, through `call_agent` with a fake runner: unparseable stdout `"not json"`, exit 1, stderr `"boom"`
   logs one WARNING with the task id, stage, exit code and both texts; success, `error` and `budget_exhausted` results log
   nothing. Implement `classify_run` in `step.py` and use it in `call_agent`.
5. Guard: a timeout logs nothing from this path (it returns before `classify`).
6. Red: `step` (implement stage) logs the same way for unparseable output. Replace its `classify` call.
7. Red: a stdout tail holding `\n` followed by a fake `agent run failed for task t9, stage audit:` line, `\x1b[31m`,
   `\r` and U+2028 is logged on one line with all of them escaped, and a stdout longer than `TAIL_CHARS` is cut to its tail.
8. Manual check of the last-resort output under `factory serve` with a fake agent; record it in `docs/spike-findings.md`.
9. README (the Server paragraph, above).
10. Full suite in the background (Monitor), `verify`, then the commit audit.

## Tests touching these strings

No test asserts the unparseable reason text changes. The reasons and records are unchanged, so these keep passing:
`test_step.py:69`, `test_readonly_stage.py:104`, `test_audit_eval.py:108-109`, and the `"unparseable": "nope"` fixtures in
`test_plan_task.py:156`, `test_review_plan.py:184` and `test_audit_task.py:159`. `test_runner.py:318` must stay green.
`test_process.py:17` covers the renamed `tail` through its existing caller; update its import only if it names `_tail`.

## Not covered

- A per-task log file, `clean` retention, and structured logging.
- Reporting `error`, `budget_exhausted` and non-zero exits.
- Redacting secrets from the output; it is escaped and truncated only.
- Capturing stderr of the gate's test run (a different path, already returned as test output).
