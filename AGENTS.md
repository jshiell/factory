# Factory

## Verification gate
Run from the repo root; the package, `uv.lock` and `.python-version` live in `orchestrator/`.
- `cd orchestrator && uv run ruff check`
- `cd orchestrator && uv run pytest -q -n auto` (background; see Tests)

## Harnesses
- A harness (`orchestrator/src/factory/harness.py`) builds the agent command and parses its output; `SubprocessRunner` owns launch, timeout, sandbox and records. To add one: implement `Harness`, register it in `HARNESSES`, and set `harness = "<name>"`. It must provide every `Capability`, or startup refuses it.
- The `harness` setting is not the harness home (`~/.harness`). The prompt checks for an argv prompt live in `command.check_prompt`; call it from any harness that passes the prompt that way.
- A sandbox (`orchestrator/src/factory/sandbox.py`) turns a `SandboxPolicy` into the argv prefix for a confined run, for both agent runs and the gate. To add one: implement `Sandbox`, register a factory taking the executable in `SANDBOXES`, and set `sandbox = "<name>"`. The policy documents today's grants (see the README), including open network; `profile` is a nono profile name that other sandboxes may ignore. The wrapper must keep the agent in the host pid namespace (`kill_tree` and the process records rely on it), and a sandbox that does not start by exec of a wrapper needs its own fail-closed path (`gate.py` catches `OSError`).
- The eval meta, the shared bundle and `eval-audit --compare` record the harness; a meta without one reads as `claude`.

## Tests
- `cd orchestrator && uv run pytest -q -m smoke` runs a hand-picked subset (about 25 tests, 3s) for a quick check while editing. It does not replace the verification gate. Mark a test with `@pytest.mark.smoke` only to cover a core seam (the marker is registered with `--strict-markers` in `pyproject.toml`).
- The full suite takes about 25s with `-n auto` on 8 cores (about 75s serial, plain `uv run pytest -q`, which CI uses) and the tool timeout is 120s. A loaded machine can exceed it, so run it in the background and wait with Monitor; foreground `sleep` is blocked.
- Tests whose child process reads the process records must wait for them: the record is written after the launch (see `tests/slowrecords.py`).
- Tests that inject a nonce must give distinct values (an iterator). A constant nonce hangs the collision loop forever.
