# Plan 65: finish the sandbox seam

Candidate 2 of the architecture review (2026-11-19). Follows plan 64 (built, audited). Draft: not reviewed yet.

## Problem
Plan 63 added the `Sandbox` seam but left the two callers (`SubprocessRunner.run` and `build_gate_sandbox`) doing the same work twice, differently:
- **Private-directory rule.** The runner drops a read grant that would expose a private directory (`runner._executable_directory`, private = configured dirs plus `$HOME`). The gate drops a uv directory only if it holds `$HOME` (`gate_sandbox._uv_read_dirs`). The gate never passes the state dir, so a uv directory that holds the state dir is granted.
- **Working directory.** nono's `--allow-cwd` grants whatever the process cwd is, so the policy does not name it. The runner launches in `worktree/project_subdir`; the gate in a directory the policy does not record. A sandbox other than nono cannot know what to make writable.
- **Choosing a sandbox.** `SubprocessRunner` and `build_gate_sandbox` each default to `NonoSandbox(sandbox_executable)`, while `agent_runner` and `gate_sandbox_for` use `sandbox_named(...)`. Three places decide, two of them with nono hard-coded.

## Decided (grilling, user-approved 2026-11-19)
- Add a `cwd` field to `SandboxPolicy`. Both callers fill it. `NonoSandbox` ignores it (nono reads the process cwd), so no argv changes.
- One shared helper for the private-directory rule, used by the runner and the gate. The gate also passes the state dir (a small behaviour change, tested).
- `sandbox_named` is the only place that chooses a default sandbox.
- `sandbox_executable` leaves both interfaces (`SubprocessRunner`, `build_gate_sandbox`) as the last code increment. `Settings.sandbox_executable` stays: it feeds `sandbox_named`.
- No assertion in an existing test changes. If one must, stop and ask.

## Open question (needs the user)
- When `sandbox` is omitted, do `SubprocessRunner` and `build_gate_sandbox` (a) default to `sandbox_named(DEFAULT_SANDBOX, "nono")`, so the many unconfined test runners keep working, or (b) require `sandbox`? Recommended: (a). It keeps the 20-odd runner tests that never confine unchanged; the only literal `"nono"` left is that one default. Increment 7 follows the answer.

## Increments (one commit each; red = new failing test first; guard = existing tests cover it)
0. Inventory, no commit. Re-grep `sandbox_executable`, `_executable_directory`, `_uv_read_dirs`, `SandboxPolicy(`, `NonoSandbox(`, `build_gate_sandbox(` across `src/`, `tests/`, `spike/`, README, AGENTS.md. Confirm in `verify_task.py` and `process.py` which directory the gate test process launches in (it must equal the `cwd` increment 5 passes). Known so far: `test_runner.py` (about 28 `sandbox_executable=` sites, plus :592 which passes both `sandbox` and `sandbox_executable`), `test_gate_sandbox.py:12`, `spike/confinement_probe.py:229`, `spike/git_hardening_probe.py:235`, `spike/gate_sandbox_probe.py:72`, `test_cli_wiring.py:69` (a settings key, unaffected).
1. Red then green: `sandbox.py` gets `without_private(directories, private_dirs)`, which drops each directory that contains a private one. Unit test in `tests/test_sandbox.py` (kept, dropped, nested, relative and symlinked inputs, empty private list). Green: the helper, with `runner._executable_directory` and `gate_sandbox._uv_read_dirs` calling it. Guarded by the `test_runner.py` private-directory tests (resolved, unresolved, no-grant, home) and `test_gate_sandbox.py:124`.
2. Red: `build_gate_sandbox(private_dirs=...)`: a uv directory that holds a private directory adds no read grant. Green: new keyword (default `()`); the gate's private list is `private_dirs` plus `HOME` (`"/"` when unset, as today).
3. Red: `gate_sandbox_for` passes the state dir as a private directory (assert through a recording sandbox in `test_stages.py`, next to the test at :224). Green: `stages.py` passes `(str(settings.state_dir),)`.
4. Red: `SubprocessRunner.run` passes `cwd=worktree/project_subdir` in the policy (recording sandbox, like `test_runner.py:592`). Green: add `cwd: str | Path | None = None` to `SandboxPolicy`; the runner fills it. Document in the `SandboxPolicy` docstring that `NonoSandbox` ignores it.
5. Red: `build_gate_sandbox` passes `cwd=project_dir`. Green: the gate fills it. (If increment 0 finds the gate launches elsewhere, pass that directory and record it as a deviation.)
6. Guard (retarget, mechanical): every `sandbox_executable=X` in `test_runner.py` and `test_gate_sandbox.py` becomes `sandbox=NonoSandbox(X)`; the three spike calls to `build_gate_sandbox` do the same (import from `factory.sandbox`). Assertions untouched. Two commits: tests, then spikes (statically checked, as in plan 64).
7. Per the open question: remove `sandbox_executable` from `SubprocessRunner.__init__` and `build_gate_sandbox`; the default is `sandbox_named(DEFAULT_SANDBOX, "nono")` (answer a). Delete the "`sandbox` wins if both are given" docstring text. **Stop and ask before touching** `test_runner.py:592` and the matching gate test: they pass both arguments and assert `sandbox` wins, so they have no meaning once the argument is gone. Recommended: delete them in this commit, since the behaviour they pin no longer exists.
8. Docs, same commit as the last code change that makes them true: README line 217 (`sandbox_executable` still selects the binary, now only through `sandbox_named`; the gate now treats the state dir as private; the policy names the working directory), AGENTS.md line 11 (a sandbox receives `cwd` and may use it).
9. Gate: `uv run ruff check` (also after every increment), the full suite in the background with `-n auto`, the `verify` skill, then `audit-commits`.
10. Record deviations in this plan.

## Tests touched (CLAUDE.md step 2: grep before changing user-visible output)
No user-visible string changes. Behaviour change: a gate prefix can now omit a uv read grant (only when the uv directory holds the state dir). Tests that pin gate read grants: `test_gate_sandbox.py` (the whole file), `test_stages.py:205-225`, `test_gate.py` (`sandbox_unavailable`, unaffected).

## Risks
- A uv installed under the state dir would lose its read grant, so uv would not start in the gate. Unlikely (the state dir is the factory's own), and the failure is the existing fail-closed path, not a silent grant.
- `cwd` is unused by the only adapter; per the codebase-design rule that is a hypothetical seam until a second sandbox exists. Accepted (user chose to add it); the recording-sandbox tests are what keep it honest.
- Retargeting about 28 test call sites is mechanical but wide; one wrong keyword shows as a failing test, not silently.
- Spikes are verified statically only; none is run.
