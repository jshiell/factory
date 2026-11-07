"""THROWAWAY e2e for plan 14 (run in a real terminal: `clean` needs a TTY for its y/N prompt). No API calls, $0.

Synthetic repo with failed tasks: empty branch, branch with a commit, empty branch with a dirty worktree, unset base,
registered worktree with the task field cleared, a merged task; then a second clean after main was rewritten.
Never touches this repo.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from dataclasses import replace  # noqa: E402

from factory.merge_task import merge_task  # noqa: E402
from factory.store import TaskStore  # noqa: E402
from factory.task import Task, TaskState  # noqa: E402
from factory.worktrees import WorktreeManager  # noqa: E402

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
S = TaskState


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


def main():
    real_before = (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list"))
    scratch = Path(tempfile.mkdtemp(prefix="branch-gc-e2e-", dir=Path.home())).resolve()
    repo, state, wt_root = scratch / "repo", scratch / "state", scratch / "wt"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "factory.toml").write_text(f'state_dir = "{state}"\nworktree_root = "{wt_root}"\n')
    (state / "tasks").mkdir(parents=True)
    store, manager = TaskStore(state / "tasks"), WorktreeManager(repo, wt_root)
    paths, bases = {}, {}

    def make(name, state_, **fields):
        paths[name], bases[name] = manager.create(name)
        store.save(Task(id=name, prompt="p", state=state_, goal=f"goal {name}", worktree=str(paths[name]),
                        base_sha=bases[name], **fields))

    def branch(name):
        return bool(git(repo, "branch", "--list", f"factory/{name}"))

    make("empty", S.FAILED)
    make("work", S.FAILED)
    (paths["work"] / "w.txt").write_text("w")
    git(paths["work"], "add", "-A")
    git(paths["work"], "commit", "-q", "-m", "work")
    make("dirty", S.FAILED)
    (paths["dirty"] / "scratch.txt").write_text("uncommitted")
    make("nobase", S.FAILED)
    store.save(replace(store.load("nobase"), base_sha=None, worktree=None))
    manager.remove("nobase")
    make("reg", S.FAILED)
    store.save(replace(store.load("reg"), worktree=None))  # git still has the worktree registered
    make("m1", S.AUDITED)
    (paths["m1"] / "m1.txt").write_text("m1")
    git(paths["m1"], "add", "-A")
    git(paths["m1"], "commit", "-q", "-m", "m1")
    tip = git(paths["m1"], "rev-parse", "HEAD")
    store.save(replace(store.load("m1"), audited_sha=tip))
    merge_task("m1", store, repo, manager, shown_sha=tip)

    def factory(*args):
        print(f"\n$ factory {' '.join(args)}")
        return subprocess.run(["uv", "run", "--project", str(ROOT / "orchestrator"), "factory", *args], cwd=repo, env=ENV)

    print("--- clean 1: expect 'empty branch will be deleted' for empty, dirty, m1; 'branch left behind' for work/nobase/reg; answer y ---")
    first = factory("clean").returncode
    checks = {
        "clean 1 exit 1 (reg's branch is refused: its worktree is registered)": first == 1,
        "empty branch deleted": not branch("empty") and not paths["empty"].exists(),
        "dirty empty branch deleted, its worktree discarded": not branch("dirty") and not paths["dirty"].exists(),
        "branch with a commit kept, its worktree removed": branch("work") and not paths["work"].exists(),
        "unset-base branch kept": branch("nobase"),
        "registered worktree with the field cleared: branch kept": branch("reg") and paths["reg"].exists(),
        "merged branch deleted": not branch("m1"),
    }
    # main rewritten so the base is no longer an ancestor of it: `branch -d` refuses an empty branch
    make("rebased", S.FAILED)
    empty_tree = git(repo, "hash-object", "-t", "tree", "/dev/null")
    git(repo, "reset", "-q", "--hard", git(repo, "commit-tree", "-m", "rewritten main", empty_tree))
    print("\n--- clean 2 (main rewritten): expect 'rebased' is refused and reported; answer y ---")
    second = factory("clean").returncode
    checks["clean 2 exit 1, rebased empty branch kept"] = second == 1 and branch("rebased")
    checks["real repo worktrees and branches untouched"] = (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list")) == real_before
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nclean up: rm -rf {scratch}")
    sys.exit(0 if all(checks.values()) else 1)


main()
