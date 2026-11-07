"""THROWAWAY e2e for plan 11 (run in a real terminal; `clean` needs a TTY for its y/N prompt).

Synthetic repo with tasks in queued, agent_done, verified, audited, running and merged states, gate-cache
dirs (one filled by a real `uv` run: needs network, no API calls), an orphan cache and a symlink entry.
Runs `abandon` on each, then `clean`. Never touches this repo. $0.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

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
    scratch = Path(tempfile.mkdtemp(prefix="abandon-e2e-", dir=Path.home())).resolve()
    repo, state, wt_root = scratch / "repo", scratch / "state", scratch / "wt"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "factory.toml").write_text(f'state_dir = "{state}"\nworktree_root = "{wt_root}"\n')
    (state / "tasks").mkdir(parents=True)
    cache_root = state / "gate-cache"
    store, manager = TaskStore(state / "tasks"), WorktreeManager(repo, wt_root)
    paths, bases = {}, {}
    for name in ("q1", "d1", "v1", "u1", "r1", "m1"):
        paths[name], bases[name] = manager.create(name)

    def save(name, state_, **fields):
        store.save(Task(id=name, prompt="p", state=state_, goal=f"goal {name}", worktree=str(paths[name]),
                        base_sha=bases[name], **fields))

    for name, state_ in (("q1", S.QUEUED), ("d1", S.AGENT_DONE), ("v1", S.VERIFIED), ("u1", S.AUDITED), ("r1", S.RUNNING)):
        save(name, state_)
    (paths["m1"] / "m1.txt").write_text("m1")
    git(paths["m1"], "add", "-A")
    git(paths["m1"], "commit", "-q", "-m", "m1")
    tip = git(paths["m1"], "rev-parse", "HEAD")
    save("m1", S.AUDITED, audited_sha=tip)
    merge_task("m1", store, repo, manager, shown_sha=tip)

    print("filling a real uv cache (needs network)...")
    (cache_root / "q1").mkdir(parents=True)
    real = subprocess.run(["uv", "run", "--no-project", "--with", "pytest", "python", "-c", "pass"], cwd=scratch,
                          env={**ENV, "UV_CACHE_DIR": str(cache_root / "q1"), "UV_LINK_MODE": "copy"},
                          capture_output=True, text=True)
    print(f"real uv cache fill exit {real.returncode}")
    for name in ("d1", "r1", "m1", "gone"):
        (cache_root / name).mkdir()
        (cache_root / name / "f").write_text("cache")
    precious = scratch / "precious"
    precious.mkdir()
    (precious / "keep").write_text("keep")
    os.symlink(precious, cache_root / "linked")

    def factory(*args):
        print(f"\n$ factory {' '.join(args)}")
        return subprocess.run(["uv", "run", "--project", str(ROOT / "orchestrator"), "factory", *args], cwd=repo, env=ENV)

    abandoned = {name: factory("abandon", name).returncode for name in ("q1", "d1", "v1", "u1")}
    refused = {name: factory("abandon", name).returncode for name in ("r1", "m1")}
    print("\n--- clean: you should see q1, d1, v1, u1 (abandoned), m1 (cache), orphan 'gone' and skipped 'linked'; answer y ---")
    clean = factory("clean").returncode
    checks = {
        "abandon exit 0 for queued, agent_done, verified, audited": all(code == 0 for code in abandoned.values()),
        "abandoned tasks are failed/abandoned": all(
            store.load(n).state is S.FAILED and store.load(n).failure_reason == "abandoned" for n in abandoned),
        "abandon refused (exit 1) for running and merged": all(code == 1 for code in refused.values()),
        "running r1 untouched": store.load("r1").state is S.RUNNING and paths["r1"].exists() and (cache_root / "r1").exists(),
        "clean exit 1 (the symlink entry is skipped)": clean == 1,
        "abandoned worktrees removed": not any(paths[n].exists() for n in abandoned),
        "abandoned branches kept": all(git(repo, "branch", "--list", f"factory/{n}") for n in abandoned),
        "real uv cache of q1 removed": real.returncode == 0 and not (cache_root / "q1").exists(),
        "caches of d1 and merged m1 removed": not (cache_root / "d1").exists() and not (cache_root / "m1").exists(),
        "orphan cache removed": not (cache_root / "gone").exists(),
        "symlink entry and its target untouched": (cache_root / "linked").is_symlink() and (precious / "keep").exists(),
        "real repo worktrees and branches untouched": (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list")) == real_before,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nclean up: rm -rf {scratch}")
    sys.exit(0 if all(checks.values()) else 1)


main()
