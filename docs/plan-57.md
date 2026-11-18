# Plan 57: the claude grant also covers a symlinked executable

## Problem
`_executable_directory` (`runner.py:16`, used at `runner.py:44` with `$HOME` added to the private dirs at `:43`) grants read on `dirname(abspath(which(claude)))`, the unresolved directory. Plan 55 found the gate could not exec `uv` until the resolved directory was granted. Plan 55 only tried adding the resolved directory; the unresolved one was never run alone, and whether `.mise-bins` is a symlinked directory or holds a symlinked file was never checked (`spike-findings.md` and the plan 56 text disagree). So "nono needs the resolved directory for a file symlink" is plausible but unproven. Unverified assumption: on Linux nono (Landlock) grants by real file location. Plan 49 measured only a symlinked directory (`latest/claude` into `2.1.325/`; both directories worked alone). Nobody has measured a `claude` that is itself a symlink into another directory.

## Scope
One behaviour: when the found `claude` file resolves into a directory other than its own (resolved) directory, also grant read on that resolved directory, each directory guarded on its own. Not covered: macOS; a relative executable; shims (unsupported since plan 49).

## First
1. `grep -rn -e sandbox_reads -e '"--read"' -e fake_wrapper orchestrator/tests`, results already known: exact `--read` lists are asserted at `test_runner.py:220, 231, 247, 264` (line 194 checks only the first `--read`); `test_cli_wiring.py:247` asserts `reads == [worktree]` for a plain fake `claude` (must stay green: a non-symlink adds nothing); `test_command.py:92, 114-118`, `test_cli_wiring.py:184, 233` and `test_gate.py:538` check the prefix start or `sandbox_prefix` directly. Re-run it before cycle 1 and list anything new.
2. Measure (below). No source change until it shows the defect.

## Measure (throwaway spike, flagged as such; plain terminal, no `!`; real profile)
- Find the path: `readlink -f <claude>` and `namei -l <claude>` (a bare `claude_executable` is resolved with `which` on the agent's `PATH`, as the runner does; the interactive `claude` is a zsh function, not the file). If no component is a file symlink into another directory, build the layout: `mkdir ~/p57-bin && ln -s "$(readlink -f <claude>)" ~/p57-bin/claude`, measure that path, then delete it. The layout is under `$HOME`, which the profile does not read (plan 6).
- From a worktree, four arms with the real prefix, saving each exit code: `nono run -s --profile nolabs-ai/claude --allow-cwd --read <worktree> [grant] -- <path> --version`, where grant is (a) none, (b) `--read <unresolved dir>`, (c) `--read <resolved dir>`, (d) both.
- Decision: (a) must fail (127), else the measurement is uninformative; (b) passes: record and stop, no code change; (b) fails and (d) passes: do the cycles; (d) fails: stop and re-measure, do not widen grants. (c) tells us whether the resolved grant alone suffices when the unresolved directory is refused.
- If the layout had to be fabricated, the fix rests on a synthetic case, not a failure seen on this host: you decide whether to build it.

## Design (only if the measurement shows the defect)
`_executable_directory` returns the unresolved directory as today, plus `Path(found).resolve().parent` when that differs from `Path(directory).resolve()`, each dropped independently if it contains a private directory. Comparing against the resolved directory keeps a symlinked-directory install (plan 49) at one grant, so `test_a_confined_run_grants_the_directory_the_claude_executable_is_found_in` (asserts exactly `[worktree, install/latest]`) stays green and is not modified. The unresolved grant is kept because plan 49 measured it. Arm (c) may later justify resolved-only; not proposed now.

## TDD cycles (`tests/test_runner.py`; one commit each; fixtures from `tmp_path.resolve()`; `os.path.abspath` for lexical paths)
1. Red: a `claude` in `bin/` that is a symlink to `release/fake-claude` is granted exactly `[worktree, bin, release]`. Green: add the resolved parent only when it differs from the resolved directory. Confirm the existing symlinked-directory test stays green in this commit.
2. Red: the resolved parent contains a private dir, so only `[worktree, bin]` is granted. Green: guard each candidate on its own.
3. Guards (green on arrival, mutation-checked, one commit each): (a) a plain file gets exactly one grant (mutation: drop the comparison); (b) a symlink inside the same directory gets one grant; (c) the resolved parent is `$HOME` (only `bin` granted); (d) the unresolved directory is refused (for example it contains a private dir) while the resolved one is allowed: only `release` is granted.

## Verification
`cd orchestrator && uv run ruff check`, then the full suite in the background (about 100s). Then, in a plain terminal, arm (d) again using the `--read` list the runner actually builds (taken from a run through `fake_wrapper`), expecting exit 0.

## Docs (one commit, after the result)
- README:259: two directories, each guarded on its own (`/`, `$HOME`, state dir).
- `docs/spike-findings.md`: a "Plan 57" entry with all four arms' exit codes and whether the layout was fabricated; it supersedes the plan 56 "Same gap elsewhere" bullet (left in place) and corrects the plan 55/56 wording about what was measured.

## Risks
- Wider read grant: the whole resolved release directory.
- Two grants instead of one change the `--read` list for symlinked-file installs only.
- Whether nono starts with the resolved grant alone is arm (c), unmeasured until then.

## Decisions (adopted)
Add-resolved, keep-unresolved design; measure with the real profile and four arms; build only if the measurement shows the defect (or you choose to on a fabricated layout).

## Files
`orchestrator/src/factory/runner.py`, `orchestrator/tests/test_runner.py`, `README.md`, `docs/spike-findings.md`, `docs/plan-57.md` (this).
