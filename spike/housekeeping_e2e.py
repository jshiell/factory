"""THROWAWAY e2e for plan 8 (run in a real terminal; `clean` needs a TTY for its y/N prompt).

Builds a synthetic repo with a merged task, a failed task with a dirty worktree, an audited task and a
stale running task, then runs `recover`, `retry` and `clean`. Never touches this repo. $0, no API calls.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from factory.ledger import Ledger  # noqa: E402
from factory.merge_task import merge_task  # noqa: E402
from factory.store import TaskStore  # noqa: E402
from factory.task import Task, TaskState  # noqa: E402
from factory.worktrees import WorktreeManager  # noqa: E402

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


def commit_file(worktree, name):
    (worktree / name).write_text(name)
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", f"add {name}")
    return git(worktree, "rev-parse", "HEAD")


def main():
    real_before = (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list"))
    scratch = Path(tempfile.mkdtemp(prefix="housekeeping-e2e-", dir=Path.home())).resolve()
    repo, state, wt_root = scratch / "repo", scratch / "state", scratch / "wt"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "factory.toml").write_text(f'state_dir = "{state}"\nworktree_root = "{wt_root}"\n')
    (state / "tasks").mkdir(parents=True)
    store, manager = TaskStore(state / "tasks"), WorktreeManager(repo, wt_root)
    paths = {}
    for name in ("m1", "f1", "a1", "r1"):
        paths[name], base = manager.create(name)

    def save(name, state_, **fields):
        store.save(Task(id=name, prompt="p", state=state_, goal=f"goal {name}", worktree=str(paths[name]),
                        base_sha=base, **fields))

    tip = commit_file(paths["m1"], "m1.txt")
    save("m1", TaskState.AUDITED, audited_sha=tip)
    merge_task("m1", store, repo, manager, shown_sha=tip)  # merged: worktree gone, branch left behind
    (paths["f1"] / "dirty.txt").write_text("uncommitted")
    save("f1", TaskState.FAILED, failure_reason="plan_rejected_by_human")
    save("a1", TaskState.AUDITED, audited_sha=commit_file(paths["a1"], "a1.txt"))  # main moved: cannot merge
    save("r1", TaskState.RUNNING)

    def factory(*args, check_exit=None):
        cmd = ["uv", "run", "--project", str(ROOT / "orchestrator"), "factory", *args]
        print(f"\n$ factory {' '.join(args)}")
        return subprocess.run(cmd, cwd=repo, env=ENV, capture_output=bool(check_exit), text=True) if check_exit else \
            subprocess.run(cmd, cwd=repo, env=ENV)

    recover = factory("recover", "r1").returncode
    retry_failed = factory("retry", "f1", check_exit=True)
    retry_audited = factory("retry", "a1", check_exit=True)
    print(f"retry f1 -> {retry_failed.stdout.strip()}\nretry a1 -> {retry_audited.stdout.strip()}")
    print("\n--- clean: you should see m1 (branch left), f1 (dirty) and r1 (recovered); answer y ---")
    clean = factory("clean").returncode
    ledger = Ledger(state / "ledger.jsonl").entries()
    new_tasks = {t.id: t for t in store.list().tasks if t.retry_of}
    checks = {
        "recover exit 0": recover == 0,
        "r1 failed as interrupted": store.load("r1").failure_reason == "interrupted",
        "assumed ledger entry for r1": any(e["task_id"] == "r1" and e["assumed"] for e in ledger),
        "retry of failed and audited each made a proposed task": sorted(t.retry_of for t in new_tasks.values()) == ["a1", "f1"]
            and all(t.state is TaskState.PROPOSED for t in new_tasks.values()),
        "clean exit 0": clean == 0,
        "m1 branch deleted": not git(repo, "branch", "--list", "factory/m1"),
        "f1 worktree removed, field cleared": not paths["f1"].exists() and store.load("f1").worktree is None,
        "f1 branch kept": bool(git(repo, "branch", "--list", "factory/f1")),
        "audited a1 worktree and branch untouched": paths["a1"].exists() and bool(git(repo, "branch", "--list", "factory/a1")),
        "failed r1 worktree cleaned too": not paths["r1"].exists(),
        "real repo worktrees and branches untouched": (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list")) == real_before,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nclean up: rm -rf {scratch}")
    sys.exit(0 if all(checks.values()) else 1)


main()
