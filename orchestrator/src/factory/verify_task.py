from collections.abc import Callable
from pathlib import Path

from factory.gate import GateResult, check_gate, commit_all, has_nested_repo
from factory.gate_sandbox import GateSandbox
from factory.store import TaskStore
from factory.task import Event, Task, apply


def verify_task(
    task_id: str,
    store: TaskStore,
    project_subdir: str,
    test_argv: list[str],
    gate_timeout_s: float,
    sandbox: Callable[[Task], GateSandbox] | None = None,
) -> Task:
    task = store.load(task_id)
    apply(task, Event.GATE_PASSED)  # reject tasks that are not agent_done before touching git
    result = _gate_once(task, project_subdir, test_argv, gate_timeout_s, sandbox)
    if result.passed:
        verified = apply(task, Event.GATE_PASSED)
    else:
        verified = apply(task, Event.GATE_FAILED, reason=result.reason)
    store.save(verified)
    return verified


def _gate_once(
    task: Task, project_subdir: str, test_argv: list[str], gate_timeout_s: float,
    sandbox: Callable[[Task], GateSandbox] | None,
) -> GateResult:
    worktree = Path(task.worktree)
    if has_nested_repo(worktree, project_subdir):  # before `git add -A` records it as a gitlink
        return GateResult(False, "nested_repo")
    commit_all(worktree, f"Task {task.id}")
    if sandbox is None:
        return check_gate(worktree, task.base_sha, project_subdir, test_argv, gate_timeout_s)
    confined = sandbox(task)
    return check_gate(
        worktree, task.base_sha, project_subdir, list(confined.argv), gate_timeout_s,
        env=confined.env, test_prefix=confined.prefix,
    )
