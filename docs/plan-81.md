# Plan 81: JSON merge and clean previews stop answering 500 on git failures

Branch `plan-81-json-git-errors`, cut from `main`. Drafted 2026-11-22, revised after plan review (`plan-reviewer`, opus; its `watch` finding checked by me in `watch/merging.py:79-88`, `client.py:42` and `watch/__init__.py:152`). Follows the plan 80 follow-ups, which fixed the web pages only. **Approved 2026-11-22.**

## Problem
- `GET /clean-preview` and `POST /clean` raise (TestClient) or answer a bare 500 when git cannot check the repository (`clean_candidates` raises `CalledProcessError`, or `OSError` for a missing directory). `POST /clean` reaches it only through `enqueue_clean`, which re-previews.
- `GET /tasks/{id}/merge-preview` does the same when the task's worktree directory is missing (`run_git` runs with `cwd=`, so the failed chdir is a `FileNotFoundError`; the handler catches only `CalledProcessError`).
- The existing merge-preview 409 for `CalledProcessError` puts `str(error)` in `detail`, which is the git command line.

## Decisions
- D1: git and OS failures are 409, matching the web pages.
- D2: their `detail` is a fixed sentence, never the git command or a path.
- D3: the existing `CalledProcessError` response changes to the fixed sentence and, per D5, to the error name `merge check failed` (status stays 409). This changes two fields of an existing response body.
- D4: the fixed sentences are the web pages' wording, held once as shared constants (the web routes import them): merge, "Git could not check this task's branch and worktree, so it cannot be merged."; clean, "Git could not check the repository, so nothing can be cleaned."
- D5 (user, 2026-11-22): `watch` must not stop an issue for good on an OS error or a git failure; both retry on the next pass. See the next section for how the plan achieves it.
- `MergeRefused` and `InvalidTransition` details are unchanged.

