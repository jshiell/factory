# Factory

## Verification gate
Run from the repo root; the package, `uv.lock` and `.python-version` live in `orchestrator/`.
- `cd orchestrator && uv run ruff check`
- `cd orchestrator && uv run pytest -q -n auto` (background; see Tests)

## Tests
- The full suite takes about 25s with `-n auto` on 8 cores (about 75s serial, plain `uv run pytest -q`, which CI uses) and the tool timeout is 120s. A loaded machine can exceed it, so run it in the background and wait with Monitor; foreground `sleep` is blocked.
- Tests whose child process reads the process records must wait for them: the record is written after the launch (see `tests/slowrecords.py`).
- Tests that inject a nonce must give distinct values (an iterator). A constant nonce hangs the collision loop forever.
