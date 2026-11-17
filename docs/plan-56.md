# Plan 56: the gate sandbox grants read on the directory holding uv

## Problem
Plan 55 (`docs/spike-findings.md`, "Plan 55") measured on Linux, nono 0.81, uv 0.13.8: the gate prefix built by `build_gate_sandbox` makes `uv --version` exit 127 with no output when `uv` lives under mise (`real_uv` = `.../mise/installs/uv/latest/.mise-bins/uv`, which resolves into `.../uv/0.13.8/uv-x86_64-unknown-linux-gnu/`). The prefix grants write to the project and cache and read to the worktree, the Python prefix and uv's Python dir, but not the directory holding the executable. Adding `--read <resolved dir>` (argv unchanged, still the symlinked path) made it exit 0, and every later probe, including a cold-cache `uv run pytest -q`, passed with only that grant. Other causes (PATH, shims) are ruled out: the argv is absolute and discovery strips shims. Caveat: one run each, and nono's own message was never seen (`-s`, empty stderr). So on such a host every gate run fails with exit 127.

## Scope
One behaviour: `build_gate_sandbox` adds a read grant for the resolved directory holding `real_uv`. No new setting, no change to `stages.py` or `discover_uv_tooling`. Not covered: macOS (unmeasured); a relative `real_uv` (what `shutil.which` returns for a relative PATH entry) gets no grant and still fails with 127, like `claude` in plan 49; the `claude` executable grant has the same symlink gap (it checks the unresolved directory), out of scope.

## Design
In `gate_sandbox.py`, `_uv_read_dir(real_uv, home)` returns `Path(real_uv).resolve().parent` as a string, or `None` when:
- `real_uv` is not an absolute path (`discover_uv_tooling` falls back to the bare name `"uv"`); or
- the directory is `/`, or contains `home` (the `HOME` value from `base_env`, resolved), so agent-written tests never get read on a private tree (the guard plan 49 has for `claude`, `runner.py` `_executable_directory`; reuse its containment test rather than copy it, moving it to a shared place if that is cheap).

`build_gate_sandbox` passes the result after `python_prefix` and `uv_python_dir` in `read=`. Resolved-only, because the spike proved that and nono appears to check the resolved directory; the unresolved symlink directory is probably useless and, for a `uv` symlinked from `~/.local/bin`, would widen the grant for nothing. Accepted gap: the factory's state dir is not available to `build_gate_sandbox`, so unlike plan 49 it is not refused here. Small race: if mise re-points `latest` between discovery and the run, the grant covers the old directory.

## First
`grep -rn "build_gate_sandbox\|gate_sandbox_for\|\"--read\"" orchestrator/tests` to confirm no other test asserts the gate's `--read` list (the reviewer could not run it). Known so far: `test_gate_sandbox.py:16` (subset assertion, stays green) and `test_command.py:118` (calls `sandbox_prefix` directly, unaffected).

## TDD cycles (test file `tests/test_gate_sandbox.py`; one commit each; build fixtures from `tmp_path.resolve()`; use `os.path.abspath` for any lexical path)
Each test is red against the minimal green of the cycle before it (a bare-absence test would pass today, so it is not cycle 1).
1. Red: an absolute `real_uv` (a real file under `tmp_path.resolve()`) adds a read on its directory. Green: `Path(real_uv).parent`.
2. Red: a bare `"uv"` and a relative `bin/uv` add no read, so the `--read` list is exactly `[worktree, /py, /uvpy]` (cycle 1's green gives `--read .`). Green: the absolute-path check.
3. Red: an absolute `real_uv` that is a symlink into another directory adds a read on the resolved parent only. Green: `Path(real_uv).resolve().parent`.
4. Red: a resolved parent of `/`, or one containing `base_env["HOME"]`, adds no read. Green: the guard.
5. Guard (green on arrival, mutation-checked): a plain file that is not a symlink still adds exactly one read, for its own directory.

## Verification
- `cd orchestrator && uv run ruff check`, then the full suite in the background (about 100s; wait with Monitor).
- Real nono, in a plain terminal (not `!`, which runs inside this session's own sandbox where nono cannot nest): `uv run --project orchestrator python spike/gate_sandbox_probe.py --smoke`. Expected: `smoke [prefix as built]: exit=0` and `deviations: none`. Then the full probe, required: its cold `uv run pytest` under the sandbox is the real test of the production prefix; expect exit 0 and `deviations: none`. If any run exits 127, stop and re-measure rather than widening grants.

## Docs (after you report the result; one commit)
- README:22 and the Operations bullet (README:354): replace the defect wording with the grant, say it was confirmed on Linux by the smoke and full probe runs, and name the limits above (relative path, symlinked layouts, state dir not refused).
- A gate sentence next to the `claude` grant explanation (README:259): the grant and its `/` and `$HOME` guard.
- `docs/spike-findings.md`: a short "Plan 56" entry with the results, and the note that the `claude` grant has the same symlink gap.

## Risks
- The grant exposes read access to everything in `uv`'s resolved directory: one release directory under mise; for a `uv` binary directly in `~/.local/bin`, every binary there (read only). Refused only when it is `/` or contains `$HOME`.
- Other layouts (system path, `~/.local/bin`, macOS) were not tried.
- `build_gate_sandbox` now touches the filesystem (`resolve()`); the existing `/real/uv` test stays green because non-strict resolve of a missing path works.

## Decisions (adopted)
Resolved-only grant; the `/` and `$HOME` guard without passing in the state dir; the manual check in a plain terminal, with the full probe required.

## Files
`orchestrator/src/factory/gate_sandbox.py`, `orchestrator/tests/test_gate_sandbox.py`, `README.md`, `docs/spike-findings.md`, `docs/plan-56.md` (this).
