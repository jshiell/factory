# Plan 82: git and OS failures stop leaking through queued jobs, and a worktree-less merge preview stops answering 500

Branch `plan-82-job-errors-and-no-worktree`, cut from `main`. Drafted 2026-11-22, revised after plan review (`plan-reviewer`, opus; its code claims checked by me where noted). Closes two "out of scope" items of plan 81. **Approved 2026-11-22.**

## Problem
- A `CalledProcessError` or `OSError` inside a queued job lands in `job.error` as `str(error)`: the git command line or an OS message with a path. For rebase and merge jobs `_refusal_as` wraps it first (`merge refused: Command '[git, ...]' ...`). `TimeoutExpired` also carries the argv.
- `job.error` is shown raw by the JSON `/jobs` routes and posted by `watch` as a public comment (`watch/merging.py:172` for a failed merge job, `:207` for a failed rebase job).
- Two paths leak through a job that **succeeds**, so the runner never sees them: `advance.py` stores `f"{stage}: {type(error).__name__}: {error}"` for a `CalledProcessError` in `result.stopped` (`watch` posts it publicly, the web panel shows it), and `clean_tasks.py:119` stores `worktree kept: git failed ({error})` in the clean outcome.
- `GET /tasks/{id}/merge-preview` for an AUDITED task with `worktree=None` raises `TypeError` (`Path(None)`) and answers 500. The web merge page answers a 500 "task unreadable" page for the same task, because `_UNREADABLE` in `web/routes.py` includes `TypeError`.

## Decisions (user, 2026-11-22)
- D1: fix at the source: the runner stores one fixed sentence for a git, subprocess or OS failure in `job.error`; the real error goes to stderr.
- D2: a merge preview with no worktree answers 409 `merge check failed` with plan 81's merge sentence, so `watch` retries it. The web merge page changes from a 500 "unreadable" page to the 409 "Git could not check" page. The signal is `WorktreeMissing(FileNotFoundError)`, so every existing `OSError` catch handles it.
- D3: the runner catches `SubprocessError` and `OSError` (adds `TimeoutExpired`).
- D4: advance and clean are in scope (their leaks go through succeeded jobs).

## Facts (read in code)
1. `job_runner.py:34-38`: `mark_running`, `run()` and `mark_succeeded` share one `try`; `except Exception` stores `str(error)`. A store failure after a good `run()` would be recorded as the job failing; the narrowing below fixes that for the new clause.
2. `web_actions.py` lists `CalledProcessError` in the rebase tuple (line ~85) and the merge tuple (line ~106); `_refusal_as` turns it into `<kind> refused: <error>`.
3. `watch` matches only the exact strings `_MERGE_JOB_MAIN_MOVED` and `_MERGE_JOB_ALREADY_IN_MAIN` (`merging.py:169`, `:38`), neither of which a git failure can produce; `test_watch.py` fakes use fixed strings. Dropping `CalledProcessError` from the tuples changes nothing `watch` matches on.
4. `merge_preview` (`merge_task.py:35`): `apply(task, Event.MERGED)` first, then the branch check, then `Path(task.worktree)`. A MERGED task also has `worktree=None`, so the new check goes after `apply` and the branch check, right before the first `Path(task.worktree)` use.
5. `advance.py`'s `_KNOWN_ERRORS` includes `CalledProcessError`; `test_advance.py:154` asserts `"CalledProcessError"` appears in `stopped` for it, so the fixed text keeps the type name (that test stays green unchanged). `WorktreeExists`, `BranchExists` and `ProjectSubdirEscapes` messages may carry paths; they are out of scope.
6. `runner.py:68` calls `Popen` unguarded, so a missing harness executable raises `FileNotFoundError` inside an advance job; it will now read as the generic sentence in `job.error`, with the detail in the log.
7. `run_git` captures output, so `str(CalledProcessError)` omits git's stderr; the log line must include `error.stderr`.

## Tests touched (grep of `tests/` for `Command '`, `returned non-zero exit status`, `worktree kept`, `git failed`, `CalledProcessError: `, `stopped`)
- `test_advance.py:154`: asserts the type name; stays green (the fixed text keeps it).
- `test_clean_tasks.py:326`, `test_cli.py:592-596`: assert `"worktree kept"` in a problem; stay green (the prefix is kept).
- `test_web_merge_refusals.py`, `test_web_clean_git_failure.py`, `test_server_merge.py`: leak checks on other routes; unchanged.
- `test_watch.py`: fixed fake strings; unaffected.
- No existing test is edited. Not checked: whether `cli.py` prints job errors or matches `refused:` prefixes (to read in increment 1).

