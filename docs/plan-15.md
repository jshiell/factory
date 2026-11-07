# Plan 15: `rebase` when main has moved after the audit

Goal: an `audited` task whose merge is refused with "main moved since the plan" can be rebased onto the current main and sent back through the existing gate and audit, instead of being retried from scratch. The audit and gate vouched for the commit on the old base, so a rebased commit is never mergeable until both have run again.

## Spike (done, $0)
In a throwaway repo (scratchpad), from a task worktree with `core.fsmonitor=false`, `core.hooksPath=/dev/null`, `commit.gpgsign=false`: `git rebase --onto main <old base>` replays the task commits on the moved main (exit 0, branch ref updated); on a conflict it exits 1 mid-rebase, and `git rebase --abort` restores the exact old tip with a clean tree. Global `commit.gpgsign=true` (the user's) would make a rebase try to sign, so the flag must be set off. Not verified: rebase with a dirty worktree (refused by precondition below), submodules, a rewritten (non-ancestor) main beyond `--onto` semantics.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test (except an approved edit list). Never push.
- **`factory rebase <id>`**, human-run, never called from `advance.py` or `stages.py` (tripwire test, like `abandon`). Takes `state_lock` (`AlreadyRunning` gives exit 1). No TTY: it grants no authority, because it only returns the task to `agent_done`, and the existing verify and audit stages must pass again before `merge` can be offered.
- **One new event, `Event.REBASED`**: `audited -> agent_done` only. (Merge is only refused for audited tasks; verified and agent_done tasks have not been merged-previewed.)
- **Preconditions** (each a `RebaseRefused` with a message, nothing changed): task is `audited`; `task.worktree` is set, exists, is clean and its HEAD equals `audited_sha`; branch `factory/<id>` is at `audited_sha`; main is on a branch with no uncommitted tracked changes; main's HEAD differs from `base_sha` (else "main has not moved: use `factory merge`").
- **Mechanism:** `git rebase --onto <main HEAD sha> <base_sha>` in the worktree through the hardened `run_git`, with `-c commit.gpgsign=false` and the factory identity (as `commit_all`). On any git failure run `git rebase --abort` and refuse with "rebase conflicts; retry from scratch with `factory retry`", leaving the task unchanged and the worktree at the old tip. If the abort itself fails, report it loudly and leave the task unchanged.
- **On success**, saved in one `store.save`: state `agent_done` (via `REBASED`), `base_sha` = the main HEAD used, and cleared `audited_sha`, `audit_findings`, `audit_error`, `fix_rounds_used=0`, `fix_in_flight=False`, `fix_frozen_sha=None`, `failure_reason=None`. Plan, goal, review fields, worktree and `attempts` stay. The gate's tamper checks then compare against the new base, so they cover only the task's own changes.
- **Crash window:** the rebase moves the branch before the save. If the process dies between them the task is still `audited` at the old `audited_sha` while the branch moved; `merge` then refuses ("branch is not at the audited commit"), and re-running `factory rebase` also refuses on the same precondition. Resolution: `abandon` and `retry`. Documented as residual; no recovery code.
- No change to `merge`, `retry`, `clean`, the gate, the fix loop or the audit.

## Cycles
R1. **State machine.** `Event.REBASED`: allowed from `audited` to `agent_done`; refused from every other state.
R2. **`rebase_task`** (`rebase_task.py`, `RebaseRefused`), real git in tmp repos.
   a. clean rebase: task is `agent_done`, `base_sha` is main's HEAD, worktree HEAD is a descendant of it with the task's commits replayed, the audit and fix fields are cleared, other fields unchanged.
   b. refuses each precondition: wrong state, no worktree, dirty worktree, worktree not at the audited commit, branch not at the audited commit, main detached, main has tracked changes, main not moved. Nothing changes in each case.
   c. a conflict: refused, rebase aborted, worktree HEAD equals the old tip and is clean, task unchanged, no `.git/rebase-merge` left.
   d. the user's global `commit.gpgsign=true` does not matter (test sets `commit.gpgsign=true` in a repo config and the rebase still succeeds without a signer).
   e. a hook in the worktree's `.git/hooks` and a configured `core.hooksPath` do not run.
R3. **CLI `rebase`.** Exit 0 and says the task is back to `agent_done` and to run `factory advance`; a refusal exits 1 with the reason; lock held exits 1, nothing changes; unknown or unsafe id exits 2 like every command; no TTY needed.
R4. **Tripwire** (new test): neither `advance.py` nor `stages.py` references `rebase_task`.
R5. **Pipeline wiring** (through `verify_task` and `audit_task` with the existing fakes): a rebased task goes through verify and audit again and reaches `audited` with a new `audited_sha`, and `merge_preview` then accepts it (main HEAD equals the new `base_sha`).
E1. **Throwaway e2e (human, $0, no agents):** `spike/rebase_e2e.py` on a synthetic repo with a real gate run: audited task, main advances with a disjoint file, `rebase`, `advance` the gate by hand, `merge` accepted; plus a conflicting case that refuses and leaves the old tip; plus the stale-audit case (a rebased task cannot merge before re-audit).

## Residual exposure
- A rebase can change behaviour without a textual conflict (main renames a function the task calls). The re-run gate catches it only if tests cover it, which is exactly the existing gate guarantee on any code.
- The audit re-runs against the new diff (`base_sha..HEAD`); a fresh agent reviews it, at a fresh spend.
- The crash window above.
- Submodules and a rewritten main are not specially handled (`--onto` semantics only).
- Rebase rewrites commits, so signatures made earlier on task commits (none: the gate commits unsigned) are not a concern.

## Out of scope
Rebasing non-audited tasks, automatic rebase on `merge`, `retry --keep-plan`, conflict resolution by an agent, the other known gaps.

## Folded from the plan-reviewer (supersedes the text above where they differ)
Reviewer confirmed: no route to `merge` for a stale or unaudited commit (every entry applies `Event.MERGED` first, and merge needs branch == `audited_sha`); the cleared-field list is complete; `audited -> agent_done` is the right target (`verified` would skip the tests, `queued` would re-run the implementer); stage dispatch, gate cache (keyed on task id) and `clean`'s `branch_empty` are unaffected.

**Command hardening (critical)**
- The rebase never reads the agent's `.git` file. Precondition: `rev-parse --absolute-git-dir` in the worktree equals the resolved `<repo>/.git/worktrees/<id>` and `--git-common-dir` equals the repo's, else refuse. The command then passes factory-computed `--git-dir=<expected> --work-tree=<wt>`.
- Exact flags: `-c rerere.enabled=false -c commit.gpgsign=false <factory identity> rebase --no-verify --no-autostash --no-update-refs --no-gpg-sign --onto <main sha> <base_sha>`, run with `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`. `run_git` gains an optional `env` parameter for this (flagged: a change to shared code; existing callers and tests untouched). If a flag is not accepted by the user's git, the e2e shows it and I stop and report.
- An in-tree `.gitattributes` with `merge=union` can silently resolve a conflict: **accepted as residual** (re-gate and re-audit still judge the result).

**Failure handling (major)**
- Abort only when `rev-parse --git-path rebase-merge` or `rebase-apply` exists. After any failure or abort, verify worktree HEAD, branch and tree equal the old audited state; if not, report loudly and leave the task unchanged.
- The git call is wrapped in `try/except BaseException`: abort if a rebase is in progress, then re-raise. SIGKILL is residual: the task stays `audited`, `merge` refuses, the way out is `abandon`.
- New preconditions: HEAD is symbolically on `refs/heads/factory/<id>`; `fix_in_flight` is a refusal (naming `advance`), never a reset; `audited_sha` not already an ancestor of main (refuse "already in main"). Dropped: the "main detached" and "main dirty" checks (merge enforces both). After success assert branch == worktree HEAD.
- `fix_rounds_used` is still reset to 0 (policy: the re-run gate gets a fresh fix budget; global spend cap still applies; `show` no longer lists the earlier rounds).

**Output**
- CLI success prints the old `audited_sha` (the audited commits become unreachable once `clean` deletes the branch). The conflict message says "`factory abandon` then `factory retry`" and quotes git's first stderr line.

**Cycles (changes)**
- R2 adds: (f) a planted `.git` redirect refuses and nothing runs; (g) detached HEAD refuses; (h) `fix_in_flight` refuses; (i) a failure before the rebase starts reports git's reason with no false abort alarm; (j) a `KeyboardInterrupt` injected during the rebase leaves the old tip, clean tree and no `rebase-merge`; (k) `.gitattributes` `merge=union` is pinned as the accepted residual; (l) `audited_sha` already in main refuses.
- R2d: set `gpg.program=false` plus `commit.gpgsign=true` in the repo config so signing fails for certain without the override. R2e: plant `pre-rebase`, `post-checkout`, `post-commit`, `post-rewrite`, `prepare-commit-msg` hooks in `<repo>/.git/hooks` and set `core.hooksPath` in the repo config; labelled a guard test (cannot go red because `run_git` is already hardened).
- R5 adds: a task seeded with `fix_frozen_sha` set to a pre-rebase sha and `fix_rounds_used>0` passes the gate after the rebase; and after the rebase, before re-audit, `merge_preview`, `merge_task(shown_sha=<old audited_sha>)` and `merge_task(shown_sha=<new HEAD>)` all refuse.
- R2a asserts the old sha is returned for printing.

**Rejected:** the reviewer's Option B (`merge-tree` plus `commit-tree`): it squashes the task's history, needs git >= 2.38, still checks out in the agent's worktree, and its attribute handling is unverified.

**Not verified:** `--no-update-refs`/`--no-autostash` on the user's git; git-dir redirect behaviour beyond the precondition check; how `clean` treats a worktree left mid-rebase (SIGKILL case).
