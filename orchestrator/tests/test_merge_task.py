from dataclasses import replace

import pytest

from gitutil import git

from factory.merge_task import MergeRefused, merge_preview
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.worktrees import WorktreeManager


@pytest.fixture
def audited(tmp_path, repo):
    """An audited task t1: a worktree branch one commit ahead of main."""
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    manager = WorktreeManager(repo, tmp_path / "wt")
    worktree, base = manager.create("t1")
    (worktree / "b.txt").write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    tip = git(worktree, "rev-parse", "HEAD")
    task = Task(id="t1", prompt="p", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base,
                audited_sha=tip, audit_findings=("looks fine",))
    store.save(task)
    return store, repo, worktree, task, manager


def test_preview_of_an_audited_task_names_the_branch_diffstat_and_findings(audited):
    store, repo, _, _, _ = audited

    preview = merge_preview("t1", store, repo)

    assert preview.branch == "factory/t1"
    assert "b.txt" in preview.diffstat
    assert preview.findings == ("looks fine",)
