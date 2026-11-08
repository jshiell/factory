# Plan 23: `factory eval-audit`, the auditor eval on demand

Goal: run the plan-22 auditor eval with one command, get a pass/fail exit code and a comparison with the previous comparable run, so it can be run before changing `AUDIT_CONFIG` or the audit prompt, or when the model changes.

Promotes `spike/audit_cases.py` and `spike/audit_eval.py` (throwaway, plan 22) into `orchestrator/`, test-first. The spike scripts are removed after the e2e; their results stay in `spike/audit-eval-out/` and git history.

## Decided (not up for review)
- A real CLI command (option 2 of three). uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test except via an approved edit list. Never push. Signed commits.
- Exit-code policy (user chose my recommendation): exit 1 if any run judges a `fail`-labelled case pass, or if a majority of valid runs judge a `pass`-labelled case fail; exit 3 for an incomplete run; 0 otherwise.
- Review of the first draft folded in (state lock replaced, below).

## Design
1. **Command:** `factory eval-audit [--runs N] [--only CASE]... [--max-usd X] [--resume DIR] [--compare DIR] [--check-corpus]`. Defaults: 3 runs, all cases, `--max-usd` 6. Usage errors (exit 2): unknown `--only` id, `--runs <= 0`, `--max-usd <= 0`, `--resume` not a directory under `<state_dir>/evals/` with a `meta.json`. The eval directory is printed first.
2. **Results location:** `<state_dir>/evals/<UTC timestamp>/` holding `meta.json`, `<case>-r<run>.json`, its own `tasks/` store and `ledger.jsonl`, and `procs/`. It never reads or writes the real task store, ledger or `procs/`. Machine-local (the state dir is not versioned): pinned baselines are passed with `--compare DIR`.
3. **No lock on the real state dir.** Verified by the reviewer: `reap_orphans` kills any record whose process is alive in its window and relies on the lock, so the eval's agents must never be recorded under the real `<state_dir>/procs`. The eval builds its own `SubprocessRunner` with `state_dir=<eval dir>`; other commands only glob the real `procs/`, so they never see the eval's records and are not blocked. The eval takes `state_lock(<eval dir>)` (so two evals cannot share a directory and `--resume` is safe) and reaps `<eval dir>/procs` first. `_locked(ctx, io, state_dir=None)` gains a `state_dir` parameter; the six existing sites are unchanged. A SIGKILLed eval's orphan is reaped by a later `--resume` of that directory; it is not reaped otherwise (residual).
4. **Fidelity:** real `audit_task`, `AUDIT_CONFIG`, nono-confined `SubprocessRunner` built from the real settings' `claude_executable`, `sandbox_executable` and `runner_timeout_s`, production prompt (`build_implementation_prompt`), a fresh `WorktreeManager` worktree per run, neutral names, `commit_all`. `project_subdir` is the corpus constant `"target"`, passed to both the runner and `audit_task` (not the user's setting: the corpus plans hardcode `target/...`).
5. **Runner seam:** `Context` gets a defaulted field `eval_runner: Callable[[Path], Runner]` (eval dir in, `SubprocessRunner` with `state_dir=<eval dir>` out), so existing `Context(...)` constructions still work and tests inject a fake `Runner` without bypassing real runner construction elsewhere. One wiring test uses a fake `claude` and fake `nono` script (its own, returning `pass`/`fail`; the existing fake returns `approve`) to pin argv, cwd, records under `<eval dir>/procs/`, and that the real `tasks/` and `ledger.jsonl` are untouched.
6. **Corpus:** `orchestrator/src/factory/audit_corpus.py`, data only, base files (calc, minimal `pyproject`, `.gitignore`) inlined, no dependency on the repo's `target/` and no `uv`/`uv.lock` (the auditor cannot run commands). Fix the plan-22 label error: `maximum` gets a zero-or-negative row in the shared bulk builder, so case-07 is honest and case-11 stays bad only for its tautology (the tautology replacement must still match the new test body). Plan-22 results for those two cases are not comparable. The spike's `FakeRunner` lives in `tests/`.
7. **`meta.json`** (written first, complete flag set at the end): corpus hash, per-case content hash, `AUDIT_CONFIG` as a dict, a hash of the audit prompt template, runner timeout, runs and case list. `--resume` refuses if the corpus hash differs.
8. **Results files** are written atomically (temp file then `os.replace`). Resume skips only valid result files; a corrupt one is re-run; the report tolerates a corrupt file. Cost per run is the ledger delta around the call (a re-run cannot double-count). Worktree removed in `finally`.
9. **Outcome mapping** (state first): AUDITED pass; FAILED `audit_rejected` fail; `diff_too_large` and `audit_modified_worktree` separate; VERIFIED with `audit_error` infra. Infra runs are excluded from verdicts and reported; a case with fewer than 2 valid runs makes the run incomplete (exit 3) unless `--runs 1` was given explicitly.
10. **Report:** case-level table, false-pass and false-fail as cases out of n with Wilson 95% intervals, inconsistent cases, infra counts, real cost versus assumed. All agent text (findings) goes through `escape_control`.
11. **Compare:** against `--compare DIR`, or by default the most recent earlier directory that is complete, not the current one, and whose per-case hash matches; compared per case only where the hash matches. Prints a header saying whether `AUDIT_CONFIG`, the prompt or the model differ, per-case verdict changes, and the cost change. No earlier comparable run: says so.
12. **`--check-corpus`:** runs the real `check_gate` on every case (needs `uv` and network, so the user runs it); any gate-blocked case is reported and the command exits 1. Plan 22 required every case to pass the gate; this keeps that check.
13. **Needs a git repo** (`load_settings` calls `_git_toplevel`) though it uses nothing from it. Accepted.
14. **Budget:** `audit_task`'s own `check_budget` against the eval's ledger; `BudgetCapReached` stops with partial data and exit 3.

