# Plan 66: sandbox executable defaults to unset

Follows plan 65 (built, audited). Reviewed; findings folded in. Deferred from plan 63's risks ("neutral executable key").

## Problem
`Settings.sandbox_executable` defaults to `"nono"` and is passed to whichever sandbox `sandbox` names. A second sandbox would receive `"nono"` as its executable unless the user also overrides the key. The literal `"nono"` also sits in `SubprocessRunner.__init__` (`runner.py:44`) and the README example says "the setting is not yet neutral".

## Decided (user-approved 2026-11-19)
- Keep the key name `sandbox_executable`. No rename, no alias, no per-sandbox table (rejected: churn or speculative with one adapter).
- The setting defaults to `None` (unset). Each sandbox owns its own default: `NonoSandbox` uses `"nono"`.
- A configured string keeps working unchanged (existing factory.toml files need no edit).
- The only place that knows nono's default executable is `NonoSandbox`.

## Design
- `NonoSandbox(executable: str | None = None)`: `None` means `"nono"`. Test `is None`, not truthiness: `""` stays as given, as today.
- `SANDBOXES: dict[str, Callable[[str | None], Sandbox]]`; `sandbox_named(name, executable: str | None = None)`.
- `Settings.sandbox_executable: str | None = None`. `_STRING_KEYS` is unchanged, so a TOML string loads and a non-string is still rejected naming the key.
- `stages.py:76` and `cli/base.py:63` pass `settings.sandbox_executable` unchanged (it may now be `None`).
- `SubprocessRunner` default becomes `sandbox_named(DEFAULT_SANDBOX)`; no literal `"nono"` left outside `NonoSandbox`.

## Approval needed before implementation (not yet given)
Increment 3 cannot go green without editing a passing test. Approve both edits to `test_settings.py`, or stop:
- `:98`: `== "nono"` becomes `is None`.
- Rename `test_sandbox_executable_defaults_to_nono_and_can_be_set` to `test_sandbox_executable_defaults_to_unset_and_can_be_set` (the old name would be false). The rest of the test (setting `"/opt/fake-nono"`) is untouched.

## Increments (one commit each; red = failing test first; guard = existing or added tests cover it)
0. Inventory, no commit, after the approval above. Re-grep `sandbox_executable`, `sandbox_named`, `SANDBOXES`, `NonoSandbox(`, `"nono"` across `src/` (including `cli/*`, `eval*`, `gate_sandbox.py`, `gate.py`), `tests/`, `spike/`, README, AGENTS.md. Settle what the review could not verify: no other reader of `settings.sandbox_executable`; eval meta and bundles do not record it; spikes pass `NonoSandbox("nono")` explicitly; no other test builds `Settings` and asserts on the key. Known so far: `settings.py:31,71`, `stages.py:76`, `cli/base.py:63`, `runner.py:42,44`, `test_settings.py:97-107`, `test_stages.py:208,224,225,239`, `test_harness_wiring.py:41`, `test_cli_wiring.py:69`, README 200 and 217, AGENTS.md 11. No test greps the README or AGENTS wording being changed.
1. Red: `NonoSandbox()` with no argument has `"nono"` as the first argv element (fails today with a `TypeError`). Guards, added in the same commit: `NonoSandbox("/x/nono")` keeps `/x/nono`; `NonoSandbox("")` keeps `""` (pins `is None`; nothing else fails if someone later writes `or`). Green: `executable: str | None = None`, resolved with `if executable is None`.
2. Red: `sandbox_named("nono")` with no executable has `"nono"` as the first argv element. Guard: an explicit executable is passed through. Green: `executable: str | None = None`; the `SANDBOXES` annotation becomes `Callable[[str | None], Sandbox]`. Same commit: AGENTS.md line 11 says the registered factory takes the executable, which may be `None`.
3. Red: `Settings.sandbox_executable is None` by default (the approved `test_settings.py:98` edit and rename). Green: the `Settings` default becomes `None`. Same commit: README line 200 becomes a commented example, `# sandbox_executable = "/path/to/nono"   # optional; unset means the sandbox's own default`, and line 217 says the key is optional and each sandbox picks its default when unset.
   - 3b. Guard: `gate_sandbox_for` with the key unset hands `None` to a recording factory (`test_stages.py`, next to :224). Red only if written before 3; otherwise a guard.
   - 3c. Guard: `agent_runner` hands `None` to a recording factory (`test_harness_wiring.py`, next to :41). Same.
   Each is its own commit after 3. The recording factories are the only real coverage of the pass-through: the existing fakes ignore their argument.
4. Red (call-shape test, labelled as such): a default `SubprocessRunner` asks the default sandbox factory for `None` (monkeypatch `SANDBOXES["nono"]` with a recording factory, construct the runner with no `sandbox`). Behaviour is the same before and after (`"nono"` either way); the test exists only to pin "no literal `nono` outside `NonoSandbox`". Depends on increment 2. Green: `sandbox_named(DEFAULT_SANDBOX)`; update the docstring at `runner.py:42`.
5. Gate: `uv run ruff check` after every increment, the full suite in the background with `-n auto`, the `verify` skill, then `audit-commits`.
6. Record deviations in this plan.

## Tests touched (CLAUDE.md step 2)
No user-visible output string changes. Behaviour: the default value of `Settings.sandbox_executable`. Tests pinning it: `test_settings.py:97-107` (one assertion and the test name change, with approval, see above). Tests passing it: `test_stages.py:208,225`, `test_cli_wiring.py:69`; these set a string and are unaffected.

## Risks
- A third-party code path reading `settings.sandbox_executable` as a `str` would now get `None`. In `src/` only `stages.py` and `cli/base.py` read it, and both just forward it. Spikes pass `NonoSandbox("nono")` explicitly and are unaffected.
- A user who relied on the old default needs no change: `None` resolves to the same `"nono"`.
- Still one adapter, so the `None` default is exercised only through nono. The recording-factory tests are what keep it honest.
- Not covered: `_check_corpus` still runs the gate unconfined (separate deferred item).
