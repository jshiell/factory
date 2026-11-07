# Plan 3: tamper gate and audit stage

Goal: narrow the test-tampering gap with a deterministic gate, then add a second, read-only agent that audits a `verified` task's diff against its prompt. Plan and review stages move to Plan 4. This plan narrows the tampering gap; it does not close it (see Known gaps).

## Decided (not up for review)
- uv, pytest, stdlib only. OAuth. Isolation flags on. TDD, one red-green-commit per bullet, never edit a passing test.
- The orchestrator owns state, commits and gates. Gates check git state and exit codes, never agent claims. `failed` is terminal.
- `step`'s behaviour and the `Runner` Protocol do not change. Plan/review (Plan 4) reuse the audit helpers.
- **Tamper rule R2, additive-only.** Existing test files may gain lines but not lose any; no delete, rename or type change. New test files are fine.
- **Protected files** fail with `tests_modified` for any change, new files included: `conftest.py` at any depth, `pytest.ini`, `tox.ini`, `setup.cfg`, and the `[tool.pytest.ini_options]` table of `pyproject.toml` (compared with `tomllib` between base and HEAD). Dependency additions in `pyproject.toml` stay allowed.
- **Auditor infrastructure failure leaves the task `verified`** with the reason recorded; a human re-runs. Only a real `fail` verdict fails the task.
- Gitignored-state tampering is a known gap, not fixed here.
- Verdict schema: `{"verdict": "pass"|"fail", "findings": [str]}`; the verdict wins over findings.

## Cycles
0. **Spike (throwaway, flagged; exit codes saved to files).** `spike/audit_probe.py`. Config: no permission mode, `allowedTools Read Grep Glob`, `disallowedTools Write Edit NotebookEdit Bash`, enum `--json-schema`. Check and record in `docs/spike-findings.md`:
   - `structured_output` comes back in the defined schema
   - an explicit write attempt is denied and `git status` stays clean
   - whether the auditor flags an added `pytestmark = pytest.mark.skip`, and import-time monkeypatching in a new test file
   - `getconf ARG_MAX`; `:(glob)<subdir>/**/test_*.py` against tests at several depths (does `**/` match zero directories?)
   - whether pytest exits 0 when every test is skipped
   Unconfirmed items are labelled as such; a missed detection is a finding, not a blocker.
A. **Tamper gate (`gate.py`)**
   - A1 modified existing test file adding only lines passes; one with deleted lines fails `tests_modified` (`git diff --numstat`, one call, `:(glob)` pathspecs prefixed by `project_subdir`)
   - A2 deleted, renamed (`--no-renames` makes it a delete) or type-changed existing test file fails `tests_modified`
   - A3 new test file passes; changed non-test file passes; existing gate tests stay green
   - A4 protected files fail `tests_modified` for any status (list above; pyproject compared by pytest table only)
   - A5 order: escape raise, `not_descendant`, `no_commits`, `no_changes`, `tests_modified`, then tests (`tests_timeout`/`tests_failed`). Test: tampered diff with failing `test_argv` reports `tests_modified` and a sentinel shows the test command never ran
   - A6 `test_globs` and protected names are defaulted parameters, added last
B. **Task states**
   - B1 `verified` --`audit_passed`--> `audited`
   - B2 `verified` --`audit_failed(reason)`--> `failed`
   - B3 both events rejected from `queued`, `running`, `agent_done`
   - B4 `audit_findings: tuple[str, ...] = ()` and `audited_sha: str | None = None` round-trip through the store (`load` converts list back to tuple, `data.get` default); old JSON loads
C. **Audit driver (`audit_task.py`)**
   - C0 refactor, step tests untouched: extract `check_budget(ledger, config, cap)` from `preflight`; make the assumed-cost helper take the failure event
   - C1 `build_audit_prompt(task, diff)` pure: task prompt plus diff, diff marked untrusted
   - C2 `AUDIT_CONFIG`: allowed `Read Grep Glob`, disallowed `Write Edit NotebookEdit Bash`, no permission mode, schema, `max_turns`, budget below the implementer's
   - C3 non-`verified` task -> `InvalidTransition` before any budget check or git call
   - C4 missing budget or cap reached -> raise, runner not called
   - C5 diff over a fixed size limit -> `failed("diff_too_large")`, nothing charged (limit set from spike ARG_MAX)
   - C6 runner receives `replace(task, prompt=audit_prompt)`; the stored task keeps its prompt (FakeRunner captures `task.prompt`)
   - C7 cost to ledger; `pass` -> `audited` with findings and `audited_sha`; `fail` -> `failed("audit_rejected")` with findings
   - C8 malformed/missing/unknown verdict, `ERROR`, `BUDGET_EXHAUSTED`, timeout, runner exception: task stays `verified`, reason recorded, cost recorded (assumed where the run was cut off). Needs a no-transition record path (field `audit_error`, default `None`)
   - C9 worktree dirty before the audit, or HEAD changed / dirty after: `failed("audit_modified_worktree")`
D. **Throwaway e2e (`spike/e2e3.py`):** one real task through implement, verify, audit, written to satisfy R2 (add `subtract` tests to the existing test file). No paid tamper case; unit tests cover it. Run `verify`, update `docs/spike-findings.md`.

## Known gaps (accepted)
- Gitignored/venv state (`.venv`, `sitecustomize.py`), import-time monkeypatching from new test modules, dependency/lockfile changes, conftest above rootdir: not caught by the gate. Only the audit may catch some.
- Added skips and shadowing in existing test files pass R2; left to the audit.
- Auditor reliability unmeasured; the diff is untrusted input that may try to steer it.
- Ledger mixes implementer and auditor cost per task id (no stage field).
- Plan 2 gaps unchanged: stderr discarded, SIGKILL no grace, gate-test grandchildren, crash recovery, worktree cleanup.
- Anything downstream (Plan 4 merge/review) must require `audited`, not `verified`.

## Out of scope
Plan and review stages, retry, concurrency, daily cap, CLI, worktree GC.

## Assumptions (unverified until cycle 0)
- With no permission mode, `--allowedTools` plus `--disallowedTools` denies Write/Edit in `-p` mode.
- An enum `--json-schema` returns conforming `structured_output`.
- macOS ARG_MAX is about 1 MiB.