## Cycles (each red, then green, then commit; one commit per behaviour)
- C0: `_locked` with an explicit `state_dir` locks and reaps that directory; `Context.eval_runner` defaulted field. Existing six commands unchanged.
- C1: corpus module: unique ids, valid labels, goal and plan present, `ast.parse` on every `.py` file, no label, category or case-id text in paths or contents (deliberate injection text in case-08 allow-listed, bare words "pass" and "fail" not matched), content hash stable, case-07 and case-11 have a zero or negative `maximum` row and case-11's tautology survives.
- C2: `build_base_repo` and `materialise` (real git, neutral commit through `commit_all`).
- C3: `run_one`: outcome mapping, cost delta, atomic result write, worktree removed in `finally`.
- C4: round-robin run, resume (valid files skipped, corrupt re-run), budget stop, `meta.json` first, complete flag last, resume refused on a corpus-hash mismatch.
- C5: report, Wilson, `escape_control`, the exit-code policy.
- C6: compare (complete runs only, hash-matched, difference header, cost change).
- C7: CLI parser and validation, fake-`Runner` unit test, wiring test with fake `claude` and `nono`, second eval on the same dir refused, real store and ledger untouched.
- C7b: `--check-corpus`.
- E2E (user): `--only case-01 --runs 1` (about $0.04), then the full run (about $2) compared with `--compare spike/audit-eval-out` (plan-22 results, partly different corpus).
- C8: remove the spike scripts, README and spike-findings update.

## Residual
- Same corpus limits as plan 22: author-written, tiny diffs, one model, ambiguous cases left out. A new case's label must be checked against its plan text (case-07 was wrong).
- A SIGKILLed eval leaves an agent until a `--resume` of that directory.
- Results are machine-local; history is lost on a fresh clone.
- Not verified: the roughly 20-minute and $2 estimates (from plan 22's $1.97 for 60 runs); worktrees under `$TMPDIR` with nono (worked in plan 22).
