# Plan 65: finish the sandbox seam

Candidate 2 of the architecture review (2026-11-19). Follows plan 64 (built, audited). Reviewed; findings folded in.

## Problem
Plan 63 added the `Sandbox` seam but left the two callers (`SubprocessRunner.run` and `build_gate_sandbox`) doing the same work twice, differently:
- **Private-directory rule.** The runner drops a read grant that would expose a private directory (`runner._executable_directory`, private = configured dirs plus `$HOME` when set, checking both the unresolved and the resolved directory). The gate drops a uv directory only if it holds `base_env.get("HOME", "/")` (`gate_sandbox._uv_read_dirs`). The gate never passes the state dir, so a uv directory that holds the state dir is granted. The two also differ when HOME is unset: the gate treats `/` as private, the runner treats nothing as private (and so grants `/` if the executable lives there and no private dirs are configured).
- **Working directory.** nono's `--allow-cwd` grants whatever the process cwd is, so the policy does not name it. The runner launches in `worktree/project_subdir`; the gate in `project_dir` (`gate.check_gate` passes `gate._resolve_inside(...)` to `run_to_exit`; same value `stages.py` passes). A sandbox other than nono cannot know what to make writable.
- **Choosing a sandbox.** `SubprocessRunner` and `build_gate_sandbox` each default to `NonoSandbox(sandbox_executable)`, while `agent_runner` and `gate_sandbox_for` use `sandbox_named(...)`. Three places decide, two of them with nono hard-coded.

## Decided (grilling, user-approved 2026-11-19)
- Add a `cwd` field to `SandboxPolicy`. Both callers fill it. `NonoSandbox` ignores it (nono reads the process cwd), so no argv changes.
- A shared helper for the "drop a directory that contains a private one" step, used by the runner and the gate. The two private lists stay separate (see Open decisions for HOME). The gate also passes the state dir (a small behaviour change, tested).
- `sandbox_named` is the only place that chooses a default sandbox.
- `sandbox_executable` leaves both interfaces (`SubprocessRunner`, `build_gate_sandbox`) as the last code increment. `Settings.sandbox_executable` stays: it feeds `sandbox_named`.
- `_executable_directory` keeps its signature (`spike/confinement_probe.py:27` imports it).
- No assertion in an existing test changes. If one must, stop and ask.

## Default when `sandbox` is omitted (split by caller; recommendation adopted on "Fold")
- `build_gate_sandbox`: `sandbox` becomes required. After increment 6 no caller relies on its default.
- `SubprocessRunner`: keeps an optional `sandbox`, defaulting to `sandbox_named(DEFAULT_SANDBOX, "nono")`. Requiring it would break the many unconfined test runners, `spike/confinement_probe.py:221` (builds a runner with no sandbox and runs it confined) and probably `spike/git_hardening_probe.py`. The one literal `"nono"` left is that default.

