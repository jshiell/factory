# Plan 63: a sandbox abstraction around nono

## Problem
nono knowledge is in `command.sandbox_prefix` (argv: `run -s`, `--profile`, `--allow-cwd`, `--read`, `--allow`), `Settings.sandbox_executable`, and `Harness.sandbox_profile` (a nono profile name, `nolabs-ai/claude`). Two callers build a prefix: `SubprocessRunner.run` (agent runs) and `gate_sandbox.build_gate_sandbox` (the gate). Everything else is neutral. Supporting another sandbox means editing all of them.

## Decided
- `factory/sandbox.py`: `SandboxPolicy(worktree, write=(), read=(), profile=None)`, a `Sandbox` protocol with `wrap(policy) -> list[str]` (an argv prefix), and `NonoSandbox(executable)`, which takes over `sandbox_prefix` unchanged.
- `SANDBOXES: dict[str, Callable[[str], Sandbox]]` maps a name to a factory taking the executable. `sandbox_named(settings)` is stateless, like the harness lookup, and is called from `agent_runner` (`cli/base.py`) and `gate_sandbox_for` (`stages.py`). Nothing is threaded through `build_stages` or `Deps`; `_real_eval_runner` and the server's eval runner are covered through `agent_runner`.
- Setting `sandbox`, default `"nono"`, allowed in the global config. `sandbox_executable` keeps selecting the binary.
- Additive API: `SubprocessRunner(sandbox=None)` and `build_gate_sandbox(sandbox=None)` default to `NonoSandbox(sandbox_executable)`. `sandbox` wins if both are given. No existing test or spike script changes.
- `sandbox_prefix` stays a permanent one-line wrapper (keeps its `SANDBOX_PROFILE` default).
- Profile (decision 1a): `policy.profile` is an opaque optional string; nono uses it, others ignore it. `Harness.sandbox_profile` stays a nono string carried by the harness. A sandbox that ignores it ends up with too few grants, not too many.
- Capabilities (decision 2a): no startup check. The contract is documented and probed manually.
- No second sandbox in this plan (decision 3a); tests use a fake.
- Not in this plan: recording the sandbox name in eval meta, bundle and compare (a missing field would read as `nono`; revisit with the second sandbox), and an automated real-nono contract test (nono is absent in CI and cannot nest in an agent session).

## Contract (documents what runs today, not an ideal)
- Agent run: the profile's grants, plus cwd (`worktree/project_subdir`) via `--allow-cwd`, plus the worktree read-only, plus read grants (plugin dirs, executable dir), plus open network. **Unverified assumption:** that `nolabs-ai/claude` makes the cwd writable (inferred from the `command.py:54` docstring and nono's docs for its claude-code profile). Increment 0 checks it.
- Gate: write to the project dir and a private uv cache; read the worktree, Python prefix, uv Python dir and uv exe dir; open network (a cold-cache `uv run pytest` passes under the prefix, plan-56).
- nono allows network by default (nono quickstart), and neither prefix passes `--block-net`. Do not claim a sandbox denies network until that is a deliberate change.
- The wrapper stays in the host pid namespace with the agent as its descendant. `kill_tree` (`pgrep -P`), `start_new_session` and the process records depend on it.

