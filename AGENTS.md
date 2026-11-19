# Factory

## Verification gate
Run from the repo root; the package, `uv.lock` and `.python-version` live in `orchestrator/`.
- `cd orchestrator && uv run ruff check`
- `cd orchestrator && uv run pytest -q -n auto` (background; see Tests)

## Harnesses
- A harness (`orchestrator/src/factory/harness.py`) builds the agent command and parses its output; `SubprocessRunner` owns launch, timeout, sandbox and records. To add one: implement `Harness`, register it in `HARNESSES`, and set `harness = "<name>"`. It must provide every `Capability`, or startup refuses it.
- The eval meta, the shared bundle and `eval-audit --compare` record the harness; a meta without one reads as `claude`.

## Tests
- `cd orchestrator && uv run pytest -q -m smoke` runs a hand-picked subset (about 25 tests, 3s) for a quick check while editing. It does not replace the verification gate. Mark a test with `@pytest.mark.smoke` only to cover a core seam (the marker is registered with `--strict-markers` in `pyproject.toml`).
- The full suite takes about 25s with `-n auto` on 8 cores (about 75s serial, plain `uv run pytest -q`, which CI uses) and the tool timeout is 120s. A loaded machine can exceed it, so run it in the background and wait with Monitor; foreground `sleep` is blocked.
- Tests whose child process reads the process records must wait for them: the record is written after the launch (see `tests/slowrecords.py`).
- Tests that inject a nonce must give distinct values (an iterator). A constant nonce hangs the collision loop forever.