## Increments (one commit each; red = new failing test first; guard = existing or newly added characterisation tests cover it)
0. Inventory, no commit. Re-grep `sandbox_executable`, `_executable_directory`, `_uv_read_dirs`, `SandboxPolicy(`, `NonoSandbox(`, `build_gate_sandbox(`, `contains(`, `SubprocessRunner(` across `src/`, `tests/`, `spike/`, README, AGENTS.md. Settle what the review could not verify: other `src/` callers (found so far: `cli/base.py`, `stages.py`), tests asserting README/AGENTS wording, `git_hardening_probe.run_agent`, other spikes calling `build_gate_sandbox`. Known sites: `test_runner.py` (19 `sandbox_executable=` sites to retarget, lines 189–567, plus line 592, which passes both `sandbox` and `sandbox_executable`; the test at 585–600 is `test_a_confined_run_is_wrapped_by_the_given_sandbox_with_the_policy_the_runner_derives`), `test_gate_sandbox.py:12` (the `build()` helper passes a dict key `"sandbox_executable": "nono"`), `spike/confinement_probe.py:229`, `:244`, `spike/git_hardening_probe.py:235`, `spike/gate_sandbox_probe.py:72`, `test_cli_wiring.py:69` (a settings key, unaffected), `gate.py:67,91`.
0.5. Guard (new tests, `test_gate_sandbox.py`): two HOME-unset characterisation tests with HOME absent from `base_env`: a uv under `tmp_path/uvbin` still gets its read grant; `real_uv="/uv"` adds no read grant (its directory `/` holds the `"/"` default). They must pass on today's code. Later increments keep `base_env.get("HOME", "/")` verbatim.
1a. Red then green: `sandbox.py` gets `without_private(directories, private_dirs)`, which drops each directory that contains a private one. Unit tests in `tests/test_sandbox.py` (new file): kept, dropped, nested, relative and symlinked inputs, empty private list.
1b. Guard (refactor): `runner._executable_directory` calls the helper (signature unchanged). Guarded by the `test_runner.py` private-directory tests (resolved, unresolved, no-grant, home).
1c. Guard (refactor): `gate_sandbox._uv_read_dirs` calls the helper. Guarded by `test_gate_sandbox.py:124` (uv holds home) and the 0.5 tests.
2. Red: `build_gate_sandbox(private_dirs=...)`: a uv directory that holds a private directory adds no read grant. Green: new keyword (default `()`); the gate's private list is `private_dirs` plus `base_env.get("HOME", "/")`, as today.
3. Red: `gate_sandbox_for` passes the state dir as a private directory (recording sandbox in `test_stages.py`, next to the test at :224). The test monkeypatches `discover_uv_tooling` so `real_uv` sits under the state dir's parent; otherwise it cannot fail first. Green: `stages.py` passes `(str(settings.state_dir),)`. Same commit: README line 217 gains "the gate treats the state dir as private".
4. Red: `SubprocessRunner.run` passes `cwd` in the policy (recording sandbox, like `test_runner.py:592`). Green: add `cwd: str | Path | None = None` to `SandboxPolicy` (docstring: `NonoSandbox` ignores it); the runner computes its launch cwd once (`Path(task.worktree) / project_subdir`, unresolved, as today) and uses that one value for both the policy and `Popen`. Same commit: README says the policy names the working directory.
5. Red: `build_gate_sandbox` passes `cwd=project_dir`. Green: the gate fills it. Risk: the gate's policy cwd and its launch cwd are two separate computations that happen to agree today; the test pins the policy side only. Same commit: AGENTS.md line 11 says a sandbox receives `cwd` and may use it.
6. Guard (retarget, mechanical): every `sandbox_executable=X` in `test_runner.py` (19 sites) and the `build()` dict key in `test_gate_sandbox.py:12` becomes `sandbox=NonoSandbox(X)`. Assertions untouched. Line 592 is left for increment 7. Two commits: tests, then the three spike calls (import from `factory.sandbox` with `# noqa: E402` after the spike's own `sys.path` insert; checked with `py_compile` and grep, never imported).
7. Guard (refactor): remove `sandbox_executable` from `SubprocessRunner.__init__` and `build_gate_sandbox`; runner default per above; delete the "`sandbox` wins if both are given" docstring text. **Stop and ask before touching `test_runner.py:592`.** The test stays: only its `sandbox_executable="/no/such/nono"` argument goes, since its assertions (the policy the runner derives) do not depend on it. There is no gate test passing both arguments, so none to touch. Same commit: README line 217 says `sandbox_executable` now selects the binary only through `sandbox_named`.
8. Gate: `uv run ruff check` (also after every increment), the full suite in the background with `-n auto`, the `verify` skill, then `audit-commits`.
9. Record deviations in this plan.

## Tests touched (CLAUDE.md step 2: grep before changing user-visible output)
No user-visible string changes. Behaviour change: a gate prefix can now omit a uv read grant (only when the uv directory holds the state dir). Tests that pin gate read grants: `test_gate_sandbox.py` (the whole file; every test uses `BASE_ENV` with `HOME="/h"`), `test_stages.py:205-233`, `test_gate.py` (`sandbox_unavailable`, unaffected). Increment 0 also greps the tests for README/AGENTS wording touched in increments 3, 4, 5 and 7.

## Risks
- A uv installed under the state dir would lose its read grant, so uv would not start in the gate. Unlikely (the state dir is the factory's own), and the failure is the existing fail-closed path, not a silent grant.
- `cwd` is unused by the only adapter; per the codebase-design rule that is a hypothetical seam until a second sandbox exists. Accepted (user chose to add it); the recording-sandbox tests are what keep it honest.
- The gate's policy cwd and launch cwd are separate computations (increment 5); a later change to one can drift from the other with no test failing.
- Retargeting 19 runner sites plus the gate dict key is mechanical but wide; one wrong keyword shows as a failing test, not silently.
- Spikes are verified statically only; none is run.
