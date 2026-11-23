# Plan 88: resolve the optional follow-ups

Drafted 2026-11-23, revised after plan review (`plan-reviewer`, opus). Draft, awaiting approval. (Number 86 stays reserved by plan 85 for the `watch` retry cap.)
Source: the "Optional follow-ups" in `todo.md`, plus the plan 87 follow-ups added there.
Base: `main` (plan 87 is merged and pushed). Branch `plan-88-followups`, cut from `main`.

## Scope
Items 1, 3 and 4 change something; 2 and 5 are closed with a note; the worker risk found in review is recorded. No existing passing test is edited.

## 1. README line 174: fix an inaccurate sentence (docs only, inline)
- Review found the premise was wrong: no sentence is duplicated. The actual defect: "A job that raises anything other than a `RuntimeError` ... ends `failed` with 'The job failed unexpectedly...'" is inaccurate, because a `subprocess.SubprocessError` or `OSError` ends with the git/system sentence instead (`job_runner.py:43-45`).
- Change: move that sentence directly after the git/subprocess/OS sentence and word it "Any other exception except a `RuntimeError` (the refusal channel, whose message is written for the user) ends `failed` with ...". The git/subprocess/OS case is stated once, in the sentence before it.
- Before editing, grep the tests for anything that reads `README.md`; list any hit here before touching it.
- Done inline (a one-sentence edit), one commit.

## 2. Plan 85 increment 6: close as already covered
- The existing `test_a_failed_result_write_is_failed_with_the_fixed_sentence_logged_and_the_worker_survives` (`test_job_runner.py`) asserts both jobs ended `failed` with the fixed sentence (the "worker ran both" line), and a worker that died after job 1 would hang `wait_idle()` and fail it. A "later job succeeds" test adds no failure the existing one misses.
- Action: remove the item from `todo.md` and add a one-line note to Plan 88's Deviations section. No test.

## 3. `has_nested_repo` and `repo=None`: accept and document (option A)
- Verified by reading: `has_nested_repo` (`gate.py:38`) skips the root `.git` (file or directory); production is safe because `verify_task.py:122-128` calls `worktree_git`, which checks it against the expected gitdir, first. With `repo=None` that check does not run: git finds its own gitdir (`worktrees.py:64-65`), so a rewritten root `.git` goes unchecked.
- This follows the plan 87 decision that `repo=None` means unpinned. Alternatives (flag any root `.git` file, or drop `repo=None`) would reopen that decision, and the first has no expected gitdir to compare against; not proposed.
- Change: one sentence in the `repo` docstring of `worktree_git` / `verify_task`'s gate step, and one in the README gate section, saying a rewritten root `.git` is unchecked when `repo` is `None`. No code, no test. One commit. Before touching the README, grep the tests for strings in that section.
- Not verified yet: that only `audit_eval` and unit tests pass `repo=None`; the implementer greps the callers first and reports.

## 4. "database is locked" on first-time state db creation
- Observed once, with `factory serve` creating the db while a CLI client polled `list`. Not reproduced.
- **Unverified hypotheses** (two, equally plausible): (a) `PRAGMA journal_mode=WAL` on a fresh db fails without waiting for the busy timeout when two connections race (`sqlite_db.py`, `timeout=5`); (b) in WAL mode a deferred transaction that reads and then writes (`CREATE TABLE IF NOT EXISTS` reads the schema first) gets `SQLITE_BUSY` immediately, without the busy handler.
- Step 1 (diagnosis, no code), `diagnostician` agent (opus), script in the scratchpad: N real processes by M rounds, each on a fresh state dir. One side builds the stores in the order `serve` does (task store, job store, ledger, process records; read `server` startup and each store's `__init__` first); the other builds them as a CLI client does and calls `list`. Log the full traceback, the failing statement and, if available, SQLite's extended error code. Check both hypotheses against SQLite's documentation.
- Step 2 (only if reproduced): a deterministic red test, not a many-rounds race: a second connection holds the lock the diagnosis names (for example an open `BEGIN IMMEDIATE`), released after a short delay; assert store construction waits and succeeds instead of raising. Fix with the smallest change the diagnosis supports (candidates: `BEGIN IMMEDIATE` for DDL and writes, retrying the pragma, setting WAL once with its creation race handled). One commit for the test, one for the fix.
- If not reproduced: record the method and result in `todo.md` and close the item.
- No existing test edits expected. Any that become necessary are reported first.

## 5. Case-insensitive root `factory.toml` check: close with no code
- The current check is `(toplevel / "factory.toml").exists()` (`settings.py:141`), which also matches `Factory.TOML` on a case-insensitive file system, and the gate denies a root `factory.toml` case-insensitively (`gate.py:23`). On a case-sensitive file system `Factory.TOML` was never read as config by the old loader either.
- That last claim concerns the old loader, which is no longer in the tree: the implementer cites the commit and line (`git show`) before closing the item.
- Action: remove the item from `todo.md`.

## New risk recorded (no code in this plan)
- `JobRunner._work` (`job_runner.py`): if `mark_failed` itself raises (for example "database is locked", the item 4 error) inside an `except` handler, the exception leaves the handler, `finally` runs and the worker thread ends; later jobs stay `queued` and `wait_idle` hangs. Added to `todo.md` under accepted risks. Item 4's fix may reduce it; revisit after step 2.

## Order and process
- Order: 1, 3 (inline docs), 2 and 5 (todo/notes), 4 step 1, then 4 step 2 if reproduced.
- Cut `plan-88-followups` from `main`; run the gate once on the base before starting. One commit per green cycle.
- Gate: `cd orchestrator && uv run ruff check`, `uv run pytest -q -n auto`; then the `verify` skill and `commit-auditor`.
- Agents named explicitly: opus `diagnostician` for 4 step 1, opus `implementation-complex` for 4 step 2. Everything else inline.
- Commits end with the Sonnet 5.5 trailer, short messages, no file lists. Sign any unsigned commits at the end (stop after the first failure and ask for the 1Password prompt). Never push.

## Tests touched
None edited. New tests only in 4 step 2, if reproduced.

## Deviations
(none yet)
