# Plan 60: state the platform story and refuse Windows cleanly

## Problem
The README says macOS (`README.md:22`) with Linux "tried by hand", but does not say what is supported versus untested, and says nothing about Windows. On Windows the `factory` script fails with a raw `ModuleNotFoundError: fcntl`: `factory.cli` imports `factory.lock` (`cli.py:25`), which does `import fcntl` (`lock.py:1`), so it fails before any code of ours runs. `factory/__init__.py` is empty, so a new launcher module can run first. Other POSIX-only code (`pgrep`, `ps`, `os.killpg`, `signal.SIGHUP`, `start_new_session`) sits behind that check.

## Decided
Document the platforms and refuse Windows with a clear message. Out of scope: Linux first-class, a macOS CI runner, real-`nono` Linux spikes. Refuse `win32` only: Cygwin and MSYS2 are believed to have `fcntl` (unverified), so no claim about them. `python -m factory.cli` bypasses the launcher; accepted.

## Step 0 (before any edit): greps, list the hits here
Done before increment 1; no hit needed a change beyond the files listed below, and the hits were not recorded.
- `factory.cli:run`, `factory.cli`, `-m factory`, `console_scripts`, `project.scripts` across `orchestrator/src`, `orchestrator/tests`, `spike/`, `docs/`, `README.md`, `AGENTS.md`; anything that launches a `factory` binary (server, JobRunner, nono profiles).
- Other importers of `factory.lock`.
- Tests that read README text: `grep -rn 'README\|Seatbelt\|macOS\|Requirements' orchestrator/tests`; any test comparing the README command table to the parser.

## Design
- `factory/launcher.py` imports only `sys` at module level. `unsupported_reason(platform)` is pure. `run()` reads `sys.platform` at call time; on a reason it prints `unsupported platform: <reason>` to stderr and `sys.exit(2)` (the same style and code as `settings error`); otherwise it looks up `factory.cli.run` at call time and calls it.
- Message, exactly: `unsupported platform: Windows is not supported; factory needs macOS or Linux`. No WSL claim in code or README beyond "untested".
- `[project.scripts]` becomes `factory = "factory.launcher:run"`; `factory.cli:run` is unchanged.

## Increments (TDD; one test, one implementation, one commit each)
1. Red: `unsupported_reason("win32")` contains "Windows is not supported"; `"darwin"` and `"linux"` return `None`. Green: `launcher.py` with `unsupported_reason` only.
2. Red: one subprocess test (a fresh interpreter, so earlier tests that loaded `factory.cli` cannot hide a regression): `sys.executable -I -c "import sys; sys.modules['fcntl'] = None; sys.platform = 'win32'; from factory.launcher import run; run()"` exits 2, stderr is exactly the message plus newline, stdout empty. Green: `run()` prints and exits on a reason. Mutation: invert the comparison; red.
3. Red (not a guard): with the platform patched to linux, `run()` calls `factory.cli.run` (monkeypatched recorder; the launcher looks it up at call time). Green: the delegate branch.
4. Red: `importlib.metadata.entry_points(group="console_scripts", name="factory")` has value `factory.launcher:run`. Green: edit `[project.scripts]` and let `uv run pytest` re-sync. Then `uv lock --check` (CI uses `uv sync --locked`) and a real smoke run from the repo root: `uv run --project orchestrator factory init --help` (`init` skips settings loading; plain `--help` may not), and look at the generated `.venv/bin/factory`.
5. README, no test. Replace the macOS bullet under Requirements with a Platforms list:
   - macOS: the main platform (Seatbelt via `nono`, written against nono 0.81); the real sandbox is mostly exercised here.
   - Linux: CI runs the suite with `claude` and `nono` faked. The real sandbox was tried by hand: plan 49 (the `claude` executable grant), plan 55 (the gate sandbox has network and runs build hooks), plan 56 (read on the directory holding `uv`, confirmed under mise). Plan 57 was measured on a fabricated layout, not a real install. Other macOS assumptions may remain.
   - Windows: refused at startup (needs `fcntl`, `pgrep`, `ps`, process groups). WSL is untested. No claim about `nono` on Windows (unverified).
   - Keep `README.md:342` and make `README.md:353` consistent. Also fix `README.md:325` ("lint (E, F, I)" is out of date; the config selects E, F, I, B, UP, C4, SIM, RUF).

## Verification
`cd orchestrator && uv run ruff check`; the full suite in the background (about 100s, Monitor). Sign any unsigned commits.

## Risks
- No Windows to test on; "fails cleanly" rests on the subprocess test with `fcntl` blocked and on the launcher importing only `sys`.
- Unverified: uv re-syncs the editable install on a pyproject edit; uv_build writes `entry_points.txt`; `uv.lock` does not record entry points. Increment 4's red/green and `uv lock --check` settle these.
- Unverified: Cygwin/MSYS Python having `fcntl`; whether the package installs on Windows at all (httpx2, starlette, uvicorn wheels).
- A stale install would still point at `factory.cli:run` (harmless, unguarded); the entry-point test catches it.

## Files
`orchestrator/src/factory/launcher.py` (new), `orchestrator/tests/test_launcher.py` (new), `orchestrator/pyproject.toml`, `README.md`, `docs/plan-60.md`.