## Increments (one test, one change, one commit each)
1. **Runner stores a fixed sentence** (`tests/test_job_runner.py`). Red, parametrised over `CalledProcessError(128, ["git","branch","secret-ref"], stderr="fatal: secret")`, `FileNotFoundError(2, "No such file", "/secret/path")` and `TimeoutExpired(["git","secret-ref"], 1)`: job `failed`, `job.error ==` the sentence, none of the secrets in it, the real detail (including `fatal: secret`) on stderr with control characters escaped, and the next submitted job still runs. Green: `result = run()` inside a `try` that catches `(SubprocessError, OSError)` before `Exception`; `mark_succeeded` outside it; the loop body is wrapped so a store failure cannot kill the worker. Sentence (one, for every kind): "Git or the system failed while running this job. The server log has the details."
2. **Guard.** A job raising `RuntimeError("boom")` still stores `boom`. Commit only the test.
3. **Merge job.** Red through the server: patch `factory.server.merge_task` to raise `CalledProcessError`; the job error is the sentence, not `merge refused: Command ...`. Green: drop `CalledProcessError` from the merge tuple. `MergeRefused` and `InvalidTransition` stay `merge refused: <reason>`.
4. **Rebase job.** The same for `factory.server.rebase_task` and the rebase tuple.
5. **Merge preview, no worktree (JSON).** Red: AUDITED task with `worktree=None`; 409, `merge check failed`, the merge sentence. Green: `WorktreeMissing(FileNotFoundError)` raised by `merge_preview` after `apply` and the branch check; update the `WebActions.merge_preview` docstring.
6. **Guard.** A VERIFIED task with `worktree=None` still answers 409 `merge refused` (the check comes after `apply`).
7. **Web merge page, no worktree.** AUDITED task with `worktree=None` gives the 409 "Git could not check" page, using `_assert_html_refusal` in `test_web_merge_refusals.py`. A guard (the existing `OSError` catch covers it); commit the test.
8. **Advance.** Red: a `CalledProcessError` from a stage, through `advance`; `stopped` is `"<stage>: CalledProcessError: git failed, see the server log"` with no argv, and the detail is on stderr. Green: handle `CalledProcessError` separately from the other known errors.
9. **Clean outcome.** Red: `clean_tasks` with a failing worktree removal; the problem is `worktree kept: git failed`, no `str(error)`, detail on stderr. Green: drop `({error})` and log it.
10. **Docs.** README (job errors for git and OS failures are a fixed sentence and the detail is in the server log; advance and clean outcomes likewise; merge-preview no-worktree is `merge check failed`, and the web page shows the 409 page); close plan 81's two out-of-scope bullets; fill in Deviations.
11. **Gate and audit.** `ruff check`, full `pytest -n auto` (background), then `commit-auditor` (opus).

## Risks
- A failed merge or rebase job's `watch` comment loses its git detail; the operator reads the server log. Intended (D1).
- Inconsistency kept: a git failure inside a merge job still posts a public `merge-refused` comment and stops the issue (terminal), while a preview git failure retries (plan 81).
- A missing harness executable, or any `OSError` inside any job kind (advance, eval-audit, clean), reads as the generic sentence in `job.error`. Before, it read e.g. `[Errno 2] No such file or directory: 'claude'`.
- `OSError` and `SubprocessError` are broad; the log line keeps the detail.
- Job rows already stored keep their raw text.
- A `mark_failed` that itself raises still kills the worker thread (existing behaviour).
- The clean `clean_scan` failure inside a queued clean job now shows the job sentence, not `CLEAN_CHECK_FAILED` (the two differ).

## Out of scope
- Unreadable-row errors from `store.load` on JSON routes.
- A `watch` retry cap for a permanent git failure.
- Generic non-git exceptions in jobs (still `str(error)`).
- Path-bearing messages of `WorktreeExists`, `BranchExists` and `ProjectSubdirEscapes`.
- A `recover` or eval-runner `OSError` path not read.

## Deviations
- Increment 2 (the `RuntimeError("boom")` guard) was not added: `test_a_job_that_raises_is_failed_with_the_message_and_the_worker_survives` already asserts a `RuntimeError` stores its message.
- Increments 6 and 7 are guards (passed with no code change), committed as tests only.
- A shared `textsafe.failure_detail` and `log_failure` produce the one escaped stderr line (error text plus the subprocess's stderr); the runner, advance and clean all use it. The runner's log line names the job id only, because the queue does not carry the job kind.
- CLI users now see the generic sentence for job failures and need the server log (recorded as intended risk).
- Not changed: `clean_tasks` still reports git's first stderr line for branch deletion problems (lines ~132 and ~147); that text can carry paths and is not covered by the plan. Flag for a follow-up.
