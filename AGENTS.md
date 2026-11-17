# Factory

## Verification gate
Run from the repo root; the package, `uv.lock` and `.python-version` live in `orchestrator/`.
- `cd orchestrator && uv run ruff check`
- `cd orchestrator && uv run pytest -q` (background; see Tests)

## Tests
- The full suite takes about 100s and the tool timeout is 120s. Run it in the background and wait with Monitor; foreground `sleep` is blocked.
- Tests that inject a nonce must give distinct values (an iterator). A constant nonce hangs the collision loop forever.
