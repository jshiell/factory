"""Run the auditor over the labelled corpus (plan 22, promoted by plan 23)."""
import json
import os
import subprocess
from pathlib import Path

from factory.approve_plan import build_implementation_prompt
from factory.audit_corpus import BASE_FILES, PROJECT_SUBDIR, Case
from factory.audit_task import AUDIT_CONFIG, audit_task
from factory.gate import commit_all
from factory.ledger import Ledger
from factory.step import Runner
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager, run_git

_IDENTITY = ("-c", "user.name=factory", "-c", "user.email=factory@localhost", "-c", "commit.gpgsign=false")


def build_base_repo(root: Path) -> Path:
    repo = root / "base"
    for rel, content in BASE_FILES.items():
        path = repo / PROJECT_SUBDIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (repo / ".gitignore").write_text(BASE_FILES[".gitignore"])
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    run_git(repo, "add", "-A")
    run_git(repo, *_IDENTITY, "commit", "-q", "-m", "base")
    return repo


def materialise(case: Case, manager: WorktreeManager, task_id: str) -> tuple[Path, str]:
    """A fresh worktree with the case's after-state committed on top of the shared base."""
    worktree, base_sha = manager.create(task_id)
    for rel, content in case.files.items():
        path = worktree / PROJECT_SUBDIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    commit_all(worktree, "change")
    return worktree, base_sha


def outcome_of(task: Task) -> str:
    """The audited task's state decides first: a stale `audit_error` on a finished task means nothing."""
    if task.state is TaskState.AUDITED:
        return "pass"
    if task.state is TaskState.FAILED:
        return "fail" if task.failure_reason == "audit_rejected" else str(task.failure_reason)
    if task.state is TaskState.VERIFIED and task.audit_error:
        return f"infra:{task.audit_error}"
    return f"unexpected:{task.state.value}"


def result_path(eval_dir: Path, case_id: str, run: int) -> Path:
    return eval_dir / f"{case_id}-r{run}.json"


def run_one(
    case: Case, run: int, manager: WorktreeManager, store: TaskStore, ledger: Ledger, runner: Runner,
    cap_usd: float, eval_dir: Path,
) -> dict:
    task_id = f"a{run}-{case.id}"
    worktree, base_sha = materialise(case, manager, task_id)
    try:
        store.save(Task(
            id=task_id, prompt=build_implementation_prompt(case.goal, case.plan), state=TaskState.VERIFIED,
            worktree=str(worktree), base_sha=base_sha, goal=case.goal, plan=case.plan,
        ))
        before = len(ledger.entries())
        task = audit_task(task_id, store, ledger, runner, AUDIT_CONFIG, cap_usd, PROJECT_SUBDIR)
        spent = ledger.entries()[before:]
    finally:
        manager.remove(task_id)
    record = {
        "case": case.id, "run": run, "label": case.label, "category": case.category,
        "outcome": outcome_of(task), "findings": list(task.audit_findings),
        "cost_usd": round(sum(e["cost_usd"] for e in spent if not e["assumed"]), 4),
        "assumed_usd": round(sum(e["cost_usd"] for e in spent if e["assumed"]), 4),
    }
    _write_atomically(result_path(eval_dir, case.id, run), json.dumps(record, indent=1))
    return record


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)
