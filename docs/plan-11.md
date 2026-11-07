# Plan 11: abandon, and gate-cache cleanup in clean

Goal: close two housekeeping gaps left by plans 8 to 10. A task stuck in `queued`, `agent_done` or `verified` (or a reviewed or audited task the human no longer wants) has no command that ends it, so its worktree and branch are never cleanable; and `.factory/gate-cache/<id>` directories (private uv caches written by the sandboxed gate) accumulate forever.

## Decided (not up for review)
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first, never edit a passing test. Never push.
- **`factory abandon <id>`** acts on every non-terminal state except `running`: proposed, planned, reviewed, queued, agent_done, verified, audited. It applies a new `Event.ABANDONED` (the only state-machine change) giving `failed` with reason `abandoned`. Nothing else on the task changes (worktree, branch, plan, audit findings stay), so `show` still explains it and `clean` can now remove its worktree. Failed-task branches stay (existing rule).
  - Refusals: `running` (message points to `recover`), `merged`, `failed`; an `agent_done` task with `fix_in_flight` set (a fix agent may still be alive: run `factory advance` first, which reports `fix_interrupted`).
  - Takes `state_lock` (`AlreadyRunning` gives exit 1, nothing changes) so it cannot race a driver mid-stage. No TTY needed: it grants no authority and removes nothing. Human-run, never called by `advance` or `build_stages` (new tripwire).
- **Gate-cache cleanup lives in `clean`**, in the same y/N preview, under the same lock.
  - New `Settings.gate_cache_dir` property (`state_dir / "gate-cache"`), used by `stages.py` and `clean`, so the path has one source.
  - A cache dir is a candidate when its name is a task id whose task is `merged` or `failed` (reloaded before acting, skipped if no longer terminal), or when no task record has that name (orphan). Dirs of non-terminal tasks (including `audited`) are never touched.
  - Safety: only entries that are real directories (not symlinks) with a safe task-id name directly under `gate_cache_dir` are removed (`shutil.rmtree`, which does not follow symlinks inside); anything else is reported and skipped. A removal failure is reported, that dir kept, clean continues.
  - Preview shows each cache dir with its size (bytes summed without following symlinks) and whether it is an orphan.
  - Cache removal is independent of worktree removal: a worktree failure does not stop the cache removal, and the reverse.
- `clean_candidates` and `clean_tasks` gain an optional `cache_root` (default None = no cache handling), so existing callers and tests are unchanged.
- No change to `merge`, `retry`, `recover`, the gate or the fix loop.

## Cycles
A1. **State machine.** `Event.ABANDONED`; allowed from the seven states to `failed`; refused from running, merged, failed.
A2. **abandon_task** (`abandon_task.py`, `AbandonRefused`).
   - a. each of the seven states becomes `failed` with reason `abandoned`, everything else on the task unchanged
   - b. refuses running, naming `recover`
   - c. refuses merged and failed
   - d. refuses agent_done with `fix_in_flight`, naming `advance`
A3. **CLI `abandon`.**
   - a. exit 0, says the task is abandoned and that `factory clean` removes its worktree
   - b. a refusal exits 1 with the reason, nothing changes
   - c. lock held exits 1, nothing changes
   - d. unknown id exits 1 as the other commands do
A4. **Tripwire** (new test): neither `advance.py` nor `stages.py` references `abandon_task`.
G0. **Refactor:** `Settings.gate_cache_dir`, used by `stages.py` (green on existing tests).
C1. **Candidates.** `clean_candidates(..., cache_root)` returns, for merged and failed tasks, the cache dir and its size; a task with only a cache dir (no worktree, no branch) is a candidate; running, audited and proposed tasks' dirs are not.
C2. **Orphans.** `orphan_caches(store, cache_root)`: a directory with no task record is listed; a symlink, a plain file, an unsafe name, or the dir of a non-terminal task is not (symlinks and odd entries are reported as skipped).
C3. **Removal.**
   - a. `clean_tasks` removes a terminal task's cache dir
   - b. a cache dir holding a symlink to outside is removed without touching the target
   - c. a removal failure is reported, the dir kept, the others still cleaned
   - d. a task that is no longer terminal at execution time keeps its cache
   - e. a worktree removal failure does not stop the cache removal
   - f. orphans are removed by a separate `remove_orphan_caches`, re-checking that each is still a real directory with no task record
