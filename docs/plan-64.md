# Plan 64: retire the command.py forwarders and break the command/harness/sandbox cycle

Candidate 1 of the architecture review (2026-11-19). Plan 65 (finish the sandbox seam) follows and is not drafted yet.

## Problem
`command.build_command` and `command.sandbox_prefix` are one-line forwarders to `ClaudeHarness.build` and `NonoSandbox.wrap`. Nothing in `src/` calls them (grep, 2026-11-19); `tests/test_command.py` and seven `spike/` scripts do. They keep `command`, `harness` and `sandbox` importing each other: `command` imports `sandbox`, `harness` imports `command`, and `build_command` imports `harness` lazily to break the loop. `command.py` also holds `contains`, a path predicate used only by `runner.py` and `gate_sandbox.py`, and `check_prompt` and `SANDBOX_PROFILE`, which only `harness.py` uses.

## Decided (grilling, user-approved)
- Two plans: this one, then plan 65.
- Retarget the `test_command.py` call sites to `ClaudeHarness.build` and `NonoSandbox.wrap`. Assertions stay byte-identical; if one must change, stop and ask.
- `command.py` stays for `RunnerConfig`, `PromptTooLarge` and `MAX_PROMPT_BYTES` (13 source modules and 11 test files import `RunnerConfig`; none of those imports change).
- `check_prompt` and `SANDBOX_PROFILE` move to `harness.py`; `contains` moves to `sandbox.py`.
- `Harness.sandbox_profile` stays a nono profile name on the harness (plan 63's decision).
- No new behaviour: every increment after the retargets is a guarded refactor.

## Decided: the spike scripts (option a, user-approved)
Add `spike/forwarders.py` holding the two forwarders and change each spike's import line. The (b) and (c) options below were rejected. Seven scripts under `spike/` import `build_command` and/or `sandbox_prefix`: `confinement_probe.py:249`, `nono_probe.py:125`, `nono_probe2.py:125`, `nono_impl_probe.py:250`, `probe2.py`, `probe3.py`, `review_variance.py:15`. Plan 63 kept the forwarders partly so no spike changed. Deleting them breaks those imports. They are manual probes with no test coverage. Options:
- (a) Put the two forwarders in `spike/forwarders.py` and change each spike's import line. Recommended: the spikes keep working, `src/` loses the dead code, and nothing else in the spike bodies changes.
- (b) Edit every call site in the spikes to use the harness and sandbox directly. More churn in evidence scripts.
- (c) Leave the spikes broken and record it. Cheapest, but `confinement_probe.py` is the regression probe named in plan 63's deviations.

## Increments (one commit each; guard = covered by existing tests, no red)
0. Re-grep `build_command`, `sandbox_prefix`, `contains`, `check_prompt`, `SANDBOX_PROFILE` across `orchestrator/src`, `orchestrator/tests`, `spike/`, `docs/`, `README.md`, `AGENTS.md`; also `from factory import command`, `import factory.command`, `command\.` attribute access, `patch("factory.command` and `factory.runner.contains` patches, and in-function imports. Confirm the hit list above, and that a `test_gate_sandbox.py` test covers "the uv directory contains home, so no grant" (add a guard test first if none does). No commit.
1. Guard (retarget): in `test_command.py`, define a local `build_command(prompt, config, executable="claude", add_dirs=())` that returns `(inv.argv, inv.env)` from `ClaudeHarness().build(...)`, and drop `build_command` from the `factory.command` import. Test bodies and assertions untouched. Guarded by the existing tests themselves; run `-m smoke` and the file.
2. Guard (retarget): `test_sandbox_prefix_grants_the_resolved_worktree_read_only_and_ends_with_double_dash` calls `NonoSandbox("nono").wrap(SandboxPolicy(link, profile=SANDBOX_PROFILE))`. Same assertion.
3. Guard (retarget): `test_a_profile_less_sandbox_prefix_grants_write_to_the_given_dirs_and_read_to_the_others` calls `NonoSandbox("nono").wrap(SandboxPolicy(tmp_path, write=(...), read=(...)))`. Same assertions. Drop `sandbox_prefix` from the import.
4. Spike scripts: add `spike/forwarders.py` (the two forwarders, importing `ClaudeHarness` and `NonoSandbox` directly, with `SANDBOX_PROFILE` imported from `factory.command`; step 7 repoints it), change the seven import lines.
   - `forwarders.py` does its own idempotent `sys.path.insert(0, <root>/orchestrator/src)`.
   - Each spike's new `from forwarders import ...` goes after that spike's own `sys.path` insert, with `# noqa: E402`; split lines that also import `RunnerConfig` (it stays on `factory.command`).
   - Verify statically, never by importing a spike (they create canary files under `$HOME`, add worktrees, make paid `claude` runs, and `nono_impl_probe.py` rewrites `~/.claude.json` at import): `py_compile` on all seven, grep the import lines, and import only the shim: `uv run --project orchestrator python -c "import sys; sys.path.insert(0,'spike'); import forwarders; forwarders.build_command; forwarders.sandbox_prefix"`.
5. Guard (refactor): delete `build_command`, `sandbox_prefix` and the `sandbox` import from `command.py`. The lazy `harness` import goes with `build_command`. Guarded by the whole suite; Same commit: README architecture label (line 294) becomes `runner.py, harness.py, sandbox.py, command.py (RunnerConfig)`. Run ruff and the full suite.
6. Guard (refactor): move `check_prompt` into `harness.py` (add `import os`; import `MAX_PROMPT_BYTES`, `PromptTooLarge`, `RunnerConfig` from `command`); drop `import os` from `command.py`. Same commit: AGENTS.md line 10 becomes `harness.check_prompt`. Guarded by the prompt tests in `test_command.py` (the dash prompt, limit-plus-one bytes and exactly-limit tests, reached through `ClaudeHarness.build`).
7. Guard (refactor): move `SANDBOX_PROFILE` into `harness.py`. Repoint `test_command.py` (step 2) and `spike/forwarders.py` to `factory.harness`.
8. Guard (refactor): move `contains` into `sandbox.py`; `runner.py` and `gate_sandbox.py` import it from there; drop `Path` from `command.py`. `sandbox.py` imports nothing from the package, so no cycle. Guarded, by name, by the `test_runner.py` tests on a directory that contains a private directory (resolved, unresolved, no-grant, home) and the `test_gate_sandbox.py` uv-directory tests.
9. Verification gate: `uv run ruff check` (also run after every step above), the full suite in the background with `-n auto`, the `verify` skill, then `audit-commits`.
10. Record deviations in this plan, as plan 63 did. Older plan docs that mention the forwarders stay as history.

## Check before step 5
`command.py` must end with no import of `factory.harness` or `factory.sandbox`: `grep -nE "^\s*(from|import) factory" orchestrator/src/factory/command.py` prints nothing.

## Risks
- The seven spike imports (above) are the only callers outside `tests/test_command.py`; a miss shows up only when a spike is run.
- `check_prompt` is named in AGENTS.md as the shared prompt check for any argv-prompt harness; after step 6 it is importable from `factory.harness` (which holds the harness protocol), and the AGENTS.md line must say so.
- Retargeted tests lose the extra indirection that `command.py` gave, so a regression in `ClaudeHarness.build` now fails them directly (intended).
- `SANDBOX_PROFILE` stays a nono-specific value in the harness module; plan 65 or a second-sandbox plan revisits it.
