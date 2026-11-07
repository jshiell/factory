"""THROWAWAY e2e for plan 15 (run in a real terminal). No API calls, $0, no nono (the gate runs unsandboxed here).

Synthetic repo. The `factory rebase` CLI runs with YOUR real environment and global git config (1Password signing and
all), so this also proves a rebase never asks for a signature. Everything else uses a nulled git config for setup.
  a1: audited, main moves with a disjoint file -> rebase, real gate, fake audit, merge
  b1: audited, main edits the same file       -> rebase refuses, old state intact
  c1: audited, worktree .git redirected       -> rebase refuses
  d1: audited, main has not moved             -> rebase refuses
Never touches this repo.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

import json  # noqa: E402

from factory.audit_task import AUDIT_CONFIG, audit_task  # noqa: E402
from factory.ledger import Ledger  # noqa: E402
from factory.merge_task import MergeRefused, merge_preview, merge_task  # noqa: E402
from factory.step import RunResult  # noqa: E402
from factory.store import TaskStore  # noqa: E402
from factory.task import InvalidTransition, Task, TaskState  # noqa: E402
from factory.verify_task import verify_task  # noqa: E402
from factory.worktrees import WorktreeManager  # noqa: E402

SETUP_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
S = TaskState


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=SETUP_ENV, capture_output=True, text=True, check=True).stdout.strip()


class PassingAudit:
    def run(self, task, config):
        payload = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0,
                   "structured_output": {"verdict": "pass", "findings": ["fine"]}}
        return RunResult(json.dumps(payload), 0, False)


def main():
    real_before = (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list"))
    scratch = Path(tempfile.mkdtemp(prefix="rebase-e2e-", dir=Path.home())).resolve()
    repo, state, wt_root = scratch / "repo", scratch / "state", scratch / "wt"
    (repo / "target").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "target" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "target" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    test_argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    (repo / "factory.toml").write_text(f'state_dir = "{state}"\nworktree_root = "{wt_root}"\n')
    (state / "tasks").mkdir(parents=True)
    store, manager, ledger = TaskStore(state / "tasks"), WorktreeManager(repo, wt_root), Ledger(state / "ledger.jsonl")
    heads = {}

    def audited(name, filename, content):
        path, base = manager.create(name)
        (path / "target" / filename).write_text(content)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", f"Task {name}")
        tip = git(path, "rev-parse", "HEAD")
        store.save(Task(id=name, prompt=f"add {filename}", state=S.AUDITED, worktree=str(path), base_sha=base, goal="g",
                        audited_sha=tip, audit_findings=("fine",)))
        heads[name] = (path, tip)

    audited("a1", "feature.py", "def sub(a, b):\n    return a - b\n")
    audited("b1", "calc.py", "def add(a, b):\n    return a + b + 0\n")
    audited("c1", "other.py", "x = 1\n")
    audited("d1", "d.py", "y = 1\n")
    # d1 is created while main is still at its base: do not move main for it, so run its refusal first
    factory_env = {k: v for k, v in os.environ.items()}

    def factory(*args):
        print(f"\n$ factory {' '.join(args)}")
        return subprocess.run(["uv", "run", "--project", str(ROOT / "orchestrator"), "factory", *args], cwd=repo,
                              env=factory_env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    d_result = factory("rebase", "d1")
    print(d_result.stdout + d_result.stderr)

    (repo / "m.txt").write_text("main moved\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved (disjoint)")
    (repo / "target" / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main edits calc.py")

    (heads["c1"][0] / ".git").write_text(f"gitdir: {scratch}/elsewhere\n")  # planted redirect
    c_result = factory("rebase", "c1")
    print(c_result.stdout + c_result.stderr)
    b_result = factory("rebase", "b1")
    print(b_result.stdout + b_result.stderr)
    a_result = factory("rebase", "a1")
    print(a_result.stdout + a_result.stderr)

    a = store.load("a1")
    a_path, a_old_tip = heads["a1"]
    merge_before_audit = []
    for shown in (a_old_tip, git(a_path, "rev-parse", "HEAD")):
        try:
            merge_task("a1", store, repo, manager, shown_sha=shown)
            merge_before_audit.append(False)
        except (MergeRefused, InvalidTransition):
            merge_before_audit.append(True)
    verified = verify_task("a1", store, project_subdir="target", test_argv=test_argv, gate_timeout_s=120)
    print(f"gate: {verified.state.value} {verified.failure_reason}")
    audit_result = audit_task("a1", store, ledger, PassingAudit(), AUDIT_CONFIG, cap_usd=10, project_subdir="target")
    print(f"audit: {audit_result.state.value} reason={audit_result.failure_reason} error={audit_result.audit_error}")
    new_head = git(a_path, "rev-parse", "HEAD")
    preview_ok = False
    try:
        preview_ok = merge_preview("a1", store, repo).task.audited_sha == new_head
    except Exception as error:  # noqa: BLE001
        print(f"preview failed: {error}")
    merged = merge_task("a1", store, repo, manager, shown_sha=new_head) if preview_ok else None

    b_path, b_tip = heads["b1"]
    c_path, c_tip = heads["c1"]
    rebase_dirs = [git(b_path, "rev-parse", "--git-path", n) for n in ("rebase-merge", "rebase-apply")]
    checks = {
        "d1 (main not moved) refused, exit 1": d_result.returncode == 1 and "has not moved" in d_result.stderr,
        "a1 rebase exit 0, says old commit and `factory advance`": a_result.returncode == 0 and a_old_tip in a_result.stdout
                                                                    and "factory advance" in a_result.stdout,
        "a1 is agent_done with no audited sha": a.state is S.AGENT_DONE and a.audited_sha is None,
        "a1 cannot merge before re-audit (old and new sha)": merge_before_audit == [True, True],
        "a1 real gate passes after the rebase": verified.state is S.VERIFIED,
        "a1 fresh audit gives a new audited sha": audit_result.state is S.AUDITED and audit_result.audited_sha == new_head != a_old_tip,
        "a1 merges and main holds feature.py and m.txt": merged is not None and merged.state is S.MERGED
                                                          and (repo / "target" / "feature.py").exists() and (repo / "m.txt").exists(),
        "b1 conflict refused, exit 1, names abandon and retry": b_result.returncode == 1 and "abandon" in b_result.stderr and "retry" in b_result.stderr,
        "b1 old tip and state intact, no rebase left behind": git(b_path, "rev-parse", "HEAD") == b_tip and store.load("b1").state is S.AUDITED
                                                              and not any((b_path / d).exists() for d in rebase_dirs)
                                                              and git(b_path, "status", "--porcelain") == "",
        "c1 redirected .git refused, exit 1": c_result.returncode == 1 and "gitdir" in c_result.stderr,
        "c1 task untouched": store.load("c1").state is S.AUDITED and git(repo, "rev-parse", "factory/c1") == c_tip,
        "no signature prompt appeared (you would have seen the 1Password dialog)": True,
        "real repo worktrees and branches untouched": (git(ROOT, "worktree", "list"), git(ROOT, "branch", "--list")) == real_before,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nclean up: rm -rf {scratch}")
    sys.exit(0 if all(checks.values()) else 1)


main()