C4. **CLI `clean`.** Preview lists cache dirs with sizes and orphans; `y` removes them; anything else changes nothing; "nothing to clean" only when there are no worktrees, branches or caches to clean; lock held exits 1.
E1. **Throwaway e2e (human, real terminal, $0):** `spike/abandon_clean_e2e.py` on a synthetic repo: tasks in queued, agent_done, verified, audited, running and merged states with worktrees and cache dirs, plus an orphan cache dir and a symlink entry. Run `abandon` (each allowed state, then the refused ones), `clean`; check states, worktrees, caches, that the running and non-terminal tasks' dirs and the symlink target are untouched, and that the real repo is untouched.

## Residual exposure
- An abandoned `audited` task's commits stay on its kept branch; `clean` discards a dirty worktree after showing the dirty flag (plan 8).
- Gate code writes the cache dir, so its contents are attacker-shaped: removal never follows symlinks and is limited to directories directly under `gate_cache_dir`; a hostile read-only tree could make a removal fail, which is reported.
- A cache dir of a task whose record was deleted by hand is treated as an orphan and removed.
- Cache sizes walk the tree, so a huge cache makes the preview slow.
- `queued`/`agent_done` tasks abandoned while an unlocked orphan agent is alive (SIGKILLed driver) are the same residual as `recover`.

## Out of scope
Deleting failed-task branches, tasks with no record, auto-cleanup, re-planning on a moved main, closing the fix agent's own-test weakening gap.

## Folded from the plan-reviewer (supersedes the text above where they differ)
- A3.d: an unknown or unsafe id exits **2**, like every command (new test, existing parametrize untouched).
- **Orphan rule:** an entry is an orphan only if `store.load(name)` raises `TaskNotFound`; any other load error (corrupt or newer-schema record) keeps the cache and is reported. A missing `cache_root` yields nothing (pinned), so existing CLI tests stay green. C3.f re-checks the same way.
- **`Candidate.cache_bytes: int | None = None`** replaces a carried path: `clean_tasks` recomputes `cache_root / task.id` and re-checks with lstat that it is a real directory before `rmtree`. With `cache_root=None` the candidate filter behaves exactly as before; with it, a terminal task with only a cache is also a candidate.
- **Cache removal is independent of the worktree steps** (worktree failure, outside-root worktree); only the "now <state>" reload check blocks every step. A failed `rmtree` is reported as "partially removed".
- **Skipped cache entries** (symlinks, plain files, unsafe names, corrupt records) are listed in the preview; they make `clean` exit 1; "nothing to clean" only when there are no candidates, orphans or skipped entries.
- A2 order: merged/failed refusal first (state-named message), then running (names `recover`), then `fix_in_flight` (names `advance`). `abandon_task` loads the task itself inside the lock. Output says a killed driver may have left an agent running.
- G0 gets a red settings test first. A4 is a new test also forbidding `orphan_caches` and `remove_orphan_caches` in `advance.py` and `stages.py` (the existing `HOUSEKEEPING` tuple is not edited).
- E1 adds one real cache populated by an actual gate run.
- Residual correction: after a SIGKILLed driver a plan, review or audit agent (states proposed, planned, verified) or the gate's test process (agent_done) may still run against a task `abandon` accepts; `queued` never has a live agent. Unledgered spend of orphaned read-only agents is a pre-existing gap.
- Not verified: whether real uv caches contain read-only entries (rmtree would then fail in normal use) and `os.walk` speed on a large cache; E1 checks the first.
