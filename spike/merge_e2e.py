"""THROWAWAY e2e for plan 7 (run in a real terminal; it needs a TTY for the y/N prompt).

Builds a synthetic audited task in a scratch repo (never touches this repo), runs `factory merge`
twice, and checks main, the worktree and the branch. Costs $0, no API calls.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from factory.store import TaskStore  # noqa: E402
from factory.task import Task, TaskState  # noqa: E402
from factory.worktrees import WorktreeManager  # noqa: E402

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


def main():
    real_before = git(ROOT, "worktree", "list")
    scratch = Path(tempfile.mkdtemp(prefix="merge-e2e-", dir=Path.home())).resolve()
    repo = scratch / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    state, wt_root = scratch / "state", scratch / "wt"
    (repo / "factory.toml").write_text(f'state_dir = "{state}"\nworktree_root = "{wt_root}"\n')
    (state / "tasks").mkdir(parents=True)
    worktree, base = WorktreeManager(repo, wt_root).create("t1")
    (worktree / "b.txt").write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    tip = git(worktree, "rev-parse", "HEAD")
    TaskStore(state / "tasks").save(Task(id="t1", prompt="p", state=TaskState.AUDITED, worktree=str(worktree),
                                         base_sha=base, audited_sha=tip, audit_findings=("synthetic",)))
    factory = ["uv", "run", "--project", str(ROOT / "orchestrator"), "factory", "merge", "t1"]
    print(f"scratch repo: {repo}\n\n--- first merge: answer y ---")
    first = subprocess.run(factory, cwd=repo, env=ENV).returncode
    print("\n--- second merge: expect a refusal ---")
    second = subprocess.run(factory, cwd=repo, env=ENV).returncode
    task = json.loads((state / "tasks" / "t1.json").read_text())
    checks = {
        "first merge exit 0": first == 0,
        "second merge exit 1": second == 1,
        "main HEAD == audited sha": git(repo, "rev-parse", "HEAD") == tip,
        "task state merged": task["state"] == "merged",
        "worktree directory gone": not worktree.exists(),
        "branch factory/t1 kept": bool(git(repo, "branch", "--list", "factory/t1")),
        "real repo worktrees untouched": git(ROOT, "worktree", "list") == real_before,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nclean up: rm -rf {scratch}")
    sys.exit(0 if all(checks.values()) else 1)


main()
