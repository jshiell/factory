# Plan 45: don't offer a rebase when the audited commit is already in `main`

Fixes a known gap from plan 44 (`docs/spike-findings.md`, "already in main"). Reviewed by `plan-reviewer`; findings folded in. Decided with the user: option A (a `main` sitting exactly at the audited commit is let through to merge recovery) and the watcher adapts its "Next" line.

## Problem

`merge_preview` (`orchestrator/src/factory/merge_task.py:38-39`) raises `MAIN_MOVED` whenever `main`'s HEAD is not the task's base. It never asks whether `main` already contains the audited commit. In that case the watcher posts a `merge-moved` offer, and a 👍 fails because `rebase_task` refuses with "the audited commit is already in main" (`rebase_task.py:93`). The task ends in `rebase-refused`.

A second, related dead end: after a crash between the fast-forward and the save, `main` and the branch both sit at the audited commit. `merge_task` recovers that (`_already_fast_forwarded`), but never runs, because `factory merge` (`cli.py:610`) and the watcher (`watch.py:470`) call the preview first and the preview refuses with `MAIN_MOVED`.

## Behaviour

The preview checks `main`'s HEAD in this order:

1. HEAD is the task's base: no refusal (unchanged).
2. HEAD is the audited commit: no refusal. The merge reaches `_already_fast_forwarded` and records the task as merged.
3. The audited commit is an ancestor of HEAD (main moved past it): refuse with `ALREADY_IN_MAIN`.
4. Anything else (including main reset backwards): refuse with `MAIN_MOVED`.

The check sits inside the `HEAD != base_sha` branch, so a task whose `audited_sha == base_sha` is not refused while `main` has not moved.

`ALREADY_IN_MAIN` is not `MAIN_MOVED`, so the watcher posts `merge-refused`, not a `merge-moved` offer. Its "Next" line adapts: both `factory rebase` and `factory merge` refuse in this case, so it says nothing was merged by the factory, the audited commit is already in `main`, and to check `main` and close the issue. It does not suggest `factory abandon` (not verified to accept an audited task).

## Cycles (TDD, one commit each)

0. **Refactor (guard).** Move `_is_ancestor` from `rebase_task.py:130` to `worktrees.py` next to `run_git`, named `is_ancestor` (`gate.py:167` has a different private `_is_ancestor`). Return False only on git exit code 1; let other errors propagate (the preview endpoint already turns `CalledProcessError` into a 409 `merge refused`). Guard: the `test_rebase_task.py` REFUSALS test (`:91`).
1. **Red:** a new `test_merge_task.py` test: fast-forward `main` to the audited commit, add one more commit on `main`; `merge_preview` raises `MergeRefused` matching `ALREADY_IN_MAIN`. **Green:** add `ALREADY_IN_MAIN` to `merge_task.py` and the ancestor check inside the `HEAD != base_sha` branch, before `MAIN_MOVED`.
2. **Red:** with `main` fast-forwarded exactly to the audited commit (the crashed ff), `merge_preview` returns a `Preview`. Server-level test in `test_server_merge.py`: the preview returns 200, then `POST /merge` succeeds and the task is merged. **Green:** let `HEAD == audited_sha` through ahead of the ancestor check.
3. **Guard:** the existing moved-main and reset-backwards tests (`test_merge_task.py:66`, `:76`, `:172`) pass unchanged: neither puts the audited commit in `main`. No commit unless something changed.
4. **Red:** `test_watch.py`: a preview refused with `ALREADY_IN_MAIN` (the constant, not a literal) posts `merge-refused`, no offer, and the "Next" line says the audited commit is already in `main` and does not say `factory rebase`. Same for a failed merge job whose error is `merge refused: ` + `ALREADY_IN_MAIN`. **Green:** `_post_merge_refused` picks the "Next" line by reason. Check at implementation that the failed-job path passes the reason through; if it does not, that is a one-line fix in the same cycle.
5. **Docs.** README: the "if main moved it refuses" bullet (~`:275`) and the diagram edge labelled "main moved" (~`:214`) get the new case. `spike-findings.md:510` is one bullet holding two gaps; remove only the "already in main" half, and add (a) the new known gap: work that reached `main` under different SHAs (cherry-pick, a GitHub rebase-merge) is not caught by the ancestor check, so the offer still appears; the rebase would then likely produce no commits and the gate fail with `no_commits` (unverified); (b) the crash-recovery behaviour of the preview.
6. Run the full suite in the background (`uv run pytest -q`, ~70s), then `verify`.

## Tests whose strings this touches (grepped)

- `tests/test_merge_task.py:66`, `:76` (its `match="moved"` is at `:82`), `:172`: match "moved"; branch states differ, expected unchanged.
- `tests/test_server_merge.py:100`: only checks `startswith("merge refused: ")`.
- `tests/test_watch.py:797-842`, `:984`, `:1090`: use `MAIN_MOVED` or free text. `:806` asserts `"factory rebase t-9" in body` for a non-moved refusal: keep it passing (the generic "Next" line is unchanged for other reasons).
- `tests/test_rebase_task.py:91`: the rebase-side refusal stays as a backstop.
- `tests/test_cli.py:457`: fakes `MergeRefused`; unaffected.
- `tests/test_rebase_task.py:300-319` calls `merge_preview` after a rebase where HEAD == base: unaffected.

## Risks and limits

- Terminal users of `factory merge` see the new wording for the already-in-main case.
- Equivalent work under different SHAs is not detected (documented, not fixed).
- Not verified: the ancestor check on a real repo (cycle 1's test is the first run); whether `factory abandon` accepts an audited task; that the gate always runs before audit, which would guarantee `audited_sha != base_sha` (the check order above makes this not matter).