## Increments (one commit each; guard = covered by existing tests, no red)
0. Inventory, no commit. Grep `sandbox_executable`, `sandbox_prefix`, `build_gate_sandbox`, `gate_sandbox_for`, `nolabs-ai/claude`, `fake_wrapper`, `"--read"`, `nono` in tests, README and `spike/`; list the hits here. Known: test_command.py:107,134; test_gate_sandbox.py:12,22,65; test_cli_wiring.py:69,188,237,252,279,398; test_runner.py:189-505 (about 20 tests, exact `--read` lists at 220, 231, 247, 264), 381, 558, 572; test_settings.py:97-107; test_stages.py:205-218; test_harness_wiring.py:20,30; test_gate.py:603-626,695-702 (pin `sandbox_unavailable`, unaffected); README.md:199; spike/confinement_probe.py:228, git_hardening_probe.py:235, gate_sandbox_probe.py:73. Also confirm the cwd-write assumption with a spike from a plain terminal.
1. Guard (refactor): add `sandbox.py` with `SandboxPolicy`, `Sandbox`, `NonoSandbox.wrap`; `sandbox_prefix` delegates. Guarded by test_command.py:107,134.
2. Red: `SubprocessRunner(..., sandbox=fake)` puts `fake.wrap(policy)` in front of a confined run; the policy holds the resolved worktree, plugin and executable read grants, and the harness profile. Green: add `sandbox=`; `sandbox` wins over `sandbox_executable`.
3. Red: `build_gate_sandbox(..., sandbox=fake)` gets a policy with no profile, write = project and cache dirs, read = Python prefix, uv Python dir, uv exe dir. Green: optional `sandbox=`; `sandbox_executable=` stays.
4. Red: the `sandbox` setting: default `nono`; unknown name gives the exact message `sandbox 'x' is unknown; known: nono`; allowed in global config; `sandbox_executable` still selects the binary. Green: `SANDBOXES`, `sandbox_named`, key in `_STRING_KEYS`, check in `load_settings`.
5. Red: a wiring test (style of `test_harness_wiring.py`): a registered fake sandbox is used by both `agent_runner` and `gate_sandbox_for`. Green: both call `sandbox_named(settings)`. Guards: test_stages.py:205-218 and the test_cli_wiring.py nono assertions.
6. Docs: README next to `sandbox_executable` (line 199); AGENTS.md ("implement `Sandbox`, register a factory in `SANDBOXES`, set `sandbox`") plus the contract caveats above; `init` template gets a commented `# sandbox = "nono"` (check its prefix test); reword the ALLOWED values in test_subprocess_sites.py only.
7. Verification gate: `uv run ruff check`, the full suite in the background with `-n auto`, the `verify` skill, then `audit-commits`.

## Risks (left for the second-sandbox plan)
- The `sandbox_executable` default `"nono"` is nono-specific; with another sandbox it would be passed to that mechanism. Neutral executable key deferred, as plan-62 did.
- More nono coupling: the executable-dir read grants exist because nono refuses to exec from an unreadable directory (`runner._executable_directory`, `gate_sandbox._uv_read_dirs`); `SANDBOX_PROFILE` sits in neutral `command.py`.
- A pid-namespacing sandbox (bwrap `--unshare-pid`) would break kill and reap.
- Fail-closed (`gate.py:90-93`) fires only when the wrapper's exec fails. An in-process mechanism (Landlock via `preexec_fn`) needs its own fail-closed path.
- A chroot or container sandbox that cannot express "read only these dirs" forces a contract revisit.
- `STRIPPED_ENV` and the HOME/private-dir logic stay in the runner and gate.
- `_check_corpus` (`cli/__init__.py:221`) calls `check_gate` with no prefix, so the corpus gate runs unconfined (trusted corpus). A third gate call site the seam does not cover.

## Deviations (as built)
- `sandbox_named(name, executable)` takes the two settings values, not `settings`: `settings.py` imports `sandbox` (for `SANDBOXES` and `DEFAULT_SANDBOX`), so a `Settings` parameter in `sandbox.py` would be a cycle.
- Two wiring tests (agent runner in `test_harness_wiring.py`, gate in `test_stages.py`) instead of one covering both.
- Increment 0: no hit list was written into this plan; each affected test was checked as the change reached it. The cwd-write spike was not re-run. It was already answered: `docs/spike-findings.md:606` records that the positive control (write to the project dir) passed under the real agent prefix, and line 609 that read-only stages can write the project directory under nono. What stays unmeasured is whether the profile or `--allow-cwd` grants it; the docs say "writable only if the profile grants it".
- `uv run --project orchestrator python spike/confinement_probe.py` (plain terminal) can be re-run as a regression check that the refactor left the real grants unchanged; it has not been run since.
- The comment above `ALLOWED` in `test_subprocess_sites.py` still says "nono prefix"; only the values were reworded.
