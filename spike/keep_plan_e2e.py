"""THROWAWAY e2e for plan 19 (run in a real terminal; it needs a TTY for the y/N prompts). No API calls, $0.

Scratch repo (never touches this repo) with three tasks: f1 failed after an approved plan, r1 failed with a plan that was
never approved (empty prompt), a1 audited with an approved plan. Runs `factory retry --keep-plan`:
  r1 -> refused without a prompt; f1 with no TTY -> refused; f1 -> you answer n (nothing), then y (queued task);
  a1 -> you answer y (queued task, warning shown). Then `factory advance` on the first kept-plan task is NOT run (it costs money).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))

from factory.approve_plan import build_implementation_prompt  # noqa: E402
from factory.digest import plan_digest  # noqa: E402
from factory.store import TaskStore  # noqa: E402
from factory.task import Task, TaskState  # noqa: E402

ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
PLAN = "1. add multiply\n2. test it"
S = TaskState


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


def main():
    real_before = git(ROOT, "worktree", "list")
    scratch = Path(tempfile.mkdtemp(prefix="keep-plan-e2e-", dir=Path.home())).resolve()
    repo, state = scratch / "repo", scratch / "state"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "factory.toml").write_text(f'state_dir = "{state}"\nworktree_root = "{scratch / "wt"}"\n')
    (state / "tasks").mkdir(parents=True)
    store = TaskStore(state / "tasks")
    approved = build_implementation_prompt("add multiply", PLAN)
    common = dict(goal="add multiply", plan=PLAN, plan_digest=plan_digest(PLAN), plan_risks=("risky",), review_findings=("fine",))
    store.save(Task(id="f1", prompt=approved, state=S.FAILED, failure_reason="tests_failed", **common))
    store.save(Task(id="r1", prompt="", state=S.FAILED, failure_reason="plan_rejected_by_human", **common))
    store.save(Task(id="a1", prompt=approved, state=S.AUDITED, audited_sha="abc", audit_findings=("naming is odd",), **common))

    def factory(*args, stdin=None):
        print(f"\n$ factory {' '.join(args)}")
        return subprocess.run(["uv", "run", "--project", str(ROOT / "orchestrator"), "factory", *args], cwd=repo, env=ENV,
                              stdin=stdin, capture_output=stdin is subprocess.DEVNULL, text=True)

    def kept():
        return sorted(t for t in (p.stem for p in (state / "tasks").glob("*.json")) if t not in ("f1", "r1", "a1"))

    r1 = factory("retry", "r1", "--keep-plan")
    after_r1 = kept()
    f1_notty = factory("retry", "f1", "--keep-plan", stdin=subprocess.DEVNULL)
    print(f1_notty.stderr)
    after_notty = kept()
    print("\n>>> answer n to the next prompt")
    f1_no = factory("retry", "f1", "--keep-plan")
    after_no = kept()
    print("\n>>> answer y to the next prompt")
    f1_yes = factory("retry", "f1", "--keep-plan")
    after_yes = kept()
    print("\n>>> answer y to the next prompt (an audited source: expect the mergeable warning)")
    a1_yes = factory("retry", "a1", "--keep-plan")
    after_a1 = kept()
    new = [store.load(t) for t in after_a1]
    checks = {
        "r1 (never approved) refused, exit 1, nothing created": r1.returncode == 1 and after_r1 == [],
        "f1 without a TTY refused, exit 1, nothing created": f1_notty.returncode == 1 and "interactive" in f1_notty.stderr and after_notty == [],
        "f1 answered n: exit 1, nothing created": f1_no.returncode == 1 and after_no == [],
        "f1 answered y: exit 0, one queued task with retry_of f1 and the approved prompt":
            f1_yes.returncode == 0 and len(after_yes) == 1 and store.load(after_yes[0]).state is S.QUEUED
            and store.load(after_yes[0]).retry_of == "f1" and store.load(after_yes[0]).prompt == approved,
        "a1 answered y: a second queued task with retry_of a1": a1_yes.returncode == 0 and len(after_a1) == 2
            and sorted(t.retry_of for t in new) == ["a1", "f1"],
        "sources untouched": store.load("f1").state is S.FAILED and store.load("a1").state is S.AUDITED,
        "real repo worktrees untouched": git(ROOT, "worktree", "list") == real_before,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nclean up: rm -rf {scratch}")
    sys.exit(0 if all(checks.values()) else 1)


main()
