"""Run the auditor over the labelled corpus (plan 22, promoted by plan 23)."""
import dataclasses
import hashlib
import json
import os
import tempfile
from pathlib import Path

from factory.approve_plan import build_implementation_prompt
from factory.audit_corpus import BASE_FILES, PROJECT_SUBDIR, Case, case_hash, corpus_hash
from factory.audit_task import AUDIT_CONFIG, audit_task, build_audit_prompt
from factory.gate import commit_all
from factory.ledger import Ledger
from factory.step import BudgetCapReached, Runner
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
    run_git(repo, "init", "-q", "-b", "main")
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


class CorpusChanged(Exception):
    """A result directory was made from different cases: its results cannot be mixed with new ones."""


def meta_path(eval_dir: Path) -> Path:
    return eval_dir / "meta.json"


def load_meta(eval_dir: Path) -> dict:
    return json.loads(meta_path(eval_dir).read_text())


def _prompt_hash() -> str:
    template = build_audit_prompt(Task(id="x", prompt="{TASK}"), "{DIFF}")
    return hashlib.sha256(template.encode()).hexdigest()[:16]


def _meta(cases: list[Case], runs: int, complete: bool) -> dict:
    return {
        "corpus_hash": corpus_hash(cases), "case_hashes": {c.id: case_hash(c) for c in cases},
        "cases": [c.id for c in cases], "runs": runs, "complete": complete,
        "audit_config": dataclasses.asdict(AUDIT_CONFIG), "prompt_hash": _prompt_hash(),
    }


def valid_result(eval_dir: Path, case_id: str, run: int) -> dict | None:
    try:
        record = json.loads(result_path(eval_dir, case_id, run).read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and "outcome" in record else None


def run_eval(eval_dir: Path, cases: list[Case], runs: int, runner: Runner, cap_usd: float) -> bool:
    """Round-robin over runs, resumable. True when every (case, run) has a result."""
    (eval_dir / "tasks").mkdir(parents=True, exist_ok=True)
    if meta_path(eval_dir).exists() and load_meta(eval_dir)["corpus_hash"] != corpus_hash(cases):
        raise CorpusChanged(str(eval_dir))
    _write_atomically(meta_path(eval_dir), json.dumps(_meta(cases, runs, complete=False), indent=1))
    store, ledger = TaskStore(eval_dir / "tasks"), Ledger(eval_dir / "ledger.jsonl")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = WorktreeManager(build_base_repo(root), root / "wt")
        for run in range(1, runs + 1):
            for case in cases:
                if valid_result(eval_dir, case.id, run):
                    continue
                try:
                    run_one(case, run, manager, store, ledger, runner, cap_usd, eval_dir)
                except BudgetCapReached:
                    return False
    _write_atomically(meta_path(eval_dir), json.dumps(_meta(cases, runs, complete=True), indent=1))
    return True
