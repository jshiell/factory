from pathlib import Path

from factory.gate import check_gate, commit_all, has_nested_repo
from factory.store import TaskStore
from factory.task import Event, Task, apply


def verify_task(
    task_id: str,
    store: TaskStore,
    project_subdir: str,
    test_argv: list[str],
    gate_timeout_s: float,
) -> Task:
    task = store.load(task_id)
    apply(task, Event.GATE_PASSED)  # reject tasks that are not agent_done before touching git
    worktree = Path(task.worktree)
    if has_nested_repo(worktree, project_subdir):  # before `git add -A` records it as a gitlink
        failed = apply(task, Event.GATE_FAILED, reason="nested_repo")
        store.save(failed)
        return failed
    commit_all(worktree, f"Task {task_id}")
    result = check_gate(worktree, task.base_sha, project_subdir, test_argv, gate_timeout_s)
    if result.passed:
        verified = apply(task, Event.GATE_PASSED)
    else:
        verified = apply(task, Event.GATE_FAILED, reason=result.reason)
    store.save(verified)
    return verified