## The `watch` interaction (corrects the first draft)
`watch` treats any 409 with `error == "merge refused"` as a terminal refusal: it posts `Merge refused: <detail>` as a public comment and moves the issue to `MERGE_STOPPED`. Any other `ServerRefused` is re-raised and `run_pass` logs it as "left for the next pass", so the issue is retried with no comment.
- **`CalledProcessError`** (a git check failed, for example the branch is missing): today `409 merge refused` with the git command line, which `watch` posts as a comment and stops on. Now `409 merge check failed` with the merge sentence, so `watch` retries next pass and posts nothing (D5, user's decision 2026-11-22).
- **`OSError`** (missing worktree directory, permission error): the same `409 merge check failed` and merge sentence. `watch` does not recognise it, re-raises, and retries next pass (D5). Today the same case answers a plain-text 500; `ServerClient._send` calls `response.json()` before it checks the status and raises a decode error, which `run_pass` does not catch (whether the caller of `run_pass` survives is unverified).
- **Clean** has no `watch` consumer, so both failures are `409 clean refused`.

## Facts (read in code)
1. `server.py:244-252`: `merge_preview_view` catches `(MergeRefused, InvalidTransition, CalledProcessError)` and sends `str(error)`. `ApiError` is at `server.py:75`, the handlers at `server.py:416`.
2. `server.py:310-339`: `clean_preview` and `enqueue_clean` call `clean_preview_now()` with no handling; `clean` runs `_required_string` first, so a red must send `{"token": ...}`. The web layer already catches `(CalledProcessError, OSError)` around both (`routes.py`).
3. The merge POST (`service.get_task`, then `enqueue_merge` compares shas and queues) and retry-preview (`keepable_plan`) run no git synchronously, so they have no git 500. The queued `clean_now` runs on the runner thread, so its errors become `job.error`, never an HTTP 500.
4. The reds raise rather than return 500: `tests/serving.py` uses `TestClient` with `raise_server_exceptions=True`. A red fails with `FileNotFoundError` or `CalledProcessError`, which is still a valid red.
5. Patching `factory.server.clean_candidates` works, as `test_web_clean_git_failure.py` shows.
6. An `OSError` is reachable in clean: `run_git(repo, ...)` with a missing repo raises `FileNotFoundError` once there is a cleanable task, and `os.listdir` in `orphan_caches` can raise `PermissionError`.

## Tests touched (grep of `tests/` for `merge-preview|clean-preview|returned non-zero exit status|merge refused|clean refused`)
- `test_server_merge.py`: merge-preview 200, not-audited 409, wrong-branch 409 (asserts a `MergeRefused` detail, unchanged); clean-preview and clean at lines 174-213. None asserts a `CalledProcessError` error name or detail.
- `test_watch.py` lines 274, 984-1304: fakes raising `ServerRefused` with fixed strings. Unaffected; they are guards for the unchanged `merge refused` path.
- `test_client.py:42`, `test_job_store.py:37-40`: fixed strings, unaffected.
- `test_web_merge_refusals.py`, `test_web_clean_git_failure.py`: separate handlers; unaffected, but they and the routes move to the shared constants (D4), so they must stay green.
- No existing test is edited.

## Increments (one test, one change, one commit each)
1. **Shared sentences.** Move the two web sentences to constants imported by `web/routes.py` and the server. Refactor only; the web tests must stay green unchanged.
2. **Merge-preview, git failure detail (D3).** Red: save a second AUDITED task `t2` with `worktree=str(tmp_path)`, made-up shas and no branch (deleting `t1`'s branch fails while its worktree has it checked out; this copies `test_web_merge_refusals.py:51-55`). Expect 409, `error == "merge check failed"`, `detail ==` the merge sentence, `rev-parse` not in the body. Green: a separate `except CalledProcessError` sending the new name and sentence; `MergeRefused` and `InvalidTransition` keep `"merge refused"` and `str(error)`.
3. **Merge-preview, OSError.** Red: `shutil.rmtree(audited.worktree)`. Expect 409, `error == "merge check failed"`, the merge sentence, the path absent. Green: add `OSError` to the increment 2 clause.
4. **Clean-preview.** Red, parametrised over `CalledProcessError(128, ["git","branch","--list","secret-ref"], stderr="fatal: secret")` and `FileNotFoundError(2, "No such file", "/secret/path")`, patching `factory.server.clean_candidates`. Expect 409 `clean refused`, the clean sentence, none of `secret-ref`, `/secret/path`, `fatal`, `CalledProcessError` in the body. Green: catch `(CalledProcessError, OSError)` in `clean_preview`.
5. **POST /clean.** Same parametrised red with `{"token": "0" * 64}`; also `client.app.state.jobs.list() == []`. Green: catch beside `CleanChanged` in `clean`.
6. **Watch guard (D5).** In `test_watch.py`, a fake whose merge-preview raises `ServerRefused(409, "merge check failed", <sentence>)`: assert no `merge-refused` comment is posted, the phase stays `AWAITING_MERGE`, and the pass logs "left for the next pass". Likely a guard, not red; commit only if the test is added.
7. **Docs.** README: the JSON merge preview, clean preview and `POST /clean` answer 409 with a fixed sentence (replace the line-174 note), and the merge preview's git and OS failures use the error name `merge check failed`; the `watch` "Refusals" bullet (line ~189) says a git failure or OS error while checking the merge is retried next pass with no comment, and only a real `merge refused` stops the issue. Plan 80's follow-up bullet is closed. This plan's Deviations are filled in.
8. **Gate and audit.** `ruff check`, full `pytest -n auto` (background), then `commit-auditor` against this plan.

## Risks
- Increment 2 changes the `error` and `detail` of an existing 409. A client matching `merge refused` for a git failure now sees `merge check failed`. The only repo client is `watch`; for a git failure it changes from a public comment plus a terminal stop to a silent retry each pass.
- A permanent git failure (for example a branch deleted by hand) now retries every pass and never reaches a human, where before the issue got a comment and stopped. It is logged on stderr each pass ("left for the next pass"). There is no retry cap. Accepted by the user's decision; flagged here.
- `OSError` is broad. Every case means the check could not run, so one sentence fits.

## Out of scope
- A `CalledProcessError` raised inside a queued job still lands in `job.error` as `str(error)` (the web panel escapes and caps it; the JSON `/jobs` shows it raw).
- `merge-preview` for an AUDITED task with `worktree=None` raises `TypeError` and stays a 500 (the web page treats it as unreadable).
- Unreadable-row errors from `store.load`, common to every JSON route.

## Deviations
- Increment 4 and 5 reds raise (TestClient re-raises server exceptions) rather than return 500, as the plan noted.
- Increment 6 was a guard (passed without code change), as the plan expected; the added test is committed.
- Increments 4 and 5 catch in `clean_preview` and `clean` separately, as planned; `clean_preview_now` itself is unchanged.
- The `watch` caller's behaviour on today's plain-text 500 stayed unverified; it is no longer reachable for these routes.
