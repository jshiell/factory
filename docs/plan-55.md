# Plan 55: measure the gate sandbox assumptions behind plan 54

## Problem
Plan 54 closed the dependency and entry-point gap in the gate, but three things it relies on were assumed, not measured, because the plan 54 spike ran under plain `uv run`, not the gate sandbox (`docs/spike-findings.md`, "Plan 54 cycle 0"; README "Known limits"):
1. `setup.py`, `dynamic` metadata and a hatch build hook can load code when the gate runs the tests.
2. Whether the gate sandbox has network on Linux. Plan 9 recorded "a cold gate run needs network" and "unrestricted network", but on macOS only; the README says Linux was not verified.
3. Whether `uv run` rewrites an existing `uv.lock` after a uv format bump. The risk is a false failure: round 1's run rewrites the lock (the project dir is writable), `commit_all` commits it in round 2, and `_dependency_file_changed` sees `M uv.lock` against `base_sha` and fails `tests_modified`. Wider consequences to record as plan 56 candidates:
   - the rewritten lock makes `git status --porcelain` non-empty, so it defeats `fix_no_changes`;
   - if round 1 passes, the task is `verified` with a dirty tracked `uv.lock`, which the audit may refuse (unverified; `audit_task.py` not read).

## Scope
Measure only. This is a throwaway spike, exempt from TDD and flagged as such. No change to `src/` or `tests/`. If a measurement shows a real defect, the fix is a separate plan (56).

## Method
`spike/gate_sandbox_probe.py` (like `spike/nono_probe.py`; run with `uv run --project orchestrator python spike/gate_sandbox_probe.py`). It:
- makes its own temp dir and copies the committed `target/` with `git archive` (no `.venv`, no `__pycache__`);
- uses a fresh, empty cache dir outside the copy;
- builds the real gate sandbox with `discover_uv_tooling` and `build_gate_sandbox`, mirroring how `stages.py` builds `base_env`, `cache_dir` and `test_argv` (read `stages.py` first); prints `real_uv` and stops cleanly if it cannot find one;
- saves exit code, stdout and stderr per probe into a gitignored out dir;
- records `uv --version`, `nono --version`, resolved paths, and the proxy and `UV_*` env vars.

The measured runs happen in your terminal by default: a nested nono run is less faithful (the outer sandbox and any proxy env confound network and filesystem results). Nothing is retried on a failure.

## Steps
0. Smoke check in this session: run `real_uv --version` through the full gate prefix. If nono cannot nest, stop and give you the one terminal command. If it exits 127 (the prefix grants no read on the directory holding `uv`; plan 49 saw this on Linux), record "gate cannot exec uv on Linux" as a plan 56 defect, and let the rest of the spike add `--read dirname(real_uv)` locally, labelled as a deviation.
1. Write the script.
2. Network probe, first (later probes depend on it). For each check, a control without the prefix, then the same check under the prefix: DNS `getaddrinfo`; raw TCP connect to a literal IP; `uv run pytest -q` on a cold cache. Then add a new package to the `dev` group and confirm it is installed (re-checks plan 54 spike (a); uv syncs only `dev` by default).
3. Build-script probes, each on a cold cache. Each marker writes a file into the project dir (uv hides build output on success):
   - (i) setuptools, `dynamic = ["entry-points"]`, and a `setup.py` that writes marker A at top level and declares a `pytest11` plugin whose `pytest_configure` writes marker B;
   - (ii) hatchling with a `hatch_build.py` hook whose `initialize` writes marker C.
   - A build failure is recorded as "not run" (for example no network), never as "hook did not run".
   - One warm-cache rerun after editing only `hatch_build.py`, as information only (uv cache keys may not include it).
4. Lock probes:
   - (a) generate `uv.lock` with an older uv (`uv tool run --from uv==<old> uv lock`, needs network and a download), then run the current uv under the gate sandbox with a fresh `.venv`; compare the bytes and `revision`; record both uv versions and revisions;
   - (b) the hand-edited `revision` variant, labelled artificial;
   - (c) whether `uv run --frozen` or `--locked` leaves the lock untouched (input for plan 56 only);
   - (d) end-to-end replay in a temp git repo with the old lock in the base, using `commit_all`, `clean_worktree` and `check_gate`: agent commit, round-1 gate with a forced test failure, simulated fix edit, round-2 gate. Record the gate reason (`tests_modified` expected). Also record `git status` after a round 1 that passes.
5. Record: a "Plan 55" entry in `docs/spike-findings.md`; each probe marked confirmed, refuted, inconclusive (host) or not run, with the exact command and the Linux, nono 0.81 and uv versions. Update the README "Known limits" and "Requirements" lines to say "on Linux"; the macOS claims stay unverified. Add the wider lock impacts as plan 56 candidates.
6. Verify: `cd orchestrator && uv run ruff check` (it does not lint `spike/`; I say so). No source change, so the full suite is optional. Then the `commit-auditor` against this plan. Commits, one each: this plan; the script; the findings and README. Sign them. Never push.

## What each result means
- Network blocked under the prefix, and the control has it: the gate is broken on Linux, because the per-task uv cache starts empty and the first run cannot install pytest. Plan 56 defect, not a limit of the flag. If the control is blocked too, the answer is "host", inconclusive.
- Build script or entry point runs: the existing watch is justified; no code change.
- Build script does not run: the watch is stricter than needed; keep it, note it.
- Lock rewritten on a format bump: a real false-failure bug in fix rounds; plan 56 would decide between ignoring a lock that differs only in `revision`, or running the gate with `--frozen`.

## Risks and limits
- Poetry hooks are not probed; they stay "watched on assumption".
- One Linux host, nono 0.81, one uv version; macOS Seatbelt behaviour may differ.
- Nested nono was denied on macOS (plan 6 known gaps); Linux is untested.
- Not verified by review: how `audit_task.py` treats a dirty worktree; whether no-profile nono on Linux grants read on the `uv` directory, `/etc` and system libraries; whether uv uses bundled TLS roots; whether setuptools accepts `dynamic = ["entry-points"]` supplied from `setup.py`; whether hatch's editable build runs `initialize`. The spike measures these.

## Decisions (adopted)
1. Measured runs in your terminal; step 0 is only a smoke check.
2. If uv cannot be executed under the gate sandbox, the spike adds `--read dirname(real_uv)` locally, labelled as a deviation.
3. Downloading an older uv to generate a real old-format lock is acceptable.
4. Measure-only scope; any fix is plan 56.

## Files
`docs/plan-55.md` (this), `spike/gate_sandbox_probe.py` (new), `docs/spike-findings.md`, `README.md`.
